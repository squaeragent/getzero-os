#!/usr/bin/env python3
"""
SessionManager — strategy-parameterized session lifecycle.

ONE active session at a time (mutually exclusive). Provides parameters
that local_evaluator and risk_guard read to control evaluation scope,
position limits, and risk thresholds.

Session state persisted to bus/session.json.
Session history appended to bus/session_history.jsonl.

Usage:
    python -m scanner.v6.session_manager activate momentum --agent zr_phantom --equity 1000
    python -m scanner.v6.session_manager status
    python -m scanner.v6.session_manager complete [--reason expired]
    python -m scanner.v6.session_manager history
"""

import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scanner.v6.bus_io import load_json_locked, save_json_locked, append_jsonl
from scanner.v6.config import BUS_DIR, ALL_COINS

# ─── PATHS ────────────────────────────────────────────────────────────────────
SESSION_FILE = BUS_DIR / "session.json"
SESSION_HISTORY_FILE = BUS_DIR / "session_history.jsonl"

# ─── STRATEGY CONFIGS ─────────────────────────────────────────────────────────

STRATEGIES = {
    'momentum': {
        'name': 'momentum_surf', 'icon': '🏄',
        'duration_hours': 48, 'credit_cost': 400,
        'consensus_threshold': 6,  # 6 of 7
        'allowed_regimes': ['trending'],
        'max_positions': 3, 'position_size_pct': 0.12,
        'stop_pct': 0.035, 'trailing_stop_pct': 0.02,
        'max_hold_hours': 12,
        'directions': ['long', 'short'],
        'funding_filter': 'moderate',  # warn if |funding| > 0.02%
        'fear_greed_filter': {'block_entry_above': 80, 'boost_conviction_below': 25},
        'scope': 'top_20', 'kill_cooldown_min': 45, 'eval_interval_min': 30,
    },
    'degen': {
        'name': 'degen_mode', 'icon': '🔥',
        'duration_hours': 24, 'credit_cost': 500,
        'consensus_threshold': 5,
        'allowed_regimes': ['trending', 'stable', 'reverting'],
        'max_positions': 5, 'position_size_pct': 0.18,
        'stop_pct': 0.06, 'trailing_stop_pct': 0.03,
        'max_hold_hours': 24,
        'directions': ['long', 'short'],
        'funding_filter': 'off',
        'fear_greed_filter': None,
        'scope': 'all_40', 'kill_cooldown_min': 30, 'eval_interval_min': 15,
    },
    'defense': {
        'name': 'defense_protocol', 'icon': '🛡️',
        'duration_hours': 168, 'credit_cost': 200,
        'consensus_threshold': 7,
        'allowed_regimes': ['trending'],
        'max_positions': 1, 'position_size_pct': 0.05,
        'stop_pct': 0.015, 'trailing_stop_pct': None,
        'max_hold_hours': 6,
        'directions': ['long'],
        'funding_filter': 'strict',
        'fear_greed_filter': {'block_entry_above': 65},
        'scope': 'top_3', 'kill_cooldown_min': 30, 'eval_interval_min': 60,
        'circuit_breaker_daily_dd_pct': 0.02,
    },
    'sniper': {
        'name': 'sniper', 'icon': '🎯',
        'duration_hours': 72, 'credit_cost': 300,
        'consensus_threshold': 7,
        'allowed_regimes': ['trending'],
        'max_positions': 1, 'position_size_pct': 0.22,
        'stop_pct': 0.02, 'trailing_stop_pct': 0.015,
        'max_hold_hours': 8,
        'directions': ['long', 'short'],
        'funding_filter': 'strict',
        'fear_greed_filter': {'block_entry_above': 75},
        'scope': 'top_10', 'kill_cooldown_min': 60, 'eval_interval_min': 30,
        'max_trades_per_session': 1,
        'enrichment_required': {'min_confirming_layers': 7, 'max_warning_layers': 0},
    },
    'scout': {
        'name': 'scout_run', 'icon': '🏃',
        'duration_hours': 72, 'credit_cost': 600,
        'consensus_threshold': 5,
        'allowed_regimes': ['trending', 'stable', 'reverting'],
        'max_positions': 8, 'position_size_pct': 0.04,
        'stop_pct': 0.05, 'trailing_stop_pct': 0.03,
        'max_hold_hours': 24,
        'directions': ['long', 'short'],
        'funding_filter': 'relaxed',
        'fear_greed_filter': None,
        'scope': 'all_40', 'kill_cooldown_min': 90, 'eval_interval_min': 20,
        'trade_credit_bonus': 10,
    },
    'fade': {
        'name': 'fade_the_crowd', 'icon': '🔄',
        'duration_hours': 168, 'credit_cost': 200,
        'consensus_threshold': 5,
        'allowed_regimes': ['trending', 'stable', 'reverting', 'chaotic'],
        'max_positions': 2, 'position_size_pct': 0.17,
        'stop_pct': 0.05, 'trailing_stop_pct': 0.025,
        'max_hold_hours': 48,
        'directions': ['long', 'short'],
        'funding_filter': 'inverted',
        'fear_greed_filter': 'inverted',
        'scope': 'top_15', 'kill_cooldown_min': 120, 'eval_interval_min': 30,
        'activation_conditions': {'fear_greed_below': 20, 'fear_greed_above': 80, 'funding_abs_above': 0.0003},
        'end_conditions': {'fear_greed_normalized': [30, 70]},
    },
    'funding': {
        'name': 'funding_farm', 'icon': '💰',
        'duration_hours': 48, 'credit_cost': 150,
        'consensus_threshold': None,
        'allowed_regimes': ['trending', 'stable', 'reverting'],
        'max_positions': 3, 'position_size_pct': 0.25,
        'stop_pct': 0.03, 'trailing_stop_pct': None,
        'max_hold_hours': 48,
        'directions': ['funding_opposite'],
        'funding_filter': None,
        'fear_greed_filter': None,
        'scope': 'funding_opportunities', 'kill_cooldown_min': 60, 'eval_interval_min': 60,
        'entry_condition': {'min_funding_abs': 0.0002},
        'exit_condition': {'funding_flipped': True, 'funding_below_abs': 0.00005},
    },
    'watch': {
        'name': 'watch_mode', 'icon': '👁️',
        'duration_hours': 48, 'credit_cost': 100,
        'consensus_threshold': 6,
        'allowed_regimes': ['trending', 'stable', 'reverting'],
        'max_positions': 0,
        'position_size_pct': 0,
        'directions': ['long', 'short'],
        'scope': 'top_20', 'eval_interval_min': 30,
        'paper_only': True,
    },
    'apex': {
        'name': 'apex_adaptive', 'icon': '⚡',
        'duration_hours': 168, 'credit_cost': 1000,
        'min_score_required': 7.0,
        'adaptive': True,
    },
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [SESSION] {msg}", flush=True)


# ─── COIN SCOPE RESOLUTION ───────────────────────────────────────────────────

# Top coins by HL volume (static ordering — updated periodically)
_TOP_COINS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "SUI",
    "ADA", "NEAR", "OP", "BNB", "AAVE", "SEI", "TIA", "INJ",
    "DOT", "UNI", "LTC", "BCH", "WLD", "ONDO", "JUP", "TON",
]


def get_coins_for_scope(scope: str) -> list[str]:
    """Resolve scope string to coin list.

    top_3: [BTC, ETH, SOL]
    top_10: top 10 by HL volume
    top_15: top 15
    top_20: top 20
    all_40: full universe
    funding_opportunities: coins with |funding| > 0.02%
    """
    if scope == 'top_3':
        return _TOP_COINS[:3]
    elif scope == 'top_10':
        return _TOP_COINS[:10]
    elif scope == 'top_15':
        return _TOP_COINS[:15]
    elif scope == 'top_20':
        return _TOP_COINS[:20]
    elif scope == 'all_40':
        return list(ALL_COINS)
    elif scope == 'funding_opportunities':
        return _resolve_funding_opportunities()
    else:
        _log(f"  WARN: unknown scope '{scope}', falling back to top_20")
        return _TOP_COINS[:20]


def _resolve_funding_opportunities() -> list[str]:
    """Find coins with |funding rate| > 0.02% from bus data."""
    funding_file = BUS_DIR.parent.parent / "bus" / "funding.json"
    if not funding_file.exists():
        # Fallback: check v6 bus
        funding_file = BUS_DIR / "funding.json"
    try:
        data = load_json_locked(funding_file, {})
        rates = data.get("rates", data.get("funding_rates", {}))
        opportunities = []
        for coin, rate in rates.items():
            if isinstance(rate, (int, float)) and abs(rate) > 0.0002:
                opportunities.append(coin)
        return opportunities if opportunities else _TOP_COINS[:10]
    except Exception:
        return _TOP_COINS[:10]


# ─── SESSION CRUD ─────────────────────────────────────────────────────────────

def get_active_session() -> dict | None:
    """Load current session from bus/session.json. Returns None if no active session."""
    session = load_json_locked(SESSION_FILE, None)
    if session is None:
        return None
    if session.get('status') != 'active':
        return None
    return session


def activate_session(strategy_key: str, agent_id: str, equity: float) -> dict:
    """Create and persist a new session. Returns session dict.
    Raises if another session is already active."""
    if strategy_key not in STRATEGIES:
        raise ValueError(f"Unknown strategy: '{strategy_key}'. Valid: {', '.join(STRATEGIES.keys())}")

    existing = get_active_session()
    if existing is not None:
        raise RuntimeError(
            f"Session already active: {existing['session_id']} "
            f"(strategy={existing['strategy']}, expires={existing['expires_at']})"
        )

    strategy = STRATEGIES[strategy_key]
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=strategy['duration_hours'])

    session = {
        'session_id': str(uuid.uuid4()),
        'strategy': strategy_key,
        'agent_id': agent_id,
        'started_at': now.isoformat(),
        'expires_at': expires.isoformat(),
        'status': 'active',
        'credits_reserved': strategy['credit_cost'],
        'credits_used': 0,
        'trades': [],
        'open_positions': [],
        'paper_equity_start': equity,
        'paper_equity_current': equity,
        'total_pnl': 0.0,
        'eval_count': 0,
    }

    save_json_locked(SESSION_FILE, session)
    _log(f"Session activated: {strategy['icon']} {strategy['name']} "
         f"(id={session['session_id'][:8]}..., agent={agent_id}, "
         f"expires={expires.strftime('%Y-%m-%d %H:%M UTC')})")
    return session


def get_session_params() -> dict:
    """Return merged params: strategy config + session overrides.
    These override config.py values for local_evaluator and risk_guard."""
    session = get_active_session()
    if session is None:
        return {}

    strategy_key = session['strategy']
    strategy = STRATEGIES.get(strategy_key, {})

    # Merge strategy config with session runtime state
    params = dict(strategy)
    params['session_id'] = session['session_id']
    params['agent_id'] = session['agent_id']
    params['started_at'] = session['started_at']
    params['expires_at'] = session['expires_at']
    params['credits_remaining'] = session['credits_reserved'] - session['credits_used']
    params['eval_count'] = session['eval_count']
    params['total_pnl'] = session['total_pnl']

    # Resolve scope to actual coin list
    scope = strategy.get('scope')
    if scope:
        params['coins'] = get_coins_for_scope(scope)

    # Convert eval_interval_min to seconds for evaluator
    eval_min = strategy.get('eval_interval_min')
    if eval_min:
        params['eval_interval_sec'] = eval_min * 60

    return params


def check_session_expiry() -> bool:
    """Check if active session has expired. If so, complete it and generate result card.
    Returns True if session was completed."""
    session = get_active_session()
    if session is None:
        return False

    expires_at = session.get('expires_at', '')
    if not expires_at:
        return False

    try:
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False

    if datetime.now(timezone.utc) >= expires_dt:
        _log(f"Session expired: {session['session_id'][:8]}...")
        complete_session(session, reason='expired')
        return True

    return False


def complete_session(session: dict, reason: str = 'expired') -> dict:
    """Mark session as completed, calculate final P&L, generate result card,
    refund unused credits. Append to bus/session_history.jsonl."""
    session['status'] = 'completed'
    session['completed_at'] = _now_iso()
    session['completion_reason'] = reason

    # Calculate credit refund
    credits_used = session.get('credits_used', 0)
    credits_reserved = session.get('credits_reserved', 0)
    credits_refunded = max(0, credits_reserved - credits_used)

    # Generate result card
    result_card = generate_result_card(session)
    session['result_card'] = result_card

    # Save final session state
    save_json_locked(SESSION_FILE, session)

    # Append to history
    history_entry = {
        'session_id': session['session_id'],
        'strategy': session['strategy'],
        'agent_id': session.get('agent_id', ''),
        'duration_hours': STRATEGIES.get(session['strategy'], {}).get('duration_hours', 0),
        'started_at': session.get('started_at', ''),
        'completed_at': session.get('completed_at', ''),
        'reason': reason,
        'trades': len(session.get('trades', [])),
        'pnl': round(session.get('total_pnl', 0.0), 2),
        'credits_used': credits_used,
        'credits_refunded': credits_refunded,
        'eval_count': session.get('eval_count', 0),
        'result_card': result_card,
    }
    append_jsonl(SESSION_HISTORY_FILE, history_entry)

    strategy = STRATEGIES.get(session['strategy'], {})
    _log(f"Session completed: {strategy.get('icon', '')} {strategy.get('name', session['strategy'])} "
         f"| reason={reason} | trades={len(session.get('trades', []))} "
         f"| pnl=${session.get('total_pnl', 0):.2f} | credits_refunded={credits_refunded}")

    return result_card


def generate_result_card(session: dict) -> dict:
    """Generate the result card dict shown in UI after session ends."""
    strategy_key = session.get('strategy', '')
    strategy = STRATEGIES.get(strategy_key, {})
    trades = session.get('trades', [])

    # Trade stats
    winning = [t for t in trades if t.get('pnl', 0) > 0]
    losing = [t for t in trades if t.get('pnl', 0) < 0]
    total_pnl = session.get('total_pnl', 0.0)

    equity_start = session.get('paper_equity_start', 0)
    equity_end = session.get('paper_equity_current', equity_start)
    roi_pct = ((equity_end - equity_start) / equity_start * 100) if equity_start > 0 else 0

    return {
        'strategy': strategy_key,
        'strategy_name': strategy.get('name', strategy_key),
        'icon': strategy.get('icon', ''),
        'session_id': session.get('session_id', ''),
        'agent_id': session.get('agent_id', ''),
        'started_at': session.get('started_at', ''),
        'completed_at': session.get('completed_at', _now_iso()),
        'reason': session.get('completion_reason', 'unknown'),
        'duration_hours': strategy.get('duration_hours', 0),
        'equity_start': round(equity_start, 2),
        'equity_end': round(equity_end, 2),
        'roi_pct': round(roi_pct, 2),
        'total_pnl': round(total_pnl, 2),
        'trade_count': len(trades),
        'wins': len(winning),
        'losses': len(losing),
        'win_rate': round(len(winning) / len(trades) * 100, 1) if trades else 0,
        'best_trade': round(max((t.get('pnl', 0) for t in trades), default=0), 2),
        'worst_trade': round(min((t.get('pnl', 0) for t in trades), default=0), 2),
        'eval_count': session.get('eval_count', 0),
        'credits_used': session.get('credits_used', 0),
        'credits_refunded': max(0, session.get('credits_reserved', 0) - session.get('credits_used', 0)),
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _cli_activate(args: list[str]):
    if not args:
        print(f"Usage: activate <strategy> [--agent ID] [--equity N]")
        print(f"Strategies: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)

    strategy_key = args[0]
    agent_id = 'cli_user'
    equity = 1000.0

    i = 1
    while i < len(args):
        if args[i] == '--agent' and i + 1 < len(args):
            agent_id = args[i + 1]
            i += 2
        elif args[i] == '--equity' and i + 1 < len(args):
            equity = float(args[i + 1])
            i += 2
        else:
            i += 1

    try:
        session = activate_session(strategy_key, agent_id, equity)
        strategy = STRATEGIES[strategy_key]
        print(f"\n{strategy['icon']} Session activated: {strategy['name']}")
        print(f"  ID:       {session['session_id']}")
        print(f"  Agent:    {agent_id}")
        print(f"  Equity:   ${equity:.2f}")
        print(f"  Credits:  {strategy['credit_cost']}")
        print(f"  Expires:  {session['expires_at']}")
        print(f"  Scope:    {strategy.get('scope', 'N/A')}")
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def _cli_status():
    session = get_active_session()
    if session is None:
        # Check if there's a completed session in the file
        raw = load_json_locked(SESSION_FILE, None)
        if raw and raw.get('status') == 'completed':
            print(f"Last session: {raw['strategy']} — completed ({raw.get('completion_reason', '?')})")
            print(f"  PnL: ${raw.get('total_pnl', 0):.2f} | Trades: {len(raw.get('trades', []))}")
        else:
            print("No active session.")
        return

    strategy = STRATEGIES.get(session['strategy'], {})
    expires_dt = datetime.fromisoformat(session['expires_at'].replace("Z", "+00:00"))
    remaining = expires_dt - datetime.now(timezone.utc)
    remaining_h = remaining.total_seconds() / 3600

    print(f"\n{strategy.get('icon', '')} Active: {strategy.get('name', session['strategy'])}")
    print(f"  ID:          {session['session_id']}")
    print(f"  Agent:       {session.get('agent_id', '?')}")
    print(f"  Strategy:    {session['strategy']}")
    print(f"  Started:     {session['started_at']}")
    print(f"  Expires:     {session['expires_at']} ({remaining_h:.1f}h remaining)")
    print(f"  Credits:     {session.get('credits_used', 0)}/{session.get('credits_reserved', 0)} used")
    print(f"  Equity:      ${session.get('paper_equity_current', 0):.2f} (start: ${session.get('paper_equity_start', 0):.2f})")
    print(f"  PnL:         ${session.get('total_pnl', 0):.2f}")
    print(f"  Trades:      {len(session.get('trades', []))} closed, {len(session.get('open_positions', []))} open")
    print(f"  Evals:       {session.get('eval_count', 0)}")

    # Show strategy params
    print(f"\n  Strategy params:")
    for key in ['max_positions', 'position_size_pct', 'stop_pct', 'trailing_stop_pct',
                'max_hold_hours', 'directions', 'scope', 'consensus_threshold',
                'allowed_regimes', 'funding_filter', 'eval_interval_min']:
        if key in strategy:
            print(f"    {key}: {strategy[key]}")


def _cli_complete(args: list[str]):
    session = get_active_session()
    if session is None:
        print("No active session to complete.")
        sys.exit(1)

    reason = 'manual'
    if args and args[0] == '--reason' and len(args) > 1:
        reason = args[1]

    result_card = complete_session(session, reason=reason)
    strategy = STRATEGIES.get(session['strategy'], {})
    print(f"\n{strategy.get('icon', '')} Session completed: {strategy.get('name', session['strategy'])}")
    print(f"  Reason:          {reason}")
    print(f"  PnL:             ${result_card['total_pnl']:.2f}")
    print(f"  ROI:             {result_card['roi_pct']:.2f}%")
    print(f"  Trades:          {result_card['trade_count']} ({result_card['wins']}W / {result_card['losses']}L)")
    print(f"  Win rate:        {result_card['win_rate']:.1f}%")
    print(f"  Credits used:    {result_card['credits_used']}")
    print(f"  Credits refund:  {result_card['credits_refunded']}")


def _cli_history():
    if not SESSION_HISTORY_FILE.exists():
        print("No session history.")
        return

    print("\nSession History:")
    print(f"{'Strategy':<15} {'Agent':<15} {'PnL':>8} {'Trades':>7} {'Credits':>8} {'Reason':<10} {'Date'}")
    print("-" * 90)

    try:
        with open(SESSION_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                strategy = STRATEGIES.get(entry.get('strategy', ''), {})
                icon = strategy.get('icon', '')
                started = entry.get('started_at', '')[:10]
                print(f"{icon} {entry.get('strategy', '?'):<13} "
                      f"{entry.get('agent_id', '?'):<15} "
                      f"${entry.get('pnl', 0):>7.2f} "
                      f"{entry.get('trades', 0):>7} "
                      f"{entry.get('credits_used', 0):>8} "
                      f"{entry.get('reason', '?'):<10} "
                      f"{started}")
    except Exception as e:
        print(f"Error reading history: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scanner.v6.session_manager [activate|status|complete|history]")
        print(f"\nStrategies: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    if command == 'activate':
        _cli_activate(args)
    elif command == 'status':
        _cli_status()
    elif command == 'complete':
        _cli_complete(args)
    elif command == 'history':
        _cli_history()
    else:
        print(f"Unknown command: {command}")
        print("Usage: python -m scanner.v6.session_manager [activate|status|complete|history]")
        sys.exit(1)


if __name__ == "__main__":
    main()

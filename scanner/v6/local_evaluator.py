#!/usr/bin/env python3
"""
Local Evaluator — SmartProvider-based signal evaluation loop.

ZERO-COST. ZERO-DEPENDENCY. Runs entirely on HL public API.
Replaces ENVY WebSocket when upstream is unavailable.

Writes to the same bus/entries.json that risk_guard reads.
Also handles exits for open positions (max_hold, trailing stops).

Usage:
    python -m scanner.v6.local_evaluator --loop
    python -m scanner.v6.local_evaluator --once
"""

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.config import (
    BUS_DIR, ENTRIES_FILE, EXITS_FILE, POSITIONS_FILE, HEARTBEAT_FILE,
    ACTIVE_COINS_COUNT, get_stop_pct,
)

# Session-aware imports — graceful fallback to hardcoded behavior
try:
    from scanner.v6.session_manager import (
        get_active_session, get_session_params, check_session_expiry,
        get_coins_for_scope,
    )
    _SESSION_AVAILABLE = True
except ImportError:
    _SESSION_AVAILABLE = False

V6_DIR = Path(__file__).parent
WATCH_ENTRIES_FILE = BUS_DIR / "watch_entries.json"
EVAL_INTERVAL = 60  # seconds between evaluation cycles (1 min)
MIN_QUALITY = 6.0   # quality threshold (0-10 scale) — matches ENVY evaluator q≥6
_idle_log_counter = 0  # throttle idle logging

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [LOCAL_EVAL] {msg}", flush=True)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}

def save_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.rename(path)

def update_heartbeat():
    save_json_atomic(HEARTBEAT_FILE, {
        "component": "local_evaluator",
        "updated_at": now_iso(),
    })


def get_active_coins() -> list[str]:
    """Get active coins from strategies.json or fall back to top coins."""
    strategies = load_json(V6_DIR / "data" / "strategies.json", {})
    active = strategies.get("active_coins", [])
    if active:
        return active[:ACTIVE_COINS_COUNT]

    # Fallback: top coins by liquidity
    return [
        "BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "SUI",
        "ADA", "NEAR", "OP", "BNB", "AAVE", "SEI", "TIA", "INJ",
        "DOT", "UNI", "LTC", "BCH", "WLD", "ONDO", "JUP", "TON",
    ][:ACTIVE_COINS_COUNT]


def evaluate_entries(smart_provider, coins: list[str], open_coins: set,
                     session_params: dict | None = None) -> list[dict]:
    """Run SmartProvider on each coin, return entries that pass quality gate."""
    new_entries = []

    # Session-aware overrides
    if session_params:
        quality_gate = MIN_QUALITY + (session_params.get('consensus_threshold', 6) - 6) * 0.5
        allowed_dirs = [d.upper() for d in session_params.get('directions', ['long', 'short'])]
        stop_override = session_params.get('stop_pct')
        hold_override = session_params.get('max_hold_hours')
    else:
        quality_gate = MIN_QUALITY
        allowed_dirs = None
        stop_override = None
        hold_override = None

    for i, coin in enumerate(coins):
        if coin in open_coins:
            continue  # already have a position

        # Rate-limit: small delay between coins to avoid HL 429
        if i > 0 and i % 8 == 0:
            time.sleep(2)

        try:
            result = smart_provider.evaluate_coin(coin)
        except Exception as e:
            _log(f"  WARN: eval {coin} failed: {e}")
            continue

        direction = result.get("signal", "NEUTRAL")
        quality = result.get("quality", 0)
        regime = result.get("regime", "unknown")

        if direction == "NEUTRAL" or quality < quality_gate:
            continue

        # Direction filter from session
        if allowed_dirs and direction not in allowed_dirs:
            continue

        # Convert to entry format
        atr_pct = result.get("atr_pct", 0.05)
        stop_pct = stop_override if stop_override else get_stop_pct(coin)
        max_hold = hold_override if hold_override else 12

        entry = {
            "coin":            coin,
            "direction":       direction,
            "signal_name":     f"SMART_{direction}_{coin}_{regime}",
            "expression":      f"SMART_REGIME={regime}",
            "exit_expression": "",
            "max_hold_hours":  max_hold,
            "sharpe":          round(quality * 0.7, 2),
            "win_rate":        round(45 + quality * 5, 1),
            "composite_score": round(quality * 0.7, 2),
            "stop_loss_pct":   round(stop_pct, 4),
            "priority":        1,
            "fired_at":        now_iso(),
            "source":          "local_smart",
            "regime":          regime,
            "hurst":           result.get("hurst", 0),
            "quality_raw":     round(quality, 2),
        }

        _log(f"  ENTRY: {coin} {direction} q={quality:.1f} regime={regime} sharpe={entry['sharpe']}")
        new_entries.append(entry)

    return new_entries


def evaluate_exits(smart_provider, positions: list[dict]) -> list[dict]:
    """Check open positions for exit conditions."""
    new_exits = []

    for pos in positions:
        coin = pos.get("coin", "")
        direction = pos.get("direction", "LONG")
        max_hold = pos.get("max_hold_hours", 12)

        # Check max hold
        entry_str = pos.get("entry_time", "")
        if entry_str:
            try:
                entry_dt = datetime.fromisoformat(entry_str.replace("Z", "+00:00"))
                held_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                if held_hours > max_hold:
                    _log(f"  EXIT (max_hold): {coin} {direction} held {held_hours:.1f}h > {max_hold}h")
                    new_exits.append({
                        "coin": coin,
                        "direction": direction,
                        "reason": f"max_hold_{held_hours:.0f}h",
                        "timestamp": now_iso(),
                    })
                    continue
            except Exception:
                pass

        # Check signal reversal
        try:
            result = smart_provider.evaluate_coin(coin)
            sig_dir = result.get("signal", "NEUTRAL")
            quality = result.get("quality", 0)

            # Strong reversal: opposite direction with quality ≥ 5
            if quality >= 5 and sig_dir != "NEUTRAL" and sig_dir != direction:
                _log(f"  EXIT (signal_reversal): {coin} {direction} → SmartProvider says {sig_dir} q={quality:.1f}")
                new_exits.append({
                    "coin": coin,
                    "direction": direction,
                    "reason": f"signal_reversal_smart_{sig_dir}_q{quality:.0f}",
                    "timestamp": now_iso(),
                })
        except Exception as e:
            _log(f"  WARN: exit eval {coin} failed: {e}")

    return new_exits


def _increment_session_eval_count():
    """Bump eval_count on the active session."""
    try:
        from scanner.v6.bus_io import load_json_locked, save_json_locked
        from scanner.v6.session_manager import SESSION_FILE
        session = load_json_locked(SESSION_FILE, None)
        if session and session.get('status') == 'active':
            session['eval_count'] = session.get('eval_count', 0) + 1
            save_json_locked(SESSION_FILE, session)
    except Exception:
        pass


def run_cycle(smart_provider):
    """Single evaluation cycle."""
    global _idle_log_counter

    # ── Session gate ──────────────────────────────────────────────────────
    session_params = None
    paper_only = False

    if _SESSION_AVAILABLE:
        session = get_active_session()
        if session is None:
            _idle_log_counter += 1
            if _idle_log_counter == 1 or _idle_log_counter % 10 == 0:
                _log("[SESSION] No active session — agent idle, skipping evaluation")
            return 0, 0

        # Check expiry
        check_session_expiry()
        session = get_active_session()
        if session is None:
            _log("[SESSION] Session expired — agent idle")
            return 0, 0

        _idle_log_counter = 0  # reset since we have an active session
        session_params = get_session_params()
        paper_only = session_params.get('paper_only', False)

        strategy_name = session_params.get('name', session.get('strategy', '?'))
        _log(f"[SESSION] Using {strategy_name} params: "
             f"max_pos={session_params.get('max_positions')}, "
             f"scope={session_params.get('scope')}, "
             f"stop={session_params.get('stop_pct')}")

    # ── Coin selection ────────────────────────────────────────────────────
    if session_params and 'coins' in session_params:
        coins = session_params['coins']
    else:
        coins = get_active_coins()

    positions = load_json(POSITIONS_FILE, {}).get("positions", [])
    open_coins = {p["coin"] for p in positions}

    # Entries
    new_entries = evaluate_entries(smart_provider, coins, open_coins,
                                  session_params=session_params)

    if new_entries:
        if paper_only:
            # Watch mode: write to watch_entries.json, not real entries
            existing = load_json(WATCH_ENTRIES_FILE, {}).get("entries", [])
            existing.extend(new_entries)
            save_json_atomic(WATCH_ENTRIES_FILE, {"updated_at": now_iso(), "entries": existing})
            _log(f"  [WATCH] Wrote {len(new_entries)} watch entries (paper_only)")
        else:
            existing = load_json(ENTRIES_FILE, {}).get("entries", [])
            existing.extend(new_entries)
            save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso(), "entries": existing})
            _log(f"  Wrote {len(new_entries)} entries to bus")

    # Exits
    n_exits = 0
    if positions:
        new_exits = evaluate_exits(smart_provider, positions)
        n_exits = len(new_exits)
        if new_exits:
            existing = load_json(EXITS_FILE, {}).get("exits", [])
            existing.extend(new_exits)
            save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing})
            _log(f"  Wrote {n_exits} exits to bus")

    # Track session eval count
    if _SESSION_AVAILABLE and session_params:
        _increment_session_eval_count()

    update_heartbeat()
    return len(new_entries), n_exits


def run_loop():
    """Main loop — evaluate every EVAL_INTERVAL seconds."""
    from scanner.v6.smart_provider import SmartProvider

    _log("=== Local Evaluator starting ===")
    _log(f"  Interval: {EVAL_INTERVAL}s | Quality gate: q≥{MIN_QUALITY}")

    sp = SmartProvider()
    _log("  SmartProvider initialized")

    cycle = 0
    while True:
        cycle += 1
        try:
            n_entries, n_exits = run_cycle(sp)
            if cycle == 1 or cycle % 10 == 0 or n_entries > 0 or n_exits > 0:
                _log(f"  Cycle #{cycle}: {n_entries} entries, {n_exits} exits")
        except Exception as e:
            _log(f"  ERROR in cycle #{cycle}: {e}")
            traceback.print_exc()

        # Use session eval interval if available, else default
        interval = EVAL_INTERVAL
        if _SESSION_AVAILABLE:
            params = get_session_params()
            if params:
                interval = params.get('eval_interval_sec', EVAL_INTERVAL)
        time.sleep(interval)


def run_once():
    """Single evaluation — for testing."""
    from scanner.v6.smart_provider import SmartProvider

    _log("=== Local Evaluator — single run ===")
    sp = SmartProvider()
    coins = get_active_coins()
    _log(f"  Evaluating {len(coins)} coins: {', '.join(coins)}")

    positions = load_json(POSITIONS_FILE, {}).get("positions", [])
    open_coins = {p["coin"] for p in positions}

    for coin in coins:
        if coin in open_coins:
            _log(f"  {coin}: SKIP (position open)")
            continue
        try:
            result = sp.evaluate_coin(coin)
            direction = result.get("signal", "NEUTRAL")
            quality = result.get("quality", 0)
            regime = result.get("regime", "?")
            gate = "✅ PASS" if quality >= MIN_QUALITY and direction != "NEUTRAL" else "❌ SKIP"
            _log(f"  {coin}: {direction} q={quality:.1f} regime={regime} {gate}")
        except Exception as e:
            _log(f"  {coin}: ERROR {e}")


def main():
    if "--loop" in sys.argv:
        run_loop()
    elif "--once" in sys.argv:
        run_once()
    else:
        print("Usage: python -m scanner.v6.local_evaluator [--loop|--once]")
        sys.exit(1)


if __name__ == "__main__":
    main()

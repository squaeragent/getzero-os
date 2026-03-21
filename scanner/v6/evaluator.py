#!/usr/bin/env python3
"""
V6 Signal Evaluator — WebSocket + expression evaluation.

Connects to ENVY WebSocket (wss://gate.getzero.dev/api/claw/ws/indicators?token=KEY).
Every 15s receives all indicator values for 40 coins.
For each active coin: evaluates entry expressions.
For each open position: evaluates exit expressions (after MIN_HOLD_MINUTES).
Writes to scanner/v6/bus/entries.json and scanner/v6/bus/exits.json.

Usage:
  python3 scanner/v6/evaluator.py      # continuous WebSocket loop
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scanner.v6.bus_io import load_json_locked
from scanner.v6.config import (
    ENVY_WS_URL, STRATEGIES_FILE, POSITIONS_FILE, ENTRIES_FILE, EXITS_FILE,
    HEARTBEAT_FILE, BUS_DIR, STOP_LOSS_PCT, MIN_HOLD_MINUTES, get_env,
)

RECONNECT_DELAY     = 5
MAX_RECONNECT_DELAY = 300
ENTRY_DEDUP_WINDOW  = 300  # seconds — don't re-fire same signal within 5 min


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [EVAL] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    return time.time()


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as _e:
            pass
    return default


def save_json_atomic(path: Path, data: dict):
    """Write atomically via temp file to avoid partial reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE, {})
    hb["evaluator"] = now_iso()
    save_json_atomic(HEARTBEAT_FILE, hb)


# ─── EXPRESSION EVALUATOR ─────────────────────────────────────────────────────

def _evaluate_weighted(expression: str, values: dict) -> tuple[bool, list[str]]:
    """Evaluate weighted sum: ((IND op val) * weight) + ... >= threshold."""
    missing = []
    m = re.search(r'\)\s*(>=|>|<=|<)\s*(-?[\d.]+)\s*$', expression)
    if not m:
        return False, missing
    threshold_op  = m.group(1)
    threshold_val = float(m.group(2))

    terms = re.findall(
        r'\(\(([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)\)\s*\*\s*([\d.]+)\)',
        expression,
    )
    if not terms:
        return False, missing

    weighted_sum = 0.0
    for indicator, op, val_str, weight_str in terms:
        val    = float(val_str)
        weight = float(weight_str)
        cur    = values.get(indicator)
        if cur is None:
            missing.append(indicator)
            continue
        hit = (
            (op == ">="  and cur >= val) or
            (op == "<="  and cur <= val) or
            (op == ">"   and cur > val)  or
            (op == "<"   and cur < val)  or
            (op == "=="  and cur == val) or
            (op == "!="  and cur != val)
        )
        if hit:
            weighted_sum += weight

    result = (
        (threshold_op == ">="  and weighted_sum >= threshold_val) or
        (threshold_op == ">"   and weighted_sum > threshold_val)  or
        (threshold_op == "<="  and weighted_sum <= threshold_val) or
        (threshold_op == "<"   and weighted_sum < threshold_val)
    )
    return result, missing


def evaluate_expression(expression: str, values: dict) -> tuple[bool, list[str]]:
    """Evaluate a signal expression against indicator values.

    Handles: AND, OR, <=, >=, <, >, ==, !=, nested parens, weighted sums.
    Returns (fired: bool, missing_indicators: list[str]).
    """
    if not expression or not expression.strip():
        return False, []

    # Weighted sum expressions: ((IND op val) * weight) + ... >= threshold
    if "((" in expression and "*" in expression:
        return _evaluate_weighted(expression, values)

    missing  = []
    clauses  = re.split(r'\s+(AND|OR)\s+', expression)
    results  = []
    operators = []

    for part in clauses:
        part = part.strip().strip("()")
        if part in ("AND", "OR"):
            operators.append(part)
            continue

        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            results.append(False)
            continue

        indicator, op, val_str = m.group(1), m.group(2), m.group(3)
        val = float(val_str)
        cur = values.get(indicator)
        if cur is None:
            missing.append(indicator)
            results.append(False)
            continue

        results.append(
            (op == ">="  and cur >= val) or
            (op == "<="  and cur <= val) or
            (op == ">"   and cur > val)  or
            (op == "<"   and cur < val)  or
            (op == "=="  and cur == val) or
            (op == "!="  and cur != val)
        )

    if not results:
        return False, missing

    final = results[0]
    for i, op in enumerate(operators):
        if i + 1 < len(results):
            if op == "AND":
                final = final and results[i + 1]
            elif op == "OR":
                final = final or results[i + 1]

    return final, missing


# ─── STATE ────────────────────────────────────────────────────────────────────

# Track recently fired entries to avoid spamming the same signal
# {(coin, signal_name): last_fired_ts}
_entry_dedup: dict[tuple, float] = {}


def _is_duplicate_entry(coin: str, signal_name: str) -> bool:
    key = (coin, signal_name)
    last = _entry_dedup.get(key, 0)
    return (now_ts() - last) < ENTRY_DEDUP_WINDOW


def _mark_entry_fired(coin: str, signal_name: str):
    _entry_dedup[(coin, signal_name)] = now_ts()
    # Prune old entries
    cutoff = now_ts() - ENTRY_DEDUP_WINDOW * 2
    for k in list(_entry_dedup):
        if _entry_dedup[k] < cutoff:
            del _entry_dedup[k]


def _minutes_held(pos: dict) -> float:
    """Minutes since position entry_time."""
    try:
        entry_str = pos.get("entry_time", "")
        if not entry_str:
            return 0
        entry_dt = datetime.fromisoformat(entry_str.replace("Z", "+00:00"))
        now_dt   = datetime.now(timezone.utc)
        return (now_dt - entry_dt).total_seconds() / 60
    except Exception as _e:  # time parse fail
        return 0


# ─── EVALUATION CYCLE ─────────────────────────────────────────────────────────

# Signals proven to lose money (0% WR over 3+ trades, or consistently negative P&L)
SIGNAL_BLACKLIST = {
    "ARCH_CHAOS_REGIME_CONVERGENCE",  # 0% WR over 6 trades
    "ARCH_SOCIAL_EXHAUSTION_LONG",    # social signals unreliable
    "RSI_OVERBOUGHT_XONE_NEGATIVE_SHORT",  # -$0.43, 0% WR
}
# P0 intelligence 2026-03-20: SOCIAL, INFLUENCER, ARCH, CHAOS all 0% WR across 10+ trades
# Combined losses: -$2.61 (more than system's entire $2.41 net profit)
SIGNAL_FAMILY_BLACKLIST = {"SOCIAL", "INFLUENCER", "ICHIMOKU", "ARCH", "CHAOS"}

# P0 intelligence 2026-03-20: Portfolio optimizer shows negative assembled Sharpe.
# PUMP: -0.96, XPL: -2.39, TRUMP: -0.66. Every trade is negative EV.
COIN_BLACKLIST = {"PUMP", "XPL", "TRUMP"}


def evaluate_tick(flat_indicators: dict[str, dict]):
    """Called on each WS tick. flat_indicators = {COIN: {IND_CODE: value}}."""
    new_entries = []
    new_exits   = []

    # ── Entry evaluation ──────────────────────────────────────────────────────
    strategies = load_json(STRATEGIES_FILE, {})
    active_coins = strategies.get("active_coins", [])
    coins_data   = strategies.get("coins", {})
    positions    = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    open_coins   = {p["coin"] for p in positions}

    for coin in active_coins:
        if coin in COIN_BLACKLIST:
            continue  # negative portfolio Sharpe — don't open new positions
        if coin in open_coins:
            continue  # already have a position on this coin
        if coin not in flat_indicators:
            continue

        ind_values = flat_indicators[coin]
        coin_signals = coins_data.get(coin, {}).get("signals", [])

        for sig in coin_signals:
            expr      = sig.get("expression", "")
            sig_name  = sig.get("name", "")
            direction = sig.get("direction", "LONG")
            if not expr or not sig_name:
                continue
            # Signal blacklist check
            if sig_name in SIGNAL_BLACKLIST:
                continue
            if any(family in sig_name.upper() for family in SIGNAL_FAMILY_BLACKLIST):
                continue
            if _is_duplicate_entry(coin, sig_name):
                continue

            fired, missing = evaluate_expression(expr, ind_values)
            if fired:
                log(f"  ENTRY FIRED: {coin} {direction} [{sig_name}]")
                _mark_entry_fired(coin, sig_name)
                new_entries.append({
                    "coin":            coin,
                    "direction":       direction,
                    "signal_name":     sig_name,
                    "expression":      expr,
                    "exit_expression": sig.get("exit_expression", ""),
                    "max_hold_hours":  sig.get("max_hold_hours", 24),
                    "sharpe":          sig.get("sharpe", 0),
                    "win_rate":        sig.get("win_rate", 0),
                    "composite_score": sig.get("composite_score", 0),
                    "stop_loss_pct":   sig.get("stop_loss_pct", STOP_LOSS_PCT),
                    "priority":        sig.get("priority", 99),
                    "fired_at":        now_iso(),
                })
                break  # one signal per coin per tick (highest priority already first)

    # ── Exit evaluation ───────────────────────────────────────────────────────
    for pos in positions:
        coin       = pos.get("coin", "")
        pos_id     = pos.get("id", "")
        direction  = pos.get("direction", "LONG")
        exit_expr  = pos.get("exit_expression", "")
        max_hold   = pos.get("max_hold_hours", 24)
        held_mins  = _minutes_held(pos)

        if coin not in flat_indicators:
            continue

        ind_values = flat_indicators[coin]

        # Check max hold time
        if held_mins > max_hold * 60:
            log(f"  EXIT (max_hold): {coin} {direction} held {held_mins:.0f}m > {max_hold*60}m")
            new_exits.append({
                "coin":        coin,
                "direction":   direction,
                "position_id": pos_id,
                "reason":      "max_hold",
                "fired_at":    now_iso(),
            })
            continue

        # Stop loss always evaluates (HL stops are backup, this is primary)
        # Minimum hold gate only blocks expression exits and signal reversals
        entry_price = pos.get("entry_price", 0)
        stop_pct    = pos.get("stop_loss_pct", STOP_LOSS_PCT)
        cur_price   = ind_values.get("CLOSE_PRICE_15M") or ind_values.get("PRICE")
        if cur_price and entry_price and entry_price > 0:
            pnl_pct = (cur_price - entry_price) / entry_price
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            if pnl_pct <= -stop_pct:
                log(f"  EXIT (stop_loss): {coin} {direction} pnl={pnl_pct*100:.2f}%")
                new_exits.append({
                    "coin":        coin,
                    "direction":   direction,
                    "position_id": pos_id,
                    "reason":      "stop_loss",
                    "pnl_pct":     pnl_pct,
                    "fired_at":    now_iso(),
                })
                continue

        # Minimum hold gate — stops fire above, but expression/reversal exits wait
        if held_mins < MIN_HOLD_MINUTES:
            continue

        # ── E3: SIGNAL REVERSAL EXIT ──────────────────────────────────────
        # If the best signal for this coin now points OPPOSITE direction, close.
        # If NEUTRAL and we're losing, close. If NEUTRAL and winning, tighten to breakeven.
        coin_strategy = coins_data.get(coin, {})
        coin_signals = coin_strategy.get("signals", [])
        if coin_signals and cur_price and entry_price:
            # Find the highest-priority signal's direction
            best_signal = coin_signals[0] if coin_signals else {}
            best_direction = best_signal.get("direction", "").upper()
            
            if direction == "LONG" and best_direction == "SHORT":
                log(f"  EXIT (signal_reversal): {coin} LONG but best signal now SHORT ({best_signal.get('name', '?')[:40]})")
                new_exits.append({
                    "coin": coin, "direction": direction,
                    "position_id": pos_id, "reason": "signal_reversal",
                    "fired_at": now_iso(),
                })
                continue
            elif direction == "SHORT" and best_direction == "LONG":
                log(f"  EXIT (signal_reversal): {coin} SHORT but best signal now LONG ({best_signal.get('name', '?')[:40]})")
                new_exits.append({
                    "coin": coin, "direction": direction,
                    "position_id": pos_id, "reason": "signal_reversal",
                    "fired_at": now_iso(),
                })
                continue

        # Exit expression
        if exit_expr:
            fired, _ = evaluate_expression(exit_expr, ind_values)
            if fired:
                log(f"  EXIT (expression): {coin} {direction}")
                new_exits.append({
                    "coin":        coin,
                    "direction":   direction,
                    "position_id": pos_id,
                    "reason":      "exit_expression",
                    "fired_at":    now_iso(),
                })

    # ── Write bus files ───────────────────────────────────────────────────────
    if new_entries:
        # Merge with existing pending entries (don't clobber unprocessed ones)
        existing = load_json(ENTRIES_FILE, {}).get("entries", [])
        # Dedup by (coin, signal_name)
        existing_keys = {(e["coin"], e["signal_name"]) for e in existing}
        for e in new_entries:
            if (e["coin"], e["signal_name"]) not in existing_keys:
                existing.append(e)
        save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso(), "entries": existing})

    if new_exits:
        existing_exits = load_json(EXITS_FILE, {}).get("exits", [])
        existing_exit_keys = {(e["coin"], e["reason"]) for e in existing_exits}
        for e in new_exits:
            if (e["coin"], e["reason"]) not in existing_exit_keys:
                existing_exits.append(e)
        save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing_exits})


# ─── WEBSOCKET ────────────────────────────────────────────────────────────────

def parse_ws_message(raw_data: dict) -> dict[str, dict]:
    """Parse WS message into {COIN: {IND_CODE: value}} flat dict."""
    ws_data = raw_data.get("data", raw_data.get("snapshot", {}))
    if not isinstance(ws_data, dict):
        return {}
    flat = {}
    for coin, ind_list in ws_data.items():
        if isinstance(ind_list, list):
            values = {}
            for ind in ind_list:
                code = ind.get("indicatorCode", "")
                val  = ind.get("value")
                if code and val is not None:
                    values[code] = val
            if values:
                flat[coin] = values
    return flat


def run_websocket(api_key: str):
    """Connect to ENVY WebSocket and stream indicator ticks. Reconnects on drop."""
    try:
        import websocket
    except ImportError as _e:
        log("FATAL: websocket-client not installed. Run: pip install websocket-client")
        sys.exit(1)

    url       = f"{ENVY_WS_URL}?token={api_key}"
    delay     = RECONNECT_DELAY
    msg_count = 0

    log("=== V6 Evaluator starting WebSocket stream ===")

    # CRASH RECOVERY: check if we have positions that may have missed exits
    try:
        positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
        if positions:
            log(f"  RECOVERY CHECK: {len(positions)} open positions at startup")
            for pos in positions:
                entry_str = pos.get("entry_time", "")
                max_hold = pos.get("max_hold_hours", 24)
                if entry_str:
                    try:
                        entry_dt = datetime.fromisoformat(entry_str.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                        if age_hours > max_hold:
                            coin = pos.get("coin", "?")
                            log(f"  ⚠️ OVERDUE POSITION: {coin} {pos.get('direction')} age={age_hours:.1f}h > max_hold={max_hold}h")
                            # Write exit signal immediately
                            exit_entry = {
                                "coin": coin,
                                "direction": pos.get("direction"),
                                "reason": f"crash_recovery_overdue_{age_hours:.0f}h",
                                "timestamp": now_iso(),
                            }
                            existing = load_json(EXITS_FILE, {}).get("exits", [])
                            existing.append(exit_entry)
                            save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing})
                            log(f"  EXIT SIGNAL written for overdue {coin}")
                    except Exception as _e:
                        pass
    except Exception as e:
        log(f"  Recovery check failed: {e}")

    while True:
        try:
            log(f"Connecting to {ENVY_WS_URL}...")
            ws = websocket.create_connection(url, timeout=30)
            log("Connected — streaming indicators every 15s")
            delay = RECONNECT_DELAY

            while True:
                try:
                    raw = ws.recv()
                    if not raw:
                        break
                    data = json.loads(raw)

                    if isinstance(data, dict) and data.get("type") == "reconnect":
                        log("Server requested reconnect")
                        break

                    # Skip auth/welcome messages
                    if "data" not in data and "snapshot" not in data:
                        log(f"  Auth msg: {data.get('type', '?')}")
                        continue

                    flat = parse_ws_message(data)
                    if not flat:
                        continue

                    msg_count += 1
                    update_heartbeat()
                    if msg_count == 1 or msg_count % 20 == 0:
                        log(f"  Tick #{msg_count}: {len(flat)} coins")

                    evaluate_tick(flat)

                except (json.JSONDecodeError, OSError) as e:
                    log(f"WARN: message error: {e}")
                    break

        except Exception as e:
            log(f"Connection error: {e}")
            # Alert on disconnect so silence is never invisible
            try:
                from scanner.v6.executor import send_telegram
                send_telegram(f"⚠️ Evaluator WebSocket disconnected: {e}\nReconnecting in {delay}s...")
            except Exception as _e:
                pass  # swallowed: {_e}

        log(f"Reconnecting in {delay}s...")
        time.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)


def main():
    global BUS_DIR, ENTRIES_FILE, EXITS_FILE, POSITIONS_FILE, HEARTBEAT_FILE

    # Paper mode isolation — redirect bus paths before any file I/O
    from scanner.v6.paper_isolation import is_paper_mode, apply_paper_isolation
    if is_paper_mode():
        apply_paper_isolation()
        import scanner.v6.config as _cfg
        BUS_DIR = _cfg.BUS_DIR
        ENTRIES_FILE = _cfg.ENTRIES_FILE
        EXITS_FILE = _cfg.EXITS_FILE
        POSITIONS_FILE = _cfg.POSITIONS_FILE
        HEARTBEAT_FILE = _cfg.HEARTBEAT_FILE
        log("=== PAPER MODE — evaluator writing to isolated bus ===")

    api_key = get_env("ENVY_API_KEY")
    if not api_key:
        log("FATAL: ENVY_API_KEY not set")
        sys.exit(1)

    BUS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure bus files exist
    for path, default in [
        (ENTRIES_FILE, {"entries": []}),
        (EXITS_FILE,   {"exits": []}),
    ]:
        if not path.exists():
            save_json_atomic(path, {"updated_at": now_iso(), **default})

    run_websocket(api_key)


if __name__ == "__main__":
    main()

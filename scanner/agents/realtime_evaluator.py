#!/usr/bin/env python3
"""
Real-Time Signal Evaluator
Reads WebSocket indicator data every 30s and evaluates ALL Tier 1 pack signals.
When entry fires → writes to candidates.json for adversary processing.
Also evaluates exit expressions for open positions → writes exit_signals.json.

This is the FAST PATH — 30s evaluation vs 10-min hypothesis generator cycle.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals_cache"

WS_FILE = BUS_DIR / "ws_indicators.json"
CANDIDATES_FILE = BUS_DIR / "candidates.json"
POSITIONS_FILE = DATA_DIR / "live" / "positions.json"
EXIT_SIGNALS_FILE = BUS_DIR / "exit_signals.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# Only evaluate Tier 1 signals (Sharpe ≥ 2.0, WR ≥ 60%, N ≥ 10)
TIER1_SHARPE = 2.0
TIER1_WR = 60
TIER1_TRADES = 10

CYCLE_SECONDS = 30
STALE_WS_THRESHOLD = 60  # Only evaluate if WS data < 60s old


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [RT-EVAL] {msg}", flush=True)


def load_json(path, default=None):
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_tier1_signals():
    """Load all Tier 1 signals from cache."""
    signals_by_coin = {}
    for fn in sorted(SIGNALS_DIR.iterdir()):
        if fn.suffix != ".json":
            continue
        coin = fn.stem
        try:
            with open(fn) as f:
                data = json.load(f)
            sigs = data if isinstance(data, list) else data.get("signals", [])
            tier1 = [s for s in sigs
                     if s.get("sharpe", 0) >= TIER1_SHARPE
                     and s.get("win_rate", 0) >= TIER1_WR
                     and s.get("trade_count", 0) >= TIER1_TRADES
                     and s.get("expression")]
            if tier1:
                signals_by_coin[coin] = tier1
        except Exception:
            continue
    return signals_by_coin


def evaluate_expression(expression, indicator_values):
    """Evaluate a signal expression against indicator values. Returns (fired, missing)."""
    if not expression or not expression.strip():
        return False, []

    missing = []

    # Handle weighted sum expressions: ((IND1 * w1) + (IND2 * w2) + ...) >= threshold
    if "((" in expression and "*" in expression:
        threshold_match = re.search(r'\)\s*(>=|>|<=|<)\s*(-?[\d.]+)\s*$', expression)
        if not threshold_match:
            return False, missing
        op = threshold_match.group(1)
        threshold = float(threshold_match.group(2))
        inner = expression[:threshold_match.start()].strip().strip("()")
        terms = re.findall(r'\(?\s*([A-Z][A-Z0-9_]+)\s*\*\s*(-?[\d.]+)\s*\)?', inner)
        if not terms:
            return False, missing
        total = 0.0
        for indicator, weight in terms:
            val = indicator_values.get(indicator)
            if val is None:
                missing.append(indicator)
            else:
                try:
                    total += float(val) * float(weight)
                except (TypeError, ValueError):
                    missing.append(indicator)
        if missing:
            return False, missing
        ops = {">=": total >= threshold, ">": total > threshold,
               "<=": total <= threshold, "<": total < threshold}
        return ops.get(op, False), missing

    # Handle AND/OR clauses
    clauses = re.split(r'\s+(AND|OR)\s+', expression)
    results = []
    operators = []
    for part in clauses:
        part = part.strip()
        if part in ("AND", "OR"):
            operators.append(part)
            continue
        # Parse: INDICATOR_NAME >= value
        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|>|<=|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            continue
        indicator, op, val_str = m.group(1), m.group(2), m.group(3)
        actual = indicator_values.get(indicator)
        if actual is None:
            missing.append(indicator)
            results.append(False)
            continue
        try:
            actual_f = float(actual)
            val_f = float(val_str)
        except (TypeError, ValueError):
            missing.append(indicator)
            results.append(False)
            continue
        ops = {">=": actual_f >= val_f, ">": actual_f > val_f,
               "<=": actual_f <= val_f, "<": actual_f < val_f,
               "==": actual_f == val_f, "!=": actual_f != val_f}
        results.append(ops.get(op, False))

    if not results:
        return False, missing

    # Combine with operators
    final = results[0]
    for i, op in enumerate(operators):
        if i + 1 < len(results):
            if op == "AND":
                final = final and results[i + 1]
            elif op == "OR":
                final = final or results[i + 1]

    return final, missing


def evaluate_entries(ws_coins, signals_by_coin):
    """Evaluate entry expressions against WS data. Returns fired signals."""
    fired = []
    for coin, coin_signals in signals_by_coin.items():
        indicators = ws_coins.get(coin, {})
        if not indicators:
            continue
        for sig in coin_signals:
            expr = sig.get("expression", "")
            if not expr:
                continue
            triggered, missing = evaluate_expression(expr, indicators)
            if triggered and len(missing) < 2:  # Allow 1 missing indicator
                fired.append({
                    "coin": coin,
                    "direction": sig.get("signal_type", "LONG"),
                    "signal": sig.get("name", "unknown"),
                    "sharpe": sig.get("sharpe", 0),
                    "win_rate": sig.get("win_rate", 0),
                    "trade_count": sig.get("trade_count", 0),
                    "expression": expr,
                    "exit_expression": sig.get("exit_expression", ""),
                    "source": "realtime_evaluator",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    return fired


def evaluate_exits(ws_coins, positions):
    """Evaluate exit expressions for open positions. Returns exit signals."""
    exits = []
    for pos in positions:
        exit_expr = pos.get("exit_expression", "")
        if not exit_expr:
            continue
        coin = pos.get("coin", "")
        indicators = ws_coins.get(coin, {})
        if not indicators:
            continue
        triggered, missing = evaluate_expression(exit_expr, indicators)
        if triggered:
            exits.append({
                "coin": coin,
                "direction": pos.get("direction"),
                "signal": pos.get("signal", ""),
                "exit_expression": exit_expr,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    return exits


def write_heartbeat():
    try:
        hb = load_json(HEARTBEAT_FILE, {})
        hb["realtime_evaluator"] = datetime.now(timezone.utc).isoformat()
        save_json(HEARTBEAT_FILE, hb)
    except Exception:
        pass


def run_cycle():
    """Single evaluation cycle."""
    # Check WS freshness
    if not WS_FILE.exists():
        return 0, 0

    ws_age = time.time() - os.path.getmtime(str(WS_FILE))
    if ws_age > STALE_WS_THRESHOLD:
        return 0, 0

    ws_data = load_json(WS_FILE, {})
    ws_coins = ws_data.get("coins", {})
    if not ws_coins:
        return 0, 0

    # Load Tier 1 signals (cached, reload every 10 cycles)
    signals_by_coin = load_tier1_signals()
    total_signals = sum(len(v) for v in signals_by_coin.values())

    # Evaluate entries
    fired_entries = evaluate_entries(ws_coins, signals_by_coin)

    # If entries fired, inject into candidates.json
    if fired_entries:
        # Merge with existing candidates (don't overwrite hypothesis generator's work)
        existing = load_json(CANDIDATES_FILE, {})
        existing_cands = existing.get("candidates", [])
        existing_names = {c.get("signal", "") for c in existing_cands}

        new_entries = [e for e in fired_entries if e["signal"] not in existing_names]
        if new_entries:
            existing_cands.extend(new_entries)
            existing["candidates"] = existing_cands
            existing["realtime_entries"] = len(new_entries)
            existing["realtime_timestamp"] = datetime.now(timezone.utc).isoformat()
            save_json(CANDIDATES_FILE, existing)
            log(f"  ⚡ {len(new_entries)} real-time entries injected (from {total_signals} Tier1 signals)")

    # Evaluate exits for open positions
    positions = load_json(POSITIONS_FILE, [])
    if isinstance(positions, dict):
        positions = positions.get("positions", [])
    fired_exits = evaluate_exits(ws_coins, positions)

    if fired_exits:
        save_json(EXIT_SIGNALS_FILE, {
            "exit_signals": fired_exits,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        log(f"  ⚡ {len(fired_exits)} exit signals written")

    write_heartbeat()
    return len(fired_entries), len(fired_exits)


def main():
    log(f"Real-Time Signal Evaluator starting (cycle: {CYCLE_SECONDS}s)")

    # Pre-load signal count
    signals = load_tier1_signals()
    total = sum(len(v) for v in signals.values())
    log(f"  Loaded {total} Tier 1 signals across {len(signals)} coins")

    cycle = 0
    while True:
        try:
            entries, exits = run_cycle()
            cycle += 1
            if entries or exits or cycle % 20 == 0:
                log(f"  cycle={cycle} entries={entries} exits={exits}")
        except Exception as e:
            log(f"  ERROR: {e}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()

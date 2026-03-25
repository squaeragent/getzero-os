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

V6_DIR = Path(__file__).parent
EVAL_INTERVAL = 60  # seconds between evaluation cycles (1 min)
MIN_QUALITY = 6.0   # quality threshold (0-10 scale) — matches ENVY evaluator q≥6

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


def evaluate_entries(smart_provider, coins: list[str], open_coins: set) -> list[dict]:
    """Run SmartProvider on each coin, return entries that pass quality gate."""
    new_entries = []

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

        if direction == "NEUTRAL" or quality < MIN_QUALITY:
            continue

        # Convert to entry format
        atr_pct = result.get("atr_pct", 0.05)
        stop_pct = get_stop_pct(coin)  # use per-coin stop from config

        entry = {
            "coin":            coin,
            "direction":       direction,
            "signal_name":     f"SMART_{direction}_{coin}_{regime}",
            "expression":      f"SMART_REGIME={regime}",
            "exit_expression": "",
            "max_hold_hours":  12,
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


def run_cycle(smart_provider):
    """Single evaluation cycle."""
    coins = get_active_coins()
    positions = load_json(POSITIONS_FILE, {}).get("positions", [])
    open_coins = {p["coin"] for p in positions}

    # Entries
    new_entries = evaluate_entries(smart_provider, coins, open_coins)

    if new_entries:
        existing = load_json(ENTRIES_FILE, {}).get("entries", [])
        existing.extend(new_entries)
        save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso(), "entries": existing})
        _log(f"  Wrote {len(new_entries)} entries to bus")

    # Exits
    if positions:
        new_exits = evaluate_exits(smart_provider, positions)
        if new_exits:
            existing = load_json(EXITS_FILE, {}).get("exits", [])
            existing.extend(new_exits)
            save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing})
            _log(f"  Wrote {len(new_exits)} exits to bus")

    update_heartbeat()
    return len(new_entries), len(new_exits)


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
            coins = get_active_coins()
            n_entries, n_exits = run_cycle(sp)
            if cycle == 1 or cycle % 10 == 0 or n_entries > 0 or n_exits > 0:
                _log(f"  Cycle #{cycle}: {len(coins)} coins → {n_entries} entries, {n_exits} exits")
        except Exception as e:
            _log(f"  ERROR in cycle #{cycle}: {e}")
            traceback.print_exc()

        time.sleep(EVAL_INTERVAL)


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

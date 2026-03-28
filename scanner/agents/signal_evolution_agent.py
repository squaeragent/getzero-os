#!/usr/bin/env python3
"""
ZERO OS — Agent 8: Signal Evolution Agent
Tracks which signals win/lose by regime, dynamically weights signals.

Inputs:
  scanner/data/closed.jsonl             — paper trades with signal names
  scanner/data/live/closed.jsonl        — live trades
  scanner/bus/regimes.json              — current regime per coin
  scanner/data/signals_cache/*.json     — signal packs

Outputs:
  scanner/bus/signal_weights.json       — per-signal weight multipliers
  scanner/bus/heartbeat.json            — last-alive timestamp

Usage:
  python3 scanner/agents/signal_evolution_agent.py           # single run
  python3 scanner/agents/signal_evolution_agent.py --loop    # continuous 10-min cycle
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scanner.utils import (
    load_json, save_json, read_jsonl, make_logger, update_heartbeat,
    SCANNER_DIR, BUS_DIR, DATA_DIR, LIVE_DIR, HEARTBEAT_FILE,
    REGIMES_FILE, CLOSED_FILE_LIVE,
)

# ─── PATHS ───
SIGNALS_CACHE_DIR = DATA_DIR / "signals_cache"
SIGNAL_WEIGHTS_FILE = BUS_DIR / "signal_weights.json"

# ─── CONFIG ───
CYCLE_SECONDS = 600  # 10 minutes

# Weight thresholds
REGIME_WR_PENALIZE = 40.0   # below this: weight = 0.5
REGIME_WR_BOOST = 60.0      # above this: weight = 1.2
MIN_REGIME_TRADES = 3        # need this many trades to use regime-specific WR
MIN_OVERALL_TRADES = 5       # fallback to overall WR if enough trades
FATIGUE_WINDOW_HOURS = 24    # look back this far for signal fatigue
FATIGUE_THRESHOLD = 3        # more than this many fires = fatigued
FATIGUE_MULTIPLIER = 0.7     # penalty for fatigued signals


# ─── DATA LOADING ───
def load_all_closed_trades():
    """Load closed trades from live trading."""
    live = read_jsonl(CLOSED_FILE_LIVE)
    live.sort(key=lambda t: t.get("exit_time", ""))
    return live


def load_regimes():
    """Load current regime state."""
    return load_json(REGIMES_FILE)


def load_signal_names():
    """Load all known signal names from signal cache."""
    names = set()
    if not SIGNALS_CACHE_DIR.exists():
        return names
    for f in SIGNALS_CACHE_DIR.glob("*.json"):
        packs = load_json(f, [])
        if isinstance(packs, list):
            for pack in packs:
                name = pack.get("name") if isinstance(pack, dict) else None
                if name:
                    names.add(name)
    return names


# ─── PERFORMANCE MATRIX ───
def build_performance_matrix(trades):
    """Build signal_name × regime → {trades, wins, losses, pnls} matrix."""
    matrix = {}  # signal_name -> {regime -> {trades, wins, losses, pnls}}

    for t in trades:
        signal = t.get("signal")
        if not signal:
            continue

        # Get regime from trade metadata or default
        regime = t.get("regime") or t.get("metadata", {}).get("regime", "unknown")
        pnl = t.get("pnl_dollars", t.get("pnl_usd", 0))

        if signal not in matrix:
            matrix[signal] = {}
        if regime not in matrix[signal]:
            matrix[signal][regime] = {"trades": 0, "wins": 0, "losses": 0, "pnls": []}

        entry = matrix[signal][regime]
        entry["trades"] += 1
        if pnl > 0:
            entry["wins"] += 1
        elif pnl < 0:
            entry["losses"] += 1
        entry["pnls"].append(pnl)

    return matrix


def compute_stats(pnls):
    """Compute win rate, avg PnL, and simple Sharpe from a list of PnLs."""
    if not pnls:
        return {"win_rate": 0, "avg_pnl": 0, "sharpe": 0}

    wins = sum(1 for p in pnls if p > 0)
    n = len(pnls)
    win_rate = wins / n * 100 if n > 0 else 0
    avg_pnl = sum(pnls) / n if n > 0 else 0

    if n > 1:
        mean = avg_pnl
        variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        stdev = variance ** 0.5
        sharpe = mean / stdev if stdev > 0 else 0
    else:
        sharpe = 0

    return {
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 4),
        "sharpe": round(sharpe, 3),
    }


def get_overall_stats(matrix, signal):
    """Aggregate stats across all regimes for a signal."""
    all_pnls = []
    total_trades = 0
    total_wins = 0
    if signal in matrix:
        for regime_data in matrix[signal].values():
            all_pnls.extend(regime_data["pnls"])
            total_trades += regime_data["trades"]
            total_wins += regime_data["wins"]
    stats = compute_stats(all_pnls)
    stats["total_trades"] = total_trades
    stats["total_wins"] = total_wins
    return stats


def compute_weight(matrix, signal, current_regime):
    """Compute weight multiplier for a signal based on regime performance."""
    # Check regime-specific data
    if signal in matrix and current_regime in matrix[signal]:
        regime_data = matrix[signal][current_regime]
        if regime_data["trades"] >= MIN_REGIME_TRADES:
            wr = regime_data["wins"] / regime_data["trades"] * 100
            if wr < REGIME_WR_PENALIZE:
                return 0.5
            elif wr > REGIME_WR_BOOST:
                return 1.2
            else:
                return 1.0

    # Fallback to overall stats
    overall = get_overall_stats(matrix, signal)
    if overall["total_trades"] >= MIN_OVERALL_TRADES:
        wr = overall["win_rate"]
        if wr < REGIME_WR_PENALIZE:
            return 0.5
        elif wr > REGIME_WR_BOOST:
            return 1.2
        else:
            return 1.0

    # No opinion
    return 1.0


# ─── SIGNAL FATIGUE ───
def compute_fatigue(trades, window_hours=FATIGUE_WINDOW_HOURS):
    """Count how many times each signal fired in the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    cutoff_iso = cutoff.isoformat()

    fatigue = {}
    for t in trades:
        signal = t.get("signal")
        if not signal:
            continue
        entry_time = t.get("entry_time", "")
        if entry_time >= cutoff_iso:
            fatigue[signal] = fatigue.get(signal, 0) + 1

    return fatigue


# ─── HEARTBEAT ───
def write_heartbeat():
    update_heartbeat("signal_evolution")


# ─── MAIN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Signal Evolution Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Load data
    trades = load_all_closed_trades()
    print(f"  Loaded {len(trades)} closed trades (paper + live)")

    regimes_data = load_regimes()
    regime_coins = regimes_data.get("coins", {})
    print(f"  Regime data for {len(regime_coins)} coins")

    signal_names = load_signal_names()
    print(f"  Known signals from cache: {len(signal_names)}")

    if not trades:
        print("  [warn] No closed trades found, writing neutral weights")
        output = {
            "timestamp": ts_iso,
            "weights": {name: 1.0 for name in signal_names},
            "performance": {},
            "fatigue": {},
        }
        save_json(SIGNAL_WEIGHTS_FILE, output)
        write_heartbeat()
        print(f"  Written neutral weights for {len(signal_names)} signals")
        print(f"{'='*60}\n")
        return

    # Build performance matrix
    matrix = build_performance_matrix(trades)
    print(f"  Performance matrix: {len(matrix)} signals with trade data")

    # Compute fatigue
    fatigue = compute_fatigue(trades)
    fatigued_count = sum(1 for v in fatigue.values() if v > FATIGUE_THRESHOLD)
    print(f"  Fatigued signals (>{FATIGUE_THRESHOLD} fires in {FATIGUE_WINDOW_HOURS}h): {fatigued_count}")

    # Build a simple "default regime" for signals not tied to a specific coin
    # Use the most common regime across all coins
    regime_counts = {}
    for coin_data in regime_coins.values():
        r = coin_data.get("regime", "stable")
        regime_counts[r] = regime_counts.get(r, 0) + 1
    dominant_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "stable"
    print(f"  Dominant regime: {dominant_regime}")

    # Compute weights for all known signals
    weights = {}
    performance = {}

    # All signals: from cache + from trades
    all_signals = signal_names | set(matrix.keys())

    for signal in sorted(all_signals):
        # Determine regime for this signal (from trade coin or dominant)
        current_regime = dominant_regime

        # Compute base weight
        w = compute_weight(matrix, signal, current_regime)

        # Apply fatigue penalty
        fires = fatigue.get(signal, 0)
        if fires > FATIGUE_THRESHOLD:
            w *= FATIGUE_MULTIPLIER

        weights[signal] = round(w, 3)

        # Build performance record
        if signal in matrix:
            overall = get_overall_stats(matrix, signal)
            by_regime = {}
            for regime, rdata in matrix[signal].items():
                rstats = compute_stats(rdata["pnls"])
                by_regime[regime] = {
                    "trades": rdata["trades"],
                    "wr": rstats["win_rate"],
                    "avg_pnl": rstats["avg_pnl"],
                }
            performance[signal] = {
                "total_trades": overall["total_trades"],
                "win_rate": overall["win_rate"],
                "avg_pnl": overall["avg_pnl"],
                "sharpe": overall["sharpe"],
                "by_regime": by_regime,
            }

    # Write output
    output = {
        "timestamp": ts_iso,
        "weights": weights,
        "performance": performance,
        "fatigue": {k: v for k, v in fatigue.items() if v > 0},
    }
    save_json(SIGNAL_WEIGHTS_FILE, output)

    write_heartbeat()

    # Summary
    boosted = sum(1 for w in weights.values() if w > 1.0)
    penalized = sum(1 for w in weights.values() if w < 1.0)
    neutral = sum(1 for w in weights.values() if w == 1.0)

    print(f"\n  Weights: {boosted} boosted, {penalized} penalized, {neutral} neutral")
    print(f"  Total signals weighted: {len(weights)}")

    # Show top/bottom performers
    if performance:
        by_wr = sorted(
            [(s, p) for s, p in performance.items() if p["total_trades"] >= 3],
            key=lambda x: x[1]["win_rate"],
            reverse=True,
        )
        if by_wr:
            print(f"\n  Top performers (>= 3 trades):")
            for s, p in by_wr[:3]:
                print(
                    f"    {weights.get(s, 1.0):.2f}x  {s[:50]:50s}  "
                    f"wr={p['win_rate']:.0f}%  trades={p['total_trades']}  "
                    f"avg=${p['avg_pnl']:.2f}"
                )
            if len(by_wr) > 3:
                print(f"  Bottom performers:")
                for s, p in by_wr[-3:]:
                    print(
                        f"    {weights.get(s, 1.0):.2f}x  {s[:50]:50s}  "
                        f"wr={p['win_rate']:.0f}%  trades={p['total_trades']}  "
                        f"avg=${p['avg_pnl']:.2f}"
                    )

    print(f"\n  Written to {SIGNAL_WEIGHTS_FILE}")
    print(f"{'='*60}\n")


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Signal Evolution Agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

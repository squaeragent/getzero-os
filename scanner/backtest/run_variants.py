#!/usr/bin/env python3
"""
Brain Fix — run 5 backtest variants with consensus, regime, and hold filters.

Indicators: rsi, macd, ema, bollinger, obv, funding (6 total).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scanner.backtest.backtest import (
    load_all_data, run_smartprovider_variant, compute_metrics, COINS, RESULTS_DIR,
)

VARIANTS = {
    "A_baseline": {
        "min_consensus": 0, "block_chaotic": False, "reduce_reverting": False, "min_hold_hours": 0,
    },
    "B_consensus_2": {
        "min_consensus": 2, "block_chaotic": False, "reduce_reverting": False, "min_hold_hours": 0,
    },
    "C_consensus_2_regime": {
        "min_consensus": 2, "block_chaotic": True, "reduce_reverting": True, "min_hold_hours": 0,
    },
    "D_full_fix": {
        "min_consensus": 2, "block_chaotic": True, "reduce_reverting": True, "min_hold_hours": 2,
    },
    "E_consensus_3": {
        "min_consensus": 3, "block_chaotic": True, "reduce_reverting": True, "min_hold_hours": 2,
    },
}


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading historical data...")
    data = load_all_data()

    for coin in COINS:
        if not data[coin]["1h"]:
            print(f"ERROR: No 1h data for {coin}. Run fetch_data.py first.")
            sys.exit(1)

    results = {}
    for name, params in VARIANTS.items():
        print(f"\n=== Running variant {name} ===")
        print(f"  Params: {params}")
        out = run_smartprovider_variant(data, **params)
        results[name] = out["metrics"]
        m = out["metrics"]
        print(f"  Return: {m['total_return_pct']:.1f}%")
        print(f"  Profit Factor: {m['profit_factor']}")
        print(f"  Trades: {m['total_trades']}")
        print(f"  Max DD: {m['max_drawdown_pct']:.1f}%")

    # Benchmarks from previous backtest run
    results["RANDOM"] = {
        "total_return_pct": 0.15, "profit_factor": 1.004, "total_trades": 694,
        "max_drawdown_pct": 8.3, "sharpe_ratio": 0, "avg_win_loss_ratio": 0,
    }
    results["HOLD"] = {
        "total_return_pct": -17.09, "profit_factor": 0, "total_trades": 3,
        "max_drawdown_pct": 60.34, "sharpe_ratio": 0, "avg_win_loss_ratio": 0,
    }

    # Print comparison table
    print("\n" + "=" * 90)
    print(f"{'Variant':<25} {'Return':>8} {'P/F':>8} {'Trades':>8} {'MaxDD':>8} {'Sharpe':>8} {'W/L':>8}")
    print("-" * 90)
    for name, m in results.items():
        pf = m.get("profit_factor", 0)
        pf_str = f"{pf:>8.3f}" if isinstance(pf, (int, float)) else f"{pf:>8}"
        print(
            f"{name:<25} {m.get('total_return_pct', 0):>7.1f}% "
            f"{pf_str} {m.get('total_trades', 0):>8} "
            f"{m.get('max_drawdown_pct', 0):>7.1f}% "
            f"{m.get('sharpe_ratio', 0):>8.3f} "
            f"{m.get('avg_win_loss_ratio', 0):>8.3f}"
        )

    # Save
    output_path = RESULTS_DIR / "variant_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Verdict
    scoreable = [(k, v) for k, v in results.items() if k not in ("RANDOM", "HOLD")]
    best = max(scoreable, key=lambda x: x[1].get("profit_factor", 0) if isinstance(x[1].get("profit_factor", 0), (int, float)) else 0)
    random_pf = results["RANDOM"]["profit_factor"]
    best_pf = best[1].get("profit_factor", 0)
    best_pf = best_pf if isinstance(best_pf, (int, float)) else 0

    print(f"\nBEST VARIANT: {best[0]}")
    print(f"  Profit Factor: {best_pf:.3f} vs Random: {random_pf:.3f}")
    if best_pf > 1.0:
        print("  BRAIN IS FIXED — positive expectancy")
    elif best_pf > random_pf:
        print("  BETTER THAN RANDOM — but still below 1.0")
    else:
        print("  ALL VARIANTS LOSE TO RANDOM — see fallback options")


if __name__ == "__main__":
    main()

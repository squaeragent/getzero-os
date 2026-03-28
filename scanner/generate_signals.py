#!/usr/bin/env python3
"""
ZERO OS — Custom Signal Generator
Scans historical indicator data to discover profitable entry/exit patterns.
Generates custom signal expressions by finding indicator thresholds that
preceded profitable price moves.

Uses Envy history endpoint (7 days of 15-min data, 50 indicators).

Usage:
  python3 scanner/generate_signals.py                    # generate for all coins
  python3 scanner/generate_signals.py --coin BTC         # single coin
  python3 scanner/generate_signals.py --min-trades 5     # min trades to qualify
  python3 scanner/generate_signals.py --min-wr 60        # min win rate %
"""

import json
import os
import sys
import time
import urllib.request
import itertools
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent
DATA_DIR = SCANNER_DIR / "data"
CUSTOM_DIR = DATA_DIR / "custom_signals"
BASE_URL = "https://gate.getzero.dev/api/claw"

# Signal generation parameters
# NOTE: History endpoint currently returns 96 points (24h), not 7 days
# With 96 bars, we need lower thresholds but higher quality filters
MIN_TRADES = 3           # minimum fires in available history
MIN_WIN_RATE = 55.0      # minimum win rate %
MIN_SHARPE = 0.3         # minimum edge (lowered for 24h window)
MAX_HOLD_BARS = 24       # max hold = 6 hours (24 × 15min, since we have 96 bars)
STOP_LOSS_PCT = 0.05     # 5% stop loss
LOOKAHEAD_BARS = [4, 8, 16, 24]  # 1h, 2h, 4h, 6h

# Indicator groups for combination search
MOMENTUM_IND = ["CMO_3H30M", "ADX_3H30M"]
TREND_IND = ["EMA_3H_N", "EMA_6H30M_N", "EMA_CROSS_15M_N", "MACD_CROSS_15M_N"]
BB_IND = ["BB_POSITION_15M", "BB_POS_6H", "BB_POS_12H", "BB_POS_24H"]
CHAOS_IND = ["HURST_24H", "DFA_24H", "LYAPUNOV_24H"]
ICHIMOKU_IND = ["ICHIMOKU_BULL", "CLOUD_POSITION_15M"]

ALL_GROUPS = [MOMENTUM_IND, TREND_IND, BB_IND, CHAOS_IND, ICHIMOKU_IND]


def load_api_key():
    env_path = os.path.expanduser("~/getzero-os/.env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("ENVY_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("ENVY_API_KEY not found")


def fetch_history(coin, api_key):
    """Fetch all available historical indicator data for a coin."""
    url = f"{BASE_URL}/paid/indicators/history?coin={coin}&indicator=HURST_24H"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    
    result = {}
    for ind_code, ind_data in data.get("data", {}).items():
        values = ind_data.get("values", [])
        result[ind_code] = {v["t"]: v["v"] for v in values}
    
    return result


def compute_returns(prices, timestamps, direction, hold_bars):
    """Compute returns for all entry points at a given hold period."""
    returns = {}
    for i, ts in enumerate(timestamps):
        exit_idx = min(i + hold_bars, len(timestamps) - 1)
        if exit_idx <= i:
            continue
        
        entry_px = prices.get(ts, 0)
        exit_px = prices.get(timestamps[exit_idx], 0)
        
        if entry_px <= 0 or exit_px <= 0:
            continue
        
        # Check stop loss along the way
        stopped = False
        for j in range(i + 1, exit_idx + 1):
            mid_px = prices.get(timestamps[j], entry_px)
            if direction == "LONG":
                pct = (mid_px - entry_px) / entry_px
            else:
                pct = (entry_px - mid_px) / entry_px
            if pct <= -STOP_LOSS_PCT:
                exit_px = mid_px
                stopped = True
                break
        
        if direction == "LONG":
            ret = (exit_px - entry_px) / entry_px * 100
        else:
            ret = (entry_px - exit_px) / entry_px * 100
        
        returns[ts] = ret
    
    return returns


def find_threshold(indicator_values, timestamps, returns, direction):
    """
    Find the best threshold for a single indicator.
    Tests percentile-based thresholds and picks the one with best Sharpe.
    Returns (operator, threshold, stats) or None.
    """
    # Get indicator values at tradeable timestamps
    vals = []
    for ts in timestamps:
        if ts in indicator_values and ts in returns:
            vals.append((indicator_values[ts], returns[ts]))
    
    if len(vals) < MIN_TRADES * 2:
        return None
    
    # Sort by indicator value
    vals.sort(key=lambda x: x[0])
    all_values = [v[0] for v in vals]
    
    best = None
    best_sharpe = 0
    
    # Test percentile thresholds: 10th, 20th, 30th, 70th, 80th, 90th
    for pct in [10, 20, 25, 30, 70, 75, 80, 90]:
        idx = int(len(all_values) * pct / 100)
        threshold = all_values[min(idx, len(all_values) - 1)]
        
        # Test ">= threshold" (high values)
        above_returns = [r for v, r in vals if v >= threshold]
        if len(above_returns) >= MIN_TRADES:
            avg = sum(above_returns) / len(above_returns)
            if above_returns:
                std = (sum((r - avg) ** 2 for r in above_returns) / len(above_returns)) ** 0.5
                sharpe = avg / std if std > 0 else 0
                wr = sum(1 for r in above_returns if r > 0) / len(above_returns) * 100
                if sharpe > best_sharpe and wr >= MIN_WIN_RATE:
                    best_sharpe = sharpe
                    best = (">=", threshold, {
                        "trades": len(above_returns),
                        "win_rate": round(wr, 1),
                        "avg_pnl": round(avg, 3),
                        "sharpe": round(sharpe, 3),
                    })
        
        # Test "<= threshold" (low values)
        below_returns = [r for v, r in vals if v <= threshold]
        if len(below_returns) >= MIN_TRADES:
            avg = sum(below_returns) / len(below_returns)
            if below_returns:
                std = (sum((r - avg) ** 2 for r in below_returns) / len(below_returns)) ** 0.5
                sharpe = avg / std if std > 0 else 0
                wr = sum(1 for r in below_returns if r > 0) / len(below_returns) * 100
                if sharpe > best_sharpe and wr >= MIN_WIN_RATE:
                    best_sharpe = sharpe
                    best = ("<=", threshold, {
                        "trades": len(below_returns),
                        "win_rate": round(wr, 1),
                        "avg_pnl": round(avg, 3),
                        "sharpe": round(sharpe, 3),
                    })
    
    return best


def generate_signals_for_coin(coin, history):
    """Generate custom signals for one coin by scanning indicator combinations."""
    if "CLOSE_PRICE_15M" not in history:
        return []
    
    prices = history["CLOSE_PRICE_15M"]
    timestamps = sorted(prices.keys())
    
    if len(timestamps) < 100:
        return []
    
    signals = []
    available_indicators = [ind for ind in history.keys() if ind != "CLOSE_PRICE_15M"]
    
    for direction in ["LONG", "SHORT"]:
        for hold_bars in LOOKAHEAD_BARS:
            returns = compute_returns(prices, timestamps, direction, hold_bars)
            if len(returns) < MIN_TRADES * 3:
                continue
            
            # Single indicator signals
            for ind in available_indicators:
                ind_vals = history.get(ind, {})
                result = find_threshold(ind_vals, timestamps, returns, direction)
                if result and result[2]["sharpe"] >= MIN_SHARPE:
                    op, thresh, stats = result
                    expr = f"{ind} {op} {thresh:.6f}"
                    name = f"CUSTOM_{ind}_{op.replace('>=','GTE').replace('<=','LTE')}_{direction}_{hold_bars}B"
                    signals.append({
                        "name": name,
                        "coin": coin,
                        "signal_type": direction,
                        "expression": expr,
                        "exit_expression": "",
                        "max_hold_hours": hold_bars * 0.25,  # bars to hours
                        **stats,
                        "source": "custom_generator",
                    })
            
            # Two-indicator combinations (from different groups)
            for g1, g2 in itertools.combinations(range(len(ALL_GROUPS)), 2):
                for ind1 in ALL_GROUPS[g1]:
                    if ind1 not in history:
                        continue
                    for ind2 in ALL_GROUPS[g2]:
                        if ind2 not in history:
                            continue
                        
                        # Find best threshold for each
                        r1 = find_threshold(history[ind1], timestamps, returns, direction)
                        r2 = find_threshold(history[ind2], timestamps, returns, direction)
                        
                        if not r1 or not r2:
                            continue
                        
                        op1, t1, s1 = r1
                        op2, t2, s2 = r2
                        
                        # Test the combination
                        combo_returns = []
                        for ts in timestamps:
                            if ts not in returns:
                                continue
                            v1 = history[ind1].get(ts)
                            v2 = history[ind2].get(ts)
                            if v1 is None or v2 is None:
                                continue
                            
                            pass1 = (v1 >= t1) if op1 == ">=" else (v1 <= t1)
                            pass2 = (v2 >= t2) if op2 == ">=" else (v2 <= t2)
                            
                            if pass1 and pass2:
                                combo_returns.append(returns[ts])
                        
                        if len(combo_returns) < MIN_TRADES:
                            continue
                        
                        avg = sum(combo_returns) / len(combo_returns)
                        std = (sum((r - avg) ** 2 for r in combo_returns) / len(combo_returns)) ** 0.5
                        sharpe = avg / std if std > 0 else 0
                        wr = sum(1 for r in combo_returns if r > 0) / len(combo_returns) * 100
                        
                        if sharpe >= MIN_SHARPE and wr >= MIN_WIN_RATE:
                            expr = f"{ind1} {op1} {t1:.6f} AND {ind2} {op2} {t2:.6f}"
                            name = f"CUSTOM_{ind1}_{ind2}_{direction}_{hold_bars}B"
                            signals.append({
                                "name": name,
                                "coin": coin,
                                "signal_type": direction,
                                "expression": expr,
                                "exit_expression": "",
                                "max_hold_hours": hold_bars * 0.25,
                                "trades": len(combo_returns),
                                "win_rate": round(wr, 1),
                                "avg_pnl": round(avg, 3),
                                "sharpe": round(sharpe, 3),
                                "source": "custom_generator",
                            })
    
    # Deduplicate and rank by Sharpe
    seen = set()
    unique = []
    for s in sorted(signals, key=lambda x: x["sharpe"], reverse=True):
        key = s["expression"]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    
    return unique


def main():
    api_key = load_api_key()
    target_coin = None
    
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--coin" and i + 1 < len(args):
            target_coin = args[i + 1]
    
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    
    # Coins to process
    COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]
    if target_coin:
        COINS = [target_coin]
    
    print(f"Custom Signal Generator — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Scanning {len(COINS)} coins, testing {len([i for g in ALL_GROUPS for i in g])} indicators")
    print(f"Thresholds: min_trades={MIN_TRADES}, min_wr={MIN_WIN_RATE}%, min_sharpe={MIN_SHARPE}")
    print()
    
    all_signals = {}
    total = 0
    
    for coin in COINS:
        print(f"  {coin}: fetching history...", end=" ", flush=True)
        try:
            history = fetch_history(coin, api_key)
            print(f"{len(history)} indicators", end=" → ", flush=True)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        
        signals = generate_signals_for_coin(coin, history)
        all_signals[coin] = signals
        total += len(signals)
        
        if signals:
            best = max(signals, key=lambda s: s["sharpe"])
            print(f"{len(signals)} signals found (best: {best['name'][:35]} sharpe={best['sharpe']:.2f} wr={best['win_rate']}%)")
        else:
            print("0 signals")
        
        time.sleep(0.2)  # be nice to API
    
    # Save results
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_signals": total,
        "coins": all_signals,
    }
    output_file = CUSTOM_DIR / "custom_signals.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    # Also merge into signal cache for the harvester to pick up
    for coin, signals in all_signals.items():
        if not signals:
            continue
        cache_file = DATA_DIR / "signals_cache" / f"{coin}.json"
        existing = []
        if cache_file.exists():
            with open(cache_file) as f:
                existing = json.load(f)
        
        # Remove old custom signals
        existing = [s for s in existing if not s.get("name", "").startswith("CUSTOM_")]
        
        # Add new custom signals (top 10 per coin by Sharpe)
        top_custom = sorted(signals, key=lambda s: s["sharpe"], reverse=True)[:10]
        for s in top_custom:
            existing.append({
                "name": s["name"],
                "_coin": coin,
                "_pack": "custom",
                "signal_type": s["signal_type"],
                "expression": s["expression"],
                "exit_expression": s.get("exit_expression", ""),
                "max_hold_hours": s.get("max_hold_hours", 12),
                "sharpe": s["sharpe"],
                "win_rate": s["win_rate"],
                "trade_count": s["trades"],
                "total_return": s.get("avg_pnl", 0) * s.get("trades", 0),
                "max_drawdown": 0,
                "active_days": 7,
                "composite_score": s["sharpe"] * 1.5 + s["win_rate"] / 100 * 3,
                "rarity": "Custom",
            })
        
        with open(cache_file, "w") as f:
            json.dump(existing, f, indent=2)
        
        if top_custom:
            print(f"  → Merged {len(top_custom)} custom signals into {coin} cache")
    
    # Summary
    print(f"\n{'='*60}")
    print(f"SIGNAL GENERATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total custom signals: {total}")
    
    top_all = []
    for coin, signals in all_signals.items():
        for s in signals:
            s["coin"] = coin
            top_all.append(s)
    
    top_all.sort(key=lambda s: s["sharpe"], reverse=True)
    
    print(f"\nTop 15 signals:")
    for s in top_all[:15]:
        print(f"  {s['coin']:6s} {s['signal_type']:5s}  sharpe={s['sharpe']:5.2f}  wr={s['win_rate']:5.1f}%  "
              f"trades={s['trades']:3d}  avg={s.get('avg_pnl',0):+6.3f}%  {s['name'][:40]}")
    
    print(f"\nSaved to {output_file}")
    print(f"Merged top signals into signal cache for harvester")


if __name__ == "__main__":
    main()

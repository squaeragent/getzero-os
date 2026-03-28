#!/usr/bin/env python3
"""
ZERO OS — Signal Backtester
Uses Envy history endpoint (7 days of 15-min data) to validate signals.
Tests entry/exit expressions against historical indicator values.

Usage:
  python3 scanner/backtest_signals.py              # backtest all coins
  python3 scanner/backtest_signals.py --coin BTC   # single coin
  python3 scanner/backtest_signals.py --top 20     # top 20 signals only
"""

import json
import os
import sys
import time
import urllib.request
import yaml
from datetime import datetime, timezone
from pathlib import Path
import re

SCANNER_DIR = Path(__file__).parent
DATA_DIR = SCANNER_DIR / "data"
CACHE_DIR = DATA_DIR / "signals_cache"
RESULTS_DIR = DATA_DIR / "backtest_results"
BASE_URL = "https://gate.getzero.dev/api/claw"


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
    """Fetch 7 days of ALL indicator history for a coin."""
    url = f"{BASE_URL}/paid/indicators/history?coin={coin}&indicator=HURST_24H"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    
    if not data.get("success"):
        return {}
    
    # Returns ALL indicators, not just the one in the query param
    result = {}
    for ind_code, ind_data in data.get("data", {}).items():
        values = ind_data.get("values", [])
        result[ind_code] = {v["t"]: v["v"] for v in values}
    
    return result


def evaluate_expression(expression, indicator_values):
    """Evaluate a signal expression against indicator values."""
    if not expression or not expression.strip():
        return False
    
    # Handle weighted expressions
    if "((" in expression and "*" in expression:
        return evaluate_weighted(expression, indicator_values)
    
    clauses = re.split(r'\s+(AND|OR)\s+', expression)
    results = []
    operators = []
    
    for part in clauses:
        part = part.strip()
        if part in ("AND", "OR"):
            operators.append(part)
            continue
        
        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            results.append(False)
            continue
        
        ind, op, val = m.group(1), m.group(2), float(m.group(3))
        actual = indicator_values.get(ind)
        if actual is None:
            results.append(False)
            continue
        
        if op == ">=": results.append(actual >= val)
        elif op == "<=": results.append(actual <= val)
        elif op == ">": results.append(actual > val)
        elif op == "<": results.append(actual < val)
        elif op == "==": results.append(actual == val)
        elif op == "!=": results.append(actual != val)
    
    if not results:
        return False
    
    result = results[0]
    for i, op in enumerate(operators):
        if i + 1 < len(results):
            if op == "AND":
                result = result and results[i + 1]
            elif op == "OR":
                result = result or results[i + 1]
    
    return result


def evaluate_weighted(expression, indicator_values):
    """Evaluate weighted sum expressions: ((IND OP VAL) * W) + ... >= THRESHOLD"""
    clause_pattern = re.compile(r'\(\(([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<)\s*(-?[\d.]+)\)\s*\*\s*([\d.]+)\)')
    threshold_match = re.search(r'>=\s*([\d.]+)\s*$', expression)
    
    if not threshold_match:
        return False
    
    threshold = float(threshold_match.group(1))
    total = 0.0
    
    for m in clause_pattern.finditer(expression):
        ind, op, val, weight = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
        actual = indicator_values.get(ind)
        if actual is None:
            continue
        
        passed = False
        if op == ">=": passed = actual >= val
        elif op == "<=": passed = actual <= val
        elif op == ">": passed = actual > val
        elif op == "<": passed = actual < val
        
        if passed:
            total += weight
    
    return total >= threshold


def backtest_coin(coin, signals, history, max_hold_bars=96):
    """Backtest signals for one coin against historical data."""
    if not history or "CLOSE_PRICE_15M" not in history:
        return []
    
    price_data = history["CLOSE_PRICE_15M"]
    timestamps = sorted(price_data.keys())
    
    results = []
    
    for signal in signals:
        expression = signal.get("expression", "")
        exit_expression = signal.get("exit_expression", "")
        direction = signal.get("signal_type", "LONG")
        max_hold = signal.get("max_hold_hours")
        max_hold_bars_sig = int(max_hold * 4) if max_hold else max_hold_bars
        name = signal.get("name", "unknown")
        
        trades = []
        
        for i, ts in enumerate(timestamps[:-max_hold_bars_sig]):
            # Build indicator snapshot at this timestamp
            snapshot = {}
            for ind_code, ind_values in history.items():
                if ts in ind_values:
                    snapshot[ind_code] = ind_values[ts]
            
            if not snapshot:
                continue
            
            # Check entry
            if not evaluate_expression(expression, snapshot):
                continue
            
            # Entry triggered — simulate trade
            entry_price = price_data.get(ts)
            if not entry_price or entry_price <= 0:
                continue
            
            # Look for exit
            exit_price = None
            exit_bar = None
            exit_reason = "max_hold"
            
            for j in range(i + 1, min(i + max_hold_bars_sig, len(timestamps))):
                exit_ts = timestamps[j]
                
                # Check exit expression
                if exit_expression:
                    exit_snapshot = {}
                    for ind_code, ind_values in history.items():
                        if exit_ts in ind_values:
                            exit_snapshot[ind_code] = ind_values[exit_ts]
                    
                    if exit_snapshot and evaluate_expression(exit_expression, exit_snapshot):
                        exit_price = price_data.get(exit_ts, entry_price)
                        exit_bar = j - i
                        exit_reason = "exit_expression"
                        break
                
                # Check stop loss (5% default)
                current_price = price_data.get(exit_ts, entry_price)
                if direction == "LONG":
                    pct_change = (current_price - entry_price) / entry_price
                    if pct_change <= -0.05:
                        exit_price = current_price
                        exit_bar = j - i
                        exit_reason = "stop_loss"
                        break
                else:
                    pct_change = (entry_price - current_price) / entry_price
                    if pct_change <= -0.05:
                        exit_price = current_price
                        exit_bar = j - i
                        exit_reason = "stop_loss"
                        break
            
            if exit_price is None:
                # Max hold reached
                last_ts = timestamps[min(i + max_hold_bars_sig - 1, len(timestamps) - 1)]
                exit_price = price_data.get(last_ts, entry_price)
                exit_bar = max_hold_bars_sig
            
            # Calculate P&L
            if direction == "LONG":
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100
            
            trades.append({
                "entry_ts": ts,
                "exit_bar": exit_bar,
                "pnl_pct": round(pnl_pct, 4),
                "exit_reason": exit_reason,
            })
        
        if trades:
            wins = sum(1 for t in trades if t["pnl_pct"] > 0)
            total_pnl = sum(t["pnl_pct"] for t in trades)
            results.append({
                "signal": name,
                "direction": direction,
                "sharpe": signal.get("sharpe", 0),
                "trades": len(trades),
                "wins": wins,
                "win_rate": round(wins / len(trades) * 100, 1),
                "total_pnl_pct": round(total_pnl, 2),
                "avg_pnl_pct": round(total_pnl / len(trades), 3),
                "max_pnl": round(max(t["pnl_pct"] for t in trades), 3),
                "min_pnl": round(min(t["pnl_pct"] for t in trades), 3),
            })
    
    return results


def main():
    api_key = load_api_key()
    target_coin = None
    top_n = None
    
    for arg in sys.argv[1:]:
        if arg == "--coin" and sys.argv.index(arg) + 1 < len(sys.argv):
            target_coin = sys.argv[sys.argv.index(arg) + 1]
        if arg == "--top" and sys.argv.index(arg) + 1 < len(sys.argv):
            top_n = int(sys.argv[sys.argv.index(arg) + 1])
    
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load signal caches
    coins = []
    for f in sorted(CACHE_DIR.glob("*.json")):
        coin = f.stem
        if target_coin and coin != target_coin:
            continue
        with open(f) as fh:
            signals = json.load(fh)
        coins.append((coin, signals))
    
    print(f"Backtesting {sum(len(s) for _, s in coins)} signals across {len(coins)} coins")
    print(f"Using 7 days of 15-min Envy data\n")
    
    all_results = {}
    
    for coin, signals in coins:
        print(f"  {coin}: fetching history...", end=" ", flush=True)
        try:
            history = fetch_history(coin, api_key)
            n_indicators = len(history)
            n_points = len(next(iter(history.values()), {}).get("values", [])) if history else 0
            print(f"{n_indicators} indicators", end=" → ", flush=True)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        
        results = backtest_coin(coin, signals, history)
        all_results[coin] = results
        
        fired = [r for r in results if r["trades"] > 0]
        profitable = [r for r in fired if r["total_pnl_pct"] > 0]
        print(f"{len(fired)}/{len(signals)} fired, {len(profitable)} profitable")
    
    # Save results
    output_file = RESULTS_DIR / "backtest_latest.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)
    
    # Summary
    print(f"\n{'='*60}")
    print("BACKTEST SUMMARY")
    print(f"{'='*60}")
    
    all_signals = []
    for coin, results in all_results.items():
        for r in results:
            r["coin"] = coin
            all_signals.append(r)
    
    all_signals.sort(key=lambda x: x["avg_pnl_pct"], reverse=True)
    
    if top_n:
        all_signals = all_signals[:top_n]
    
    total_fired = sum(1 for s in all_signals if s["trades"] > 0)
    total_profitable = sum(1 for s in all_signals if s.get("total_pnl_pct", 0) > 0)
    
    print(f"Total signals with trades: {total_fired}")
    print(f"Profitable: {total_profitable} ({total_profitable/total_fired*100:.0f}%)" if total_fired else "")
    
    print(f"\nTop 10 by avg P&L:")
    for s in all_signals[:10]:
        print(f"  {s['coin']:8s} {s['direction']:5s}  wr={s['win_rate']:5.1f}%  "
              f"avg={s['avg_pnl_pct']:+6.3f}%  trades={s['trades']:2d}  {s['signal'][:40]}")
    
    print(f"\nBottom 10:")
    for s in all_signals[-10:]:
        print(f"  {s['coin']:8s} {s['direction']:5s}  wr={s['win_rate']:5.1f}%  "
              f"avg={s['avg_pnl_pct']:+6.3f}%  trades={s['trades']:2d}  {s['signal'][:40]}")
    
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()

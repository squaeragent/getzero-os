#!/usr/bin/env python3
"""
SmartProvider Backtest Framework — 4 strategies compared.

A: SmartProvider (full reasoning engine)
B: Buy and Hold
C: Random Entry + Same Stops (100 sims, median)
D: Regime-Only (enter any signal in trending regimes)

Usage: python3 scanner/backtest/backtest.py
"""

import sys
import os
import csv
import json
import math
import random
import copy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scanner.v6.smart_provider import SmartProvider
from scanner.v6.smart_data import MarketData

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
COINS = ["SOL", "ETH", "BTC"]

# Regimes considered "trending" for Strategy D
TRENDING_REGIMES = {"strong_trend", "moderate_trend", "weak_trend", "chaotic_trend"}


# ─── Data Types ──────────────────────────────────────────────

@dataclass
class Trade:
    coin: str
    direction: str  # LONG or SHORT
    entry_price: float
    entry_time: str
    exit_price: float = 0.0
    exit_time: str = ""
    stop_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_hours: float = 0.0


# ─── Data Loading ────────────────────────────────────────────

def load_csv(path: Path) -> list[dict]:
    """Load OHLCV CSV into list of dicts."""
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })
    return rows


def load_all_data() -> dict:
    """Load all coin data. Returns {coin: {1h: [...], 4h: [...], 1d: [...]}}."""
    data = {}
    for coin in COINS:
        data[coin] = {}
        for tf in ["1h", "4h", "1d"]:
            path = DATA_DIR / f"{coin}_{tf}.csv"
            if path.exists():
                data[coin][tf] = load_csv(path)
                print(f"  Loaded {coin} {tf}: {len(data[coin][tf])} candles")
            else:
                print(f"  WARNING: {path} not found")
                data[coin][tf] = []
    return data


def ts_to_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─── Build MarketData from historical window ────────────────

def build_market_data(coin: str, candles_1h: list[dict], idx: int,
                      candles_4h: list[dict], candles_1d: list[dict]) -> MarketData:
    """Build MarketData from a window ending at idx in the 1h candles."""
    window = max(0, idx - 499)
    chunk = candles_1h[window:idx + 1]

    current_ts = candles_1h[idx]["timestamp"]

    # Find 4h candles up to current time
    c4h = [c for c in candles_4h if c["timestamp"] <= current_ts]
    c4h = c4h[-200:] if len(c4h) > 200 else c4h

    # Find 1d candles up to current time
    c1d = [c for c in candles_1d if c["timestamp"] <= current_ts]
    c1d = c1d[-400:] if len(c1d) > 400 else c1d

    return MarketData(
        coin=coin,
        closes=[c["close"] for c in chunk],
        highs=[c["high"] for c in chunk],
        lows=[c["low"] for c in chunk],
        volumes=[c["volume"] for c in chunk],
        closes_1h=[c["close"] for c in chunk],
        closes_4h=[c["close"] for c in c4h] if c4h else [c["close"] for c in chunk],
        closes_1d=[c["close"] for c in c1d] if c1d else [c["close"] for c in chunk],
        funding_current=0.0,
        funding_predicted=0.0,
        funding_history=[],
        book_depth_usd=0.0,
        spread_bps=0.0,
        open_interest=0.0,
        mid_price=chunk[-1]["close"] if chunk else 0.0,
    )


# ─── Metrics Computation ────────────────────────────────────

def compute_metrics(trades: list[Trade], equity_curve: list[dict],
                    initial_equity: float) -> dict:
    """Compute all performance metrics from trades and equity curve."""
    if not equity_curve:
        return _empty_metrics()

    final_equity = equity_curve[-1]["equity"]
    total_return_pct = ((final_equity - initial_equity) / initial_equity) * 100

    # Max drawdown from equity curve
    max_eq = initial_equity
    max_dd = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        max_eq = max(max_eq, eq)
        dd = (max_eq - eq) / max_eq if max_eq > 0 else 0
        max_dd = max(max_dd, dd)

    # Hourly returns for Sharpe
    hourly_returns = []
    for i in range(1, len(equity_curve)):
        prev_eq = equity_curve[i - 1]["equity"]
        curr_eq = equity_curve[i]["equity"]
        if prev_eq > 0:
            hourly_returns.append((curr_eq - prev_eq) / prev_eq)

    sharpe = 0.0
    if hourly_returns and len(hourly_returns) > 1:
        mean_r = sum(hourly_returns) / len(hourly_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in hourly_returns) / (len(hourly_returns) - 1))
        if std_r > 0:
            sharpe = (mean_r / std_r) * math.sqrt(8760)  # Annualized from hourly

    # Trade stats
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0

    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0
    avg_wl_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    avg_hold = sum(t.hold_hours for t in trades) / len(trades) if trades else 0
    largest_loss = min((t.pnl_pct for t in trades), default=0) * 100

    calmar = (total_return_pct / (max_dd * 100)) if max_dd > 0 else 0

    return {
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_loss_ratio": round(avg_wl_ratio, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "inf",
        "total_trades": len(trades),
        "avg_hold_hours": round(avg_hold, 1),
        "largest_loss_pct": round(largest_loss, 2),
        "calmar_ratio": round(calmar, 3),
    }


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0, "max_drawdown_pct": 0, "sharpe_ratio": 0,
        "win_rate_pct": 0, "avg_win_loss_ratio": 0, "profit_factor": 0,
        "total_trades": 0, "avg_hold_hours": 0, "largest_loss_pct": 0,
        "calmar_ratio": 0,
    }


# ─── Backtester Engine ──────────────────────────────────────

class Backtester:
    def __init__(self, initial_equity=10000, position_size_pct=0.10):
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.position_size_pct = position_size_pct
        self.trades: list[Trade] = []
        self.open_trades: dict[str, Trade] = {}  # coin -> Trade
        self.equity_curve: list[dict] = []
        self.rejections = 0
        self.evaluations = 0

    def check_stops_and_expiry(self, coin: str, candle: dict, current_ts: int):
        """Check if open trade for coin hit stop or max hold."""
        if coin not in self.open_trades:
            return
        trade = self.open_trades[coin]
        price = candle["close"]
        high = candle["high"]
        low = candle["low"]

        hit_stop = False
        if trade.direction == "LONG" and low <= trade.stop_price:
            hit_stop = True
            price = trade.stop_price
        elif trade.direction == "SHORT" and high >= trade.stop_price:
            hit_stop = True
            price = trade.stop_price

        hours_held = (current_ts - int(datetime.strptime(trade.entry_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)) / 3_600_000
        expired = hours_held >= 24

        if hit_stop or expired:
            self._close_trade(coin, price, ts_to_str(current_ts))

    def open_trade(self, coin: str, direction: str, price: float, stop_price: float, ts_str: str):
        if coin in self.open_trades:
            return  # Already in position
        trade = Trade(
            coin=coin, direction=direction,
            entry_price=price, entry_time=ts_str,
            stop_price=stop_price,
        )
        self.open_trades[coin] = trade

    def close_trade_signal(self, coin: str, price: float, ts_str: str):
        """Close trade due to signal change."""
        if coin in self.open_trades:
            self._close_trade(coin, price, ts_str)

    def _close_trade(self, coin: str, exit_price: float, exit_time: str):
        trade = self.open_trades.pop(coin)
        trade.exit_price = exit_price
        trade.exit_time = exit_time
        if trade.direction == "LONG":
            trade.pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price
        else:
            trade.pnl_pct = (trade.entry_price - trade.exit_price) / trade.entry_price
        trade.pnl = self.equity * self.position_size_pct * trade.pnl_pct
        self.equity += trade.pnl

        entry_ts = int(datetime.strptime(trade.entry_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)
        exit_ts = int(datetime.strptime(trade.exit_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)
        trade.hold_hours = (exit_ts - entry_ts) / 3_600_000
        self.trades.append(trade)

    def record_equity(self, ts_str: str):
        self.equity_curve.append({"timestamp": ts_str, "equity": round(self.equity, 2)})

    def close_all(self, prices: dict[str, float], ts_str: str):
        """Close all open trades at given prices."""
        for coin in list(self.open_trades.keys()):
            if coin in prices:
                self._close_trade(coin, prices[coin], ts_str)


# ─── Strategy A: SmartProvider ───────────────────────────────

def run_smartprovider(data: dict) -> dict:
    print("\n=== Strategy A: SmartProvider ===")
    sp = SmartProvider()
    bt = Backtester()

    # We need at least 500 candles of warmup
    warmup = 500
    total_hours = len(data[COINS[0]]["1h"])

    for i in range(warmup, total_hours):
        if i % 500 == 0:
            print(f"  [{i}/{total_hours}] evaluating...", flush=True)

        for coin in COINS:
            candles_1h = data[coin]["1h"]
            if i >= len(candles_1h):
                continue

            candle = candles_1h[i]
            ts_str = ts_to_str(candle["timestamp"])

            # Check stops first
            bt.check_stops_and_expiry(coin, candle, candle["timestamp"])

            # Evaluate every 4 hours to match realistic frequency
            if i % 4 != 0:
                continue

            bt.evaluations += 1
            try:
                md = build_market_data(coin, candles_1h, i, data[coin]["4h"], data[coin]["1d"])
                result = sp.evaluate_coin(coin, md)
            except Exception as e:
                bt.rejections += 1
                continue

            direction = result.get("direction", "NEUTRAL")
            quality = result.get("quality", 0)
            atr_pct = result.get("atr_pct", 0.03)

            # If we have an open trade and direction changed, close it
            if coin in bt.open_trades:
                existing = bt.open_trades[coin]
                if direction != existing.direction:
                    bt.close_trade_signal(coin, candle["close"], ts_str)

            # Enter new trade if signal is actionable
            if direction in ("LONG", "SHORT") and quality >= 3 and coin not in bt.open_trades:
                stop_pct = max(atr_pct * 2, 0.03)
                if direction == "LONG":
                    stop_price = candle["close"] * (1 - stop_pct)
                else:
                    stop_price = candle["close"] * (1 + stop_pct)
                bt.open_trade(coin, direction, candle["close"], stop_price, ts_str)
            elif direction == "NEUTRAL" or quality < 3:
                bt.rejections += 1

        bt.record_equity(ts_to_str(data[COINS[0]]["1h"][i]["timestamp"]))

    # Close all remaining at end
    final_prices = {coin: data[coin]["1h"][-1]["close"] for coin in COINS}
    bt.close_all(final_prices, ts_to_str(data[COINS[0]]["1h"][-1]["timestamp"]))

    print(f"  Evaluations: {bt.evaluations}, Rejections: {bt.rejections}, Trades: {len(bt.trades)}")
    metrics = compute_metrics(bt.trades, bt.equity_curve, bt.initial_equity)
    return {"metrics": metrics, "equity_curve": bt.equity_curve, "trades": len(bt.trades)}


# ─── Strategy B: Buy and Hold ───────────────────────────────

def run_buy_and_hold(data: dict) -> dict:
    print("\n=== Strategy B: Buy and Hold ===")
    initial_equity = 10000
    allocation = initial_equity / len(COINS)

    equity_curve = []
    first_prices = {}
    for coin in COINS:
        first_prices[coin] = data[coin]["1h"][0]["close"]

    total_hours = len(data[COINS[0]]["1h"])
    for i in range(total_hours):
        total_eq = 0.0
        for coin in COINS:
            if i < len(data[coin]["1h"]):
                current = data[coin]["1h"][i]["close"]
                coin_eq = allocation * (current / first_prices[coin])
                total_eq += coin_eq
        equity_curve.append({
            "timestamp": ts_to_str(data[COINS[0]]["1h"][i]["timestamp"]),
            "equity": round(total_eq, 2),
        })

    final_equity = equity_curve[-1]["equity"]
    total_return = ((final_equity - initial_equity) / initial_equity) * 100

    # Compute drawdown and Sharpe from curve
    metrics = compute_metrics([], equity_curve, initial_equity)
    metrics["total_return_pct"] = round(total_return, 2)
    metrics["total_trades"] = len(COINS)  # Just the initial buys

    print(f"  Final equity: ${final_equity:,.2f} ({total_return:+.2f}%)")
    return {"metrics": metrics, "equity_curve": equity_curve}


# ─── Strategy C: Random Entry + Same Stops ───────────────────

def run_random(data: dict, n_sims: int = 100) -> dict:
    print(f"\n=== Strategy C: Random Entry + Same Stops ({n_sims} sims) ===")
    rng = random.Random(42)

    # First, get SmartProvider's entry frequency and ATR stops
    # Count how many entries SmartProvider made per coin
    sp = SmartProvider()
    warmup = 500
    total_hours = len(data[COINS[0]]["1h"])

    # Collect entry points and ATR values from SmartProvider run
    sp_entries_per_coin = {coin: 0 for coin in COINS}
    atr_at_time = {}  # (coin, idx) -> atr_pct

    for i in range(warmup, total_hours, 4):
        for coin in COINS:
            if i >= len(data[coin]["1h"]):
                continue
            try:
                md = build_market_data(coin, data[coin]["1h"], i, data[coin]["4h"], data[coin]["1d"])
                result = sp.evaluate_coin(coin, md)
                atr_pct = result.get("atr_pct", 0.03)
                atr_at_time[(coin, i)] = atr_pct
                if result.get("direction") in ("LONG", "SHORT") and result.get("quality", 0) >= 3:
                    sp_entries_per_coin[coin] += 1
            except Exception:
                atr_at_time[(coin, i)] = 0.03

    total_sp_entries = sum(sp_entries_per_coin.values())
    eval_points = list(range(warmup, total_hours, 4))
    entry_probability = total_sp_entries / (len(eval_points) * len(COINS)) if eval_points else 0.01
    print(f"  SP entry probability: {entry_probability:.4f} ({total_sp_entries} entries)")

    all_sim_metrics = []

    for sim in range(n_sims):
        bt = Backtester()
        sim_rng = random.Random(42 + sim)

        for i in range(warmup, total_hours):
            for coin in COINS:
                if i >= len(data[coin]["1h"]):
                    continue
                candle = data[coin]["1h"][i]
                bt.check_stops_and_expiry(coin, candle, candle["timestamp"])

            if i % 4 != 0:
                bt.record_equity(ts_to_str(data[COINS[0]]["1h"][i]["timestamp"]))
                continue

            for coin in COINS:
                if i >= len(data[coin]["1h"]):
                    continue
                candle = data[coin]["1h"][i]
                ts_str = ts_to_str(candle["timestamp"])

                if coin not in bt.open_trades and sim_rng.random() < entry_probability:
                    direction = sim_rng.choice(["LONG", "SHORT"])
                    atr_pct = atr_at_time.get((coin, i), 0.03)
                    stop_pct = max(atr_pct * 2, 0.03)
                    if direction == "LONG":
                        stop_price = candle["close"] * (1 - stop_pct)
                    else:
                        stop_price = candle["close"] * (1 + stop_pct)
                    bt.open_trade(coin, direction, candle["close"], stop_price, ts_str)

            bt.record_equity(ts_to_str(data[COINS[0]]["1h"][i]["timestamp"]))

        final_prices = {coin: data[coin]["1h"][-1]["close"] for coin in COINS}
        bt.close_all(final_prices, ts_to_str(data[COINS[0]]["1h"][-1]["timestamp"]))
        all_sim_metrics.append(compute_metrics(bt.trades, bt.equity_curve, bt.initial_equity))

    # Take median of each metric
    median_metrics = {}
    for key in all_sim_metrics[0]:
        vals = sorted(m[key] for m in all_sim_metrics if isinstance(m[key], (int, float)))
        if vals:
            mid = len(vals) // 2
            median_metrics[key] = vals[mid]
        else:
            median_metrics[key] = 0

    print(f"  Median return: {median_metrics.get('total_return_pct', 0):+.2f}%")
    return {"metrics": median_metrics, "equity_curve": []}


# ─── Strategy D: Regime-Only ─────────────────────────────────

def run_regime_only(data: dict) -> dict:
    print("\n=== Strategy D: Regime-Only ===")
    sp = SmartProvider()
    bt = Backtester()

    warmup = 500
    total_hours = len(data[COINS[0]]["1h"])

    for i in range(warmup, total_hours):
        if i % 500 == 0:
            print(f"  [{i}/{total_hours}] evaluating...", flush=True)

        for coin in COINS:
            if i >= len(data[coin]["1h"]):
                continue
            candle = data[coin]["1h"][i]
            ts_str = ts_to_str(candle["timestamp"])

            bt.check_stops_and_expiry(coin, candle, candle["timestamp"])

            if i % 4 != 0:
                continue

            try:
                md = build_market_data(coin, data[coin]["1h"], i, data[coin]["4h"], data[coin]["1d"])
                result = sp.evaluate_coin(coin, md)
            except Exception:
                continue

            regime = result.get("regime", "random_quiet")
            direction = result.get("direction", "NEUTRAL")
            atr_pct = result.get("atr_pct", 0.03)

            # Regime-only: enter ANY directional signal during trending regimes
            if regime in TRENDING_REGIMES:
                if direction in ("LONG", "SHORT") and coin not in bt.open_trades:
                    stop_pct = max(atr_pct * 2, 0.03)
                    if direction == "LONG":
                        stop_price = candle["close"] * (1 - stop_pct)
                    else:
                        stop_price = candle["close"] * (1 + stop_pct)
                    bt.open_trade(coin, direction, candle["close"], stop_price, ts_str)
            else:
                # Close positions in non-trending regimes
                if coin in bt.open_trades:
                    bt.close_trade_signal(coin, candle["close"], ts_str)

        bt.record_equity(ts_to_str(data[COINS[0]]["1h"][i]["timestamp"]))

    final_prices = {coin: data[coin]["1h"][-1]["close"] for coin in COINS}
    bt.close_all(final_prices, ts_to_str(data[COINS[0]]["1h"][-1]["timestamp"]))

    print(f"  Trades: {len(bt.trades)}")
    metrics = compute_metrics(bt.trades, bt.equity_curve, bt.initial_equity)
    return {"metrics": metrics, "equity_curve": bt.equity_curve}


# ─── Main ────────────────────────────────────────────────────

def print_summary(results: dict):
    """Print comparison table."""
    print("\n" + "=" * 90)
    print("BACKTEST RESULTS — SmartProvider vs Alternatives")
    print("Period: 2025-03-25 to 2026-03-25 | Coins: SOL, ETH, BTC")
    print("=" * 90)

    headers = ["Metric", "SmartProvider", "Buy & Hold", "Random+Stops", "Regime-Only"]
    col_w = [20, 15, 15, 15, 15]

    print("".join(h.ljust(w) for h, w in zip(headers, col_w)))
    print("-" * 80)

    metric_labels = {
        "total_return_pct": "Return %",
        "max_drawdown_pct": "Max DD %",
        "sharpe_ratio": "Sharpe",
        "win_rate_pct": "Win Rate %",
        "avg_win_loss_ratio": "Avg W/L",
        "profit_factor": "Profit Factor",
        "total_trades": "Trades",
        "avg_hold_hours": "Avg Hold (h)",
        "largest_loss_pct": "Largest Loss %",
        "calmar_ratio": "Calmar",
    }

    strategies = ["smartprovider", "buy_and_hold", "random_stops", "regime_only"]

    for key, label in metric_labels.items():
        row = [label.ljust(col_w[0])]
        for strat in strategies:
            val = results["strategies"][strat].get(key, "N/A")
            if isinstance(val, float):
                row.append(f"{val:>12.2f}   ")
            else:
                row.append(f"{str(val):>12}   ")
        print("".join(row))

    print("=" * 90)


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading historical data...")
    data = load_all_data()

    # Validate data
    for coin in COINS:
        if not data[coin]["1h"]:
            print(f"ERROR: No 1h data for {coin}. Run fetch_data.py first.")
            sys.exit(1)

    # Run all 4 strategies
    result_a = run_smartprovider(data)
    result_b = run_buy_and_hold(data)
    result_c = run_random(data)
    result_d = run_regime_only(data)

    # Assemble results
    results = {
        "period": "2025-03-25 to 2026-03-25",
        "coins": COINS,
        "strategies": {
            "smartprovider": result_a["metrics"],
            "buy_and_hold": result_b["metrics"],
            "random_stops": result_c["metrics"],
            "regime_only": result_d["metrics"],
        },
        "equity_curves": {
            "smartprovider": result_a.get("equity_curve", []),
            "buy_and_hold": result_b.get("equity_curve", []),
        },
    }

    # Save results
    output_path = RESULTS_DIR / "backtest_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    # Print summary table
    print_summary(results)


if __name__ == "__main__":
    main()

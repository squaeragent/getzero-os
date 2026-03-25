#!/usr/bin/env python3
"""
Fetch historical 1h OHLCV candles from Binance public API.
Generates 4h and 1d aggregations from 1h data.
No API key needed.
"""

import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
COINS = ["SOL", "ETH", "BTC"]
BINANCE_URL = "https://api.binance.com/api/v3/klines"

# 12 months: 2025-03-25 to 2026-03-25
START_MS = int(datetime(2025, 3, 25, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

LIMIT = 1000  # Binance max per request


def fetch_1h_candles(coin: str) -> list[dict]:
    """Fetch all 1h candles for a coin via paginated requests."""
    symbol = f"{coin}USDT"
    all_candles = []
    current_start = START_MS

    while current_start < END_MS:
        url = (
            f"{BINANCE_URL}?symbol={symbol}&interval=1h"
            f"&startTime={current_start}&endTime={END_MS}&limit={LIMIT}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
        except Exception as e:
            print(f"  Error fetching {symbol} at {current_start}: {e}")
            time.sleep(2)
            continue

        if not data:
            break

        for candle in data:
            all_candles.append({
                "timestamp": int(candle[0]),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })

        # Next page starts after last candle
        current_start = int(data[-1][0]) + 1
        print(f"  {coin}: {len(all_candles)} candles fetched...", flush=True)
        time.sleep(0.2)  # Rate limit courtesy

    return all_candles


def save_csv(candles: list[dict], path: Path):
    """Save candles to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(candles)


def aggregate_candles(candles_1h: list[dict], factor: int) -> list[dict]:
    """Aggregate 1h candles into higher timeframe (4h=4, 1d=24)."""
    aggregated = []
    for i in range(0, len(candles_1h) - factor + 1, factor):
        group = candles_1h[i:i + factor]
        aggregated.append({
            "timestamp": group[0]["timestamp"],
            "open": group[0]["open"],
            "high": max(c["high"] for c in group),
            "low": min(c["low"] for c in group),
            "close": group[-1]["close"],
            "volume": sum(c["volume"] for c in group),
        })
    return aggregated


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching 1h candles from Binance: {', '.join(COINS)}")
    print(f"Period: 2025-03-25 to 2026-03-25\n")

    for coin in COINS:
        print(f"Fetching {coin}...")
        candles_1h = fetch_1h_candles(coin)
        print(f"  {coin}: {len(candles_1h)} total 1h candles")

        if not candles_1h:
            print(f"  WARNING: No data for {coin}, skipping")
            continue

        # Save 1h
        save_csv(candles_1h, DATA_DIR / f"{coin}_1h.csv")

        # Generate and save 4h
        candles_4h = aggregate_candles(candles_1h, 4)
        save_csv(candles_4h, DATA_DIR / f"{coin}_4h.csv")
        print(f"  {coin}: {len(candles_4h)} 4h candles")

        # Generate and save 1d
        candles_1d = aggregate_candles(candles_1h, 24)
        save_csv(candles_1d, DATA_DIR / f"{coin}_1d.csv")
        print(f"  {coin}: {len(candles_1d)} 1d candles")

        print()

    print("Done. Data saved to scanner/backtest/data/")


if __name__ == "__main__":
    main()

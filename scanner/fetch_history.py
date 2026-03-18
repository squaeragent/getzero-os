#!/usr/bin/env python3
"""
Historical Data Pipeline — fetches 1+ year of OHLCV candle data from
Hyperliquid's candle API and stores locally for backtesting.

Usage:
  python3 scanner/fetch_history.py                     # fetch all 10 coins, 1h candles, 200 days
  python3 scanner/fetch_history.py --coin BTC           # single coin
  python3 scanner/fetch_history.py --interval 15m       # 15-min candles
  python3 scanner/fetch_history.py --days 365           # 1 year
  python3 scanner/fetch_history.py --funding             # also fetch funding history

Data stored in: scanner/data/history/{COIN}_{interval}.json
Funding stored: scanner/data/history/{COIN}_funding.json
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCANNER_DIR = Path(__file__).resolve().parent
DATA_DIR = SCANNER_DIR / "data" / "history"
HL_URL = "https://api.hyperliquid.xyz/info"

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]
DEFAULT_INTERVAL = "1h"
DEFAULT_DAYS = 200  # HL returns ~5000 candles max per request

# HL interval options: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1w, 1M
VALID_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]


def fetch_candles(coin, interval, start_ms, end_ms):
    """Fetch candles from HL. Returns list of OHLCV dicts."""
    req = urllib.request.Request(HL_URL,
        data=json.dumps({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            }
        }).encode(),
        headers={"Content-Type": "application/json"})
    
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return resp  # list of {"t": ms, "T": ms, "s": "BTC", "i": "1h", "o": "...", "c": "...", "h": "...", "l": "...", "v": "...", "n": count}


def fetch_funding_history(coin, start_ms, end_ms=None, retries=3):
    """Fetch hourly funding rate history from HL."""
    req_data = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_ms,
    }
    if end_ms:
        req_data["endTime"] = end_ms
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(HL_URL,
                data=json.dumps(req_data).encode(),
                headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            return resp
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f" [429 rate limited, waiting {wait}s]", end="", flush=True)
                time.sleep(wait)
            else:
                raise


def interval_to_ms(interval):
    """Convert interval string to milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    num = int(interval[:-1])
    unit = interval[-1]
    return num * units[unit]


def fetch_all_candles(coin, interval, days):
    """Fetch candles in chunks, handling the 5000 candle limit."""
    now = int(time.time() * 1000)
    start = now - days * 86_400_000
    
    all_candles = []
    chunk_start = start
    interval_ms = interval_to_ms(interval)
    chunk_size = 5000 * interval_ms  # max candles per request
    
    while chunk_start < now:
        chunk_end = min(chunk_start + chunk_size, now)
        candles = fetch_candles(coin, interval, chunk_start, chunk_end)
        
        if not candles:
            break
        
        all_candles.extend(candles)
        chunk_start = chunk_end
        
        if chunk_end >= now:
            break
        
        time.sleep(0.2)  # polite rate limiting
    
    # Deduplicate by timestamp
    seen = set()
    unique = []
    for c in all_candles:
        t = c["t"]
        if t not in seen:
            seen.add(t)
            unique.append(c)
    
    unique.sort(key=lambda c: c["t"])
    return unique


def fetch_all_funding(coin, days):
    """Fetch funding history in chunks."""
    now = int(time.time() * 1000)
    start = now - days * 86_400_000
    
    all_entries = []
    chunk_start = start
    chunk_days = 30  # ~30 days per chunk (720 entries at 1/hour)
    
    while chunk_start < now:
        chunk_end = min(chunk_start + chunk_days * 86_400_000, now)
        entries = fetch_funding_history(coin, chunk_start, chunk_end)
        
        if not entries:
            break
        
        all_entries.extend(entries)
        
        # Next chunk starts after last entry
        if entries:
            last_time = max(e.get("time", 0) for e in entries)
            chunk_start = last_time + 1
        else:
            chunk_start = chunk_end
        
        if chunk_start >= now:
            break
        
        time.sleep(1.5)  # funding endpoint is more rate-limited
    
    # Deduplicate
    seen = set()
    unique = []
    for e in all_entries:
        t = e.get("time", 0)
        if t not in seen:
            seen.add(t)
            unique.append(e)
    
    unique.sort(key=lambda e: e.get("time", 0))
    return unique


def save_candles(coin, interval, candles):
    """Save candles to JSON file with metadata."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / f"{coin}_{interval}.json"
    
    # Convert to compact format
    compact = []
    for c in candles:
        compact.append({
            "t": c["t"],
            "o": float(c["o"]),
            "h": float(c["h"]),
            "l": float(c["l"]),
            "c": float(c["c"]),
            "v": float(c["v"]),
        })
    
    output = {
        "coin": coin,
        "interval": interval,
        "count": len(compact),
        "first": datetime.fromtimestamp(compact[0]["t"] / 1000, tz=timezone.utc).isoformat() if compact else None,
        "last": datetime.fromtimestamp(compact[-1]["t"] / 1000, tz=timezone.utc).isoformat() if compact else None,
        "fetched": datetime.now(timezone.utc).isoformat(),
        "candles": compact,
    }
    
    with open(filepath, "w") as f:
        json.dump(output, f)
    
    return filepath


def save_funding(coin, entries):
    """Save funding history."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / f"{coin}_funding.json"
    
    compact = []
    for e in entries:
        compact.append({
            "t": e.get("time", 0),
            "rate": float(e.get("fundingRate", 0)),
            "premium": float(e.get("premium", 0)),
        })
    
    output = {
        "coin": coin,
        "count": len(compact),
        "first": datetime.fromtimestamp(compact[0]["t"] / 1000, tz=timezone.utc).isoformat() if compact else None,
        "last": datetime.fromtimestamp(compact[-1]["t"] / 1000, tz=timezone.utc).isoformat() if compact else None,
        "fetched": datetime.now(timezone.utc).isoformat(),
        "entries": compact,
    }
    
    with open(filepath, "w") as f:
        json.dump(output, f)
    
    return filepath


def main():
    args = sys.argv[1:]
    
    # Parse args
    coins = COINS
    interval = DEFAULT_INTERVAL
    days = DEFAULT_DAYS
    fetch_fund = False
    
    i = 0
    while i < len(args):
        if args[i] == "--coin" and i + 1 < len(args):
            coins = [args[i + 1].upper()]
            i += 2
        elif args[i] == "--interval" and i + 1 < len(args):
            interval = args[i + 1]
            i += 2
        elif args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1])
            i += 2
        elif args[i] == "--funding":
            fetch_fund = True
            i += 1
        else:
            i += 1
    
    if interval not in VALID_INTERVALS:
        print(f"Invalid interval: {interval}. Valid: {VALID_INTERVALS}")
        sys.exit(1)
    
    print(f"Historical Data Pipeline")
    print(f"  Coins: {', '.join(coins)}")
    print(f"  Interval: {interval}")
    print(f"  Days: {days}")
    print(f"  Funding: {'yes' if fetch_fund else 'no'}")
    print(f"  Output: {DATA_DIR}/")
    print()
    
    for coin in coins:
        print(f"  {coin}:", end=" ", flush=True)
        
        # Fetch candles
        candles = fetch_all_candles(coin, interval, days)
        if candles:
            fp = save_candles(coin, interval, candles)
            first_dt = datetime.fromtimestamp(candles[0]["t"] / 1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(candles[-1]["t"] / 1000, tz=timezone.utc)
            span_days = (last_dt - first_dt).days
            size_kb = os.path.getsize(fp) / 1024
            print(f"{len(candles)} candles, {span_days} days ({first_dt.strftime('%Y-%m-%d')} → {last_dt.strftime('%Y-%m-%d')}), {size_kb:.0f}KB", end="")
        else:
            print("no data", end="")
        
        # Fetch funding
        if fetch_fund:
            entries = fetch_all_funding(coin, days)
            if entries:
                fp = save_funding(coin, entries)
                size_kb = os.path.getsize(fp) / 1024
                print(f" + {len(entries)} funding entries ({size_kb:.0f}KB)", end="")
        
        print()
        time.sleep(0.5)  # be polite
    
    print(f"\nDone. Data saved to {DATA_DIR}/")


if __name__ == "__main__":
    main()

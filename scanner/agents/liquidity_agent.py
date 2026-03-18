#!/usr/bin/env python3
"""
ZERO OS — Agent 6: Liquidity Agent
Monitors Hyperliquid order book depth before trades, rejects thin-liquidity entries.

Inputs:
  Hyperliquid L2 book API (https://api.hyperliquid.xyz/info)

Outputs:
  scanner/bus/liquidity.json   — per-coin liquidity scores
  scanner/bus/heartbeat.json   — last-alive timestamp

Usage:
  python3 scanner/agents/liquidity_agent.py           # single run
  python3 scanner/agents/liquidity_agent.py --loop    # continuous 2-min cycle
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
LIQUIDITY_FILE = BUS_DIR / "liquidity.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# ─── CONFIG ───
HL_API_URL = "https://api.hyperliquid.xyz/info"
CYCLE_SECONDS = 120  # 2 minutes

TRADEABLE_COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]

# Tradeability thresholds
MAX_SPREAD_PCT = 0.05    # 0.05% max bid-ask spread
MIN_DEPTH_50 = 500.0     # $500 minimum depth within 0.5% of best bid/ask

# Depth bands (percentage from mid)
DEPTH_BAND_50 = 0.005    # 0.5% for $50 depth calc (also tradeability check)
DEPTH_BAND_100 = 0.01    # 1.0%
DEPTH_BAND_500 = 0.05    # 5.0%


# ─── HYPERLIQUID API ───
def fetch_l2_book(coin):
    """Fetch L2 order book for a coin from Hyperliquid."""
    payload = json.dumps({"type": "l2Book", "coin": coin}).encode()
    req = urllib.request.Request(
        HL_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def parse_book(raw):
    """Parse L2 book response into bid/ask lists of (price, size) tuples."""
    levels = raw.get("levels", [[], []])
    bids = [(float(l["px"]), float(l["sz"])) for l in levels[0]] if len(levels) > 0 else []
    asks = [(float(l["px"]), float(l["sz"])) for l in levels[1]] if len(levels) > 1 else []
    return bids, asks


# ─── LIQUIDITY CALCULATIONS ───
def compute_spread(bids, asks):
    """Compute bid-ask spread as percentage of mid price."""
    if not bids or not asks:
        return float("inf"), 0.0
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / 2
    if mid <= 0:
        return float("inf"), mid
    spread_pct = (best_ask - best_bid) / mid * 100
    return spread_pct, mid


def compute_depth(levels, mid, band_pct):
    """Sum USD notional within band_pct of mid price."""
    depth = 0.0
    for price, size in levels:
        if abs(price - mid) / mid <= band_pct:
            depth += price * size
    return depth


def compute_imbalance(bid_depth, ask_depth):
    """Book imbalance ratio: -1 (all asks) to +1 (all bids)."""
    total = bid_depth + ask_depth
    if total <= 0:
        return 0.0
    return (bid_depth - ask_depth) / total


def compute_liquidity_score(spread_pct, bid_depth_50, ask_depth_50, bid_depth_500, ask_depth_500):
    """Composite liquidity score 0-100."""
    # Spread component (0-40): lower spread = higher score
    if spread_pct <= 0.01:
        spread_score = 40
    elif spread_pct <= 0.05:
        spread_score = 40 * (1 - (spread_pct - 0.01) / 0.04)
    elif spread_pct <= 0.20:
        spread_score = 10 * (1 - (spread_pct - 0.05) / 0.15)
    else:
        spread_score = 0

    # Near depth component (0-30): depth within 0.5%
    near_depth = bid_depth_50 + ask_depth_50
    if near_depth >= 50000:
        depth_near_score = 30
    elif near_depth >= 5000:
        depth_near_score = 10 + 20 * (near_depth - 5000) / 45000
    elif near_depth >= 500:
        depth_near_score = 10 * (near_depth - 500) / 4500
    else:
        depth_near_score = 0

    # Far depth component (0-30): depth within 5%
    far_depth = bid_depth_500 + ask_depth_500
    if far_depth >= 500000:
        depth_far_score = 30
    elif far_depth >= 50000:
        depth_far_score = 10 + 20 * (far_depth - 50000) / 450000
    elif far_depth >= 5000:
        depth_far_score = 10 * (far_depth - 5000) / 45000
    else:
        depth_far_score = 0

    return round(min(100, max(0, spread_score + depth_near_score + depth_far_score)), 1)


def analyze_coin(coin):
    """Fetch and analyze liquidity for a single coin."""
    raw = fetch_l2_book(coin)
    bids, asks = parse_book(raw)

    spread_pct, mid = compute_spread(bids, asks)

    if mid <= 0:
        return {
            "tradeable": False,
            "spread_pct": None,
            "bid_depth_50": 0,
            "ask_depth_50": 0,
            "bid_depth_100": 0,
            "ask_depth_100": 0,
            "bid_depth_500": 0,
            "ask_depth_500": 0,
            "imbalance": 0,
            "score": 0,
            "error": "no_mid_price",
        }

    bid_depth_50 = compute_depth(bids, mid, DEPTH_BAND_50)
    ask_depth_50 = compute_depth(asks, mid, DEPTH_BAND_50)
    bid_depth_100 = compute_depth(bids, mid, DEPTH_BAND_100)
    ask_depth_100 = compute_depth(asks, mid, DEPTH_BAND_100)
    bid_depth_500 = compute_depth(bids, mid, DEPTH_BAND_500)
    ask_depth_500 = compute_depth(asks, mid, DEPTH_BAND_500)

    imbalance = compute_imbalance(bid_depth_50, ask_depth_50)
    score = compute_liquidity_score(spread_pct, bid_depth_50, ask_depth_50, bid_depth_500, ask_depth_500)

    # Tradeability check
    min_side_depth = min(bid_depth_50, ask_depth_50)
    tradeable = spread_pct < MAX_SPREAD_PCT and min_side_depth > MIN_DEPTH_50

    return {
        "tradeable": tradeable,
        "spread_pct": round(spread_pct, 6),
        "bid_depth_50": round(bid_depth_50, 2),
        "ask_depth_50": round(ask_depth_50, 2),
        "bid_depth_100": round(bid_depth_100, 2),
        "ask_depth_100": round(ask_depth_100, 2),
        "bid_depth_500": round(bid_depth_500, 2),
        "ask_depth_500": round(ask_depth_500, 2),
        "imbalance": round(imbalance, 4),
        "score": score,
    }


# ─── HEARTBEAT ───
def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = {}
    if HEARTBEAT_FILE.exists() and HEARTBEAT_FILE.stat().st_size > 0:
        try:
            with open(HEARTBEAT_FILE) as f:
                heartbeat = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    heartbeat["liquidity"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── MAIN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Liquidity Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    coins_out = {}
    tradeable_count = 0
    errors = 0

    for coin in TRADEABLE_COINS:
        try:
            result = analyze_coin(coin)
            coins_out[coin] = result
            if result["tradeable"]:
                tradeable_count += 1
            tag = "OK" if result["tradeable"] else "THIN"
            print(
                f"  {coin:6s} [{tag:4s}]  spread={result['spread_pct']:.4f}%  "
                f"depth50=${result['bid_depth_50']:,.0f}/{result['ask_depth_50']:,.0f}  "
                f"imbal={result['imbalance']:+.3f}  score={result['score']}"
            )
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError) as e:
            print(f"  {coin:6s} [ERR ]  {e}")
            coins_out[coin] = {"tradeable": False, "score": 0, "error": str(e)}
            errors += 1
        time.sleep(0.15)  # gentle rate limit

    # Write output
    output = {
        "timestamp": ts_iso,
        "coins": coins_out,
    }
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIQUIDITY_FILE, "w") as f:
        json.dump(output, f, indent=2)

    write_heartbeat()

    print(f"\n  Tradeable: {tradeable_count}/{len(TRADEABLE_COINS)} | Errors: {errors}")
    print(f"  Written to {LIQUIDITY_FILE}")
    print(f"{'='*60}\n")


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Liquidity Agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

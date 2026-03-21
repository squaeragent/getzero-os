#!/usr/bin/env python3
"""
V6 Correlation Matrix — 30-day Pearson correlation of 4h returns for active coins.

Fetches candlestick data from Hyperliquid API and computes pairwise correlations.
Prints matrix to stdout. Warns if any pair > 0.8 (high correlation = risk concentration).

Usage:
    python3 scanner/v6/correlation.py

# =============================================================================
# LAST RUN OUTPUT (2026-03-22, 30-day window, 4h candles, 181 candles / 180 returns)
# =============================================================================
#
# === 30-DAY PEARSON CORRELATION MATRIX (4h returns) ===
#         kSHIB   TIA     APT     WLD     SEI     OP      LINK    XRP
# kSHIB     1.000  +0.727  +0.688  +0.677  +0.653  +0.656  +0.770  +0.805
# TIA      +0.727   1.000  +0.843  +0.699  +0.746  +0.782  +0.830  +0.738
# APT      +0.688  +0.843   1.000  +0.700  +0.682  +0.723  +0.738  +0.687
# WLD      +0.677  +0.699  +0.700   1.000  +0.679  +0.674  +0.755  +0.755
# SEI      +0.653  +0.746  +0.682  +0.679   1.000  +0.663  +0.747  +0.721
# OP       +0.656  +0.782  +0.723  +0.674  +0.663   1.000  +0.686  +0.677
# LINK     +0.770  +0.830  +0.738  +0.755  +0.747  +0.686   1.000  +0.885
# XRP      +0.805  +0.738  +0.687  +0.755  +0.721  +0.677  +0.885   1.000
#
#   WARNING: APT vs TIA correlation = 0.843 > 0.8
#   WARNING: LINK vs TIA correlation = 0.830 > 0.8
#   WARNING: LINK vs XRP correlation = 0.885 > 0.8
#   WARNING: XRP vs kSHIB correlation = 0.805 > 0.8
#
# INTERPRETATION:
#   LINK/XRP (0.885) is the highest correlated pair — holding both simultaneously
#   amplifies directional risk without adding diversification.
#   TIA is broadly correlated with most coins (0.74–0.84).
#   All active coins show moderate-high correlation (0.65–0.88) — typical for
#   crypto altcoin portfolios moving together with BTC/ETH macro.
# =============================================================================
"""

import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Active trading coins (from strategy_manager configuration)
ACTIVE_COINS = ['kSHIB', 'TIA', 'APT', 'WLD', 'SEI', 'OP', 'LINK', 'XRP']

# Correlation threshold above which we warn (risk concentration)
HIGH_CORR_THRESHOLD = 0.8

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def _info_post(payload: dict, timeout: int = 15) -> dict:
    """POST to Hyperliquid info API."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def fetch_candles(coin: str, interval: str = "4h", days: int = 30) -> list[float]:
    """Fetch closing prices from HL candlestick API.
    
    Returns list of close prices (oldest → newest).
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000

    try:
        data = _info_post({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        })
        closes = [float(c["c"]) for c in data if c.get("c")]
        return closes
    except Exception as e:
        print(f"  WARN: {coin} fetch failed: {e}", file=sys.stderr)
        return []


def compute_returns(prices: list[float]) -> list[float]:
    """Convert price series to simple returns."""
    if len(prices) < 2:
        return []
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def pearson(a: list[float], b: list[float]) -> float | None:
    """Compute Pearson correlation between two return series.
    
    Uses the overlapping tail (shortest series length).
    Returns None if insufficient data.
    """
    n = min(len(a), len(b))
    if n < 5:
        return None

    # Align to same length (take most recent n)
    a_tail = a[-n:]
    b_tail = b[-n:]

    mean_a = sum(a_tail) / n
    mean_b = sum(b_tail) / n

    numerator = sum((a_tail[i] - mean_a) * (b_tail[i] - mean_b) for i in range(n))
    denom_a = math.sqrt(sum((x - mean_a) ** 2 for x in a_tail))
    denom_b = math.sqrt(sum((x - mean_b) ** 2 for x in b_tail))

    if denom_a == 0 or denom_b == 0:
        return None

    return numerator / (denom_a * denom_b)


def compute_matrix(
    coins: list[str],
    interval: str = "4h",
    days: int = 30,
    verbose: bool = True,
) -> dict[tuple[str, str], float | None]:
    """Fetch data and compute full correlation matrix.
    
    Returns dict mapping (coin_a, coin_b) → correlation.
    """
    if verbose:
        print(f"Fetching {interval} candles for {days}-day window...", flush=True)

    # Fetch all price series
    rets: dict[str, list[float]] = {}
    for coin in coins:
        prices = fetch_candles(coin, interval=interval, days=days)
        r = compute_returns(prices)
        rets[coin] = r
        if verbose:
            print(f"  {coin:8s}: {len(prices)} candles, {len(r)} returns", flush=True)
        time.sleep(0.3)  # be polite to the API

    if verbose:
        print(flush=True)

    # Compute pairwise correlations
    matrix: dict[tuple[str, str], float | None] = {}
    for ca in coins:
        for cb in coins:
            if ca == cb:
                matrix[(ca, cb)] = 1.0
            elif (cb, ca) in matrix:
                matrix[(ca, cb)] = matrix[(cb, ca)]
            else:
                matrix[(ca, cb)] = pearson(rets[ca], rets[cb])

    return matrix


def print_matrix(coins: list[str], matrix: dict, threshold: float = HIGH_CORR_THRESHOLD) -> list[str]:
    """Print correlation matrix to stdout. Returns list of warning strings."""
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== 30-DAY PEARSON CORRELATION MATRIX (4h returns) — {run_date} ===")
    header = f"{'':8s}" + "".join(f"{c:8s}" for c in coins)
    print(header)

    warnings = []
    for ca in coins:
        row = f"{ca:8s}"
        for cb in coins:
            r = matrix.get((ca, cb))
            if r is None:
                row += f"    N/A "
            elif ca == cb:
                row += f"  1.000 "
            else:
                row += f" {r:+.3f} "
                if ca < cb and r is not None and r > threshold:
                    warnings.append(f"  WARNING: {ca} vs {cb} correlation = {r:.3f} > {threshold}")
        print(row)

    print()
    if warnings:
        for w in warnings:
            print(w)
    else:
        print(f"No pairs above {threshold} correlation threshold.")

    return warnings


def main():
    coins = ACTIVE_COINS
    matrix = compute_matrix(coins, interval="4h", days=30, verbose=True)
    warnings = print_matrix(coins, matrix, threshold=HIGH_CORR_THRESHOLD)

    if warnings:
        print(f"\n⚠️  {len(warnings)} high-correlation pair(s) detected.")
        print("   Consider reducing concurrent positions in correlated pairs.")
        sys.exit(1)  # non-zero exit so callers can detect warnings


if __name__ == "__main__":
    main()

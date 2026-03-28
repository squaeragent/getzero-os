#!/usr/bin/env python3
"""
ZERO OS — ENVY Indicator History Fetcher
Fetches up to 168 hours (7 days) of historical indicator data at 15-min resolution.
Used for backtesting and analysis.

Usage:
  python3 fetch_history.py --coin BTC --indicators HURST_24H,RSI_24H --hours 168
  python3 fetch_history.py --coin ETH --all-chaos --hours 48
  python3 fetch_history.py --coin BTC --all --hours 24 --output history_btc.json
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://gate.getzero.dev/api/claw"

CHAOS_INDICATORS = ["HURST_24H", "HURST_48H", "DFA_24H", "DFA_48H", "LYAPUNOV_24H", "LYAPUNOV_48H"]
PREDICTOR_INDICATORS = ["DOJI_SIGNAL", "DOJI_DISTANCE", "DOJI_VELOCITY", "DOJI_SIGNAL_L", "DOJI_DISTANCE_L", "DOJI_VELOCITY_L"]
SOCIAL_INDICATORS = ["XONE_A_NET", "XONE_I_NET", "XONE_U_NET", "XONE_A_U_DIV", "XONE_AVG_NET_DELTA", "XONE_AVG_NET", "XONE_SPREAD"]
CORE_TECHNICALS = ["RSI_3H30M", "RSI_24H", "ROC_3H", "ROC_24H", "ADX_3H30M", "BB_POSITION_15M", "MACD_N_24H", "EMA_N_24H"]


def get_api_key():
    key = os.environ.get("ENVY_API_KEY")
    if key:
        return key
    env_file = Path.home() / ".config" / "getzero-os" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ENVY_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("ENVY_API_KEY not found")


def fetch_history(coin, indicators, hours, api_key):
    """Fetch history for a coin. Returns dict of indicator -> list of {t, v}."""
    ind_param = ",".join(indicators)
    url = f"{BASE_URL}/paid/indicators/history?coin={coin}&indicators={ind_param}&hours={hours}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    if not data.get("success"):
        print(f"  WARN: API returned success=false for {coin}")
        return {}

    return data.get("data", {})


def main():
    parser = argparse.ArgumentParser(description="Fetch ENVY indicator history")
    parser.add_argument("--coin", required=True, help="Coin symbol (e.g., BTC)")
    parser.add_argument("--indicators", help="Comma-separated indicator codes")
    parser.add_argument("--all-chaos", action="store_true", help="Fetch all chaos indicators")
    parser.add_argument("--all-predictor", action="store_true", help="Fetch all DOJI predictor indicators")
    parser.add_argument("--all-social", action="store_true", help="Fetch all social indicators")
    parser.add_argument("--all-core", action="store_true", help="Fetch core technical indicators")
    parser.add_argument("--all", action="store_true", help="Fetch ALL indicator groups")
    parser.add_argument("--hours", type=int, default=168, help="Hours of history (max 168)")
    parser.add_argument("--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    indicators = []
    if args.all:
        indicators = CHAOS_INDICATORS + PREDICTOR_INDICATORS + SOCIAL_INDICATORS + CORE_TECHNICALS
    else:
        if args.all_chaos:
            indicators.extend(CHAOS_INDICATORS)
        if args.all_predictor:
            indicators.extend(PREDICTOR_INDICATORS)
        if args.all_social:
            indicators.extend(SOCIAL_INDICATORS)
        if args.all_core:
            indicators.extend(CORE_TECHNICALS)
        if args.indicators:
            indicators.extend(args.indicators.split(","))

    if not indicators:
        print("Error: specify --indicators, --all-chaos, --all-core, --all, etc.")
        sys.exit(1)

    # API limit: 16 indicators per request
    api_key = get_api_key()
    all_data = {}

    for i in range(0, len(indicators), 16):
        batch = indicators[i:i+16]
        print(f"Fetching {len(batch)} indicators for {args.coin} ({args.hours}h)...", file=sys.stderr)
        data = fetch_history(args.coin, batch, args.hours, api_key)
        all_data.update(data)

    result = {
        "coin": args.coin,
        "hours": args.hours,
        "indicators": len(all_data),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": all_data,
    }

    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Saved to {args.output} ({len(all_data)} indicators, {sum(v.get('pointCount', 0) for v in all_data.values())} data points)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

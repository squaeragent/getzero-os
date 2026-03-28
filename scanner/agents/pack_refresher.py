#!/usr/bin/env python3
"""
ZERO OS — Signal Pack Refresher
Periodically pulls fresh signal packs from ENVY API to expand the signal library.
Each API call returns 10 random signals — over time this builds a comprehensive library.

Deduplicates by signal name. Filters out garbage (negative Sharpe, low trade count).
Runs every 2 hours — pulls 3 packs (common/rare/trump) × 40 coins = 120 API calls.

Inputs:
  ENVY API /paid/signals/pack endpoint
  scanner/data/signals_cache/*.json — existing cached signals

Outputs:
  scanner/data/signals_cache/*.json — updated with new signals
  scanner/bus/heartbeat.json — last-alive timestamp
"""

import json
import sys
import time
import urllib.request
import urllib.error
import yaml
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    load_json, save_json, make_logger, load_api_key, update_heartbeat,
    DATA_DIR,
)

log = make_logger("PACKS")

SIGNALS_DIR = DATA_DIR / "signals_cache"

BASE_URL = "https://gate.getzero.dev/api/claw"
PACK_TYPES = ["common", "rare", "trump"]
REFRESH_INTERVAL = 7200  # 2 hours

# Quality floor — don't cache signals worse than this
MIN_TRADE_COUNT = 3
MIN_SHARPE = -1.0  # Allow slightly negative for diversity, but not garbage

ALL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]


def fetch_pack(coin, pack_type, api_key):
    """Fetch a signal pack from the API. Returns list of signal dicts."""
    url = f"{BASE_URL}/paid/signals/pack/{pack_type}?coin={coin}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
    except Exception as e:
        log(f"  WARN: {coin} {pack_type} fetch failed: {e}")
        return []

    # Response is YAML
    try:
        data = yaml.safe_load(raw)
    except Exception:
        # Try JSON fallback
        try:
            data = json.loads(raw)
        except Exception:
            log(f"  WARN: {coin} {pack_type} parse failed")
            return []

    if not isinstance(data, dict):
        return []

    signals = data.get("signals", [])
    result = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        # Quality filter
        tc = sig.get("trade_count", 0)
        sharpe = sig.get("sharpe", 0)
        if tc < MIN_TRADE_COUNT:
            continue
        if sharpe < MIN_SHARPE:
            continue
        sig["_pack"] = pack_type
        sig["_coin"] = coin
        sig["_fetched"] = datetime.now(timezone.utc).isoformat()
        result.append(sig)
    return result


def load_existing(coin):
    """Load existing cached signals for a coin."""
    cache_file = SIGNALS_DIR / f"{coin}.json"
    return load_json(cache_file, [])


def merge_signals(existing, new_signals):
    """Merge new signals into existing, dedup by name."""
    by_name = {}
    for s in existing:
        by_name[s.get("name", "")] = s
    added = 0
    for s in new_signals:
        name = s.get("name", "")
        if name and name not in by_name:
            by_name[name] = s
            added += 1
    return list(by_name.values()), added


def save_signals(coin, signals):
    """Save signals to cache."""
    cache_file = SIGNALS_DIR / f"{coin}.json"
    save_json(cache_file, signals)


def refresh_all():
    """Pull fresh packs for all coins and merge into cache."""
    api_key = load_api_key()
    total_added = 0
    total_existing = 0

    for coin in ALL_COINS:
        existing = load_existing(coin)
        total_existing += len(existing)
        coin_new = []

        for pack_type in PACK_TYPES:
            signals = fetch_pack(coin, pack_type, api_key)
            coin_new.extend(signals)
            time.sleep(0.3)  # Rate limit courtesy

        if coin_new:
            merged, added = merge_signals(existing, coin_new)
            if added > 0:
                save_signals(coin, merged)
                log(f"  {coin}: +{added} new signals (total: {len(merged)})")
            total_added += added

        time.sleep(0.2)  # Between coins

    log(f"Refresh complete: +{total_added} new signals. Library: {total_existing + total_added} total")
    update_heartbeat("pack_refresher")
    return total_added


def main():
    log("=== ZERO OS Signal Pack Refresher ===")
    loop = "--loop" in sys.argv

    if loop:
        log(f"Looping every {REFRESH_INTERVAL}s")
        while True:
            try:
                refresh_all()
            except Exception as e:
                log(f"ERROR: {e}")
            time.sleep(REFRESH_INTERVAL)
    else:
        refresh_all()


if __name__ == "__main__":
    main()

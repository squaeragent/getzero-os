#!/usr/bin/env python3
"""
ZERO OS — ENVY Snapshot Cache Agent

Fetches the full ENVY indicator snapshot for all coins and persists it
as a JSONL file (one line per fetch cycle) for downstream delta analysis.

Output:  scanner/data/envy_history/YYYY-MM-DD.jsonl
Cycle:   900 s (15 min) — called by supervisor

Usage:
  python3 scanner/agents/envy_cache.py           # single run
  python3 scanner/agents/envy_cache.py --loop    # continuous 900s loop
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from scanner.utils import make_logger, load_api_key, append_jsonl, DATA_DIR

log = make_logger("ENVY_CACHE")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HISTORY_DIR = DATA_DIR / "envy_history"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENVY_BASE_URL     = "https://gate.getzero.dev/api/claw"
SNAPSHOT_ENDPOINT = "/paid/indicators/snapshot"
COINS_PER_REQUEST = 10
MAX_HISTORY_DAYS  = 30
CYCLE_SEC         = 900

ALL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

FAST_INDICATORS = [
    "CLOSE_PRICE_15M", "RSI_3H30M", "EMA_CROSS_15M_N", "MACD_CROSS_15M_N",
    "BB_POSITION_15M", "CMO_3H30M", "ADX_3H30M", "MOMENTUM_2H30M_N",
    "EMA_3H_N", "CLOUD_POSITION_15M",
]

SLOW_AND_CHAOS_INDICATORS = [
    "RSI_24H", "EMA_N_24H", "MACD_N_24H", "ROC_24H",
    "HURST_24H", "HURST_48H", "DFA_24H", "DFA_48H",
    "LYAPUNOV_24H", "LYAPUNOV_48H", "BB_POS_24H",
    "MOMENTUM_N_24H", "EMA_N_48H",
]

ALL_INDICATORS = FAST_INDICATORS + SLOW_AND_CHAOS_INDICATORS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def envy_get(path: str, params: dict, api_key: str) -> dict:
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def parse_snapshot(snapshot: dict) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for coin, ind_list in snapshot.items():
        if not isinstance(ind_list, list):
            continue
        values: dict[str, float] = {}
        for ind in ind_list:
            try:
                values[ind["indicatorCode"]] = float(ind["value"])
            except (KeyError, TypeError, ValueError):
                pass
        result[coin] = values
    return result


def fetch_all_indicators(coins: list[str], indicators: list[str], api_key: str) -> dict[str, dict[str, float]]:
    """Fetch indicators for all coins in batches."""
    all_data: dict[str, dict[str, float]] = {}
    ind_param = ",".join(indicators)

    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i: i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = envy_get(
                SNAPSHOT_ENDPOINT,
                {"coins": coins_param, "indicators": ind_param},
                api_key,
            )
            parsed = parse_snapshot(resp.get("snapshot", {}))
            for coin, vals in parsed.items():
                all_data.setdefault(coin, {}).update(vals)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            log(f"WARN batch {i//COINS_PER_REQUEST + 1} failed ({e}), retrying coin by coin")
            for coin in batch:
                try:
                    resp = envy_get(
                        SNAPSHOT_ENDPOINT,
                        {"coins": coin, "indicators": ind_param},
                        api_key,
                    )
                    parsed = parse_snapshot(resp.get("snapshot", {}))
                    for c, vals in parsed.items():
                        all_data.setdefault(c, {}).update(vals)
                except Exception as ex:
                    log(f"WARN  {coin} failed: {ex}")
                time.sleep(0.1)

        if i + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)

    return all_data


def fetch_snapshot(api_key: str) -> dict[str, dict[str, float]]:
    """Fetch the full indicator snapshot (fast + slow) for all coins."""
    fast = fetch_all_indicators(ALL_COINS, FAST_INDICATORS, api_key)
    slow = fetch_all_indicators(ALL_COINS, SLOW_AND_CHAOS_INDICATORS, api_key)

    merged: dict[str, dict[str, float]] = {}
    all_seen = set(list(fast.keys()) + list(slow.keys()))
    for coin in all_seen:
        merged[coin] = {}
        merged[coin].update(fast.get(coin, {}))
        merged[coin].update(slow.get(coin, {}))
    return merged


def purge_old_files() -> None:
    """Delete JSONL history files older than MAX_HISTORY_DAYS."""
    cutoff = datetime.now(timezone.utc).timestamp() - MAX_HISTORY_DAYS * 86400
    for f in sorted(HISTORY_DIR.glob("*.jsonl")):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            log(f"Purged old file: {f.name}")


def save_snapshot(snapshot: dict[str, dict[str, float]]) -> None:
    """Append the snapshot as one JSONL line to today's file."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    filepath = HISTORY_DIR / f"{date_str}.jsonl"

    record = {
        "t": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "coins": snapshot,
    }
    append_jsonl(filepath, record)


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_once() -> None:
    api_key = load_api_key()
    snapshot = fetch_snapshot(api_key)

    if not snapshot:
        log("ERROR: empty snapshot, nothing cached")
        return

    save_snapshot(snapshot)
    purge_old_files()

    n_coins = len(snapshot)
    n_indicators = max((len(v) for v in snapshot.values()), default=0)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    log(f"Cached {n_coins} coins, {n_indicators} indicators at {ts}")


def main() -> None:
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log("Starting in loop mode (900s cycle)")
        while True:
            try:
                run_once()
            except Exception as e:
                log(f"ERROR: {e}")
            time.sleep(CYCLE_SEC)
    else:
        run_once()


if __name__ == "__main__":
    main()

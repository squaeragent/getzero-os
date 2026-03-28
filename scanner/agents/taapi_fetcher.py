#!/usr/bin/env python3
"""
ZERO OS — TAAPI Snapshot Fetcher
Fetches TAAPI indicators for core coins and writes scanner/bus/taapi_snapshot.json.

Designed to be called by the supervisor every 15 minutes.
Supports --loop mode (900s cycle) for integration with run_agents.py supervisor.

Output format:
  {
    "timestamp": "ISO",
    "coins": {
      "BTC": {"RSI_24H": 45.2, "RSI_6H": 52.1, "EMA_N_24H": 0.98, ...},
      ...
    }
  }

Usage:
  python3 scanner/agents/taapi_fetcher.py           # single run
  python3 scanner/agents/taapi_fetcher.py --loop    # continuous 900s cycle
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CYCLE_SECONDS = 900  # 15 minutes

# ─── PATH SETUP ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
SNAPSHOT_FILE = BUS_DIR / "taapi_snapshot.json"

# Add project root to path so scanner.* imports work
sys.path.insert(0, str(SCANNER_DIR.parent))

CORE_COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]

# Indicators we care about for cross-source comparison
TARGET_INDICATORS = [
    "RSI_24H", "RSI_6H",
    "EMA_N_24H",
    "MACD_N_24H",
    "ADX_3H30M",
    "CMO_3H30M",
    "BB_POS_24H",
    "ROC_24H",
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [TAAPI_FETCHER] {msg}")


def extract_indicators_from_observations(observations) -> dict[str, dict[str, float]]:
    """
    Convert TaapiPlugin observations list into a coin → indicator dict.
    Each observation has dimension like "taapi.RSI_24H", coin, value, metadata.normalized.
    We store the normalized value where available, fallback to raw.
    """
    result: dict[str, dict[str, float]] = {}
    for obs in observations:
        coin = obs.coin
        dim = obs.dimension  # "taapi.RSI_24H"
        if not dim.startswith("taapi."):
            continue
        indicator = dim[len("taapi."):]  # strip "taapi." prefix

        # Only store indicators we care about
        if indicator not in TARGET_INDICATORS:
            continue

        # Use normalized value for cross-source comparison; fall back to raw
        norm = obs.metadata.get("normalized")
        value = norm if norm is not None else obs.value

        if coin not in result:
            result[coin] = {}
        result[coin][indicator] = round(float(value), 8)

    return result


def main():
    log(f"Starting TAAPI snapshot fetch for {len(CORE_COINS)} coins")
    start = time.time()

    try:
        from scanner.senses.taapi_plugin import TaapiPlugin
    except ImportError as e:
        log(f"ERROR: Cannot import TaapiPlugin: {e}")
        log("Make sure you're running from the getzero-os root directory")
        sys.exit(1)

    try:
        plugin = TaapiPlugin()
        # Verify API key is available (raises RuntimeError if not)
        plugin._get_api_key()
    except RuntimeError as e:
        log(f"ERROR: {e}")
        log("Set TAAPI_API_KEY in environment or ~/getzero-os/.env")
        sys.exit(1)

    log(f"Fetching indicators: {', '.join(TARGET_INDICATORS)}")

    observations = plugin.fetch(CORE_COINS)

    if not observations:
        log("WARNING: No observations returned from TAAPI. API limit or key issue?")
        # Write empty snapshot so adversary can detect "no data" cleanly
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coins": {},
            "error": "No observations returned",
        }
        BUS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
        log(f"Wrote empty snapshot to {SNAPSHOT_FILE}")
        return

    coin_data = extract_indicators_from_observations(observations)

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coins": coin_data,
        "api_calls": plugin._call_count,
        "coins_fetched": len(coin_data),
    }

    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

    elapsed = time.time() - start
    log(f"Snapshot written to {SNAPSHOT_FILE} in {elapsed:.1f}s")
    log(f"Coins: {len(coin_data)} | API calls: {plugin._call_count}")

    # Summary
    for coin in CORE_COINS:
        inds = coin_data.get(coin, {})
        if inds:
            sample = ", ".join(f"{k}={v:.4f}" for k, v in list(inds.items())[:3])
            log(f"  {coin}: {len(inds)} indicators — {sample}")
        else:
            log(f"  {coin}: NO DATA")


def write_heartbeat():
    """Write heartbeat so supervisor knows we're alive."""
    hb_file = BUS_DIR / "heartbeat.json"
    try:
        hb = {}
        if hb_file.exists():
            try:
                with open(hb_file) as f:
                    hb = json.load(f)
            except Exception:
                pass
        hb["taapi_fetcher"] = datetime.now(timezone.utc).isoformat()
        BUS_DIR.mkdir(parents=True, exist_ok=True)
        with open(hb_file, "w") as f:
            json.dump(hb, f, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                main()
                write_heartbeat()
            except Exception as e:
                log(f"Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        main()
        write_heartbeat()

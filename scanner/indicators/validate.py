#!/usr/bin/env /opt/homebrew/bin/python3
"""
ZERO OS — Indicator Validation
================================
Compares our self-computed indicators against the Envy API (current source)
for HURST_24H, DFA_24H, and LYAPUNOV_24H.

Outputs:
    scanner/indicators/validation_log.jsonl   — one entry per coin/indicator
    (also prints table to stdout)

Usage:
    python3 scanner/indicators/validate.py
    python3 scanner/indicators/validate.py --coins BTC ETH SOL
"""

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── Path setup ─────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent
SCANNER_DIR   = SCRIPT_DIR.parent
WORLD_STATE   = SCANNER_DIR / "bus" / "world_state.json"
LOG_FILE      = SCRIPT_DIR / "validation_log.jsonl"

sys.path.insert(0, str(SCANNER_DIR.parent))
from scanner.indicators.engine import IndicatorEngine, fetch_hl_candles

# ─── Config ─────────────────────────────────────────────────────────────────
CANDLES_PER_COIN = 300
CHAOS_INDICATORS = ["HURST_24H", "DFA_24H", "LYAPUNOV_24H"]

DEFAULT_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "AVAX", "DOGE", "LINK", "ARB", "SUI",
]


# ─── Logging ────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


# ─── Load API key ────────────────────────────────────────────────────────────

def _load_api_key() -> str | None:
    key = os.environ.get("ENVY_API_KEY")
    if key:
        return key
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("ENVY_API_KEY="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


# ─── Fetch Envy indicators ───────────────────────────────────────────────────

def _fetch_envy_indicators(coins: list, api_key: str) -> dict:
    """Fetch chaos indicators from Envy for a list of coins."""
    BASE  = "https://gate.getzero.dev/api/claw"
    inds  = ",".join(CHAOS_INDICATORS)
    result = {}
    for i in range(0, len(coins), 10):
        batch = coins[i:i + 10]
        coins_param = ",".join(batch)
        url = f"{BASE}/paid/indicators/snapshot?coins={coins_param}&indicators={inds}"
        req = urllib.request.Request(url, headers={"X-API-Key": api_key})
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            snapshot = resp.get("snapshot", {})
            for coin, ind_list in snapshot.items():
                if not isinstance(ind_list, list):
                    continue
                result[coin] = {
                    ind["indicatorCode"]: ind["value"]
                    for ind in ind_list
                }
        except Exception as e:
            _log(f"  [warn] Envy fetch failed for {batch}: {e}")
        time.sleep(0.3)
    return result


# ─── Load world_state indicators ─────────────────────────────────────────────

def _load_world_state_indicators(coins: list) -> dict:
    """Load current indicator values from world_state.json if available."""
    if not WORLD_STATE.exists():
        return {}
    try:
        with open(WORLD_STATE) as f:
            ws = json.load(f)
        result = {}
        for coin in coins:
            coin_data = ws.get("coins", {}).get(coin, {})
            inds = coin_data.get("indicators", {})
            filtered = {k: inds[k] for k in CHAOS_INDICATORS if k in inds}
            if filtered:
                result[coin] = filtered
        return result
    except Exception as e:
        _log(f"  [warn] world_state load failed: {e}")
        return {}


# ─── Main validation ─────────────────────────────────────────────────────────

def validate_against_current(coins: list | None = None) -> list:
    """
    Fetch current chaos indicators from source AND compute our own.
    Log deltas for each coin/indicator.

    Returns list of comparison records.
    """
    if coins is None:
        coins = DEFAULT_COINS

    _log(f"Starting validation for {len(coins)} coins: {', '.join(coins)}")

    # Try Envy API first, fall back to world_state.json
    api_key  = _load_api_key()
    theirs   = {}

    if api_key:
        _log("  Loading Envy indicators...")
        theirs = _fetch_envy_indicators(coins, api_key)
        _log(f"  Envy returned data for {len(theirs)} coins")
    else:
        _log("  No ENVY_API_KEY — loading from world_state.json")
        theirs = _load_world_state_indicators(coins)
        _log(f"  world_state returned data for {len(theirs)} coins")

    # Compute our own indicators for each coin
    ts_iso   = datetime.now(timezone.utc).isoformat()
    records  = []
    col_w    = 14

    print(f"\n{'─'*75}")
    print(f"  {'COIN':<6} {'INDICATOR':<16} {'THEIRS':>12} {'OURS':>12} {'DELTA':>10} {'DELTA%':>8}")
    print(f"{'─'*75}")

    for coin in coins:
        _log(f"  Fetching {CANDLES_PER_COIN} candles for {coin}...")
        try:
            candles = fetch_hl_candles(coin, "1h", CANDLES_PER_COIN)
            if len(candles) < 50:
                _log(f"  [warn] {coin}: only {len(candles)} candles, skipping")
                continue
        except Exception as e:
            _log(f"  [warn] {coin}: candle fetch failed: {e}")
            continue

        engine = IndicatorEngine(candles)
        ours_map = {
            "HURST_24H":    engine.hurst(window=200),
            "DFA_24H":      engine.dfa(window=200),
            "LYAPUNOV_24H": engine.lyapunov(window=200),
        }

        their_map = theirs.get(coin, {})

        for ind in CHAOS_INDICATORS:
            ours   = ours_map.get(ind)
            theirs_val = their_map.get(ind)

            # Compute delta
            if (ours is not None and not math.isnan(ours) and
                    theirs_val is not None and not math.isnan(float(theirs_val))):
                theirs_f = float(theirs_val)
                delta    = ours - theirs_f
                delta_pct = (delta / theirs_f * 100) if theirs_f != 0 else float("nan")
            else:
                theirs_f  = None
                delta     = None
                delta_pct = None

            rec = {
                "timestamp": ts_iso,
                "coin":      coin,
                "indicator": ind,
                "theirs":    theirs_f,
                "ours":      ours,
                "delta":     delta,
                "delta_pct": delta_pct,
                "n_candles": len(candles),
            }
            records.append(rec)

            # Print row
            def fmt(v):
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return "N/A"
                return f"{v:.4f}"

            print(f"  {coin:<6} {ind:<16} {fmt(theirs_f):>12} {fmt(ours):>12} "
                  f"{fmt(delta):>10} {(fmt(delta_pct)+'%') if delta_pct is not None else 'N/A':>8}")

        time.sleep(0.5)

    print(f"{'─'*75}\n")

    # Write to log
    with open(LOG_FILE, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    _log(f"Validation complete. {len(records)} records written to {LOG_FILE}")
    return records


# ─── Entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    coins = None
    if "--coins" in sys.argv:
        idx   = sys.argv.index("--coins")
        coins = sys.argv[idx + 1:]

    validate_against_current(coins=coins)

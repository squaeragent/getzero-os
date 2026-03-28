#!/usr/bin/env python3
"""
ZERO OS — Agent 1: Regime Agent
Detects market regime and regime transitions across all 40 coins
using Envy chaos indicators (DFA, Hurst, Lyapunov).

Outputs:
  scanner/bus/regimes.json       — current regime per coin
  scanner/bus/regime_history.jsonl — append log of all regime snapshots
  scanner/bus/heartbeat.json     — last-alive timestamp

Usage:
  python3 scanner/agents/regime_agent.py           # single run
  python3 scanner/agents/regime_agent.py --loop    # continuous 5-min cycle
"""

import json
import os
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
REGIMES_FILE = BUS_DIR / "regimes.json"
HISTORY_FILE = BUS_DIR / "regime_history.jsonl"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
CYCLE_SECONDS = 300  # 5 minutes

CHAOS_INDICATORS = [
    "DFA_24H", "DFA_48H",
    "HURST_24H", "HURST_48H",
    "LYAPUNOV_24H", "LYAPUNOV_48H",
]

ALL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

COINS_PER_REQUEST = 10  # API limit

# ─── REGIME THRESHOLDS ───
HURST_HIGH = 0.55
HURST_LOW = 0.45
DFA_HIGH = 0.55
DFA_LOW = 0.45
LYAPUNOV_CHAOTIC = 1.90  # crypto Lyapunov typically 1.4-2.0; >1.9 = truly chaotic
INDICATOR_NEUTRAL_LOW = 0.47
INDICATOR_NEUTRAL_HIGH = 0.53
TREND_THRESHOLD = 0.03  # min diff between 24H and 48H to call rising/falling


# ─── API ───
def load_api_key():
    env_path = os.path.expanduser("~/getzero-os/.env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("ENVY_API_KEY="):
                val = line.split("=", 1)[1]
                return val.strip().strip('"').strip("'")
    raise RuntimeError("ENVY_API_KEY not found in ~/getzero-os/.env")


def api_get(path, params, api_key):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_snapshot(snapshot):
    """Extract indicator values from API snapshot response."""
    result = {}
    for coin, ind_list in snapshot.items():
        if not isinstance(ind_list, list):
            continue
        values = {}
        for ind in ind_list:
            values[ind["indicatorCode"]] = ind["value"]
        result[coin] = values
    return result


def fetch_chaos_indicators(coins, api_key):
    """Fetch chaos indicators for a list of coins. Batches by 10.
    On batch failure, retries coins individually to handle unsupported tickers."""
    all_data = {}
    indicators_param = ",".join(CHAOS_INDICATORS)

    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i:i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = api_get(
                "/paid/indicators/snapshot",
                {"coins": coins_param, "indicators": indicators_param},
                api_key,
            )
            all_data.update(_parse_snapshot(resp.get("snapshot", {})))
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            # Batch failed — retry each coin individually
            print(f"  [warn] batch {batch[0]}-{batch[-1]} failed, retrying individually")
            for coin in batch:
                try:
                    resp = api_get(
                        "/paid/indicators/snapshot",
                        {"coins": coin, "indicators": indicators_param},
                        api_key,
                    )
                    all_data.update(_parse_snapshot(resp.get("snapshot", {})))
                except Exception:
                    pass
                time.sleep(0.1)

        if i + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)

    return all_data


# ─── REGIME CLASSIFICATION ───
def classify_regime(hurst, dfa, lyapunov):
    """Classify into: trending, reverting, chaotic, shift, stable."""
    if lyapunov is not None and lyapunov > LYAPUNOV_CHAOTIC:
        return "chaotic"
    if hurst is not None and dfa is not None:
        if hurst > HURST_HIGH and dfa > DFA_HIGH:
            return "trending"
        if hurst < HURST_LOW and dfa < DFA_LOW:
            return "reverting"
        if (hurst > HURST_HIGH and dfa < DFA_LOW) or (hurst < HURST_LOW and dfa > DFA_HIGH):
            return "shift"
    # Check stable: all near 0.50
    vals = [v for v in (hurst, dfa, lyapunov) if v is not None]
    if vals and all(INDICATOR_NEUTRAL_LOW <= v <= INDICATOR_NEUTRAL_HIGH for v in vals):
        return "stable"
    # Default: stable if we can't determine
    return "stable"


def compute_confidence(hurst, dfa, lyapunov, regime):
    """How strongly the indicators support the classified regime. 0-1."""
    if regime == "trending":
        h_dist = max(0, (hurst - HURST_HIGH)) / 0.45 if hurst else 0
        d_dist = max(0, (dfa - DFA_HIGH)) / 0.45 if dfa else 0
        return min(1.0, 0.5 + (h_dist + d_dist) / 2)
    if regime == "reverting":
        h_dist = max(0, (HURST_LOW - hurst)) / 0.45 if hurst else 0
        d_dist = max(0, (DFA_LOW - dfa)) / 0.45 if dfa else 0
        return min(1.0, 0.5 + (h_dist + d_dist) / 2)
    if regime == "chaotic":
        l_dist = max(0, (lyapunov - LYAPUNOV_CHAOTIC)) / 0.15 if lyapunov else 0
        return min(1.0, 0.6 + l_dist * 0.4)
    if regime == "shift":
        if hurst is not None and dfa is not None:
            disagreement = abs(hurst - dfa)
            return min(1.0, 0.4 + disagreement)
        return 0.5
    # stable
    return 0.5


def compute_trend(val_24h, val_48h):
    """Compare 24H vs 48H to determine direction: rising, falling, flat."""
    if val_24h is None or val_48h is None:
        return "flat"
    diff = val_24h - val_48h
    if diff > TREND_THRESHOLD:
        return "rising"
    if diff < -TREND_THRESHOLD:
        return "falling"
    return "flat"


def detect_transition(current, previous):
    """Detect regime transition between current and previous state."""
    if previous is None:
        return False
    if current["regime"] != previous.get("regime"):
        return True
    # Also detect forming transitions: Hurst crossing 0.50
    prev_h = previous.get("hurst_24h")
    curr_h = current.get("hurst_24h")
    if prev_h is not None and curr_h is not None:
        if prev_h < 0.50 and curr_h > 0.50:
            return True
        if prev_h > 0.50 and curr_h < 0.50:
            return True
    return False


# ─── STATE ───
def load_previous_regimes():
    """Load the most recent regime state from bus."""
    if REGIMES_FILE.exists() and REGIMES_FILE.stat().st_size > 0:
        try:
            with open(REGIMES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def write_regimes(state):
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGIMES_FILE, "w") as f:
        json.dump(state, f, indent=2)


def append_history(state):
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(state) + "\n")


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
    heartbeat["regime"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── MAIN ───
def run_cycle(api_key):
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Regime Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Load previous state for transition detection
    prev_state = load_previous_regimes()
    prev_coins = prev_state.get("coins", {})
    prev_ts = prev_state.get("timestamp")

    # Fetch indicators
    print(f"  Fetching chaos indicators for {len(ALL_COINS)} coins...")
    raw = fetch_chaos_indicators(ALL_COINS, api_key)
    print(f"  Got data for {len(raw)} coins")

    if not raw:
        print("  [error] No indicator data returned, skipping cycle")
        write_heartbeat()
        return

    # Classify each coin
    coins_out = {}
    transitions = 0

    for coin in ALL_COINS:
        vals = raw.get(coin)
        if not vals:
            continue

        hurst_24h = vals.get("HURST_24H")
        hurst_48h = vals.get("HURST_48H")
        dfa_24h = vals.get("DFA_24H")
        dfa_48h = vals.get("DFA_48H")
        lyapunov_24h = vals.get("LYAPUNOV_24H")
        lyapunov_48h = vals.get("LYAPUNOV_48H")

        regime = classify_regime(hurst_24h, dfa_24h, lyapunov_24h)
        confidence = compute_confidence(hurst_24h, dfa_24h, lyapunov_24h, regime)
        hurst_trend = compute_trend(hurst_24h, hurst_48h)
        dfa_trend = compute_trend(dfa_24h, dfa_48h)

        prev_coin = prev_coins.get(coin)
        prev_regime = prev_coin.get("regime") if prev_coin else None

        entry = {
            "regime": regime,
            "confidence": round(confidence, 3),
            "prev_regime": prev_regime,
            "transition": False,
            "transition_age_min": 0,
            "hurst_24h": hurst_24h,
            "hurst_48h": hurst_48h,
            "dfa_24h": dfa_24h,
            "dfa_48h": dfa_48h,
            "lyapunov_24h": lyapunov_24h,
            "lyapunov_48h": lyapunov_48h,
            "hurst_trend": hurst_trend,
            "dfa_trend": dfa_trend,
        }

        # Transition detection
        is_transition = detect_transition(entry, prev_coin)
        if is_transition:
            transitions += 1
            entry["transition"] = True
            entry["transition_age_min"] = 0
        elif prev_coin and prev_coin.get("transition"):
            # Ongoing transition: increment age
            prev_age = prev_coin.get("transition_age_min", 0)
            if prev_ts:
                try:
                    prev_dt = datetime.fromisoformat(prev_ts)
                    elapsed = (ts - prev_dt).total_seconds() / 60
                    entry["transition"] = True
                    entry["transition_age_min"] = round(prev_age + elapsed)
                except (ValueError, TypeError):
                    entry["transition"] = True
                    entry["transition_age_min"] = prev_age + 5

        coins_out[coin] = entry

    # Build output
    state = {"timestamp": ts_iso, "coins": coins_out}

    # Write outputs
    write_regimes(state)
    append_history(state)
    write_heartbeat()

    # Summary
    regime_counts = {}
    for c in coins_out.values():
        r = c["regime"]
        regime_counts[r] = regime_counts.get(r, 0) + 1

    print(f"\n  Regimes: {dict(sorted(regime_counts.items()))}")
    print(f"  Transitions: {transitions}")

    if transitions > 0:
        for coin, data in coins_out.items():
            if data["transition"] and data["transition_age_min"] == 0:
                print(f"    >> {coin}: {data['prev_regime']} -> {data['regime']} (confidence {data['confidence']:.2f})")

    print(f"  Written to {REGIMES_FILE}")
    print(f"{'='*60}\n")


def main():
    api_key = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Regime Agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle(api_key)
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(api_key)


if __name__ == "__main__":
    main()

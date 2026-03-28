#!/usr/bin/env python3
"""
ZERO OS — Agent 7: Cross-Timeframe Agent
Detects divergences between fast (15M) and slow (24H/48H) timeframes
for entry confirmation.

Inputs:
  Envy API snapshots — 15M and 24H/48H indicators per coin

Outputs:
  scanner/bus/timeframe_signals.json  — per-coin timeframe analysis
  scanner/bus/heartbeat.json          — last-alive timestamp

Usage:
  python3 scanner/agents/cross_timeframe_agent.py           # single run
  python3 scanner/agents/cross_timeframe_agent.py --loop    # continuous 5-min cycle
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
TIMEFRAME_FILE = BUS_DIR / "timeframe_signals.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
CYCLE_SECONDS = 300  # 5 minutes
COINS_PER_REQUEST = 10
INDICATORS_PER_REQUEST = 10  # max 16 per snapshot, we use 10 per batch

TRADEABLE_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

# Indicator batches
FAST_INDICATORS = [
    "CLOSE_PRICE_15M", "RSI_3H30M", "EMA_CROSS_15M_N", "MACD_CROSS_15M_N",
    "BB_POSITION_15M", "CMO_3H30M", "ADX_3H30M", "MOMENTUM_2H30M_N",
    "EMA_3H_N", "CLOUD_POSITION_15M",
]

SLOW_INDICATORS = [
    "RSI_24H", "EMA_N_24H", "MACD_N_24H", "ROC_24H", "HURST_24H",
    "DFA_24H", "LYAPUNOV_24H", "BB_POS_24H", "MOMENTUM_N_24H", "EMA_N_48H",
]


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


def fetch_indicators_batch(coins, indicators, api_key):
    """Fetch a batch of indicators for a list of coins."""
    all_data = {}
    ind_param = ",".join(indicators)
    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i:i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = api_get(
                "/paid/indicators/snapshot",
                {"coins": coins_param, "indicators": ind_param},
                api_key,
            )
            parsed = _parse_snapshot(resp.get("snapshot", {}))
            for coin, vals in parsed.items():
                all_data.setdefault(coin, {}).update(vals)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            print(f"  [warn] batch failed ({batch[0]}-{batch[-1]}): {e}")
            for coin in batch:
                try:
                    resp = api_get(
                        "/paid/indicators/snapshot",
                        {"coins": coin, "indicators": ind_param},
                        api_key,
                    )
                    parsed = _parse_snapshot(resp.get("snapshot", {}))
                    for c, vals in parsed.items():
                        all_data.setdefault(c, {}).update(vals)
                except Exception:
                    pass
                time.sleep(0.1)
        time.sleep(0.3)
    return all_data


# ─── TIMEFRAME CLASSIFICATION ───
def classify_fast(vals):
    """Classify fast timeframe as bullish/bearish/neutral.
    Returns (bias, sub_scores dict)."""
    rsi = vals.get("RSI_3H30M")
    ema_cross = vals.get("EMA_CROSS_15M_N")
    cmo = vals.get("CMO_3H30M")

    if rsi is None or ema_cross is None or cmo is None:
        return "neutral", {}

    bullish_count = 0
    bearish_count = 0
    sub = {}

    # RSI
    if rsi > 50:
        bullish_count += 1
        sub["rsi_fast"] = "bullish"
    elif rsi < 50:
        bearish_count += 1
        sub["rsi_fast"] = "bearish"
    else:
        sub["rsi_fast"] = "neutral"

    # EMA cross
    if ema_cross > 0:
        bullish_count += 1
        sub["ema_cross_fast"] = "bullish"
    elif ema_cross < 0:
        bearish_count += 1
        sub["ema_cross_fast"] = "bearish"
    else:
        sub["ema_cross_fast"] = "neutral"

    # CMO
    if cmo > 0:
        bullish_count += 1
        sub["cmo_fast"] = "bullish"
    elif cmo < 0:
        bearish_count += 1
        sub["cmo_fast"] = "bearish"
    else:
        sub["cmo_fast"] = "neutral"

    # Additional sub-indicators for strength calculation
    macd_cross = vals.get("MACD_CROSS_15M_N")
    if macd_cross is not None:
        sub["macd_cross_fast"] = "bullish" if macd_cross > 0 else ("bearish" if macd_cross < 0 else "neutral")
        if macd_cross > 0:
            bullish_count += 1
        elif macd_cross < 0:
            bearish_count += 1

    bb_pos = vals.get("BB_POSITION_15M")
    if bb_pos is not None:
        sub["bb_fast"] = "bullish" if bb_pos > 0.5 else ("bearish" if bb_pos < 0.5 else "neutral")

    momentum = vals.get("MOMENTUM_2H30M_N")
    if momentum is not None:
        sub["momentum_fast"] = "bullish" if momentum > 0 else ("bearish" if momentum < 0 else "neutral")
        if momentum > 0:
            bullish_count += 1
        elif momentum < 0:
            bearish_count += 1

    # Core classification: majority wins if leading by 2+
    total = bullish_count + bearish_count
    if bullish_count >= 2 and bullish_count > bearish_count:
        return "bullish", sub
    if bearish_count >= 2 and bearish_count > bullish_count:
        return "bearish", sub
    return "neutral", sub


def classify_slow(vals):
    """Classify slow timeframe as bullish/bearish/neutral.
    Returns (bias, sub_scores dict)."""
    rsi = vals.get("RSI_24H")
    ema = vals.get("EMA_N_24H")
    macd = vals.get("MACD_N_24H")

    if rsi is None or ema is None or macd is None:
        return "neutral", {}

    bullish_count = 0
    bearish_count = 0
    sub = {}

    # RSI
    if rsi > 50:
        bullish_count += 1
        sub["rsi_slow"] = "bullish"
    elif rsi < 50:
        bearish_count += 1
        sub["rsi_slow"] = "bearish"
    else:
        sub["rsi_slow"] = "neutral"

    # EMA
    if ema > 1.0:
        bullish_count += 1
        sub["ema_slow"] = "bullish"
    elif ema < 1.0:
        bearish_count += 1
        sub["ema_slow"] = "bearish"
    else:
        sub["ema_slow"] = "neutral"

    # MACD
    if macd > 0:
        bullish_count += 1
        sub["macd_slow"] = "bullish"
    elif macd < 0:
        bearish_count += 1
        sub["macd_slow"] = "bearish"
    else:
        sub["macd_slow"] = "neutral"

    # Additional sub-indicators
    roc = vals.get("ROC_24H")
    if roc is not None:
        sub["roc_slow"] = "bullish" if roc > 0 else ("bearish" if roc < 0 else "neutral")
        if roc > 0:
            bullish_count += 1
        elif roc < 0:
            bearish_count += 1

    hurst = vals.get("HURST_24H")
    if hurst is not None:
        sub["hurst"] = round(hurst, 4)

    momentum = vals.get("MOMENTUM_N_24H")
    if momentum is not None:
        sub["momentum_slow"] = "bullish" if momentum > 0 else ("bearish" if momentum < 0 else "neutral")
        if momentum > 0:
            bullish_count += 1
        elif momentum < 0:
            bearish_count += 1

    ema_48 = vals.get("EMA_N_48H")
    if ema_48 is not None:
        sub["ema_48h"] = "bullish" if ema_48 > 1.0 else ("bearish" if ema_48 < 1.0 else "neutral")

    # Core classification: majority wins if leading by 2+
    if bullish_count >= 2 and bullish_count > bearish_count:
        return "bullish", sub
    if bearish_count >= 2 and bearish_count > bullish_count:
        return "bearish", sub
    return "neutral", sub


def detect_pattern(fast_bias, slow_bias, vals):
    """Detect cross-timeframe pattern."""
    adx = vals.get("ADX_3H30M")

    if slow_bias == "bullish" and fast_bias == "bullish":
        return "CONFIRMATION_LONG"
    if slow_bias == "bearish" and fast_bias == "bearish":
        return "CONFIRMATION_SHORT"
    if slow_bias == "bearish" and fast_bias == "bullish":
        if adx is not None and adx < 20:
            return "TRAP_LONG"
        return "DIVERGENCE_BULL"
    if slow_bias == "bullish" and fast_bias == "bearish":
        if adx is not None and adx < 20:
            return "TRAP_SHORT"
        return "DIVERGENCE_BEAR"

    return "NEUTRAL"


def compute_strength(fast_subs, slow_subs):
    """Compute agreement strength 0-100 based on how many sub-indicators agree."""
    agree_count = 0
    total_count = 0

    # Count fast sub-indicators that are bullish or bearish
    fast_bull = 0
    fast_bear = 0
    for k, v in fast_subs.items():
        if isinstance(v, str):
            total_count += 1
            if v == "bullish":
                fast_bull += 1
            elif v == "bearish":
                fast_bear += 1

    # Count slow sub-indicators
    slow_bull = 0
    slow_bear = 0
    for k, v in slow_subs.items():
        if isinstance(v, str):
            total_count += 1
            if v == "bullish":
                slow_bull += 1
            elif v == "bearish":
                slow_bear += 1

    if total_count == 0:
        return 0

    # Agreement: all pointing same direction
    total_directional = fast_bull + fast_bear + slow_bull + slow_bear
    if total_directional == 0:
        return 0

    max_direction = max(fast_bull + slow_bull, fast_bear + slow_bear)
    return round(max_direction / total_count * 100)


def compute_confirmation_score(pattern):
    """Confirmation score: -1 to 1."""
    scores = {
        "CONFIRMATION_LONG": 1.0,
        "CONFIRMATION_SHORT": 1.0,
        "DIVERGENCE_BULL": -0.5,
        "DIVERGENCE_BEAR": -0.5,
        "TRAP_LONG": -1.0,
        "TRAP_SHORT": -1.0,
        "NEUTRAL": 0.0,
    }
    return scores.get(pattern, 0.0)


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
    heartbeat["cross_timeframe"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── MAIN CYCLE ───
def run_cycle(api_key):
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Cross-Timeframe Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Fetch fast indicators (batch 1)
    print(f"  Fetching fast indicators for {len(TRADEABLE_COINS)} coins...")
    fast_data = fetch_indicators_batch(TRADEABLE_COINS, FAST_INDICATORS, api_key)
    print(f"  Got fast data for {len(fast_data)} coins")

    # Fetch slow indicators (batch 2)
    print(f"  Fetching slow indicators for {len(TRADEABLE_COINS)} coins...")
    slow_data = fetch_indicators_batch(TRADEABLE_COINS, SLOW_INDICATORS, api_key)
    print(f"  Got slow data for {len(slow_data)} coins")

    coins_out = {}
    pattern_counts = {}

    for coin in TRADEABLE_COINS:
        fast_vals = fast_data.get(coin, {})
        slow_vals = slow_data.get(coin, {})

        if not fast_vals and not slow_vals:
            print(f"  {coin:6s} [SKIP] no data")
            continue

        # Merge vals for pattern detection (ADX is in fast_vals)
        all_vals = {}
        all_vals.update(fast_vals)
        all_vals.update(slow_vals)

        fast_bias, fast_subs = classify_fast(fast_vals)
        slow_bias, slow_subs = classify_slow(slow_vals)
        pattern = detect_pattern(fast_bias, slow_bias, all_vals)
        strength = compute_strength(fast_subs, slow_subs)
        confirmation = compute_confirmation_score(pattern)

        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

        coins_out[coin] = {
            "fast_bias": fast_bias,
            "slow_bias": slow_bias,
            "pattern": pattern,
            "strength": strength,
            "confirmation_score": confirmation,
            "adx": round(all_vals["ADX_3H30M"], 4) if all_vals.get("ADX_3H30M") is not None else None,
            "fast_indicators": {k: round(v, 6) if isinstance(v, float) else v for k, v in fast_vals.items()},
            "slow_indicators": {k: round(v, 6) if isinstance(v, float) else v for k, v in slow_vals.items()},
        }

        conf_tag = {1.0: "++", -0.5: "~", -1.0: "!!", 0.0: "--"}.get(confirmation, "??")
        print(
            f"  {coin:6s} fast={fast_bias:8s} slow={slow_bias:8s}  "
            f"{pattern:20s} str={strength:3d}  [{conf_tag}]"
        )

    # Write output
    output = {
        "timestamp": ts_iso,
        "coins": coins_out,
    }
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(TIMEFRAME_FILE, "w") as f:
        json.dump(output, f, indent=2)

    write_heartbeat()

    print(f"\n  Patterns: {dict(sorted(pattern_counts.items()))}")
    print(f"  Written to {TIMEFRAME_FILE}")
    print(f"{'='*60}\n")


def main():
    api_key = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Cross-Timeframe Agent starting in loop mode (every {CYCLE_SECONDS}s)")
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

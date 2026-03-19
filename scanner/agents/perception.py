#!/usr/bin/env python3
"""
ZERO OS — Perception Agent (Cognitive Loop Phase 1)
Unified perception layer replacing: regime_agent, liquidity_agent,
cross_timeframe_agent, funding_agent, spread_monitor.

Builds a complete world model every 120s (full Envy indicator refresh every 300s).

Outputs:
  scanner/bus/world_state.json       -- unified world model (new)
  scanner/bus/regimes.json           -- legacy compat
  scanner/bus/liquidity.json         -- legacy compat
  scanner/bus/timeframe_signals.json -- legacy compat
  scanner/bus/funding.json           -- legacy compat
  scanner/bus/spread.json            -- legacy compat
  scanner/bus/heartbeat.json         -- heartbeat["perception"]

Usage:
  python3 scanner/agents/perception.py           # single run (full Envy fetch)
  python3 scanner/agents/perception.py --loop    # continuous 120s cycle
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# -- PATHS --
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"

WORLD_STATE_FILE  = BUS_DIR / "world_state.json"
REGIMES_FILE      = BUS_DIR / "regimes.json"
REGIME_HISTORY    = BUS_DIR / "regime_history.jsonl"
LIQUIDITY_FILE    = BUS_DIR / "liquidity.json"
TIMEFRAME_FILE    = BUS_DIR / "timeframe_signals.json"
FUNDING_FILE      = BUS_DIR / "funding.json"
FUNDING_HISTORY   = BUS_DIR / "funding_history.jsonl"
SPREAD_FILE       = BUS_DIR / "spread.json"
SPREAD_HISTORY    = BUS_DIR / "spread_history.jsonl"
HEARTBEAT_FILE    = BUS_DIR / "heartbeat.json"

# -- CONFIG --
ENVY_BASE_URL  = "https://gate.getzero.dev/api/claw"
HL_INFO_URL    = "https://api.hyperliquid.xyz/info"

CYCLE_FAST     = 120   # HL data fetched every 2 min
ENVY_INTERVAL  = 300   # Full Envy refresh every 5 min

COINS_PER_REQUEST = 10  # Envy API batch limit

ALL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

# Envy indicator batches -- max 16 per request
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

# -- REGIME THRESHOLDS --
HURST_HIGH             = 0.55
HURST_LOW              = 0.45
DFA_HIGH               = 0.55
DFA_LOW                = 0.45
LYAPUNOV_CHAOTIC       = 1.90   # crypto: >1.9 = truly chaotic (NOT 0.85)
INDICATOR_NEUTRAL_LOW  = 0.47
INDICATOR_NEUTRAL_HIGH = 0.53
TREND_THRESHOLD        = 0.03

# -- LIQUIDITY THRESHOLDS --
MAX_SPREAD_PCT  = 0.05
MIN_DEPTH_50    = 500.0
DEPTH_BAND_50   = 0.005
DEPTH_BAND_100  = 0.01
DEPTH_BAND_500  = 0.05

# -- FUNDING THRESHOLDS --
EXTREME_FUNDING_PCT        = 0.005
VERY_EXTREME_PCT           = 0.01
FUNDING_VELOCITY_WINDOW    = 6
FUNDING_REVERSAL_THRESHOLD = 0.003

# -- SPREAD THRESHOLDS --
SPREAD_WARNING_PCT    = 0.10
SPREAD_ALERT_PCT      = 0.30
SPREAD_COLLAPSE_SPEED = 0.05
MAX_SPREAD_HISTORY    = 30


# ==========================================================================
# LOGGING
# ==========================================================================

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


# ==========================================================================
# TRADING SESSION CLASSIFICATION (Upgrade 3)
# ==========================================================================

def get_trading_session(utc_hour):
    """Classify current UTC hour into trading session."""
    if 0 <= utc_hour < 7:
        return "ASIA"
    elif 7 <= utc_hour < 13:
        return "EUROPE"
    elif 13 <= utc_hour < 20:
        return "US"
    else:
        return "LATE_US"


# ==========================================================================
# API KEY
# ==========================================================================

def load_api_key():
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
    raise RuntimeError("ENVY_API_KEY not found in env or ~/.config/openclaw/.env")


# ==========================================================================
# ENVY API
# ==========================================================================

def envy_get(path, params, api_key):
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?" + qs
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_snapshot(snapshot):
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
    all_data = {}
    ind_param = ",".join(indicators)
    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i:i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = envy_get(
                "/paid/indicators/snapshot",
                {"coins": coins_param, "indicators": ind_param},
                api_key,
            )
            parsed = _parse_snapshot(resp.get("snapshot", {}))
            for coin, vals in parsed.items():
                all_data.setdefault(coin, {}).update(vals)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            log(f"  [warn] batch {batch[0]}-{batch[-1]} failed: {e}, retrying individually")
            for coin in batch:
                try:
                    resp = envy_get(
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
        if i + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)
    return all_data


def fetch_all_indicators(api_key):
    log(f"  [envy] fast indicators ({len(FAST_INDICATORS)} x {len(ALL_COINS)} coins)...")
    fast_data = fetch_indicators_batch(ALL_COINS, FAST_INDICATORS, api_key)
    log(f"  [envy] fast done: {len(fast_data)} coins")

    log(f"  [envy] slow+chaos indicators ({len(SLOW_AND_CHAOS_INDICATORS)} x {len(ALL_COINS)} coins)...")
    slow_data = fetch_indicators_batch(ALL_COINS, SLOW_AND_CHAOS_INDICATORS, api_key)
    log(f"  [envy] slow+chaos done: {len(slow_data)} coins")

    merged = {}
    for coin in set(list(fast_data.keys()) + list(slow_data.keys())):
        d = {}
        d.update(fast_data.get(coin, {}))
        d.update(slow_data.get(coin, {}))
        merged[coin] = d
    return merged


# ==========================================================================
# HYPERLIQUID API
# ==========================================================================

def fetch_hl_meta():
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    meta_list = resp[0]
    ctxs = resp[1]

    result = {}
    for i, ctx in enumerate(ctxs):
        coin   = meta_list["universe"][i]["name"]
        mark   = float(ctx.get("markPx")   or 0)
        oracle = float(ctx.get("oraclePx") or 0)
        mid    = float(ctx.get("midPx")    or 0)
        fund   = float(ctx.get("funding")  or 0)
        oi     = float(ctx.get("openInterest", 0))
        vol    = float(ctx.get("dayNtlVlm", 0))

        spread_pct = (mark - oracle) / oracle * 100 if oracle > 0 else 0.0

        result[coin] = {
            "mark":          mark,
            "oracle":        oracle,
            "mid":           mid,
            "funding":       fund,
            "funding_pct":   round(fund * 100, 6),
            "funding_ann":   round(fund * 365 * 3 * 100, 1),
            "open_interest": round(oi, 2),
            "volume_24h":    round(vol, 2),
            "spread_pct":    round(spread_pct, 6),
            "spread_abs":    round(abs(spread_pct), 6),
        }
    return result


def fetch_l2_book(coin):
    payload = json.dumps({"type": "l2Book", "coin": coin}).encode()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_all_l2_books():
    result = {}
    for coin in ALL_COINS:
        try:
            raw    = fetch_l2_book(coin)
            levels = raw.get("levels", [[], []])
            bids   = [(float(l["px"]), float(l["sz"])) for l in (levels[0] if len(levels) > 0 else [])]
            asks   = [(float(l["px"]), float(l["sz"])) for l in (levels[1] if len(levels) > 1 else [])]
            result[coin] = {"bids": bids, "asks": asks}
        except Exception as e:
            result[coin] = {"bids": [], "asks": [], "error": str(e)}
        time.sleep(0.15)
    return result


# ==========================================================================
# REGIME LOGIC
# ==========================================================================

def classify_regime(hurst, dfa, lyapunov):
    if lyapunov is not None and lyapunov > LYAPUNOV_CHAOTIC:
        return "chaotic"
    if hurst is not None and dfa is not None:
        if hurst > HURST_HIGH and dfa > DFA_HIGH:
            return "trending"
        if hurst < HURST_LOW and dfa < DFA_LOW:
            return "reverting"
        if (hurst > HURST_HIGH and dfa < DFA_LOW) or (hurst < HURST_LOW and dfa > DFA_HIGH):
            return "shift"
    vals = [v for v in (hurst, dfa, lyapunov) if v is not None]
    if vals and all(INDICATOR_NEUTRAL_LOW <= v <= INDICATOR_NEUTRAL_HIGH for v in vals):
        return "stable"
    return "stable"


def compute_regime_confidence(hurst, dfa, lyapunov, regime):
    if regime == "trending":
        h = max(0, (hurst - HURST_HIGH)) / 0.45 if hurst else 0
        d = max(0, (dfa   - DFA_HIGH))   / 0.45 if dfa   else 0
        return min(1.0, 0.5 + (h + d) / 2)
    if regime == "reverting":
        h = max(0, (HURST_LOW - hurst)) / 0.45 if hurst else 0
        d = max(0, (DFA_LOW   - dfa))   / 0.45 if dfa   else 0
        return min(1.0, 0.5 + (h + d) / 2)
    if regime == "chaotic":
        l = max(0, (lyapunov - LYAPUNOV_CHAOTIC)) / 0.15 if lyapunov else 0
        return min(1.0, 0.6 + l * 0.4)
    if regime == "shift":
        if hurst is not None and dfa is not None:
            return min(1.0, 0.4 + abs(hurst - dfa))
        return 0.5
    return 0.5


def trend_direction(v24, v48):
    if v24 is None or v48 is None:
        return "flat"
    diff = v24 - v48
    if diff >  TREND_THRESHOLD: return "rising"
    if diff < -TREND_THRESHOLD: return "falling"
    return "flat"


def detect_regime_transition(entry, prev_coin):
    if prev_coin is None:
        return False
    if entry["regime"] != prev_coin.get("regime"):
        return True
    ph = prev_coin.get("hurst_24h")
    ch = entry.get("hurst_24h")
    if ph is not None and ch is not None:
        if (ph < 0.50 and ch > 0.50) or (ph > 0.50 and ch < 0.50):
            return True
    return False


# ==========================================================================
# LIQUIDITY LOGIC
# ==========================================================================

def _spread_and_mid(bids, asks):
    if not bids or not asks:
        return float("inf"), 0.0
    bb = bids[0][0]
    ba = asks[0][0]
    mid = (bb + ba) / 2
    if mid <= 0:
        return float("inf"), mid
    return (ba - bb) / mid * 100, mid


def _depth(levels, mid, band_pct):
    return sum(px * sz for px, sz in levels
               if mid > 0 and abs(px - mid) / mid <= band_pct)


def _imbalance(bid_d, ask_d):
    total = bid_d + ask_d
    return 0.0 if total <= 0 else (bid_d - ask_d) / total


def compute_liquidity_score(spread_pct, bd50, ad50, bd500, ad500):
    if spread_pct <= 0.01:   ss = 40
    elif spread_pct <= 0.05: ss = 40 * (1 - (spread_pct - 0.01) / 0.04)
    elif spread_pct <= 0.20: ss = 10 * (1 - (spread_pct - 0.05) / 0.15)
    else:                    ss = 0

    near = bd50 + ad50
    if near >= 50000:   dn = 30
    elif near >= 5000:  dn = 10 + 20 * (near - 5000) / 45000
    elif near >= 500:   dn = 10 * (near - 500) / 4500
    else:               dn = 0

    far = bd500 + ad500
    if far >= 500000:   df = 30
    elif far >= 50000:  df = 10 + 20 * (far - 50000) / 450000
    elif far >= 5000:   df = 10 * (far - 5000) / 45000
    else:               df = 0

    return round(min(100, max(0, ss + dn + df)), 1)


def analyze_liquidity(book_data):
    bids  = book_data.get("bids", [])
    asks  = book_data.get("asks", [])
    error = book_data.get("error")

    empty = {
        "tradeable": False, "spread_pct": None,
        "bid_depth_50": 0, "ask_depth_50": 0,
        "bid_depth_100": 0, "ask_depth_100": 0,
        "bid_depth_500": 0, "ask_depth_500": 0,
        "imbalance": 0, "score": 0,
    }
    if error:
        empty["error"] = error
        return empty
    if not bids or not asks:
        empty["error"] = "no_book_data"
        return empty

    spread_pct, mid = _spread_and_mid(bids, asks)
    if mid <= 0:
        empty["error"] = "no_mid_price"
        return empty

    bd50  = _depth(bids, mid, DEPTH_BAND_50)
    ad50  = _depth(asks, mid, DEPTH_BAND_50)
    bd100 = _depth(bids, mid, DEPTH_BAND_100)
    ad100 = _depth(asks, mid, DEPTH_BAND_100)
    bd500 = _depth(bids, mid, DEPTH_BAND_500)
    ad500 = _depth(asks, mid, DEPTH_BAND_500)

    imbal    = _imbalance(bd50, ad50)
    score    = compute_liquidity_score(spread_pct, bd50, ad50, bd500, ad500)
    tradeable = spread_pct < MAX_SPREAD_PCT and min(bd50, ad50) > MIN_DEPTH_50

    return {
        "tradeable":     tradeable,
        "spread_pct":    round(spread_pct, 6),
        "bid_depth_50":  round(bd50, 2),
        "ask_depth_50":  round(ad50, 2),
        "bid_depth_100": round(bd100, 2),
        "ask_depth_100": round(ad100, 2),
        "bid_depth_500": round(bd500, 2),
        "ask_depth_500": round(ad500, 2),
        "imbalance":     round(imbal, 4),
        "score":         score,
    }


# ==========================================================================
# CROSS-TIMEFRAME LOGIC
# ==========================================================================

def classify_fast(vals):
    rsi       = vals.get("RSI_3H30M")
    ema_cross = vals.get("EMA_CROSS_15M_N")
    cmo       = vals.get("CMO_3H30M")
    if rsi is None or ema_cross is None or cmo is None:
        return "neutral", {}

    bull = bear = 0
    sub  = {}

    if rsi > 50:   bull += 1; sub["rsi_fast"] = "bullish"
    elif rsi < 50: bear += 1; sub["rsi_fast"] = "bearish"
    else:                     sub["rsi_fast"] = "neutral"

    if ema_cross > 0:   bull += 1; sub["ema_cross_fast"] = "bullish"
    elif ema_cross < 0: bear += 1; sub["ema_cross_fast"] = "bearish"
    else:                          sub["ema_cross_fast"] = "neutral"

    if cmo > 0:   bull += 1; sub["cmo_fast"] = "bullish"
    elif cmo < 0: bear += 1; sub["cmo_fast"] = "bearish"
    else:                    sub["cmo_fast"] = "neutral"

    macd = vals.get("MACD_CROSS_15M_N")
    if macd is not None:
        sub["macd_cross_fast"] = "bullish" if macd > 0 else ("bearish" if macd < 0 else "neutral")
        if macd > 0: bull += 1
        elif macd < 0: bear += 1

    bb = vals.get("BB_POSITION_15M")
    if bb is not None:
        sub["bb_fast"] = "bullish" if bb > 0.5 else ("bearish" if bb < 0.5 else "neutral")

    mom = vals.get("MOMENTUM_2H30M_N")
    if mom is not None:
        sub["momentum_fast"] = "bullish" if mom > 0 else ("bearish" if mom < 0 else "neutral")
        if mom > 0: bull += 1
        elif mom < 0: bear += 1

    if bull >= 2 and bull > bear: return "bullish", sub
    if bear >= 2 and bear > bull: return "bearish", sub
    return "neutral", sub


def classify_slow(vals):
    rsi  = vals.get("RSI_24H")
    ema  = vals.get("EMA_N_24H")
    macd = vals.get("MACD_N_24H")
    if rsi is None or ema is None or macd is None:
        return "neutral", {}

    bull = bear = 0
    sub  = {}

    if rsi > 50:   bull += 1; sub["rsi_slow"] = "bullish"
    elif rsi < 50: bear += 1; sub["rsi_slow"] = "bearish"
    else:                     sub["rsi_slow"] = "neutral"

    if ema > 1.0:   bull += 1; sub["ema_slow"] = "bullish"
    elif ema < 1.0: bear += 1; sub["ema_slow"] = "bearish"
    else:                      sub["ema_slow"] = "neutral"

    if macd > 0:   bull += 1; sub["macd_slow"] = "bullish"
    elif macd < 0: bear += 1; sub["macd_slow"] = "bearish"
    else:                     sub["macd_slow"] = "neutral"

    roc = vals.get("ROC_24H")
    if roc is not None:
        sub["roc_slow"] = "bullish" if roc > 0 else ("bearish" if roc < 0 else "neutral")
        if roc > 0: bull += 1
        elif roc < 0: bear += 1

    hurst = vals.get("HURST_24H")
    if hurst is not None:
        sub["hurst"] = round(hurst, 4)

    mom = vals.get("MOMENTUM_N_24H")
    if mom is not None:
        sub["momentum_slow"] = "bullish" if mom > 0 else ("bearish" if mom < 0 else "neutral")
        if mom > 0: bull += 1
        elif mom < 0: bear += 1

    ema48 = vals.get("EMA_N_48H")
    if ema48 is not None:
        sub["ema_48h"] = "bullish" if ema48 > 1.0 else ("bearish" if ema48 < 1.0 else "neutral")

    if bull >= 2 and bull > bear: return "bullish", sub
    if bear >= 2 and bear > bull: return "bearish", sub
    return "neutral", sub


def detect_pattern(fast_bias, slow_bias, vals):
    adx = vals.get("ADX_3H30M")
    if slow_bias == "bullish" and fast_bias == "bullish":
        return "CONFIRMATION_LONG"
    if slow_bias == "bearish" and fast_bias == "bearish":
        return "CONFIRMATION_SHORT"
    if slow_bias == "bearish" and fast_bias == "bullish":
        return "TRAP_LONG" if (adx is not None and adx < 20) else "DIVERGENCE_BULL"
    if slow_bias == "bullish" and fast_bias == "bearish":
        return "TRAP_SHORT" if (adx is not None and adx < 20) else "DIVERGENCE_BEAR"
    return "NEUTRAL"


def compute_tf_strength(fast_subs, slow_subs):
    total = bull = bear = 0
    for sub in (fast_subs, slow_subs):
        for v in sub.values():
            if isinstance(v, str):
                total += 1
                if v == "bullish": bull += 1
                elif v == "bearish": bear += 1
    if total == 0:
        return 0
    return round(max(bull, bear) / total * 100)


def confirmation_score(pattern):
    return {
        "CONFIRMATION_LONG":  1.0,
        "CONFIRMATION_SHORT": 1.0,
        "DIVERGENCE_BULL":   -0.5,
        "DIVERGENCE_BEAR":   -0.5,
        "TRAP_LONG":         -1.0,
        "TRAP_SHORT":        -1.0,
        "NEUTRAL":            0.0,
    }.get(pattern, 0.0)


# ==========================================================================
# FUNDING LOGIC
# ==========================================================================

def classify_funding(funding_pct):
    abs_f = abs(funding_pct)
    if abs_f >= VERY_EXTREME_PCT:   return "extreme"
    if abs_f >= EXTREME_FUNDING_PCT: return "elevated"
    return "neutral"


def compute_funding_velocity(prev_coin_data, curr_funding_pct):
    """Compute velocity, direction, reversal for one coin."""
    prev_rate   = prev_coin_data.get("funding_pct", 0) if prev_coin_data else 0
    history     = list(prev_coin_data.get("rate_history", [])) if prev_coin_data else []
    history.append(curr_funding_pct)
    if len(history) > FUNDING_VELOCITY_WINDOW:
        history = history[-FUNDING_VELOCITY_WINDOW:]

    velocity = curr_funding_pct - prev_rate if prev_rate != 0 else 0.0

    if abs(velocity) < 0.0001:
        direction = "stable"
    elif abs(curr_funding_pct) < abs(prev_rate):
        direction = "normalizing"
    else:
        direction = "intensifying"

    is_reversal = False
    if prev_rate != 0:
        was_extreme      = abs(prev_rate) >= EXTREME_FUNDING_PCT * 100
        normalizing_fast = abs(velocity) >= FUNDING_REVERSAL_THRESHOLD and direction == "normalizing"
        sign_flip        = (prev_rate > 0 and curr_funding_pct < 0) or (prev_rate < 0 and curr_funding_pct > 0)
        is_reversal      = (was_extreme and normalizing_fast) or sign_flip

    return {
        "velocity":    round(velocity, 6),
        "direction":   direction,
        "is_reversal": is_reversal,
        "prev_rate":   prev_rate,
        "rate_history": history,
    }


def detect_funding_convergence(coin, funding_pct, funding_ann, regime_str):
    """Detect funding+regime convergence signal."""
    f_class  = classify_funding(funding_pct)
    if f_class == "neutral":
        return None

    fund_raw = funding_pct / 100  # back to raw rate for sign check

    signal = strength = 0
    reason = ""

    if regime_str == "trending":
        if fund_raw < 0 and f_class in ("elevated", "extreme"):
            signal   = "LONG"
            strength = 1.5 if f_class == "extreme" else 1.0
            reason   = f"trending + negative funding ({funding_ann:.1f}% ann) = shorts paying"
        elif fund_raw > 0 and f_class in ("elevated", "extreme"):
            signal   = "SHORT"
            strength = 1.0 if f_class == "extreme" else 0.7
            reason   = f"trending + positive funding ({funding_ann:.1f}% ann) = overextended longs"
    elif regime_str == "reverting":
        if fund_raw < 0 and f_class in ("elevated", "extreme"):
            signal   = "LONG"
            strength = 1.2 if f_class == "extreme" else 0.8
            reason   = "reverting + negative funding = fade shorts"
        elif fund_raw > 0 and f_class in ("elevated", "extreme"):
            signal   = "SHORT"
            strength = 1.2 if f_class == "extreme" else 0.8
            reason   = "reverting + positive funding = fade longs"
    elif regime_str == "chaotic":
        if f_class == "extreme":
            strength = -0.5
            reason   = "chaotic + extreme funding = high risk"
    elif regime_str == "stable":
        if f_class == "extreme":
            if fund_raw < 0:
                signal   = "LONG"
                strength = 0.8
                reason   = "stable + extreme negative funding = pressure building"
            else:
                signal   = "SHORT"
                strength = 0.8
                reason   = "stable + extreme positive funding = pressure building"

    if signal or strength != 0:
        return {"direction": signal, "strength": round(strength, 2),
                "reason": reason, "funding_class": f_class, "regime": regime_str}
    return None


# ==========================================================================
# SPREAD LOGIC
# ==========================================================================

def classify_spread_status(spread_abs, funding_raw, velocity, collapsed):
    funding_extreme = abs(funding_raw) >= EXTREME_FUNDING_PCT
    if collapsed:
        return "UNWIND"
    if spread_abs >= SPREAD_ALERT_PCT and funding_extreme:
        return "MM_SETUP"
    if spread_abs >= SPREAD_ALERT_PCT:
        return "DIVERGED"
    if spread_abs >= SPREAD_WARNING_PCT:
        return "ELEVATED"
    return "NORMAL"


def _compute_btc_roc_4h():
    """Compute BTC ROC over last 4 hours from HL candle API."""
    try:
        now_ms = int(time.time() * 1000)
        four_h_ago = now_ms - (4 * 3600 * 1000)
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({
                "type": "candleSnapshot",
                "req": {"coin": "BTC", "interval": "1h", "startTime": four_h_ago, "endTime": now_ms}
            }).encode(),
            headers={"Content-Type": "application/json"}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if resp and len(resp) >= 2:
            open_4h = float(resp[0]["o"])
            close_now = float(resp[-1]["c"])
            if open_4h > 0:
                return ((close_now - open_4h) / open_4h) * 100
    except Exception as e:
        log(f"  WARN: BTC ROC_4H computation failed: {e}")
    return 0.0


def compute_spread_velocity_and_collapse(coin, current_spread, prev_spread_state):
    coin_hist = prev_spread_state.get("coins", {}).get(coin, {}).get("history", [])
    if not coin_hist:
        return 0.0, False, coin_hist
    prev_spread = coin_hist[-1]
    velocity    = current_spread - prev_spread
    was_extreme = abs(prev_spread) >= SPREAD_ALERT_PCT
    collapsed   = was_extreme and abs(current_spread) < abs(prev_spread) - SPREAD_COLLAPSE_SPEED
    return round(velocity, 6), collapsed, coin_hist


# ==========================================================================
# FILE HELPERS
# ==========================================================================

def load_json(path, default=None):
    if default is None:
        default = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_heartbeat():
    hb = load_json(HEARTBEAT_FILE)
    hb["perception"] = datetime.now(timezone.utc).isoformat()
    save_json(HEARTBEAT_FILE, hb)


# ==========================================================================
# MAIN CYCLE
# ==========================================================================

def run_cycle(api_key, prev_state, envy_cache, last_envy_ts):
    """
    One perception cycle.

    envy_cache: dict of {coin: {indicatorCode: value}} from last Envy fetch (or None)
    last_envy_ts: float timestamp of last Envy fetch (or 0)

    Returns (new_envy_cache, new_last_envy_ts)
    """
    cycle_start = time.time()
    ts          = datetime.now(timezone.utc)
    ts_iso      = ts.isoformat()

    log(f"{'='*60}")
    log(f"Perception Agent cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"{'='*60}")

    # ── 1. Envy indicators (only every 300s) ──
    need_envy = (envy_cache is None) or ((cycle_start - last_envy_ts) >= ENVY_INTERVAL)
    if need_envy:
        try:
            envy_cache    = fetch_all_indicators(api_key)
            last_envy_ts  = time.time()
            log(f"  [envy] refresh done: {len(envy_cache)} coins with indicators")
        except Exception as e:
            log(f"  [envy] FAILED: {e} — using previous cache if available")
            if envy_cache is None:
                envy_cache = {}
    else:
        age = int(cycle_start - last_envy_ts)
        log(f"  [envy] skipping refresh (cache age {age}s < {ENVY_INTERVAL}s)")

    # ── 2. HL metadata (every cycle) ──
    hl_meta = {}
    try:
        hl_meta = fetch_hl_meta()
        log(f"  [hl] metadata fetched: {len(hl_meta)} coins")
    except Exception as e:
        log(f"  [hl] metadata FAILED: {e}")

    # ── 3. L2 books (every cycle) ──
    books = {}
    try:
        log(f"  [hl] fetching L2 books for {len(ALL_COINS)} coins...")
        books = fetch_all_books_parallel(ALL_COINS)
        log(f"  [hl] L2 books done: {len(books)} coins")
    except Exception as e:
        log(f"  [hl] L2 books FAILED: {e}")

    # ── Load previous states for velocity / transition tracking ──
    prev_regimes  = prev_state.get("regimes",  {})
    prev_funding  = prev_state.get("funding",  {})
    prev_spread   = prev_state.get("spread",   {})

    # ── Load HL enrichment data (written by hl_enrichment agent) ──
    hl_enrich = load_json(BUS_DIR / "hl_enrichment.json", {})

    # ── 4. Build per-coin world state ──
    world_coins        = {}
    regime_dist        = {}
    elevated_spreads   = 0
    funding_intensify  = 0
    tradeable_count    = 0

    # Legacy output dicts
    legacy_regimes_coins   = {}
    legacy_liquidity_coins = {}
    legacy_tf_coins        = {}
    legacy_funding_coins   = {}
    legacy_spread_coins    = {}
    funding_convergence    = []
    funding_reversals      = []
    funding_extreme_list   = []
    spread_alerts          = []

    for coin in ALL_COINS:
        ind  = envy_cache.get(coin, {})
        hl   = hl_meta.get(coin, {})
        book = books.get(coin, {"bids": [], "asks": []})

        # ── Regime ──
        hurst_24h   = ind.get("HURST_24H")
        hurst_48h   = ind.get("HURST_48H")
        dfa_24h     = ind.get("DFA_24H")
        dfa_48h     = ind.get("DFA_48H")
        lyap_24h    = ind.get("LYAPUNOV_24H")
        lyap_48h    = ind.get("LYAPUNOV_48H")

        regime      = classify_regime(hurst_24h, dfa_24h, lyap_24h)
        confidence  = compute_regime_confidence(hurst_24h, dfa_24h, lyap_24h, regime)
        hurst_trend = trend_direction(hurst_24h, hurst_48h)
        dfa_trend   = trend_direction(dfa_24h,   dfa_48h)

        prev_reg    = prev_regimes.get(coin, {})
        prev_regime = prev_reg.get("regime")

        reg_entry = {
            "regime":         regime,
            "confidence":     round(confidence, 3),
            "prev_regime":    prev_regime,
            "transition":     False,
            "transition_age_min": 0,
            "hurst_24h":      hurst_24h,
            "hurst_48h":      hurst_48h,
            "dfa_24h":        dfa_24h,
            "dfa_48h":        dfa_48h,
            "lyapunov_24h":   lyap_24h,
            "lyapunov_48h":   lyap_48h,
            "hurst_trend":    hurst_trend,
            "dfa_trend":      dfa_trend,
        }

        is_trans = detect_regime_transition(reg_entry, prev_reg)
        if is_trans:
            reg_entry["transition"]         = True
            reg_entry["transition_age_min"] = 0
        elif prev_reg and prev_reg.get("transition"):
            prev_age = prev_reg.get("transition_age_min", 0)
            reg_entry["transition"]         = True
            reg_entry["transition_age_min"] = prev_age + int(CYCLE_FAST / 60)

        regime_dist[regime] = regime_dist.get(regime, 0) + 1
        legacy_regimes_coins[coin] = reg_entry

        # ── Liquidity ──
        liq = analyze_liquidity(book)
        legacy_liquidity_coins[coin] = liq
        if liq.get("tradeable"):
            tradeable_count += 1

        # ── Cross-timeframe ──
        fast_bias, fast_subs = classify_fast(ind)
        slow_bias, slow_subs = classify_slow(ind)
        all_vals_for_pattern = {}
        all_vals_for_pattern.update(ind)
        pattern    = detect_pattern(fast_bias, slow_bias, all_vals_for_pattern)
        strength   = compute_tf_strength(fast_subs, slow_subs)
        conf_score = confirmation_score(pattern)
        adx_val    = ind.get("ADX_3H30M")

        tf_entry = {
            "fast_bias":          fast_bias,
            "slow_bias":          slow_bias,
            "pattern":            pattern,
            "strength":           strength,
            "confirmation_score": conf_score,
            "adx":                round(adx_val, 4) if adx_val is not None else None,
            "fast_indicators":    {k: round(v, 6) if isinstance(v, float) else v
                                   for k, v in ind.items()
                                   if k in FAST_INDICATORS},
            "slow_indicators":    {k: round(v, 6) if isinstance(v, float) else v
                                   for k, v in ind.items()
                                   if k in SLOW_AND_CHAOS_INDICATORS},
        }
        legacy_tf_coins[coin] = tf_entry

        # ── Funding ──
        fund_pct  = hl.get("funding_pct", 0.0)
        fund_ann  = hl.get("funding_ann",  0.0)
        fund_raw  = hl.get("funding",      0.0)

        f_class   = classify_funding(fund_pct)
        prev_fund = prev_funding.get(coin, {})
        vel_data  = compute_funding_velocity(prev_fund, fund_pct)

        fund_entry = {
            "funding_rate":       fund_raw,
            "funding_pct":        fund_pct,
            "annualized_pct":     fund_ann,
            "open_interest":      hl.get("open_interest", 0),
            "mark_price":         hl.get("mark", 0),
            "volume_24h":         hl.get("volume_24h", 0),
            "classification":     f_class,
            "velocity":           vel_data["velocity"],
            "velocity_direction": vel_data["direction"],
            "is_reversal":        vel_data["is_reversal"],
            "prev_rate":          vel_data["prev_rate"],
            "rate_history":       vel_data["rate_history"],
        }
        legacy_funding_coins[coin] = fund_entry

        if vel_data["direction"] == "intensifying":
            funding_intensify += 1

        if vel_data["is_reversal"]:
            funding_reversals.append({
                "coin":      coin,
                "from_rate": vel_data["prev_rate"],
                "to_rate":   fund_pct,
                "velocity":  vel_data["velocity"],
                "direction": vel_data["direction"],
            })

        if f_class != "neutral":
            funding_extreme_list.append(coin)

        conv = detect_funding_convergence(coin, fund_pct, fund_ann, regime)
        if conv:
            conv["coin"]           = coin
            conv["funding_pct"]    = fund_pct
            conv["annualized_pct"] = fund_ann
            funding_convergence.append(conv)

        # ── Spread ──
        spread_pct = hl.get("spread_pct", 0.0)
        spread_abs = hl.get("spread_abs", 0.0)
        mark       = hl.get("mark",   0.0)
        oracle     = hl.get("oracle", 0.0)

        spread_vel, collapsed, spread_hist = compute_spread_velocity_and_collapse(
            coin, spread_pct, prev_spread
        )
        spread_hist.append(spread_pct)
        if len(spread_hist) > MAX_SPREAD_HISTORY:
            spread_hist = spread_hist[-MAX_SPREAD_HISTORY:]

        spread_status = classify_spread_status(spread_abs, fund_raw, spread_vel, collapsed)

        spreads_abs_hist = [abs(s) for s in spread_hist]
        avg_sp = sum(spreads_abs_hist) / len(spreads_abs_hist) if spreads_abs_hist else 0
        max_sp = max(spreads_abs_hist) if spreads_abs_hist else 0

        spread_entry = {
            "spread_pct":     spread_pct,
            "spread_abs":     spread_abs,
            "velocity":       spread_vel,
            "status":         spread_status,
            "funding":        round(fund_raw * 100, 6),
            "funding_ann":    fund_ann,
            "mark":           mark,
            "oracle":         oracle,
            "oi":             hl.get("open_interest", 0),
            "vol24h":         hl.get("volume_24h", 0),
            "avg_spread_1h":  round(avg_sp, 6),
            "max_spread_1h":  round(max_sp, 6),
            "history":        spread_hist,
        }
        legacy_spread_coins[coin] = spread_entry

        if spread_status in ("ELEVATED", "DIVERGED", "MM_SETUP", "UNWIND"):
            elevated_spreads += 1

        if spread_status in ("MM_SETUP", "UNWIND"):
            spread_alerts.append({
                "coin":       coin,
                "status":     spread_status,
                "spread_pct": spread_pct,
                "funding_pct": round(fund_raw * 100, 4),
                "velocity":   spread_vel,
                "timestamp":  ts_iso,
            })

        # ── Own indicators (parallel computation via IndicatorEngine) ──
        # Runs in parallel/shadow mode: never blocks the main cycle.
        # Stores results under indicators_own; logs drift to drift_log.jsonl.
        _own_indicators = {}
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from indicators.engine import IndicatorEngine, fetch_hl_candles
            from indicators.signal_logger import log_indicator_drift
            _own_candles = fetch_hl_candles(coin, "1h", 300)
            if len(_own_candles) >= 50:
                _eng = IndicatorEngine(_own_candles)
                _own_indicators = {
                    "HURST_24H":    _eng.hurst(window=200),
                    "DFA_24H":      _eng.dfa(window=200),
                    "LYAPUNOV_24H": _eng.lyapunov(window=200),
                }
                # Log drift vs Envy source
                for _ind_key in ("HURST_24H", "DFA_24H", "LYAPUNOV_24H"):
                    _theirs = ind.get(_ind_key)
                    _ours   = _own_indicators.get(_ind_key)
                    if _theirs is not None and _ours is not None:
                        log_indicator_drift(coin, _ind_key, float(_theirs), _ours)
        except Exception as _e:
            log(f"  [own_indicators] {coin} failed (non-fatal): {_e}")

        # ── Assemble world_state coin entry ──
        # Pull in HL enrichment fields if available
        _enrich = hl_enrich.get("coins", {}).get(coin, {})
        _premium      = _enrich.get("premium",       hl.get("premium", 0))
        _impact_spread = _enrich.get("impact_spread", 0.0)
        _oi_usd       = _enrich.get("oi_usd",        hl.get("open_interest", 0) * hl.get("mark", 0))

        world_coins[coin] = {
            "regime":            regime,
            "regime_confidence": round(confidence, 3),
            "funding": {
                "rate":               fund_pct,
                "velocity_direction": vel_data["direction"],
                "reversal":           vel_data["is_reversal"],
            },
            "spread": {
                "mark_oracle_pct": spread_pct,
                "status":          spread_status,
            },
            "oi": {
                "open_interest": hl.get("open_interest", 0),
                "volume_24h":    hl.get("volume_24h", 0),
                "mark_price":    hl.get("mark", 0),
                "premium":       _premium,
                "impact_spread": round(_impact_spread, 8),
                "oi_usd":        round(_oi_usd, 2),
            },
            "liquidity": {
                "score":      liq.get("score", 0),
                "tradeable":  liq.get("tradeable", False),
                "spread_pct": liq.get("spread_pct"),
                "depth_1pct": round(
                    liq.get("bid_depth_100", 0) + liq.get("ask_depth_100", 0), 0
                ),
            },
            "timeframe": {
                "fast_bias": fast_bias,
                "slow_bias": slow_bias,
                "pattern":   pattern,
            },
            "indicators":     {k: round(v, 6) if isinstance(v, float) else v
                               for k, v in ind.items()},
            "indicators_own": {k: round(v, 6) if isinstance(v, float) and not __import__("math").isnan(v) else v
                               for k, v in _own_indicators.items()},
        }

    cycle_ms = int((time.time() - cycle_start) * 1000)

    # ── Upgrade 1: Market-wide Macro Regime ──
    total_coins = len(world_coins) or 1
    n_chaotic  = sum(1 for c in world_coins.values() if c["regime"] == "chaotic")
    n_trending = sum(1 for c in world_coins.values() if c["regime"] == "trending")
    n_shift    = sum(1 for c in world_coins.values() if c["regime"] == "shift")
    n_stable   = sum(1 for c in world_coins.values() if c["regime"] == "stable")

    btc_data     = world_coins.get("BTC", {})
    btc_roc_24h  = btc_data.get("indicators", {}).get("ROC_24H", 0) or 0
    # Compute ROC_4H from HL candles (indicators_own doesn't have it)
    btc_roc_4h   = _compute_btc_roc_4h()

    if btc_roc_4h < -2 or btc_roc_24h < -3 or (n_chaotic / total_coins > 0.3):
        macro_state = "RISK_OFF"
    elif btc_roc_4h > 2 and btc_roc_24h > 0 and (n_trending / total_coins > 0.4 and n_chaotic / total_coins < 0.15):
        macro_state = "RISK_ON"
    else:
        macro_state = "CHOPPY"

    fear_score = 50
    fear_score -= n_chaotic * 3
    fear_score -= min(0, btc_roc_4h) * 5
    fear_score += max(0, btc_roc_4h) * 3
    fear_score -= elevated_spreads * 2
    fear_score = max(0, min(100, fear_score))

    # ── Upgrade 3: Session detection ──
    utc_now = datetime.now(timezone.utc)
    session = get_trading_session(utc_now.hour)

    # ── 5. World state ──
    # Load market_stability from regime_predictions if available
    _regime_pred_file = BUS_DIR / "regime_predictions.json"
    _market_stability = None
    try:
        if _regime_pred_file.exists():
            with open(_regime_pred_file) as _f:
                _rp = json.load(_f)
            _market_stability = _rp.get("market_stability")
    except Exception:
        pass

    _macro_dict = {
        "state":       macro_state,
        "fear_score":  round(fear_score, 1),
        "btc_roc_4h":  round(btc_roc_4h, 4),
        "btc_roc_24h": round(btc_roc_24h, 4),
        "chaos_pct":   round(n_chaotic / total_coins * 100, 1),
    }
    if _market_stability is not None:
        _macro_dict["market_stability"] = _market_stability

    # ── Macro intel enrichment (fear & greed, DVOL, FOMC gate) ──
    macro_intel = load_json(BUS_DIR / "macro_intel.json", {})

    world_state = {
        "timestamp": ts_iso,
        "coins":     world_coins,
        "macro": {
            "fear_greed":             macro_intel.get("fear_greed"),
            "fear_greed_class":       macro_intel.get("fear_greed_class"),
            "btc_dvol":               macro_intel.get("btc_dvol"),
            "macro_event_imminent":   macro_intel.get("macro_event_imminent", False),
            "days_to_fomc":           macro_intel.get("days_to_fomc"),
            "days_to_options_expiry": macro_intel.get("days_to_options_expiry"),
        },
        "meta": {
            "coins_total":        len(ALL_COINS),
            "coins_tradeable":    tradeable_count,
            "regime_distribution": regime_dist,
            "elevated_spreads":   elevated_spreads,
            "funding_intensifying": funding_intensify,
            "cycle_time_ms":      cycle_ms,
            "macro":              _macro_dict,
            "session":   session,
            "utc_hour":  utc_now.hour,
        },
    }
    save_json(WORLD_STATE_FILE, world_state)
    log(f"  [world_state] written ({len(world_coins)} coins, {cycle_ms}ms)")

    # ── 6. Legacy bus files ──
    save_json(REGIMES_FILE, {"timestamp": ts_iso, "coins": legacy_regimes_coins})
    log(f"  [regimes] written")

    # Append regime history
    with open(REGIME_HISTORY, "a") as f:
        f.write(json.dumps({"timestamp": ts_iso, "coins": legacy_regimes_coins}) + "\n")

    save_json(LIQUIDITY_FILE, {"timestamp": ts_iso, "coins": legacy_liquidity_coins})
    log(f"  [liquidity] written")

    save_json(TIMEFRAME_FILE, {"timestamp": ts_iso, "coins": legacy_tf_coins})
    log(f"  [timeframe_signals] written")

    # Funding legacy
    fund_legacy = {
        "timestamp":          ts_iso,
        "coins":              legacy_funding_coins,
        "convergence_signals": funding_convergence,
        "extreme_funding":    funding_extreme_list,
        "reversals":          funding_reversals,
    }
    save_json(FUNDING_FILE, fund_legacy)
    log(f"  [funding] written ({len(funding_extreme_list)} extreme, {len(funding_reversals)} reversals)")

    # Funding history append
    history_entry = {
        "t":           ts_iso,
        "coins":       {c: {"f": d["funding_pct"], "oi": d["open_interest"]}
                        for c, d in legacy_funding_coins.items()},
        "convergence": len(funding_convergence),
    }
    with open(FUNDING_HISTORY, "a") as f:
        f.write(json.dumps(history_entry) + "\n")

    # Spread legacy
    spread_summary = {
        "total_monitored": len(legacy_spread_coins),
        "normal":    sum(1 for c in legacy_spread_coins.values() if c["status"] == "NORMAL"),
        "elevated":  sum(1 for c in legacy_spread_coins.values() if c["status"] == "ELEVATED"),
        "diverged":  sum(1 for c in legacy_spread_coins.values() if c["status"] == "DIVERGED"),
        "mm_setup":  sum(1 for c in legacy_spread_coins.values() if c["status"] == "MM_SETUP"),
        "unwind":    sum(1 for c in legacy_spread_coins.values() if c["status"] == "UNWIND"),
    }
    spread_legacy = {
        "timestamp": ts_iso,
        "coins":     legacy_spread_coins,
        "alerts":    spread_alerts,
        "summary":   spread_summary,
    }
    save_json(SPREAD_FILE, spread_legacy)
    log(f"  [spread] written ({spread_summary['mm_setup']} MM_SETUP, {spread_summary['unwind']} UNWIND)")

    if spread_alerts:
        with open(SPREAD_HISTORY, "a") as f:
            for a in spread_alerts:
                f.write(json.dumps(a) + "\n")

    write_heartbeat()

    # ── 7. Summary ──
    log(f"\n  Regimes: {dict(sorted(regime_dist.items()))}")
    log(f"  Tradeable: {tradeable_count}/{len(ALL_COINS)}")
    log(f"  Elevated spreads: {elevated_spreads} | Funding intensifying: {funding_intensify}")
    log(f"  Macro: {macro_state} | Fear: {fear_score:.0f}/100 | Session: {session}")
    log(f"  Cycle: {cycle_ms}ms")
    log(f"{'='*60}\n")

    # ── Update prev_state for next cycle ──
    new_prev = {
        "regimes": {coin: legacy_regimes_coins[coin] for coin in legacy_regimes_coins},
        "funding": {coin: legacy_funding_coins[coin] for coin in legacy_funding_coins},
        "spread":  spread_legacy,
    }

    return envy_cache, last_envy_ts, new_prev


def fetch_all_books_parallel(coins):
    """Sequential L2 book fetch (threading not needed — total ~6s for 40 coins)."""
    result = {}
    for coin in coins:
        try:
            raw    = fetch_l2_book(coin)
            levels = raw.get("levels", [[], []])
            bids   = [(float(l["px"]), float(l["sz"])) for l in (levels[0] if len(levels) > 0 else [])]
            asks   = [(float(l["px"]), float(l["sz"])) for l in (levels[1] if len(levels) > 1 else [])]
            result[coin] = {"bids": bids, "asks": asks}
        except Exception as e:
            result[coin] = {"bids": [], "asks": [], "error": str(e)}
        time.sleep(0.15)
    return result


# ==========================================================================
# ENTRY POINT
# ==========================================================================

def main():
    api_key   = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Perception Agent starting — loop every {CYCLE_FAST}s, Envy refresh every {ENVY_INTERVAL}s")
        envy_cache   = None
        last_envy_ts = 0.0
        prev_state   = {"regimes": {}, "funding": {}, "spread": {}}

        while True:
            try:
                envy_cache, last_envy_ts, prev_state = run_cycle(
                    api_key, prev_state, envy_cache, last_envy_ts
                )
            except Exception as e:
                log(f"[error] Cycle failed: {e}")
                write_heartbeat()
            time.sleep(CYCLE_FAST)
    else:
        # Single run — full Envy fetch
        prev_state = {"regimes": {}, "funding": {}, "spread": {}}
        run_cycle(api_key, prev_state, None, 0.0)


if __name__ == "__main__":
    main()

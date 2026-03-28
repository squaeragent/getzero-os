#!/usr/bin/env python3
"""
ZERO OS — Indicator Delta Monitor

Parallel computation layer: fetches HL candle data, computes the same technical
indicators that ENVY reports, then logs the delta (divergence) between the two
sources.  Acts as a resilience / calibration check for ENVY data.

Input:   scanner/data/envy_history/YYYY-MM-DD.jsonl  (latest line)
Output:  scanner/data/indicator_deltas/YYYY-MM-DD.jsonl
Cycle:   900 s (15 min) — should run after envy_cache

Usage:
  python3 scanner/agents/indicator_delta.py           # single run
  python3 scanner/agents/indicator_delta.py --loop    # continuous 900s loop
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import talib  # type: ignore
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False

try:
    from scanner.senses.taapi_plugin import TaapiPlugin
    HAS_TAAPI = True
except ImportError:
    HAS_TAAPI = False

from scanner.utils import (
    DATA_DIR,
    append_jsonl,
    make_logger,
    read_jsonl,
    update_heartbeat,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HISTORY_DIR  = DATA_DIR / "envy_history"
DELTA_DIR    = DATA_DIR / "indicator_deltas"

HL_INFO_URL  = "https://api.hyperliquid.xyz/info"
CYCLE_SEC    = 900
WARNING_PCT  = 5.0  # delta threshold that triggers WARNING log

# ---------------------------------------------------------------------------
# Whitelisted coins for delta computation
# ---------------------------------------------------------------------------
DELTA_COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = make_logger("INDICATOR_DELTA")


# ---------------------------------------------------------------------------
# ENVY cache reader
# ---------------------------------------------------------------------------

def load_latest_envy_snapshot() -> Optional[dict[str, dict[str, float]]]:
    """Return the most recent snapshot from today's (or yesterday's) JSONL file."""
    now = datetime.now(timezone.utc)
    for days_back in (0, 1):
        ts = now.timestamp() - days_back * 86400
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        filepath = HISTORY_DIR / f"{date_str}.jsonl"
        records = read_jsonl(filepath, max_lines=1)
        if records:
            return records[-1].get("coins", {})
    return None


# ---------------------------------------------------------------------------
# Hyperliquid candle fetcher
# ---------------------------------------------------------------------------

def fetch_hl_candles(coin: str, interval: str, lookback_ms: int) -> list[dict]:
    """Fetch candles from Hyperliquid info API."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_ms
    payload = json.dumps({
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms,
        }
    }).encode()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def candles_to_close(candles: list[dict]) -> np.ndarray:
    return np.array([float(c["c"]) for c in candles], dtype=float)


def candles_to_high(candles: list[dict]) -> np.ndarray:
    return np.array([float(c["h"]) for c in candles], dtype=float)


def candles_to_low(candles: list[dict]) -> np.ndarray:
    return np.array([float(c["l"]) for c in candles], dtype=float)


# ---------------------------------------------------------------------------
# Indicator computation with TA-Lib
# ---------------------------------------------------------------------------

def safe_last(arr) -> Optional[float]:
    """Return the last finite value in a TA-Lib output array, or None."""
    if arr is None or len(arr) == 0:
        return None
    val = float(arr[-1])
    return None if (val != val) else val  # NaN check


def compute_rsi(close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles + 1:
        return None
    result = talib.RSI(close, timeperiod=period_candles)
    return safe_last(result)


def compute_ema(close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles:
        return None
    result = talib.EMA(close, timeperiod=period_candles)
    return safe_last(result)


def compute_roc(close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles + 1:
        return None
    result = talib.ROC(close, timeperiod=period_candles)
    return safe_last(result)


def compute_macd_signal(close: np.ndarray, fast: int, slow: int, signal: int = 9) -> Optional[float]:
    """Return MACD histogram (macd - signal) as a normalised [-1,1] position."""
    if not HAS_TALIB or len(close) < slow + signal:
        return None
    macd, sig, hist = talib.MACD(close, fastperiod=fast, slowperiod=slow, signalperiod=signal)
    h = safe_last(hist)
    if h is None:
        return None
    # Normalise by current price magnitude so it's comparable across coins
    price = safe_last(close)
    if price and price > 0:
        return h / price
    return h


def compute_bb_position(close: np.ndarray, period_candles: int) -> Optional[float]:
    """Return BB position as (price - lower) / (upper - lower), i.e. 0..1."""
    if not HAS_TALIB or len(close) < period_candles:
        return None
    upper, middle, lower = talib.BBANDS(close, timeperiod=period_candles)
    u = safe_last(upper)
    l = safe_last(lower)
    p = safe_last(close)
    if u is None or l is None or p is None:
        return None
    band = u - l
    if band <= 0:
        return 0.5
    return (p - l) / band


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles * 2:
        return None
    result = talib.ADX(high, low, close, timeperiod=period_candles)
    return safe_last(result)


def compute_cmo(close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles + 1:
        return None
    result = talib.CMO(close, timeperiod=period_candles)
    return safe_last(result)


def compute_momentum(close: np.ndarray, period_candles: int) -> Optional[float]:
    if not HAS_TALIB or len(close) < period_candles + 1:
        return None
    result = talib.MOM(close, timeperiod=period_candles)
    val = safe_last(result)
    if val is None:
        return None
    price = safe_last(close)
    if price and price > 0:
        return (val / price) * 100  # normalise as % move
    return val


def compute_ichimoku(candles_1h: list[dict]) -> dict[str, Optional[float]]:
    """
    Compute Ichimoku components from 1h candles.
    Tenkan-sen  (9-period midpoint)
    Kijun-sen   (26-period midpoint)
    Senkou A    = (Tenkan + Kijun) / 2
    Senkou B    = 52-period midpoint
    Returns a dict with keys: ICHI_TENKAN, ICHI_KIJUN, SENKOU_A, SENKOU_B
    """
    result: dict[str, Optional[float]] = {
        "ICHI_TENKAN": None, "ICHI_KIJUN": None,
        "SENKOU_A": None,    "SENKOU_B": None,
    }
    if len(candles_1h) < 52:
        return result

    highs = [float(c["h"]) for c in candles_1h]
    lows  = [float(c["l"]) for c in candles_1h]

    def midpoint(h_list, l_list):
        return (max(h_list) + min(l_list)) / 2

    tenkan = midpoint(highs[-9:],  lows[-9:])
    kijun  = midpoint(highs[-26:], lows[-26:])
    senkou_b = midpoint(highs[-52:], lows[-52:])
    senkou_a = (tenkan + kijun) / 2

    result["ICHI_TENKAN"] = tenkan
    result["ICHI_KIJUN"]  = kijun
    result["SENKOU_A"]    = senkou_a
    result["SENKOU_B"]    = senkou_b
    return result


# ---------------------------------------------------------------------------
# Per-coin own computation
# ---------------------------------------------------------------------------

MS_PER_HOUR = 3_600_000

def compute_own_indicators(coin: str) -> dict[str, float]:
    """
    Fetch HL candles and compute all indicators we want to cross-check.
    Returns dict of {INDICATOR_CODE: value}.
    """
    own: dict[str, float] = {}

    # --- Fetch candle data ---
    try:
        candles_1m = fetch_hl_candles(coin, "1m", 4 * MS_PER_HOUR)   # 4h of 1-min
    except Exception as e:
        log(f"  WARN {coin}: 1m candle fetch failed: {e}")
        candles_1m = []

    try:
        candles_1h = fetch_hl_candles(coin, "1h", 48 * MS_PER_HOUR)  # 48h of 1h
    except Exception as e:
        log(f"  WARN {coin}: 1h candle fetch failed: {e}")
        candles_1h = []

    if not candles_1m and not candles_1h:
        return own

    # --- 1m-based indicators (using period in minutes) ---
    if candles_1m:
        close_1m = candles_to_close(candles_1m)
        high_1m  = candles_to_high(candles_1m)
        low_1m   = candles_to_low(candles_1m)

        # RSI using 1m candles for shorter windows
        # RSI_3H30M: 3.5h = 210 min
        v = compute_rsi(close_1m, 210)
        if v is not None:
            own["RSI_3H30M"] = v

        # CMO_3H30M: 210 min
        v = compute_cmo(close_1m, 210)
        if v is not None:
            own["CMO_3H30M"] = v

        # ADX_3H30M: 210 min
        v = compute_adx(high_1m, low_1m, close_1m, 210)
        if v is not None:
            own["ADX_3H30M"] = v

    # --- 1h-based indicators ---
    if candles_1h:
        close_1h = candles_to_close(candles_1h)
        high_1h  = candles_to_high(candles_1h)
        low_1h   = candles_to_low(candles_1h)

        # RSI
        for hours, code in [(6, "RSI_6H"), (12, "RSI_12H"), (24, "RSI_24H")]:
            v = compute_rsi(close_1h, hours)
            if v is not None:
                own[code] = v

        # EMA
        for hours, code in [(6, "EMA_6H"), (12, "EMA_12H"), (24, "EMA_N_24H"), (48, "EMA_N_48H")]:
            v = compute_ema(close_1h, hours)
            if v is not None:
                own[code] = v

        # MACD 6H: fast=3, slow=6, signal=9 on 1h candles
        v = compute_macd_signal(close_1h, fast=3, slow=6, signal=4)
        if v is not None:
            own["MACD_6H"] = v

        # MACD 24H: fast=12, slow=26, signal=9 on 1h candles
        v = compute_macd_signal(close_1h, fast=12, slow=26, signal=9)
        if v is not None:
            own["MACD_N_24H"] = v

        # ROC
        for hours, code in [
            (3, "ROC_3H"), (6, "ROC_6H"), (12, "ROC_12H"),
            (24, "ROC_24H"), (48, "ROC_48H"),
        ]:
            v = compute_roc(close_1h, hours)
            if v is not None:
                own[code] = v

        # BB position
        for hours, code in [
            (6, "BB_POS_6H"), (12, "BB_POS_12H"),
            (24, "BB_POS_24H"), (48, "BB_POS_48H"),
        ]:
            v = compute_bb_position(close_1h, hours)
            if v is not None:
                own[code] = v

        # Momentum
        for hours, code in [(6, "MOMENTUM_6H"), (12, "MOMENTUM_N_12H")]:
            v = compute_momentum(close_1h, hours)
            if v is not None:
                own[code] = v

        # Ichimoku
        ichi = compute_ichimoku(candles_1h)
        for k, v in ichi.items():
            if v is not None:
                own[k] = v

    return own


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

# Map from ENVY indicator codes → our computed codes (where they align)
ENVY_TO_OWN: dict[str, str] = {
    "RSI_24H":      "RSI_24H",
    "EMA_N_24H":    "EMA_N_24H",
    "EMA_N_48H":    "EMA_N_48H",
    "MACD_N_24H":   "MACD_N_24H",
    "ROC_24H":      "ROC_24H",
    "BB_POS_24H":   "BB_POS_24H",
    "MOMENTUM_N_24H": "MOMENTUM_N_12H",  # closest match
    "RSI_3H30M":    "RSI_3H30M",
    "CMO_3H30M":    "CMO_3H30M",
    "ADX_3H30M":    "ADX_3H30M",
}


def compute_deltas(
    envy_vals: dict[str, float],
    own_vals: dict[str, float],
    taapi_vals: Optional[dict[str, float]] = None,
) -> dict[str, dict]:
    """
    Compare ENVY vs TA-Lib (own) and optionally TAAPI for each matched indicator.

    Output shape per indicator:
      {
        "envy":  0.45,
        "talib": 44.8,
        "taapi": 45.1,           # if taapi_vals provided
        "delta_taapi_pct": 0.66, # if taapi_vals provided
        "delta": ...,
        "pct": ...,
      }
    """
    deltas: dict[str, dict] = {}
    for envy_code, own_code in ENVY_TO_OWN.items():
        e_val = envy_vals.get(envy_code)
        o_val = own_vals.get(own_code)
        if e_val is None or o_val is None:
            continue

        try:
            e_f = float(e_val)
            o_f = float(o_val)
        except (TypeError, ValueError):
            continue

        delta = abs(e_f - o_f)
        # Use mean of the two as denominator to avoid /0 and extreme pcts
        denom = (abs(e_f) + abs(o_f)) / 2
        pct = (delta / denom * 100) if denom > 1e-9 else 0.0

        entry: dict = {
            "envy":  round(e_f, 6),
            "talib": round(o_f, 6),
            "delta": round(delta, 6),
            "pct":   round(pct, 4),
        }

        # Wire TAAPI values if provided — use the same ENVY code as key
        if taapi_vals is not None:
            t_val = taapi_vals.get(envy_code)
            if t_val is not None:
                try:
                    t_f = float(t_val)
                    t_delta = abs(e_f - t_f)
                    t_denom = (abs(e_f) + abs(t_f)) / 2
                    t_pct   = (t_delta / t_denom * 100) if t_denom > 1e-9 else 0.0
                    entry["taapi"]           = round(t_f, 6)
                    entry["delta_taapi_pct"] = round(t_pct, 4)
                except (TypeError, ValueError):
                    pass

        deltas[envy_code] = entry
    return deltas


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def fetch_taapi_snapshot(coins: list[str]) -> dict[str, dict[str, float]]:
    """
    Fetch TAAPI indicators for all coins and return a snapshot dict:
      { coin: { INDICATOR_CODE: raw_value, ... } }

    Maps taapi.{INDICATOR} dimension names back to bare indicator codes
    so they align with ENVY_TO_OWN keys.
    """
    if not HAS_TAAPI:
        log("WARN: taapi_plugin not importable — skipping TAAPI source")
        return {}

    taapi_snapshot: dict[str, dict[str, float]] = {}
    try:
        plugin = TaapiPlugin()
        observations = plugin.fetch(coins)
        for obs in observations:
            # dimension format: "taapi.RSI_24H"
            if obs.dimension.startswith("taapi."):
                code = obs.dimension[len("taapi."):]
                taapi_snapshot.setdefault(obs.coin, {})[code] = obs.value
        log(f"TAAPI snapshot: {sum(len(v) for v in taapi_snapshot.values())} indicators across {len(taapi_snapshot)} coins")
    except Exception as e:
        log(f"WARN: TAAPI fetch failed: {e}")

    return taapi_snapshot


def run_once() -> None:
    if not HAS_TALIB:
        log("ERROR: TA-Lib not installed (import talib failed). Cannot compute own indicators.")
        return

    # 1. Load latest ENVY snapshot
    envy_snapshot = load_latest_envy_snapshot()
    if not envy_snapshot:
        log("WARN: No ENVY cache found — run envy_cache.py first")
        return

    log(f"Loaded ENVY snapshot for {len(envy_snapshot)} coins")

    # 2. Fetch TAAPI snapshot (best-effort — won't block on failure)
    taapi_snapshot = fetch_taapi_snapshot(DELTA_COINS)

    now = datetime.now(timezone.utc)
    ts  = now.strftime("%Y-%m-%dT%H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")
    delta_file = DELTA_DIR / f"{date_str}.jsonl"

    summaries: list[str] = []

    for coin in DELTA_COINS:
        envy_vals = envy_snapshot.get(coin, {})
        if not envy_vals:
            log(f"  WARN {coin}: not in ENVY snapshot")
            continue

        # 3. Compute own (TA-Lib) indicators from HL
        try:
            own_vals = compute_own_indicators(coin)
        except Exception as e:
            log(f"  ERROR {coin}: own computation failed: {e}")
            continue

        if not own_vals:
            log(f"  WARN {coin}: own computation returned empty")
            continue

        # 4. Get TAAPI values for this coin (may be empty if fetch failed)
        taapi_vals = taapi_snapshot.get(coin)

        # 5. Compute deltas (ENVY vs TA-Lib, plus optional TAAPI cross-check)
        deltas = compute_deltas(envy_vals, own_vals, taapi_vals)

        if not deltas:
            log(f"  WARN {coin}: no comparable indicators found")
            continue

        pcts = [d["pct"] for d in deltas.values()]
        avg_pct = sum(pcts) / len(pcts) if pcts else 0.0
        max_pct = max(pcts) if pcts else 0.0
        max_ind = max(deltas, key=lambda k: deltas[k]["pct"]) if deltas else ""

        # 6. Warn on large deltas
        for ind_code, d in deltas.items():
            if d["pct"] > WARNING_PCT:
                log(f"  WARNING {coin} {ind_code}: delta={d['delta']:.4f} ({d['pct']:.2f}%) exceeds {WARNING_PCT}% threshold")

        # 7. Write to JSONL
        record = {
            "t":                  ts,
            "coin":               coin,
            "deltas":             deltas,
            "avg_delta_pct":      round(avg_pct, 4),
            "max_delta_pct":      round(max_pct, 4),
            "max_delta_indicator": max_ind,
            "sources":            {
                "envy":  True,
                "talib": bool(own_vals),
                "taapi": bool(taapi_vals),
            },
        }
        append_jsonl(delta_file, record)

        summaries.append(f"{coin} avg_delta={avg_pct:.1f}% max={max_pct:.1f}% ({max_ind})")
        time.sleep(0.2)  # light rate limit between coins

    if summaries:
        log(" | ".join(summaries))
    else:
        log("No delta summaries produced")

    update_heartbeat("indicator_delta")


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

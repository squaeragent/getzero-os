#!/usr/bin/env python3
"""
ZERO OS — TAAPI-Powered Historical Signal Backtester
Uses TAAPI's `results` parameter to fetch 300 hourly candles (~12.5 days)
and replay signal entry/exit expressions against historical data.

Usage:
  python3 scanner/backtester_taapi.py                        # full backtest
  python3 scanner/backtester_taapi.py --coin BTC             # single coin
  python3 scanner/backtester_taapi.py --signal "WEIGHTED*"   # filter signals
  python3 scanner/backtester_taapi.py --top 20               # top 20 by P&L
  python3 scanner/backtester_taapi.py --coin BTC --top 10    # combined
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional

# ─── Paths ────────────────────────────────────────────────────────────────────
SCANNER_DIR    = Path(__file__).parent
DATA_DIR       = SCANNER_DIR / "data"
SIGNALS_CACHE  = DATA_DIR / "signals_cache"
BACKTEST_CACHE = DATA_DIR / "backtest_cache"
RESULTS_DIR    = DATA_DIR / "backtest_results"

# ─── TAAPI Config ─────────────────────────────────────────────────────────────
TAAPI_BASE    = "https://api.taapi.io"
EXCHANGE      = "binance"
INTERVAL      = "1h"
N_CANDLES     = 300            # ~12.5 days of hourly data
RATE_LIMIT_MS = 200            # ms between API calls
FEE_PCT       = 0.001          # 0.1% trading fee
SLIPPAGE_PCT  = 0.0005         # 0.05% slippage
STOP_LOSS_PCT = 0.08           # 8% stop-loss

# ─── Coins ────────────────────────────────────────────────────────────────────
CORE_COINS = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX",
    "LINK", "ARB", "NEAR", "SUI", "INJ",
]

# Expression operators (not indicator names)
EXPR_OPERATORS = {"AND", "OR", "NOT"}


# ─── API Key ──────────────────────────────────────────────────────────────────
def load_taapi_key() -> str:
    key = os.environ.get("TAAPI_API_KEY")
    if key:
        return key.strip().strip('"').strip("'")
    env_path = os.path.expanduser("~/getzero-os/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TAAPI_API_KEY="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("TAAPI_API_KEY not found in env or ~/getzero-os/.env")


# ─── TAAPI HTTP ───────────────────────────────────────────────────────────────
_call_count = 0


def taapi_get(endpoint: str, params: dict, secret: str) -> Any:
    """Single TAAPI GET request. Returns parsed JSON."""
    global _call_count
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{TAAPI_BASE}/{endpoint}?secret={secret}&{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            _call_count += 1
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        if e.code == 429:
            print(f"  [taapi] Rate limit hit (429). Sleeping 10s...")
            time.sleep(10)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    _call_count += 1
                    return json.loads(resp.read().decode())
            except Exception as retry_e:
                print(f"  [taapi] Retry failed: {retry_e}")
                return None
        else:
            print(f"  [taapi] HTTP {e.code}: {body[:100]}")
            return None
    except Exception as e:
        print(f"  [taapi] Request error: {e}")
        return None


def _sleep():
    """Respect 200ms rate limit between calls."""
    time.sleep(RATE_LIMIT_MS / 1000)


# ─── Data Fetching ────────────────────────────────────────────────────────────
# Indicators to fetch (maps TAAPI endpoint → list of param configs → result key(s))
# Each entry: (indicator_name_in_taapi_api, params_dict, result_key_name)
# For multi-value responses (MACD, BB) we extract multiple columns.

INDICATOR_SPECS = [
    # RSI
    ("rsi",    {"period": 14}, "rsi_14"),
    ("rsi",    {"period": 24}, "rsi_24"),
    # EMA
    ("ema",    {"period": 24}, "ema_24"),
    ("ema",    {"period": 6},  "ema_6"),
    ("ema",    {"period": 12}, "ema_12"),
    ("ema",    {"period": 48}, "ema_48"),
    # MACD
    ("macd",   {"fastPeriod": 12, "slowPeriod": 26, "signalPeriod": 9}, "macd_std"),
    ("macd",   {"fastPeriod": 6,  "slowPeriod": 12, "signalPeriod": 9}, "macd_6"),
    ("macd",   {"fastPeriod": 24, "slowPeriod": 52, "signalPeriod": 9}, "macd_24"),
    ("macd",   {"fastPeriod": 48, "slowPeriod": 104,"signalPeriod": 9}, "macd_48"),
    # BB
    ("bbands", {"period": 24}, "bb_24"),
    ("bbands", {"period": 6},  "bb_6"),
    ("bbands", {"period": 12}, "bb_12"),
    ("bbands", {"period": 48}, "bb_48"),
    # ROC
    ("roc",    {"period": 24}, "roc_24"),
    ("roc",    {"period": 3},  "roc_3"),
    ("roc",    {"period": 6},  "roc_6"),
    ("roc",    {"period": 12}, "roc_12"),
    ("roc",    {"period": 48}, "roc_48"),
    # ADX
    ("adx",    {"period": 14}, "adx_14"),
    # CMO
    ("cmo",    {"period": 14}, "cmo_14"),
    # MOM
    ("mom",    {"period": 6},  "mom_6"),
    ("mom",    {"period": 12}, "mom_12"),
    ("mom",    {"period": 24}, "mom_24"),
    ("mom",    {"period": 48}, "mom_48"),
    # ATR (for price estimation)
    ("atr",    {"period": 24}, "atr_24"),
]


def fetch_indicator_history(coin: str, secret: str) -> dict[str, list[float]]:
    """
    Fetch 300 hourly candles for each indicator.
    Returns dict: indicator_name → list of N values (newest first).
    Also returns 'close' as a synthetic price series derived from EMA_24.
    """
    symbol = f"{coin}/USDT"
    base_params = {
        "exchange": EXCHANGE,
        "symbol": symbol,
        "interval": INTERVAL,
        "results": N_CANDLES,
    }

    raw: dict[str, list] = {}

    for endpoint, extra_params, key in INDICATOR_SPECS:
        params = {**base_params, **extra_params}
        data = taapi_get(endpoint, params, secret)
        _sleep()

        if data is None:
            continue

        # TAAPI with results=N returns different formats:
        # Simple indicators: {"value": [v1, v2, ...]}
        # MACD: {"valueMACD": [...], "valueMACDSignal": [...], "valueMACDHist": [...]}
        # BBands: {"valueUpperBand": [...], "valueMiddleBand": [...], "valueLowerBand": [...]}
        # Ichimoku: {"conversion": [...], "base": [...], "spanA": [...], ...}
        if isinstance(data, dict):
            # Check if it has a "value" key with an array → simple indicator
            if "value" in data and isinstance(data["value"], list):
                raw[key] = data["value"]  # unwrap to plain array of floats
            elif "value" in data and isinstance(data["value"], (int, float)):
                raw[key] = [data["value"]]  # single value
            else:
                # Multi-value indicator (MACD, BBands, Ichimoku) — store as-is
                raw[key] = data
        elif isinstance(data, list):
            raw[key] = data
        else:
            continue

    return raw


def build_indicator_series(raw: dict[str, list], coin: str) -> dict[str, list[float]]:
    """
    Convert raw TAAPI response arrays to named series matching ENVY indicator names.
    Returns dict: envy_indicator_name → list[float] (index 0 = newest candle).
    Also builds 'close' series from EMA_24 as price proxy.
    """
    series: dict[str, list[float]] = {}

    # Helper: extract scalar from item
    def scalar(item: Any, key: str = "value") -> Optional[float]:
        if isinstance(item, dict):
            v = item.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        elif isinstance(item, (int, float)):
            return float(item)
        return None

    def extract_series(raw_key: str, extract_fn) -> list[float]:
        items = raw.get(raw_key, [])
        result = []
        for item in items:
            v = extract_fn(item)
            if v is not None:
                result.append(v)
        return result

    # RSI
    series["RSI_24H"]   = extract_series("rsi_24",  lambda x: scalar(x))
    series["RSI_6H"]    = extract_series("rsi_14",  lambda x: scalar(x))   # rsi_14 → RSI_6H (14 periods, 14h)
    series["RSI_12H"]   = extract_series("rsi_14",  lambda x: scalar(x))   # best proxy
    series["RSI_3H30M"] = extract_series("rsi_14",  lambda x: scalar(x))   # 14h proxy
    series["RSI_48H"]   = extract_series("rsi_24",  lambda x: scalar(x))   # 24h proxy

    # Cleaner RSI mappings
    series["RSI_24H"]   = extract_series("rsi_24",  lambda x: scalar(x))
    series["RSI_6H"]    = extract_series("rsi_6",   lambda x: scalar(x)) if "rsi_6" in raw else series.get("RSI_6H", [])
    series["RSI_12H"]   = extract_series("rsi_12",  lambda x: scalar(x)) if "rsi_12" in raw else []
    series["RSI_48H"]   = extract_series("rsi_48",  lambda x: scalar(x)) if "rsi_48" in raw else []

    # Use rsi_14 as fallback for 3H30M
    series["RSI_3H30M"] = extract_series("rsi_14",  lambda x: scalar(x)) if not series.get("RSI_3H30M") else series["RSI_3H30M"]

    # EMA (as ratio to price — we'll normalize later)
    ema_24_raw = extract_series("ema_24", lambda x: scalar(x))
    ema_6_raw  = extract_series("ema_6",  lambda x: scalar(x))
    ema_12_raw = extract_series("ema_12", lambda x: scalar(x))
    ema_48_raw = extract_series("ema_48", lambda x: scalar(x))

    # Use EMA_24 as price proxy
    close_series = ema_24_raw[:]
    series["_close"] = close_series  # internal price reference

    # EMA_N_* = ema / ema_24 (ratio)
    def ratio_series(num_series, den_series):
        result = []
        n = min(len(num_series), len(den_series))
        for i in range(n):
            d = den_series[i]
            if d and d != 0:
                result.append(num_series[i] / d)
            else:
                result.append(1.0)
        return result

    series["EMA_N_24H"] = [1.0] * len(ema_24_raw)   # self-ratio = 1.0 always (used as anchor)
    series["EMA_N_6H"]  = ratio_series(ema_6_raw,  ema_24_raw)
    series["EMA_N_12H"] = ratio_series(ema_12_raw, ema_24_raw)
    series["EMA_N_48H"] = ratio_series(ema_48_raw, ema_24_raw)
    series["EMA_3H_N"]  = series["EMA_N_6H"]    # alias
    series["EMA_6H30M_N"] = series["EMA_N_12H"] # alias (13h ≈ 12h)

    # EMA_CROSS_15M_N — use EMA_6/EMA_12 cross as proxy
    cross = []
    n = min(len(ema_6_raw), len(ema_12_raw))
    for i in range(n):
        d = ema_12_raw[i]
        if d and d != 0:
            cross.append((ema_6_raw[i] - d) / d * 100)
        else:
            cross.append(0.0)
    series["EMA_CROSS_15M_N"] = cross

    # MACD_N_* = macd_value / price
    def macd_series(raw_key, price_series, value_key="valueMACD", hist_key="valueMACDHist"):
        items = raw.get(raw_key, {})
        macd_vals, hist_vals = [], []
        # TAAPI with results=N returns {valueMACD: [...], valueMACDHist: [...]} — parallel arrays
        if isinstance(items, dict) and value_key in items:
            macd_arr = items.get(value_key, [])
            hist_arr = items.get(hist_key, [])
            for i in range(len(macd_arr)):
                v = macd_arr[i] if i < len(macd_arr) else None
                h = hist_arr[i] if i < len(hist_arr) else None
                macd_vals.append(float(v) if v is not None else None)
                hist_vals.append(float(h) if h is not None else None)
        elif isinstance(items, list):
            # Fallback: array of dicts format
            for item in items:
                if isinstance(item, dict):
                    v = item.get(value_key)
                    h = item.get(hist_key)
                    macd_vals.append(float(v) if v is not None else None)
                    hist_vals.append(float(h) if h is not None else None)
        n = min(len(macd_vals), len(price_series))
        macd_norm, hist_norm = [], []
        for i in range(n):
            p = price_series[i]
            mv = macd_vals[i] if i < len(macd_vals) else None
            hv = hist_vals[i] if i < len(hist_vals) else None
            if mv is not None and p and p != 0:
                macd_norm.append(mv / p)
            else:
                macd_norm.append(0.0)
            if hv is not None and p and p != 0:
                hist_norm.append(hv / p)
            else:
                hist_norm.append(0.0)
        return macd_norm, hist_norm

    for raw_k, envy_k in [("macd_std", "MACD_N_12H"), ("macd_6", "MACD_N_6H"),
                            ("macd_24", "MACD_N_24H"), ("macd_48", "MACD_N_48H")]:
        m_vals, h_vals = macd_series(raw_k, close_series)
        series[envy_k] = m_vals
        series[f"{envy_k}_HIST"] = h_vals

    # MACD aliases
    series["MACD_6H30M_N"]      = series.get("MACD_N_12H", [])
    series["MACD_CROSS_15M_N"]  = series.get("MACD_N_6H_HIST", [])
    series["MACD_SIGNAL_2H15M_N"] = series.get("MACD_N_6H", [])

    # BB_POS_* = (mid - lower) / (upper - lower)
    def bb_pos_series(raw_key):
        items = raw.get(raw_key, {})
        result = []
        # TAAPI returns parallel arrays: {valueUpperBand: [...], valueLowerBand: [...], valueMiddleBand: [...]}
        if isinstance(items, dict) and "valueUpperBand" in items:
            upper = items.get("valueUpperBand", [])
            lower = items.get("valueLowerBand", [])
            middle = items.get("valueMiddleBand", [])
            for i in range(min(len(upper), len(lower), len(middle))):
                try:
                    u, l, m = float(upper[i]), float(lower[i]), float(middle[i])
                    band = u - l
                    result.append((m - l) / band if band > 0 else 0.5)
                except (TypeError, ValueError):
                    result.append(0.5)
        elif isinstance(items, list):
            # Fallback: array of dicts
            for item in items:
                if isinstance(item, dict):
                    u = item.get("valueUpperBand")
                    l = item.get("valueLowerBand")
                    m = item.get("valueMiddleBand")
                    if u is not None and l is not None and m is not None:
                        try:
                            band = float(u) - float(l)
                            result.append((float(m) - float(l)) / band if band > 0 else 0.5)
                        except (TypeError, ValueError):
                            result.append(0.5)
        return result

    series["BB_POS_24H"]      = bb_pos_series("bb_24")
    series["BB_POS_6H"]       = bb_pos_series("bb_6")
    series["BB_POS_12H"]      = bb_pos_series("bb_12")
    series["BB_POS_48H"]      = bb_pos_series("bb_48")
    series["BB_POSITION_15M"] = bb_pos_series("bb_24")  # use 24h as proxy

    # BB_UPPER_5H_N and BB_LOWER_5H_N — ratio to close
    def bb_ratio_series(raw_key, band_key, price_series):
        items = raw.get(raw_key, [])
        result = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                v = item.get(band_key)
                p = price_series[i] if i < len(price_series) else None
                if v is not None and p and p != 0:
                    try:
                        result.append(float(v) / float(p))
                    except (TypeError, ValueError):
                        result.append(1.0)
        return result

    series["BB_UPPER_5H_N"] = bb_ratio_series("bb_6", "valueUpperBand", close_series)
    series["BB_LOWER_5H_N"] = bb_ratio_series("bb_6", "valueLowerBand", close_series)

    # ROC
    series["ROC_24H"] = extract_series("roc_24", lambda x: scalar(x))
    series["ROC_3H"]  = extract_series("roc_3",  lambda x: scalar(x))
    series["ROC_6H"]  = extract_series("roc_6",  lambda x: scalar(x))
    series["ROC_12H"] = extract_series("roc_12", lambda x: scalar(x))
    series["ROC_48H"] = extract_series("roc_48", lambda x: scalar(x))

    # ADX
    def adx_series(raw_key):
        items = raw.get(raw_key, [])
        result = []
        for item in items:
            if isinstance(item, dict):
                v = item.get("value") or item.get("adx")
                if v is not None:
                    try:
                        result.append(float(v))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(item, (int, float)):
                result.append(float(item))
        return result

    series["ADX_3H30M"] = adx_series("adx_14")

    # CMO
    series["CMO_3H30M"] = extract_series("cmo_14", lambda x: scalar(x))

    # MOMENTUM_N_* = mom / price
    def mom_norm_series(raw_key, price_series):
        items = raw.get(raw_key, [])
        result = []
        n = min(len(items), len(price_series))
        for i in range(n):
            item = items[i]
            p = price_series[i]
            v = scalar(item)
            if v is not None and p and p != 0:
                result.append(v / p)
            else:
                result.append(0.0)
        return result

    series["MOMENTUM_N_6H"]   = mom_norm_series("mom_6",  close_series)
    series["MOMENTUM_N_12H"]  = mom_norm_series("mom_12", close_series)
    series["MOMENTUM_N_24H"]  = mom_norm_series("mom_24", close_series)
    series["MOMENTUM_N_48H"]  = mom_norm_series("mom_48", close_series)
    series["MOMENTUM_2H30M_N"] = series["MOMENTUM_N_6H"]   # alias

    # Remove empty series
    series = {k: v for k, v in series.items() if v}
    return series


# ─── Cache I/O ────────────────────────────────────────────────────────────────
def cache_path(coin: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return BACKTEST_CACHE / f"{coin}_{today}.json"


def load_cache(coin: str) -> Optional[dict]:
    p = cache_path(coin)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_cache(coin: str, series: dict) -> None:
    BACKTEST_CACHE.mkdir(parents=True, exist_ok=True)
    p = cache_path(coin)
    # Convert lists to JSON-serializable form
    with open(p, "w") as f:
        json.dump(series, f)


# ─── Signal Loading ───────────────────────────────────────────────────────────
def load_signals(coins: list[str], signal_filter: Optional[str] = None) -> dict[str, list[dict]]:
    """Load signal packs for the given coins. Optionally filter by name pattern."""
    result = {}
    for coin in coins:
        f = SIGNALS_CACHE / f"{coin}.json"
        if not f.exists():
            continue
        try:
            signals = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        if signal_filter:
            signals = [s for s in signals if fnmatch(s.get("name", ""), signal_filter)]
        result[coin] = signals
    return result


def extract_indicators_from_expr(expression: str) -> set[str]:
    """Extract indicator names from an expression string."""
    tokens = re.findall(r'[A-Z][A-Z0-9_]+', expression)
    return {t for t in tokens if t not in EXPR_OPERATORS}


# ─── Evaluate Expression ──────────────────────────────────────────────────────
def _evaluate_weighted(expression: str, values: dict[str, float]) -> tuple[bool, list[str]]:
    """Evaluate weighted sum expressions like: ((IND OP VAL) * W) + ... >= THRESHOLD"""
    missing = []
    threshold_match = re.search(r'\)\s*(>=|>|<=|<)\s*(-?[\d.]+)\s*$', expression)
    if not threshold_match:
        return False, missing
    threshold_op = threshold_match.group(1)
    threshold_val = float(threshold_match.group(2))

    terms = re.findall(
        r'\(\(([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)\)\s*\*\s*([\d.]+)\)',
        expression
    )
    if not terms:
        return False, missing

    weighted_sum = 0.0
    for indicator, op, val_str, weight_str in terms:
        val = float(val_str)
        weight = float(weight_str)
        current = values.get(indicator)
        if current is None:
            missing.append(indicator)
            continue
        cond = False
        if   op == ">=": cond = current >= val
        elif op == "<=": cond = current <= val
        elif op == ">":  cond = current > val
        elif op == "<":  cond = current < val
        elif op == "==": cond = current == val
        elif op == "!=": cond = current != val
        if cond:
            weighted_sum += weight

    if   threshold_op == ">=": result = weighted_sum >= threshold_val
    elif threshold_op == ">":  result = weighted_sum > threshold_val
    elif threshold_op == "<=": result = weighted_sum <= threshold_val
    elif threshold_op == "<":  result = weighted_sum < threshold_val
    else: result = False
    return result, missing


def evaluate_expression(expression: str, values: dict[str, float]) -> tuple[bool, list[str]]:
    """
    Evaluate a signal expression against indicator values at one candle.
    Returns (fired: bool, missing_indicators: list[str]).
    """
    if not expression or not expression.strip():
        return False, []

    if "((" in expression and "*" in expression:
        return _evaluate_weighted(expression, values)

    missing = []
    clauses = re.split(r'\s+(AND|OR)\s+', expression)
    results = []
    operators = []

    for part in clauses:
        part = part.strip()
        if part in ("AND", "OR"):
            operators.append(part)
            continue

        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            results.append(False)
            continue

        indicator, op, val_str = m.group(1), m.group(2), m.group(3)
        val = float(val_str)
        current = values.get(indicator)
        if current is None:
            missing.append(indicator)
            results.append(False)
            continue

        if   op == ">=": results.append(current >= val)
        elif op == "<=": results.append(current <= val)
        elif op == ">":  results.append(current > val)
        elif op == "<":  results.append(current < val)
        elif op == "==": results.append(current == val)
        elif op == "!=": results.append(current != val)
        else:            results.append(False)

    if not results:
        return False, missing

    final = results[0]
    for i, op in enumerate(operators):
        if i + 1 < len(results):
            if op == "AND":
                final = final and results[i + 1]
            elif op == "OR":
                final = final or results[i + 1]
    return final, missing


# ─── Signal Testability ───────────────────────────────────────────────────────
# Indicators we CAN evaluate (from TAAPI)
TAAPI_AVAILABLE = {
    # Standard TA — from TAAPI
    "RSI_6H", "RSI_12H", "RSI_24H", "RSI_48H", "RSI_3H30M",
    "EMA_N_6H", "EMA_N_12H", "EMA_N_24H", "EMA_N_48H",
    "EMA_3H_N", "EMA_6H30M_N", "EMA_CROSS_15M_N",
    "MACD_N_6H", "MACD_N_12H", "MACD_N_24H", "MACD_N_48H",
    "MACD_6H30M_N", "MACD_CROSS_15M_N", "MACD_SIGNAL_2H15M_N",
    "BB_POS_6H", "BB_POS_12H", "BB_POS_24H", "BB_POS_48H",
    "BB_POSITION_15M", "BB_UPPER_5H_N", "BB_LOWER_5H_N",
    "ROC_3H", "ROC_6H", "ROC_12H", "ROC_24H", "ROC_48H",
    "ADX_3H30M", "CMO_3H30M",
    "MOMENTUM_N_6H", "MOMENTUM_N_12H", "MOMENTUM_N_24H", "MOMENTUM_N_48H",
    "MOMENTUM_2H30M_N",
    # Ichimoku — from TAAPI
    "ICHIMOKU_BULL", "CLOUD_POSITION_15M",
    "KIJUN_6H30M_N", "TENKAN_2H15M_N",
    "SENKOU_A_6H30M_N", "SENKOU_B_13H_N",
    "TENKAN_KIJUN_CROSS_15M_N",
    # Price & ATR — from TAAPI/HL
    "CLOSE_PRICE_15M", "ATR_24H",
    # Chaos indicators — computed ourselves from HL candles
    "HURST_24H", "HURST_48H",
    "DFA_24H", "DFA_48H",
    "LYAPUNOV_24H", "LYAPUNOV_48H",
    # Doji — ENVY-only, but available via cache (mark as testable, will use cached values)
    "DOJI_VELOCITY", "DOJI_VELOCITY_L", "DOJI_DISTANCE", "DOJI_DISTANCE_L",
    "DOJI_SIGNAL", "DOJI_SIGNAL_L",
    # BTC correlation — can compute from HL candles
    "BTC_CORR_7D", "BTC_CORR_7D_DELTA",
}


def is_signal_testable(signal: dict) -> tuple[bool, set[str]]:
    """
    Returns (testable, missing_indicators).
    A signal is testable if ALL indicators in its entry expression are TAAPI-available.
    """
    expression = signal.get("entry_expression", "") or signal.get("expression", "")
    exit_expr  = signal.get("exit_expression", "")
    required = extract_indicators_from_expr(expression)
    required |= extract_indicators_from_expr(exit_expr)
    missing = required - TAAPI_AVAILABLE
    return len(missing) == 0, missing


# ─── Signal Replay ────────────────────────────────────────────────────────────
def replay_signal(
    signal: dict,
    series: dict[str, list[float]],
) -> list[dict]:
    """
    Replay one signal across all historical candles.
    series: indicator_name → list[float], index 0 = newest, index N-1 = oldest.
    We iterate oldest → newest (reverse), looking for entries.
    Returns list of trade dicts.
    """
    entry_expr = signal.get("expression", "")
    exit_expr  = signal.get("exit_expression", "")
    direction  = signal.get("signal_type", "LONG")
    max_hold_h = signal.get("max_hold_hours") or 48

    close_series = series.get("_close", [])
    if not close_series:
        return []

    n = len(close_series)

    # Find min length across all required series
    required_inds = extract_indicators_from_expr(entry_expr) | extract_indicators_from_expr(exit_expr)
    avail_inds = required_inds & TAAPI_AVAILABLE
    if not avail_inds:
        return []

    min_len = n
    for ind in avail_inds:
        s = series.get(ind, [])
        if s:
            min_len = min(min_len, len(s))
    
    if min_len < 2:
        return []

    trades = []
    in_trade = False
    entry_price = 0.0
    entry_bar = 0
    max_hold_bars = min(max_hold_h, n - 1)

    # Iterate oldest → newest (index n-1 → 0)
    for i in range(min_len - 1, 0, -1):
        # Snapshot at candle i
        snap: dict[str, float] = {}
        for ind_name, ind_series in series.items():
            if ind_name.startswith("_"):
                continue
            if i < len(ind_series):
                snap[ind_name] = ind_series[i]

        price = close_series[i] if i < len(close_series) else 0.0
        if not price or price <= 0:
            continue

        if not in_trade:
            fired, _ = evaluate_expression(entry_expr, snap)
            if fired:
                # Enter trade — apply slippage
                if direction == "LONG":
                    entry_price = price * (1 + SLIPPAGE_PCT)
                else:
                    entry_price = price * (1 - SLIPPAGE_PCT)
                entry_bar = i
                in_trade = True
        else:
            hold_bars = entry_bar - i  # i decreases as we go newer
            exited = False
            exit_price = price
            exit_reason = ""

            # Check exit expression
            if exit_expr:
                exit_fired, _ = evaluate_expression(exit_expr, snap)
                if exit_fired:
                    exit_price = price
                    exit_reason = "exit_expr"
                    exited = True

            # Check stop-loss
            if not exited:
                if direction == "LONG":
                    pct_move = (price - entry_price) / entry_price
                else:
                    pct_move = (entry_price - price) / entry_price
                if pct_move <= -STOP_LOSS_PCT:
                    exit_price = price
                    exit_reason = "stop_loss"
                    exited = True

            # Check max hold
            if not exited and hold_bars >= max_hold_bars:
                exit_price = price
                exit_reason = "max_hold"
                exited = True

            if exited:
                # Apply slippage on exit
                if direction == "LONG":
                    exit_price_final = exit_price * (1 - SLIPPAGE_PCT)
                    raw_pnl = (exit_price_final - entry_price) / entry_price
                else:
                    exit_price_final = exit_price * (1 + SLIPPAGE_PCT)
                    raw_pnl = (entry_price - exit_price_final) / entry_price

                # Subtract fees (entry + exit)
                net_pnl = raw_pnl - (FEE_PCT * 2)
                net_pnl_pct = net_pnl * 100

                trades.append({
                    "entry_bar": entry_bar,
                    "exit_bar": i,
                    "hold_bars": hold_bars,
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(exit_price, 6),
                    "pnl_pct": round(net_pnl_pct, 4),
                    "exit_reason": exit_reason,
                    "direction": direction,
                })
                in_trade = False

    return trades


# ─── Aggregate Results ────────────────────────────────────────────────────────
def aggregate_signal_result(signal: dict, trades: list[dict]) -> dict:
    """Compute per-signal backtest stats."""
    if not trades:
        return {}
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    total_pnl = sum(t["pnl_pct"] for t in trades)
    hold_times = [t["hold_bars"] for t in trades]

    return {
        "signal": signal.get("name", "unknown"),
        "direction": signal.get("signal_type", "LONG"),
        "sharpe_orig": round(signal.get("sharpe", 0), 4),
        "win_rate_orig": round(signal.get("win_rate", 0), 2),
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "total_pnl_pct": round(total_pnl, 3),
        "avg_pnl_pct": round(total_pnl / n, 4) if n else 0,
        "max_pnl_pct": round(max(t["pnl_pct"] for t in trades), 4),
        "min_pnl_pct": round(min(t["pnl_pct"] for t in trades), 4),
        "avg_hold_bars": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0,
        "avg_hold_hours": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0,  # 1 bar = 1h
    }


# ─── Main Backtest ────────────────────────────────────────────────────────────
def run_backtest(
    coins: list[str],
    signal_filter: Optional[str],
    top_n: Optional[int],
    secret: str,
) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*65}")
    print(f"TAAPI Backtester — {today}")
    print(f"Period: {N_CANDLES} hourly candles (~12.5 days)")
    print(f"Coins: {', '.join(coins)}")
    print(f"{'='*65}\n")

    # Load signals
    all_signals = load_signals(coins, signal_filter)
    total_signals = sum(len(v) for v in all_signals.values())
    print(f"Loaded {total_signals} signals across {len(all_signals)} coins\n")

    # Check testability
    testable_count = 0
    skipped_count  = 0
    skip_reasons: dict[str, int] = {}

    for coin, sigs in all_signals.items():
        for sig in sigs:
            ok, missing = is_signal_testable(sig)
            if ok:
                testable_count += 1
            else:
                skipped_count += 1
                for ind in missing:
                    skip_reasons[ind] = skip_reasons.get(ind, 0) + 1

    print(f"Testable signals:  {testable_count}")
    print(f"Skipped signals:   {skipped_count} (indicators unavailable from TAAPI)")
    if skip_reasons:
        top_missing = sorted(skip_reasons.items(), key=lambda x: -x[1])[:10]
        print(f"Top missing indicators: {', '.join(f'{k}({v})' for k,v in top_missing)}")
    print()

    # Fetch / load cached data
    coin_series: dict[str, dict[str, list[float]]] = {}

    for coin in coins:
        cached = load_cache(coin)
        if cached is not None:
            print(f"  {coin}: loaded from cache ({len(cached)} indicator series)")
            coin_series[coin] = cached
        else:
            print(f"  {coin}: fetching from TAAPI ({len(INDICATOR_SPECS)} calls)...", end=" ", flush=True)
            raw = fetch_indicator_history(coin, secret)
            if not raw:
                print("FAILED — no data returned")
                continue
            series = build_indicator_series(raw, coin)
            # Report candle count
            n_candles = max((len(v) for v in series.values()), default=0)
            print(f"OK ({n_candles} candles, {len(series)} series)")
            save_cache(coin, series)
            coin_series[coin] = series
            _sleep()

    print(f"\nAPI calls used: {_call_count}")
    print(f"\nReplaying signals...\n")

    # Replay
    per_signal_results: list[dict] = []
    per_coin_trades: dict[str, list[dict]] = {c: [] for c in coins}
    trades_total = 0
    signals_with_trades = 0

    for coin in coins:
        series = coin_series.get(coin)
        if series is None:
            continue

        sigs = all_signals.get(coin, [])
        coin_results = []

        for sig in sigs:
            ok, _ = is_signal_testable(sig)
            if not ok:
                continue

            trades = replay_signal(sig, series)
            if not trades:
                continue

            agg = aggregate_signal_result(sig, trades)
            if not agg:
                continue

            agg["coin"] = coin
            coin_results.append(agg)
            per_signal_results.append(agg)
            per_coin_trades[coin].extend(trades)
            trades_total += len(trades)
            signals_with_trades += 1

        if coin_results:
            best = max(coin_results, key=lambda x: x["avg_pnl_pct"])
            worst = min(coin_results, key=lambda x: x["avg_pnl_pct"])
            coin_pnl = sum(t["pnl_pct"] for t in per_coin_trades[coin])
            coin_wins = sum(1 for t in per_coin_trades[coin] if t["pnl_pct"] > 0)
            coin_n = len(per_coin_trades[coin])
            print(f"  {coin:6s}: {len(coin_results):3d} signals | {coin_n:3d} trades | "
                  f"wr={coin_wins/coin_n*100:.0f}% | pnl={coin_pnl:+.2f}% | "
                  f"best={best['signal'][:25]}")

    # Aggregate global stats
    all_trade_pnls = [t["pnl_pct"] for coin in coins for t in per_coin_trades.get(coin, [])]
    n_total = len(all_trade_pnls)
    n_wins = sum(1 for p in all_trade_pnls if p > 0)
    total_pnl = sum(all_trade_pnls)

    win_rate = round(n_wins / n_total * 100, 1) if n_total else 0
    avg_pnl  = round(total_pnl / n_total, 4) if n_total else 0
    avg_hold = 0.0
    if per_signal_results:
        avg_hold = round(
            sum(r["avg_hold_hours"] for r in per_signal_results) / len(per_signal_results), 1
        )

    # Best/worst signals
    per_signal_results.sort(key=lambda x: x["avg_pnl_pct"], reverse=True)
    best_signal  = per_signal_results[0]["signal"]  if per_signal_results else "N/A"
    worst_signal = per_signal_results[-1]["signal"] if per_signal_results else "N/A"

    # Per-coin summary
    per_coin_summary: dict[str, dict] = {}
    for coin in coins:
        trades = per_coin_trades.get(coin, [])
        if not trades:
            continue
        n = len(trades)
        w = sum(1 for t in trades if t["pnl_pct"] > 0)
        pnl = sum(t["pnl_pct"] for t in trades)
        per_coin_summary[coin] = {
            "trades": n,
            "wins": w,
            "losses": n - w,
            "win_rate": round(w / n * 100, 1) if n else 0,
            "total_pnl": round(pnl, 3),
            "avg_pnl": round(pnl / n, 4) if n else 0,
        }

    # Build output
    output = {
        "run_date": today,
        "period": f"~12.5 days ({N_CANDLES} hourly candles)",
        "coins_tested": len([c for c in coins if c in coin_series]),
        "signals_total": total_signals,
        "signals_testable": testable_count,
        "signals_skipped": skipped_count,
        "signals_with_trades": signals_with_trades,
        "trades_simulated": trades_total,
        "api_calls_used": _call_count,
        "results": {
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "total_pnl_pct": round(total_pnl, 3),
            "avg_hold_hours": avg_hold,
            "best_signal": best_signal,
            "worst_signal": worst_signal,
        },
        "per_coin": per_coin_summary,
        "per_signal": per_signal_results,
    }

    # Write results
    out_file = RESULTS_DIR / f"taapi_backtest_{today}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)

    return output


# ─── Display ─────────────────────────────────────────────────────────────────
def display_results(output: dict, top_n: Optional[int] = None) -> None:
    r = output["results"]

    print(f"\n{'='*65}")
    print(f"BACKTEST RESULTS — {output['run_date']}")
    print(f"Period:  {output['period']}")
    print(f"Coins:   {output['coins_tested']} tested")
    print(f"Signals: {output['signals_total']} total | "
          f"{output['signals_testable']} testable | "
          f"{output['signals_skipped']} skipped")
    print(f"Trades:  {output['trades_simulated']} simulated")
    print(f"{'='*65}")

    print(f"\n📊 OVERALL PERFORMANCE")
    print(f"  Win Rate:       {r['win_rate']:.1f}%")
    print(f"  Avg P&L/trade:  {r['avg_pnl_pct']:+.4f}%")
    print(f"  Total P&L:      {r['total_pnl_pct']:+.3f}%")
    print(f"  Avg Hold:       {r['avg_hold_hours']:.1f}h")
    print(f"  Best signal:    {r['best_signal']}")
    print(f"  Worst signal:   {r['worst_signal']}")

    print(f"\n🪙 PER-COIN SUMMARY")
    for coin, stats in sorted(output.get("per_coin", {}).items()):
        print(f"  {coin:6s}  trades={stats['trades']:3d}  "
              f"wr={stats['win_rate']:5.1f}%  "
              f"pnl={stats['total_pnl']:+6.2f}%")

    signals = output.get("per_signal", [])
    if top_n:
        signals = signals[:top_n]

    print(f"\n🏆 TOP {len(signals)} SIGNALS BY AVG P&L")
    print(f"  {'Signal':<45} {'Coin':<6} {'Dir':<5} {'Trades':>6} {'WR':>5} {'AvgP&L':>8} {'TotP&L':>8}")
    print(f"  {'-'*45} {'-'*6} {'-'*5} {'-'*6} {'-'*5} {'-'*8} {'-'*8}")
    for s in signals:
        print(f"  {s['signal'][:45]:<45} {s.get('coin','?'):<6} {s['direction']:<5} "
              f"{s['trades']:>6} {s['win_rate']:>4.0f}% {s['avg_pnl_pct']:>+7.3f}% "
              f"{s['total_pnl_pct']:>+7.2f}%")

    print(f"\n✅ Results saved to: scanner/data/backtest_results/taapi_backtest_{output['run_date']}.json")
    print(f"   API calls used: {output.get('api_calls_used', '?')} / 150,000 daily limit")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args() -> tuple[list[str], Optional[str], Optional[int]]:
    args = sys.argv[1:]
    coins = CORE_COINS[:]
    signal_filter = None
    top_n = None

    i = 0
    while i < len(args):
        if args[i] == "--coin" and i + 1 < len(args):
            coins = [args[i + 1].upper()]
            i += 2
        elif args[i] == "--signal" and i + 1 < len(args):
            signal_filter = args[i + 1]
            i += 2
        elif args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])
            i += 2
        elif args[i] == "--help":
            print(__doc__)
            sys.exit(0)
        else:
            i += 1

    return coins, signal_filter, top_n


def main():
    coins, signal_filter, top_n = parse_args()

    # Limit coins to those that have signal cache files
    available_coins = {f.stem for f in SIGNALS_CACHE.glob("*.json")}
    coins = [c for c in coins if c in available_coins]

    if not coins:
        print(f"ERROR: No signal cache found for requested coins.")
        print(f"Available: {', '.join(sorted(available_coins))}")
        sys.exit(1)

    secret = load_taapi_key()
    output = run_backtest(coins, signal_filter, top_n, secret)
    display_results(output, top_n)


if __name__ == "__main__":
    main()

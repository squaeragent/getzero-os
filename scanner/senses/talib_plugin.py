"""
ZERO OS — TA-Lib SensePlugin.

Computes technical indicators from local OHLCV history files using TA-Lib.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scanner.core.interfaces import Observation
from scanner.senses.base import SensePlugin

HISTORY_DIR = Path(__file__).parent.parent / "data" / "history"

try:
    import talib
    import numpy as np
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def _load_ohlcv(coin: str) -> dict | None:
    """Load OHLCV data from {coin}_1h.json, return arrays or None."""
    path = HISTORY_DIR / f"{coin}_1h.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    candles = data.get("candles", [])
    if len(candles) < 50:
        return None

    o = np.array([c["o"] for c in candles], dtype=np.float64)
    h = np.array([c["h"] for c in candles], dtype=np.float64)
    l = np.array([c["l"] for c in candles], dtype=np.float64)
    c = np.array([c["c"] for c in candles], dtype=np.float64)
    v = np.array([c["v"] for c in candles], dtype=np.float64)
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


class TalibPlugin(SensePlugin):
    """Computes technical indicators using TA-Lib on local history data."""

    name = "talib"

    def fetch(self, coins: list[str]) -> list[Observation]:
        if not _TALIB_AVAILABLE:
            return []

        now = time.time()
        observations: list[Observation] = []

        for coin in coins:
            ohlcv = _load_ohlcv(coin)
            if ohlcv is None:
                continue

            close = ohlcv["close"]
            high = ohlcv["high"]
            low = ohlcv["low"]

            indicators: dict[str, float] = {}

            # RSI(14)
            rsi = talib.RSI(close, timeperiod=14)
            if len(rsi) > 0 and not np.isnan(rsi[-1]):
                indicators["RSI_14"] = round(float(rsi[-1]), 4)

            # EMA(20) and EMA(50)
            ema20 = talib.EMA(close, timeperiod=20)
            ema50 = talib.EMA(close, timeperiod=50)
            if len(ema20) > 0 and not np.isnan(ema20[-1]):
                indicators["EMA_20"] = round(float(ema20[-1]), 4)
            if len(ema50) > 0 and not np.isnan(ema50[-1]):
                indicators["EMA_50"] = round(float(ema50[-1]), 4)

            # MACD
            macd, macd_signal, macd_hist = talib.MACD(close)
            if len(macd_hist) > 0 and not np.isnan(macd_hist[-1]):
                indicators["MACD_HIST"] = round(float(macd_hist[-1]), 4)
                indicators["MACD"] = round(float(macd[-1]), 4)
                indicators["MACD_SIGNAL"] = round(float(macd_signal[-1]), 4)

            # ATR(14)
            atr = talib.ATR(high, low, close, timeperiod=14)
            if len(atr) > 0 and not np.isnan(atr[-1]):
                indicators["ATR_14"] = round(float(atr[-1]), 4)

            # Bollinger Bands
            upper, middle, lower = talib.BBANDS(close, timeperiod=20)
            if len(upper) > 0 and not np.isnan(upper[-1]):
                indicators["BB_UPPER"] = round(float(upper[-1]), 4)
                indicators["BB_MIDDLE"] = round(float(middle[-1]), 4)
                indicators["BB_LOWER"] = round(float(lower[-1]), 4)
                band_width = upper[-1] - lower[-1]
                if band_width > 0:
                    indicators["BB_POSITION"] = round(
                        float((close[-1] - lower[-1]) / band_width), 4
                    )

            # ADX(14)
            adx = talib.ADX(high, low, close, timeperiod=14)
            if len(adx) > 0 and not np.isnan(adx[-1]):
                indicators["ADX_14"] = round(float(adx[-1]), 4)

            for ind_name, value in indicators.items():
                observations.append(Observation(
                    coin=coin,
                    dimension=f"talib.{ind_name}",
                    value=value,
                    confidence=1.0,
                    source="talib",
                    timestamp=now,
                ))

        return observations

    def health_check(self) -> dict:
        if not _TALIB_AVAILABLE:
            return {"name": self.name, "status": "unavailable"}
        return {"name": self.name, "status": "ok"}

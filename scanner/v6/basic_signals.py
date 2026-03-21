#!/usr/bin/env python3
"""
Basic Signal Engine — local technical analysis using FREE HL WebSocket prices.

Computes RSI(14), EMA(9/21), MACD(12,26,9), Bollinger Bands(20,2) locally.
No API key required. Lower quality (5/10 vs 10/10) but works when API is down.

HL WebSocket: wss://api.hyperliquid.xyz/ws (FREE, no auth)
HL REST for funding: https://api.hyperliquid.xyz/info (FREE, no auth)
"""

import json
import math
import time
import urllib.request
from datetime import datetime, timezone

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [BASIC] {msg}", flush=True)


def _hl_info_post(payload: dict) -> dict:
    """POST to HL info API (free, no auth)."""
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


# ─── INDICATOR MATH ──────────────────────────────────────────────────────────

def compute_rsi(closes: list[float], period: int = 14) -> float:
    """RSI(period) from close prices."""
    if len(closes) < period + 1:
        return 50.0  # neutral default
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    # Wilder's smoothed RSI
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_ema(values: list[float], period: int) -> list[float]:
    """EMA series from values."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram) latest values."""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line[slow - 1:], signal)
    if not signal_line:
        return macd_line[-1], 0.0, macd_line[-1]
    histogram = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], histogram


def compute_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    """Returns (upper, middle, lower, %b) for latest value."""
    if len(closes) < period:
        price = closes[-1] if closes else 0
        return price, price, price, 0.5
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = math.sqrt(variance)
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    band_width = upper - lower
    pct_b = (closes[-1] - lower) / band_width if band_width > 0 else 0.5
    return upper, middle, lower, pct_b


# ─── PRICE FETCHING ──────────────────────────────────────────────────────────

def fetch_candles(coin: str, interval: str = "15m", limit: int = 100) -> list[dict]:
    """Fetch candles from HL REST API (free).

    Returns list of {t, o, h, l, c, v} dicts.
    """
    # HL candleSnapshot endpoint
    end_ms = int(time.time() * 1000)
    # interval_ms lookup
    interval_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000,
        "1h": 3_600_000, "4h": 14_400_000,
    }.get(interval, 900_000)
    start_ms = end_ms - (limit * interval_ms)

    payload = {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
    }
    try:
        data = _hl_info_post(payload)
        if not isinstance(data, list):
            return []
        candles = []
        for c in data:
            candles.append({
                "t": c.get("t", 0),
                "o": float(c.get("o", 0)),
                "h": float(c.get("h", 0)),
                "l": float(c.get("l", 0)),
                "c": float(c.get("c", 0)),
                "v": float(c.get("v", 0)),
            })
        return candles
    except Exception as e:
        _log(f"candle fetch failed for {coin}: {e}")
        return []


def fetch_mid_price(coin: str) -> float:
    """Get current mid price from HL (free)."""
    try:
        data = _hl_info_post({"type": "allMids"})
        return float(data.get(coin, 0))
    except Exception:
        return 0.0


def fetch_funding_rate(coin: str) -> float:
    """Get predicted funding rate from HL (free)."""
    try:
        data = _hl_info_post({"type": "predictedFundings"})
        for item in data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == coin:
                return float(item[1].get("predictedFundingRate", 0))
        return 0.0
    except Exception:
        return 0.0


# ─── SIGNAL GENERATION ───────────────────────────────────────────────────────

class BasicSignalEngine:
    """Local technical analysis engine using FREE HL data."""

    def compute_signals(self, coin: str) -> dict:
        """Compute all indicators for a coin. Returns dict matching NVProtocol format."""
        candles = fetch_candles(coin, "15m", 100)
        if len(candles) < 30:
            return {"coin": coin, "error": "insufficient_data", "signal": "NEUTRAL"}

        closes = [c["c"] for c in candles]
        highs = [c["h"] for c in candles]
        lows = [c["l"] for c in candles]
        volumes = [c["v"] for c in candles]
        price = closes[-1]

        # Indicators
        rsi = compute_rsi(closes, 14)
        ema9 = compute_ema(closes, 9)
        ema21 = compute_ema(closes, 21)
        macd_line, macd_signal, macd_hist = compute_macd(closes)
        bb_upper, bb_mid, bb_lower, bb_pct = compute_bollinger(closes)
        funding = fetch_funding_rate(coin)

        # EMA crossover detection
        ema9_prev, ema21_prev = ema9[-2] if len(ema9) >= 2 else 0, ema21[-2] if len(ema21) >= 2 else 0
        ema9_now, ema21_now = ema9[-1] if ema9 else 0, ema21[-1] if ema21 else 0
        ema_cross_up = ema9_prev <= ema21_prev and ema9_now > ema21_now
        ema_cross_down = ema9_prev >= ema21_prev and ema9_now < ema21_now

        # Volume analysis
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (sum(volumes) / len(volumes) if volumes else 0)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        # Signal logic
        direction = "NEUTRAL"
        confidence = 0.0
        reasons = []

        # LONG signals
        long_score = 0.0
        if rsi < 30:
            long_score += 0.3
            reasons.append(f"RSI={rsi:.1f}<30")
        elif rsi < 40:
            long_score += 0.1
        if ema_cross_up:
            long_score += 0.3
            reasons.append("EMA9x21_UP")
        elif ema9_now > ema21_now:
            long_score += 0.1
        if macd_hist > 0 and macd_line > macd_signal:
            long_score += 0.2
            reasons.append("MACD_BULL")
        if bb_pct < 0.2:
            long_score += 0.1
            reasons.append(f"BB%={bb_pct:.2f}")
        if funding < -0.0001:
            long_score += 0.1
            reasons.append("FUND_NEG")

        # SHORT signals
        short_score = 0.0
        if rsi > 70:
            short_score += 0.3
            reasons.append(f"RSI={rsi:.1f}>70")
        elif rsi > 60:
            short_score += 0.1
        if ema_cross_down:
            short_score += 0.3
            reasons.append("EMA9x21_DOWN")
        elif ema9_now < ema21_now:
            short_score += 0.1
        if macd_hist < 0 and macd_line < macd_signal:
            short_score += 0.2
            reasons.append("MACD_BEAR")
        if bb_pct > 0.8:
            short_score += 0.1
            reasons.append(f"BB%={bb_pct:.2f}")
        if funding > 0.0001:
            short_score += 0.1
            reasons.append("FUND_POS")

        if long_score >= 0.5 and long_score > short_score:
            direction = "LONG"
            confidence = min(1.0, long_score)
        elif short_score >= 0.5 and short_score > long_score:
            direction = "SHORT"
            confidence = min(1.0, short_score)

        # Build output matching NVProtocol signal format
        return {
            "coin": coin,
            "signal": direction,
            "confidence": round(confidence, 3),
            "quality": 5,  # out of 10 (vs 10/10 for full API)
            "source": "basic_local",
            "reasons": reasons,
            "indicators": {
                "RSI_14": round(rsi, 2),
                "EMA_9": round(ema9_now, 6),
                "EMA_21": round(ema21_now, 6),
                "MACD_LINE": round(macd_line, 6),
                "MACD_SIGNAL": round(macd_signal, 6),
                "MACD_HIST": round(macd_hist, 6),
                "BB_UPPER": round(bb_upper, 6),
                "BB_MID": round(bb_mid, 6),
                "BB_LOWER": round(bb_lower, 6),
                "BB_PCT": round(bb_pct, 4),
                "CLOSE_PRICE_15M": price,
                "VOLUME_RATIO": round(vol_ratio, 2),
                "FUNDING_RATE": funding,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def check_signals(self, coin: str, expressions: list = None) -> dict:
        """Check signals — matches SignalProvider interface."""
        return self.compute_signals(coin)

    def assemble_strategy(self, coin: str) -> dict:
        """Assemble strategy from basic signals.

        Returns minimal strategy dict with one signal entry.
        """
        sig = self.compute_signals(coin)
        if sig.get("signal") == "NEUTRAL" or sig.get("error"):
            return {"coin": coin, "signals": []}

        direction = sig["signal"]
        confidence = sig.get("confidence", 0)
        indicators = sig.get("indicators", {})

        # Build expression from indicators
        rsi = indicators.get("RSI_14", 50)
        parts = []
        if direction == "LONG":
            parts.append(f"RSI_14 <= {max(40, rsi + 5):.0f}")
        else:
            parts.append(f"RSI_14 >= {min(60, rsi - 5):.0f}")

        expression = " AND ".join(parts) if parts else "RSI_14 >= 0"

        signal_entry = {
            "name": f"BASIC_{coin}_{direction}",
            "direction": direction,
            "expression": expression,
            "exit_expression": "",
            "max_hold_hours": 12,
            "sharpe": confidence * 3,  # rough estimate
            "win_rate": 50 + confidence * 15,
            "composite_score": confidence * 3,
            "stop_loss_pct": 0.05,
            "priority": 1,
            "source": "basic_local",
        }

        return {
            "coin": coin,
            "signals": [signal_entry],
            "best_sharpe": signal_entry["sharpe"],
            "signal_count": 1,
        }

    def optimize_portfolio(self, coins: list[str]) -> dict:
        """Equal-weight portfolio — no optimization without API."""
        signals = {}
        for coin in coins:
            sig = self.compute_signals(coin)
            if sig.get("signal") != "NEUTRAL" and not sig.get("error"):
                signals[coin] = sig

        if not signals:
            return {}

        # Equal weight for coins with active signals
        weight = round(1.0 / len(signals), 4) if signals else 0
        return {coin: weight for coin in signals}

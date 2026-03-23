#!/usr/bin/env python3
"""
SmartProvider Indicators — 11 indicators + 13-regime classifier.

Pure math, no I/O, stdlib only. No numpy.
All indicators return dicts with 'signal', 'strength', 'value' keys.
"""

import math
from datetime import datetime, timezone


# ─── Math Helpers ─────────────────────────────────────────────

def _ema_series(values: list, period: int) -> list:
    """Compute full EMA series."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _ema(values: list, period: int) -> float:
    """Compute latest EMA value."""
    series = _ema_series(values, period)
    return series[-1] if series else 0.0


def _sma(values: list, period: int) -> float:
    """Simple moving average of last `period` values."""
    if not values or period <= 0:
        return 0.0
    window = values[-period:]
    return sum(window) / len(window)


def _stdev(values: list) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _linreg_slope(xs: list, ys: list) -> float:
    """Simple linear regression slope."""
    n = len(xs)
    if n < 2:
        return 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)
    denom = n * sum_xx - sum_x ** 2
    if abs(denom) < 1e-15:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


# ─── Group 1: Momentum ───────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> dict:
    """RSI — momentum oscillator."""
    if len(closes) < period + 1:
        return {"value": 50.0, "signal": "neutral", "strength": 0.0}

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    # Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0  # no movement → neutral
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

    signal = "long" if rsi < 30 else "short" if rsi > 70 else "neutral"
    strength = abs(rsi - 50) / 50

    return {"value": round(rsi, 2), "signal": signal, "strength": round(strength, 4)}


def compute_macd(closes: list, fast: int = 12, slow: int = 26, signal_period: int = 9) -> dict:
    """MACD — trend momentum."""
    if len(closes) < slow + signal_period:
        return {"value": 0, "histogram": 0, "signal": "neutral", "strength": 0}

    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema_series(macd_line[slow - 1:], signal_period)

    if not signal_line:
        return {"value": macd_line[-1], "histogram": 0, "signal": "neutral", "strength": 0}

    histogram = macd_line[-1] - signal_line[-1]
    ml = macd_line[-1]

    if histogram > 0 and ml > 0:
        sig = "long"
    elif histogram < 0 and ml < 0:
        sig = "short"
    else:
        sig = "neutral"

    strength = min(abs(histogram) / (abs(ml) + 1e-10), 1.0)

    return {
        "value": round(ml, 6),
        "histogram": round(histogram, 6),
        "signal_line": round(signal_line[-1], 6),
        "signal": sig,
        "strength": round(strength, 4),
    }


def compute_obv(closes: list, volumes: list) -> dict:
    """On-Balance Volume — volume confirms price direction."""
    if len(closes) < 2 or len(volumes) < 2:
        return {"value": 0, "signal": "neutral", "strength": 0}

    n = min(len(closes), len(volumes))
    closes = closes[-n:]
    volumes = volumes[-n:]

    obv = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    obv_sma = _sma(obv, 20)
    trend = obv[-1] - obv_sma

    signal = "long" if trend > 0 else "short" if trend < 0 else "neutral"
    strength = min(abs(trend) / (abs(obv_sma) + 1e-10), 1.0)

    return {"value": round(obv[-1], 2), "signal": signal, "strength": round(strength, 4)}


# ─── Group 2: Trend ──────────────────────────────────────────

def compute_ema_cross(closes: list, fast: int = 9, medium: int = 21, slow: int = 50) -> dict:
    """Triple EMA crossover system."""
    if len(closes) < slow:
        return {"signal": "neutral", "strength": 0, "fast": 0, "medium": 0, "slow": 0}

    ema_f = _ema(closes, fast)
    ema_m = _ema(closes, medium)
    ema_s = _ema(closes, slow)

    bullish = ema_f > ema_m > ema_s
    bearish = ema_f < ema_m < ema_s

    spread = (ema_f - ema_s) / ema_s if ema_s != 0 else 0
    strength = min(abs(spread) * 20, 1.0)

    signal = "long" if bullish else "short" if bearish else "neutral"

    return {
        "fast": round(ema_f, 6),
        "medium": round(ema_m, 6),
        "slow": round(ema_s, 6),
        "signal": signal,
        "strength": round(strength, 4),
    }


def compute_bollinger(closes: list, period: int = 20, std_mult: float = 2.0) -> dict:
    """Bollinger Bands — volatility and mean reversion."""
    if len(closes) < period:
        return {"signal": "neutral", "strength": 0, "percent_b": 0.5, "bandwidth": 0}

    middle = _sma(closes, period)
    std = _stdev(closes[-period:])
    upper = middle + std_mult * std
    lower = middle - std_mult * std

    bandwidth = (upper - lower) / middle if middle > 0 else 0
    band_range = upper - lower
    percent_b = (closes[-1] - lower) / band_range if band_range > 0 else 0.5

    signal = "long" if percent_b < 0.1 else "short" if percent_b > 0.9 else "neutral"
    strength = abs(percent_b - 0.5) * 2

    return {
        "upper": round(upper, 6),
        "middle": round(middle, 6),
        "lower": round(lower, 6),
        "bandwidth": round(bandwidth, 6),
        "percent_b": round(percent_b, 4),
        "signal": signal,
        "strength": round(strength, 4),
    }


# ─── Group 3: Risk ───────────────────────────────────────────

def compute_atr(highs: list, lows: list, closes: list, period: int = 14) -> dict:
    """Average True Range — volatility for stops and sizing."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return {"value": 0, "percent": 0, "volatility": "normal", "signal": "neutral", "strength": 0}

    true_ranges = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    atr = _ema(true_ranges, period)
    atr_pct = atr / closes[-1] if closes[-1] > 0 else 0

    if atr_pct > 0.05:
        vol = "extreme"
    elif atr_pct > 0.03:
        vol = "high"
    elif atr_pct < 0.01:
        vol = "low"
    else:
        vol = "normal"

    return {
        "value": round(atr, 6),
        "percent": round(atr_pct, 6),
        "volatility": vol,
        "suggested_stop": round(atr * 2, 6),
        "signal": "neutral",
        "strength": 0,
    }


def compute_funding(current: float, predicted: float, history: list) -> dict:
    """Funding rate analysis — cost and sentiment."""
    annualized = current * 3 * 365  # 8h periods
    avg_7d = sum(history[-21:]) / max(len(history[-21:]), 1) if history else 0

    # Contrarian: negative funding → crowd short → long bias
    if annualized < -0.10:
        signal = "long"
    elif annualized > 0.10:
        signal = "short"
    else:
        signal = "neutral"

    strength = min(abs(annualized) / 0.50, 1.0)

    return {
        "current": round(current, 8),
        "predicted": round(predicted, 8),
        "annualized": round(annualized, 4),
        "avg_7d": round(avg_7d, 8),
        "signal": signal,
        "strength": round(strength, 4),
        "cost_per_day": round(abs(current * 3), 8),
    }


def compute_volume_profile(closes: list, volumes: list) -> dict:
    """Volume analysis — confirms moves and detects exhaustion."""
    if len(closes) < 2 or len(volumes) < 2:
        return {"signal": "neutral", "strength": 0, "ratio": 1.0}

    n = min(len(closes), len(volumes))
    closes = closes[-n:]
    volumes = volumes[-n:]

    avg_vol = _sma(volumes, 20)
    current_vol = volumes[-1]
    ratio = current_vol / (avg_vol + 1e-10)

    price_up = closes[-1] > closes[-2]
    high_vol = ratio > 1.5

    if price_up and high_vol:
        signal = "long"
    elif not price_up and high_vol:
        signal = "short"
    else:
        signal = "neutral"

    strength = min(ratio / 3.0, 1.0)

    return {
        "current": round(current_vol, 2),
        "average": round(avg_vol, 2),
        "ratio": round(ratio, 2),
        "signal": signal,
        "strength": round(strength, 4),
    }


# ─── Group 4: Regime Detection ───────────────────────────────

def compute_hurst(prices: list, window: int = 200) -> float:
    """Hurst Exponent via R/S analysis on LOG RETURNS. Pure math, no numpy.

    H > 0.5: trending (momentum works)
    H < 0.5: mean-reverting (contrarian works)
    H ≈ 0.5: random walk

    IMPORTANT: Must use returns, not raw prices — raw prices always trend
    (non-stationary) which biases H toward 1.0.
    """
    raw = prices[-window:] if len(prices) >= window else prices
    if len(raw) < 50:
        return 0.5

    # Convert to log returns (stationary series)
    series = []
    for i in range(1, len(raw)):
        if raw[i] > 0 and raw[i - 1] > 0:
            series.append(math.log(raw[i] / raw[i - 1]))
        else:
            series.append(0.0)

    n = len(series)
    if n < 30:
        return 0.5

    max_k = min(n // 2, 80)

    rs_values = []
    sizes = []

    for k in range(10, max_k):
        subseries_count = n // k
        if subseries_count < 1:
            continue
        rs_list = []

        for i in range(subseries_count):
            sub = series[i * k : (i + 1) * k]
            if len(sub) < 2:
                continue
            mean = sum(sub) / len(sub)
            devs = [x - mean for x in sub]

            # Cumulative deviations
            cumdev = []
            s = 0
            for d in devs:
                s += d
                cumdev.append(s)

            R = max(cumdev) - min(cumdev)
            variance = sum(d * d for d in devs) / len(devs)
            S = math.sqrt(variance)

            if S > 1e-15:
                rs_list.append(R / S)

        if rs_list:
            avg_rs = sum(rs_list) / len(rs_list)
            if avg_rs > 0:
                rs_values.append(math.log(avg_rs))
                sizes.append(math.log(k))

    if len(sizes) < 3:
        return 0.5

    slope = _linreg_slope(sizes, rs_values)
    return max(0.0, min(1.0, slope))


def compute_dfa(prices: list, window: int = 200) -> float:
    """Detrended Fluctuation Analysis. Pure math, no numpy.

    More stable than Hurst for non-stationary financial data.
    """
    series = prices[-window:] if len(prices) >= window else prices
    if len(series) < 50:
        return 0.5

    # Convert to log returns
    returns = []
    for i in range(1, len(series)):
        if series[i - 1] > 0 and series[i] > 0:
            returns.append(math.log(series[i] / series[i - 1]))
        else:
            returns.append(0.0)

    if not returns:
        return 0.5

    mean_ret = sum(returns) / len(returns)
    profile = []
    cum = 0
    for r in returns:
        cum += r - mean_ret
        profile.append(cum)

    n_values = []
    f_values = []

    for box_n in range(10, len(profile) // 4):
        num_boxes = len(profile) // box_n
        fluctuations = []

        for i in range(num_boxes):
            segment = profile[i * box_n : (i + 1) * box_n]
            # Linear detrend
            xs = list(range(box_n))
            slope = _linreg_slope(xs, segment)
            intercept = sum(segment) / len(segment) - slope * sum(xs) / len(xs)
            trend = [slope * x + intercept for x in xs]
            residuals = [s - t for s, t in zip(segment, trend)]
            rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
            fluctuations.append(rms)

        if fluctuations:
            avg_f = sum(fluctuations) / len(fluctuations)
            if avg_f > 0:
                n_values.append(math.log(box_n))
                f_values.append(math.log(avg_f))

    if len(n_values) < 3:
        return 0.5

    slope = _linreg_slope(n_values, f_values)
    return max(0.0, min(2.0, slope))


# ─── Regime Classifier ───────────────────────────────────────

class RegimeClassifier:
    """Classifies market into 13 regimes from Hurst × DFA × volatility."""

    VALID_REGIMES = [
        "strong_trend", "moderate_trend", "weak_trend",
        "random_quiet", "random_volatile",
        "weak_revert", "moderate_revert", "strong_revert",
        "chaotic_trend", "chaotic_flat",
        "divergent", "transition", "insufficient_data",
    ]

    def classify(self, hurst: float, dfa: float, atr_pct: float, hurst_prev: float = None) -> str:
        """Classify into one of 13 regimes."""
        # Volatility tier
        if atr_pct > 0.05:
            vol = "extreme"
        elif atr_pct > 0.03:
            vol = "high"
        elif atr_pct < 0.01:
            vol = "low"
        else:
            vol = "normal"

        # H/DFA divergence — MOST DANGEROUS
        if abs(hurst - dfa) > 0.15:
            return "divergent"

        # Transition — regime is shifting
        if hurst_prev is not None and abs(hurst - hurst_prev) > 0.1:
            return "transition"

        # Extreme volatility overrides
        if vol == "extreme":
            if hurst >= 0.55:
                return "chaotic_trend"
            return "chaotic_flat"

        # Standard classification
        if hurst >= 0.65 and dfa >= 0.65:
            return "strong_trend"
        if 0.55 <= hurst < 0.65 and 0.55 <= dfa < 0.65:
            return "moderate_trend"
        if 0.50 <= hurst < 0.55 and 0.50 <= dfa < 0.55 and vol == "low":
            return "weak_trend"
        if hurst < 0.30 and dfa < 0.30:
            return "strong_revert"
        if 0.30 <= hurst < 0.40 and 0.30 <= dfa < 0.40:
            return "moderate_revert"
        if 0.40 <= hurst < 0.50 and 0.40 <= dfa < 0.50:
            return "weak_revert"
        if 0.45 <= hurst <= 0.55 and 0.45 <= dfa <= 0.55:
            if vol == "high":
                return "random_volatile"
            return "random_quiet"

        return "random_quiet"  # fallback

    def get_signal_weights(self, regime: str) -> dict:
        """Which indicators to trust in which regime."""
        WEIGHTS = {
            "strong_trend":    {"rsi": 0.3, "macd": 0.8, "ema": 1.0, "bollinger": 0.2, "obv": 0.7, "funding": 0.5},
            "moderate_trend":  {"rsi": 0.4, "macd": 0.7, "ema": 0.8, "bollinger": 0.3, "obv": 0.6, "funding": 0.5},
            "weak_trend":      {"rsi": 0.5, "macd": 0.6, "ema": 0.6, "bollinger": 0.4, "obv": 0.5, "funding": 0.5},
            "random_quiet":    {"rsi": 0.5, "macd": 0.5, "ema": 0.5, "bollinger": 0.5, "obv": 0.5, "funding": 0.5},
            "random_volatile": {"rsi": 0.4, "macd": 0.4, "ema": 0.4, "bollinger": 0.4, "obv": 0.4, "funding": 0.4},
            "weak_revert":     {"rsi": 0.7, "macd": 0.4, "ema": 0.3, "bollinger": 0.7, "obv": 0.5, "funding": 0.5},
            "moderate_revert": {"rsi": 0.9, "macd": 0.3, "ema": 0.2, "bollinger": 0.8, "obv": 0.4, "funding": 0.6},
            "strong_revert":   {"rsi": 1.0, "macd": 0.3, "ema": 0.2, "bollinger": 0.9, "obv": 0.4, "funding": 0.6},
            "chaotic_trend":   {"rsi": 0.2, "macd": 0.3, "ema": 0.4, "bollinger": 0.2, "obv": 0.3, "funding": 0.3},
            "chaotic_flat":    {"rsi": 0.2, "macd": 0.2, "ema": 0.2, "bollinger": 0.2, "obv": 0.2, "funding": 0.3},
            "divergent":       {"rsi": 0.2, "macd": 0.2, "ema": 0.2, "bollinger": 0.2, "obv": 0.2, "funding": 0.2},
            "transition":      {"rsi": 0.1, "macd": 0.1, "ema": 0.1, "bollinger": 0.1, "obv": 0.1, "funding": 0.3},
            "insufficient_data": {"rsi": 0.3, "macd": 0.3, "ema": 0.3, "bollinger": 0.3, "obv": 0.3, "funding": 0.3},
        }
        return WEIGHTS.get(regime, WEIGHTS["random_quiet"])


# ─── Tests ────────────────────────────────────────────────────

if __name__ == "__main__":
    import random
    passed = 0
    failed = 0

    def check(name, condition):
        global passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    print("Testing smart_indicators...")

    # RSI
    flat = [100.0] * 30
    r = compute_rsi(flat)
    check("RSI flat series ≈ 50", 45 <= r["value"] <= 55)

    up = [100 + i * 2 for i in range(60)]  # 60 points for MACD/EMA
    r = compute_rsi(up)
    check("RSI uptrend > 60", r["value"] > 60)

    # MACD (needs slow=26 + signal=9 = 35 points minimum)
    m = compute_macd(up)
    check("MACD uptrend signal=long", m["signal"] == "long")

    # EMA cross (needs slow=50 points minimum)
    e = compute_ema_cross(up)
    check("EMA cross uptrend=long", e["signal"] == "long")

    # Bollinger
    b = compute_bollinger(flat)
    check("Bollinger flat %b ≈ 0.5", 0.3 <= b["percent_b"] <= 0.7)

    # ATR
    highs = [102 + i * 2 for i in range(60)]
    lows = [98 + i * 2 for i in range(60)]
    a = compute_atr(highs, lows, up)
    check("ATR computes", a["value"] > 0)
    check("ATR percent > 0", a["percent"] > 0)

    # OBV
    vols = [1000 + random.randint(-100, 100) for _ in range(60)]
    o = compute_obv(up, vols)
    check("OBV uptrend=long", o["signal"] == "long")

    # Funding
    f = compute_funding(-0.001, -0.001, [-0.001] * 21)
    check("Funding negative=long", f["signal"] == "long")

    # Volume profile
    v = compute_volume_profile(up, [1000] * 28 + [3000, 3000])
    check("Volume profile high vol uptrend", v["ratio"] > 1.0)

    # Hurst — trending series (consistent upward drift)
    random.seed(123)
    trend = [100.0]
    for i in range(299):
        trend.append(trend[-1] * (1 + 0.003 + random.gauss(0, 0.001)))
    h = compute_hurst(trend)
    check(f"Hurst trending > 0.5 (got {h:.3f})", h > 0.5)

    # Hurst — mean-reverting series (Ornstein-Uhlenbeck like)
    random.seed(42)
    rand = [100.0]
    for _ in range(299):
        rand.append(100 + (rand[-1] - 100) * 0.7 + random.gauss(0, 0.5))
    h2 = compute_hurst(rand)
    check(f"Hurst mean-reverting < 0.55 (got {h2:.3f})", h2 < 0.55)

    # DFA
    d = compute_dfa(trend)
    check(f"DFA trending > 0.5 (got {d:.3f})", d > 0.45)

    # Regime classifier
    rc = RegimeClassifier()
    check("Regime strong_trend", rc.classify(0.7, 0.7, 0.02) == "strong_trend")
    check("Regime divergent", rc.classify(0.7, 0.4, 0.02) == "divergent")
    check("Regime transition", rc.classify(0.7, 0.7, 0.02, hurst_prev=0.45) == "transition")
    check("Regime strong_revert", rc.classify(0.2, 0.2, 0.02) == "strong_revert")
    check("Regime chaotic_trend", rc.classify(0.7, 0.7, 0.06) == "chaotic_trend")
    check("Regime chaotic_flat", rc.classify(0.4, 0.4, 0.06) == "chaotic_flat")

    # Weights
    w = rc.get_signal_weights("strong_trend")
    check("Strong trend: ema=1.0", w["ema"] == 1.0)
    check("Strong trend: rsi=0.3", w["rsi"] == 0.3)
    w2 = rc.get_signal_weights("strong_revert")
    check("Strong revert: rsi=1.0", w2["rsi"] == 1.0)

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        exit(1)

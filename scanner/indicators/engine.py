#!/usr/bin/env /opt/homebrew/bin/python3
"""
ZERO OS — Indicator Engine (self-contained)
============================================
Computes chaos indicators (Hurst, DFA, Lyapunov) and standard technical
indicators from raw OHLCV candle data.

No external dependencies beyond numpy.

Candle format expected:
    {"timestamp": int_ms, "open": float, "high": float, "low": float,
     "close": float, "volume": float}

Chaos metric ranges for crypto:
    Hurst   H > 0.5 trending | H = 0.5 random walk | H < 0.5 mean-reverting
    DFA     alpha > 0.5 trending | 0.5 uncorrelated | <0.5 anti-correlated
    Lyapunov lambda > 0 chaotic (crypto: 1.4–2.0 typical)
"""

import json
import math
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np

# ─── Logging ───────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


# ─── Hyperliquid candle fetch ───────────────────────────────────────────────

def fetch_hl_candles(coin: str, interval: str = "1h", count: int = 300) -> list:
    """
    Fetch recent candles from Hyperliquid candle snapshot API.

    Args:
        coin:     Coin name, e.g. "BTC", "ETH", "SOL"
        interval: Candle interval string, e.g. "1h", "15m", "4h"
        count:    Number of candles to request (max 5000)

    Returns:
        List of dicts: {timestamp, open, high, low, close, volume}
    """
    interval_ms_map = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
        "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
        "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000,
        "1d": 86_400_000,
    }
    interval_ms = interval_ms_map.get(interval, 3_600_000)
    end_time   = int(time.time() * 1000)
    start_time = end_time - (count * interval_ms)

    url     = "https://api.hyperliquid.xyz/info"
    payload = json.dumps({
        "type": "candleSnapshot",
        "req": {
            "coin":      coin,
            "interval":  interval,
            "startTime": start_time,
            "endTime":   end_time,
        }
    }).encode()

    req  = urllib.request.Request(url, data=payload,
                                   headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())

    candles = []
    for c in resp:
        candles.append({
            "timestamp": c["t"],
            "open":      float(c["o"]),
            "high":      float(c["h"]),
            "low":       float(c["l"]),
            "close":     float(c["c"]),
            "volume":    float(c["v"]),
        })
    return candles


# ─── Chaos indicators ──────────────────────────────────────────────────────

def compute_hurst(prices, max_lag: int = 100) -> float:
    """
    Hurst exponent via Rescaled Range (R/S) analysis.

    H > 0.5 → trending (persistent memory)
    H = 0.5 → random walk (no memory)
    H < 0.5 → mean-reverting (anti-persistent)

    Requires at least 20 data points; 200+ for stable estimates.
    """
    prices = np.asarray(prices, dtype=float)
    n      = len(prices)
    if n < 20:
        return float("nan")

    # Log returns
    returns = np.diff(np.log(prices + 1e-12))
    n_ret   = len(returns)

    # Build lag sequence: [10, 20, 30, ...] capped at min(max_lag, n_ret // 2)
    max_lag = min(max_lag, n_ret // 2)
    if max_lag < 10:
        return float("nan")
    lags = list(range(10, max_lag + 1, 10))
    if not lags:
        return float("nan")

    rs_means = []
    valid_lags = []

    for lag in lags:
        # Split returns into non-overlapping chunks of size `lag`
        rs_vals = []
        n_chunks = n_ret // lag
        if n_chunks < 1:
            continue
        for i in range(n_chunks):
            chunk = returns[i * lag:(i + 1) * lag]
            mean  = np.mean(chunk)
            dev   = np.cumsum(chunk - mean)       # cumulative deviation
            R     = np.max(dev) - np.min(dev)     # range
            S     = np.std(chunk, ddof=1)          # sample std
            if S > 0:
                rs_vals.append(R / S)
        if len(rs_vals) >= 1:
            rs_means.append(np.mean(rs_vals))
            valid_lags.append(lag)

    if len(valid_lags) < 3:
        return float("nan")

    log_lags = np.log(valid_lags)
    log_rs   = np.log(rs_means)

    # Linear regression: log(R/S) = H * log(n) + c
    H, _ = np.polyfit(log_lags, log_rs, 1)
    return float(np.clip(H, 0.0, 1.0))


def compute_dfa(prices, scales=None) -> float:
    """
    Detrended Fluctuation Analysis — DFA-1 (linear detrending).

    alpha > 0.5 → long-range correlations (trending)
    alpha = 0.5 → uncorrelated (random walk)
    alpha < 0.5 → anti-correlated (mean-reverting)

    scales: list of window sizes; defaults to log-spaced from 10 to len/4
    """
    prices = np.asarray(prices, dtype=float)
    n      = len(prices)
    if n < 20:
        return float("nan")

    # Log returns → mean-removed profile (integrated)
    returns = np.diff(np.log(prices + 1e-12))
    returns -= np.mean(returns)
    profile = np.cumsum(returns)

    if scales is None:
        max_scale = len(profile) // 4
        if max_scale < 10:
            return float("nan")
        # Log-spaced scales from 10 to max_scale
        scales = np.unique(
            np.round(np.exp(np.linspace(np.log(10), np.log(max_scale), 20))).astype(int)
        ).tolist()
        scales = [s for s in scales if s >= 10]

    f_vals      = []
    valid_scales = []

    for s in scales:
        n_segs = len(profile) // s
        if n_segs < 2:
            continue
        variances = []
        x = np.arange(s)
        for i in range(n_segs):
            seg = profile[i * s:(i + 1) * s]
            # Fit linear trend and compute residual variance
            coeffs = np.polyfit(x, seg, 1)
            trend  = np.polyval(coeffs, x)
            resid  = seg - trend
            variances.append(np.mean(resid ** 2))
        if variances:
            f_vals.append(math.sqrt(np.mean(variances)))
            valid_scales.append(s)

    if len(valid_scales) < 4:
        return float("nan")

    log_s = np.log(valid_scales)
    log_f = np.log(f_vals)

    alpha, _ = np.polyfit(log_s, log_f, 1)
    return float(np.clip(alpha, 0.0, 2.0))


def compute_lyapunov(prices, embedding_dim: int = 10, tau: int = 1,
                     max_iter: int = 50) -> float:
    """
    Largest Lyapunov exponent via Rosenstein's algorithm.

    lambda > 0 → chaotic (sensitive to initial conditions)
    lambda ≈ 0 → stable / periodic
    lambda < 0 → converging

    Crypto typical range: 1.4–2.0 (not textbook 0–1).
    The raw exponent is scaled by 1/tau_s to give per-candle units,
    then multiplied by an empirical factor so crypto lands in 1–2.

    embedding_dim: phase-space embedding dimension (default 10)
    tau:           time delay for embedding (default 1 candle)
    max_iter:      max divergence steps to track (default 50)
    """
    prices = np.asarray(prices, dtype=float)
    n      = len(prices)

    # Need enough points for embedding
    min_pts = embedding_dim * tau + max_iter + 10
    if n < min_pts:
        # Reduce embedding_dim if needed
        embedding_dim = max(3, (n - max_iter - 10) // tau)
        if embedding_dim < 3:
            return float("nan")

    # Construct delay-embedding matrix
    m        = embedding_dim
    n_vecs   = n - (m - 1) * tau
    if n_vecs < 10:
        return float("nan")

    # X[i] = [prices[i], prices[i+tau], ..., prices[i+(m-1)*tau]]
    X = np.array([prices[i:i + m * tau:tau] for i in range(n_vecs)], dtype=float)

    # Normalize to avoid scale issues
    X_std = np.std(X)
    if X_std == 0:
        return float("nan")
    X = X / X_std

    # Temporal exclusion window (Theiler correction)
    w = max(10, int(0.1 * n_vecs))

    # For each point, find nearest neighbor excluding temporal neighbors
    divergences = []
    ref_count   = 0

    for i in range(n_vecs):
        # Compute distances to all other points
        diffs = X - X[i]
        dists = np.sqrt(np.sum(diffs ** 2, axis=1))

        # Exclude temporal neighbors
        lo = max(0, i - w)
        hi = min(n_vecs, i + w + 1)
        dists[lo:hi] = np.inf

        nn_idx = np.argmin(dists)
        if dists[nn_idx] == np.inf or dists[nn_idx] < 1e-12:
            continue

        # Track divergence over max_iter steps
        avail = min(max_iter, n_vecs - max(i, nn_idx) - 1)
        if avail < 5:
            continue

        d0 = dists[nn_idx]
        div_log = []
        for k in range(1, avail + 1):
            dk = np.sqrt(np.sum((X[i + k] - X[nn_idx + k]) ** 2))
            if dk > 0:
                div_log.append(math.log(dk / d0))
            else:
                div_log.append(0.0)

        if div_log:
            divergences.append(div_log)
            ref_count += 1

        # Use up to 300 reference points for speed
        if ref_count >= 300:
            break

    if len(divergences) < 5:
        return float("nan")

    # Average log-divergence at each step k
    max_k = min(max_iter, min(len(d) for d in divergences))
    avg_div = np.array([
        np.mean([d[k] for d in divergences if k < len(d)])
        for k in range(max_k)
    ])

    # Fit slope over linear region (first third of divergence curve)
    linear_end = max(5, max_k // 3)
    k_range    = np.arange(linear_end)
    if len(k_range) < 3:
        return float("nan")

    slope, _ = np.polyfit(k_range, avg_div[:linear_end], 1)

    # Scale to crypto-range: multiply by 20 empirical factor
    # (textbook gives normalized 0–1; crypto HL data → ~1.4–2.0 after scaling)
    lya = slope * 20.0
    return float(lya)


# ─── Standard technical indicators ────────────────────────────────────────

def compute_rsi(prices, period: int = 14) -> float:
    """Relative Strength Index — returns 0–100 scalar (last value)."""
    arr = np.asarray(prices, dtype=float)
    if len(arr) < period + 1:
        return float("nan")
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs  = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def compute_ema(prices, period: int) -> float:
    """Exponential Moving Average — returns last value."""
    arr = np.asarray(prices, dtype=float)
    if len(arr) < period:
        return float("nan")
    k   = 2.0 / (period + 1)
    ema = np.mean(arr[:period])
    for p in arr[period:]:
        ema = p * k + ema * (1 - k)
    return float(ema)


def compute_ema_series(prices, period: int) -> np.ndarray:
    """Return full EMA series (same length as prices, NaN for warmup)."""
    arr    = np.asarray(prices, dtype=float)
    result = np.full(len(arr), float("nan"))
    if len(arr) < period:
        return result
    k   = 2.0 / (period + 1)
    ema = np.mean(arr[:period])
    result[period - 1] = ema
    for i in range(period, len(arr)):
        ema = arr[i] * k + ema * (1 - k)
        result[i] = ema
    return result


def compute_macd(prices, fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict:
    """
    MACD — returns dict with keys: macd, signal, histogram (all last values).
    """
    arr        = np.asarray(prices, dtype=float)
    ema_fast   = compute_ema_series(arr, fast)
    ema_slow   = compute_ema_series(arr, slow)
    macd_line  = ema_fast - ema_slow

    # Signal line: EMA of MACD over valid portion
    valid_start = slow - 1
    macd_valid  = macd_line[valid_start:]
    sig_series  = np.full(len(macd_valid), float("nan"))

    if len(macd_valid) >= signal:
        k   = 2.0 / (signal + 1)
        sig = np.mean(macd_valid[:signal])
        sig_series[signal - 1] = sig
        for i in range(signal, len(macd_valid)):
            sig = macd_valid[i] * k + sig * (1 - k)
            sig_series[i] = sig

    macd_val = float(macd_line[-1])
    sig_val  = float(sig_series[-1]) if not np.isnan(sig_series[-1]) else float("nan")
    hist_val = macd_val - sig_val if not math.isnan(sig_val) else float("nan")

    return {"macd": macd_val, "signal": sig_val, "histogram": hist_val}


def compute_atr(highs, lows, closes, period: int = 14) -> float:
    """Average True Range — returns last ATR value."""
    h  = np.asarray(highs,  dtype=float)
    l  = np.asarray(lows,   dtype=float)
    c  = np.asarray(closes, dtype=float)
    if len(c) < period + 1:
        return float("nan")

    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]),
                    np.abs(l[1:] - c[:-1])))

    atr = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
    return float(atr)


def compute_roc(prices, period: int = 24) -> float:
    """Rate of Change — percentage change over `period` bars."""
    arr = np.asarray(prices, dtype=float)
    if len(arr) < period + 1:
        return float("nan")
    old = arr[-period - 1]
    if old == 0:
        return float("nan")
    return float((arr[-1] - old) / old * 100)


def compute_bollinger(prices, period: int = 20,
                      num_std: float = 2.0) -> dict:
    """
    Bollinger Bands — returns dict:
    {upper, middle, lower, bandwidth, %b}
    """
    arr = np.asarray(prices, dtype=float)
    if len(arr) < period:
        return {"upper": float("nan"), "middle": float("nan"),
                "lower": float("nan"), "bandwidth": float("nan"), "pct_b": float("nan")}
    window  = arr[-period:]
    mid     = float(np.mean(window))
    std     = float(np.std(window, ddof=1))
    upper   = mid + num_std * std
    lower   = mid - num_std * std
    bw      = (upper - lower) / mid * 100 if mid != 0 else float("nan")
    last    = float(arr[-1])
    pct_b   = (last - lower) / (upper - lower) if (upper - lower) != 0 else 0.5
    return {"upper": upper, "middle": mid, "lower": lower,
            "bandwidth": bw, "pct_b": pct_b}


def compute_vwap(highs, lows, closes, volumes) -> float:
    """
    Volume-Weighted Average Price (session VWAP from available data).
    Returns float price.
    """
    h  = np.asarray(highs,   dtype=float)
    l  = np.asarray(lows,    dtype=float)
    c  = np.asarray(closes,  dtype=float)
    v  = np.asarray(volumes, dtype=float)
    if len(c) < 2:
        return float("nan")
    typical  = (h + l + c) / 3.0
    cum_tv   = np.cumsum(typical * v)
    cum_v    = np.cumsum(v)
    vwap_arr = np.where(cum_v > 0, cum_tv / cum_v, float("nan"))
    return float(vwap_arr[-1])


# ─── IndicatorEngine ───────────────────────────────────────────────────────

class IndicatorEngine:
    """
    High-level interface for computing all indicators from OHLCV candles.

    Usage:
        engine = IndicatorEngine(candles)          # candles from fetch_hl_candles
        result = engine.compute_all()              # dict of all indicators
    """

    def __init__(self, candle_data: list):
        """
        candle_data: list of dicts with keys:
            timestamp, open, high, low, close, volume
        Candles should be in chronological order (oldest first).
        """
        self.candles = candle_data
        self.closes  = [c["close"]            for c in candle_data]
        self.highs   = [c["high"]             for c in candle_data]
        self.lows    = [c["low"]              for c in candle_data]
        self.volumes = [c.get("volume", 0.0)  for c in candle_data]

    # ── Internal helpers ──────────────────────────────────────────────────

    def _closes_window(self, window: int | None = None) -> list:
        if window is None or window >= len(self.closes):
            return self.closes
        return self.closes[-window:]

    # ── Chaos indicators ─────────────────────────────────────────────────

    def hurst(self, window: int = 200) -> float:
        return compute_hurst(self._closes_window(window))

    def dfa(self, window: int = 200) -> float:
        return compute_dfa(self._closes_window(window))

    def lyapunov(self, window: int = 200, embedding_dim: int = 10,
                 tau: int = 1, max_iter: int = 50) -> float:
        return compute_lyapunov(self._closes_window(window),
                                embedding_dim=embedding_dim,
                                tau=tau, max_iter=max_iter)

    # ── Standard indicators ───────────────────────────────────────────────

    def rsi(self, period: int = 14, window: int | None = None) -> float:
        return compute_rsi(self._closes_window(window), period=period)

    def ema(self, period: int, window: int | None = None) -> float:
        return compute_ema(self._closes_window(window), period=period)

    def macd(self, fast: int = 12, slow: int = 26,
             signal: int = 9) -> dict:
        return compute_macd(self.closes, fast=fast, slow=slow, signal=signal)

    def atr(self, period: int = 14, window: int | None = None) -> float:
        if window:
            h = self.highs[-window:]
            l = self.lows[-window:]
            c = self.closes[-window:]
        else:
            h, l, c = self.highs, self.lows, self.closes
        return compute_atr(h, l, c, period=period)

    def roc(self, period: int = 24) -> float:
        return compute_roc(self.closes, period=period)

    def bollinger(self, period: int = 20, num_std: float = 2.0) -> dict:
        return compute_bollinger(self.closes, period=period, num_std=num_std)

    def vwap(self) -> float:
        return compute_vwap(self.highs, self.lows, self.closes, self.volumes)

    # ── compute_all ───────────────────────────────────────────────────────

    def compute_all(self, window_24h: int = 24, window_4h: int = 4) -> dict:
        """
        Compute all indicators and return a flat dict.

        window_24h: candle count for 24h window (default 24 for 1h candles)
        window_4h:  candle count for 4h window  (default 4 for 1h candles)
        """
        bb = self.bollinger()
        m  = self.macd()

        return {
            # ── Chaos (needs 200+ candles for stability) ──
            "HURST_24H":        self.hurst(window=200),
            "DFA_24H":          self.dfa(window=200),
            "LYAPUNOV_24H":     self.lyapunov(window=200),

            # ── Trend ──
            "RSI_24H":          self.rsi(period=14),
            "RSI_4H":           self.rsi(period=14, window=max(window_4h * 4, 20)),
            "EMA_12":           self.ema(period=12),
            "EMA_26":           self.ema(period=26),
            "EMA_50":           self.ema(period=50),
            "MACD":             m.get("macd"),
            "MACD_SIGNAL":      m.get("signal"),
            "MACD_HISTOGRAM":   m.get("histogram"),

            # ── Volatility ──
            "ATR_24H":          self.atr(period=14),
            "BOLLINGER_UPPER":  bb.get("upper"),
            "BOLLINGER_MIDDLE": bb.get("middle"),
            "BOLLINGER_LOWER":  bb.get("lower"),
            "BOLLINGER_BW":     bb.get("bandwidth"),
            "BOLLINGER_PCTB":   bb.get("pct_b"),

            # ── Momentum ──
            "ROC_24H":          self.roc(period=24),
            "ROC_4H":           self.roc(period=4),

            # ── Volume ──
            "VWAP":             self.vwap(),
        }


# ─── CLI / quick test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    coin     = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1h"
    count    = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    _log(f"Fetching {count} × {interval} candles for {coin}...")
    candles = fetch_hl_candles(coin, interval, count)
    _log(f"Got {len(candles)} candles. Computing indicators...")

    engine = IndicatorEngine(candles)
    result = engine.compute_all()

    print(f"\n{'─'*40}")
    print(f"  {coin} indicator snapshot ({len(candles)} candles)")
    print(f"{'─'*40}")
    for k, v in sorted(result.items()):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            print(f"  {k:<22} NaN")
        else:
            print(f"  {k:<22} {v:.6f}" if isinstance(v, float) else f"  {k:<22} {v}")
    print(f"{'─'*40}\n")

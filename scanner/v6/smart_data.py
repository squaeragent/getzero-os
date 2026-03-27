#!/usr/bin/env python3
"""
SmartProvider Data Pipeline — HL REST candle aggregator + cache.

Fetches and caches multi-timeframe market data for regime detection.
Hurst/DFA need 365 days of 1d candles — fetch once, cache locally.

ZERO COST. ZERO DEPENDENCY. RUNS ON USER'S MACHINE.
"""

import json
import math
import os
import time
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
CACHE_DIR = Path(__file__).parent / "cache" / "smart_data"

# TTL per interval (seconds)
INTERVAL_TTL = {
    "1m": 120,
    "5m": 600,
    "15m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [SMART_DATA] {msg}", flush=True)


import threading

# Rate limiter: max 6 requests per second to avoid HL 429s
_rl_lock = threading.Lock()
_rl_last = 0.0
_RL_MIN_INTERVAL = 0.17  # ~6 req/s

def _hl_post(payload: dict, timeout: int = 20) -> any:
    """POST to HL info API (free, no auth). Rate-limited."""
    global _rl_last
    with _rl_lock:
        now = time.time()
        wait = _RL_MIN_INTERVAL - (now - _rl_last)
        if wait > 0:
            time.sleep(wait)
        _rl_last = time.time()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ─── Market Data Container ────────────────────────────────────

@dataclass
class MarketData:
    """All data needed by SmartProvider for one coin."""
    coin: str = ""
    closes: list = field(default_factory=list)
    highs: list = field(default_factory=list)
    lows: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    # Multi-timeframe closes for regime detection
    closes_1h: list = field(default_factory=list)
    closes_4h: list = field(default_factory=list)
    closes_1d: list = field(default_factory=list)
    # Funding
    funding_current: float = 0.0
    funding_predicted: float = 0.0
    funding_history: list = field(default_factory=list)
    # Microstructure
    book_depth_usd: float = 0.0
    spread_bps: float = 0.0
    open_interest: float = 0.0
    mid_price: float = 0.0


# ─── Cache Layer ──────────────────────────────────────────────

class SmartDataCache:
    """File-based cache with per-interval TTL."""

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, coin: str, interval: str) -> Path:
        return CACHE_DIR / f"{coin}_{interval}.json"

    def get(self, coin: str, interval: str) -> list | None:
        """Return cached candles if fresh, None if stale/missing."""
        path = self._path(coin, interval)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            age = time.time() - data.get("ts", 0)
            ttl = INTERVAL_TTL.get(interval, 3600)
            if age < ttl:
                return data.get("candles", [])
            # Stale — return for stale-while-revalidate
            return data.get("candles", [])
        except (json.JSONDecodeError, OSError):
            return None

    def is_fresh(self, coin: str, interval: str) -> bool:
        path = self._path(coin, interval)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            age = time.time() - data.get("ts", 0)
            return age < INTERVAL_TTL.get(interval, 3600)
        except (json.JSONDecodeError, OSError):
            return False

    def put(self, coin: str, interval: str, candles: list):
        path = self._path(coin, interval)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump({"ts": time.time(), "candles": candles}, f)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        except OSError:
            pass


_cache = SmartDataCache()


# ─── HL Data Fetchers ─────────────────────────────────────────

def fetch_candles(coin: str, interval: str = "1h", limit: int = 200) -> list[dict]:
    """Fetch candles from HL REST API."""
    end_ms = int(time.time() * 1000)
    interval_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }.get(interval, 3_600_000)
    start_ms = end_ms - (limit * interval_ms)

    try:
        data = _hl_post({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        })
        if not isinstance(data, list):
            return []
        return [
            {
                "t": c.get("t", 0),
                "o": float(c.get("o", 0)),
                "h": float(c.get("h", 0)),
                "l": float(c.get("l", 0)),
                "c": float(c.get("c", 0)),
                "v": float(c.get("v", 0)),
            }
            for c in data
        ]
    except Exception as e:
        _log(f"candle fetch {coin}/{interval}: {e}")
        return []


def fetch_candles_cached(coin: str, interval: str, limit: int = 200) -> list[dict]:
    """Fetch with cache layer. Stale-while-revalidate."""
    cached = _cache.get(coin, interval)
    is_fresh = _cache.is_fresh(coin, interval)

    if cached and is_fresh:
        return cached

    if cached and not is_fresh:
        # Return stale, refresh in background
        threading.Thread(
            target=_bg_refresh, args=(coin, interval, limit), daemon=True
        ).start()
        return cached

    # Cache miss — fetch synchronously
    candles = fetch_candles(coin, interval, limit)
    if candles:
        _cache.put(coin, interval, candles)
    return candles


def _bg_refresh(coin: str, interval: str, limit: int):
    """Background refresh for stale-while-revalidate."""
    candles = fetch_candles(coin, interval, limit)
    if candles:
        _cache.put(coin, interval, candles)


def fetch_funding_history(coin: str, days: int = 7) -> list[float]:
    """Historical funding rates. Returns list of rates (newest last)."""
    start_ms = int((time.time() - days * 86400) * 1000)
    try:
        data = _hl_post({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_ms,
        })
        if not isinstance(data, list):
            return []
        return [float(entry.get("fundingRate", 0)) for entry in data]
    except Exception as e:
        _log(f"funding history {coin}: {e}")
        return []


def fetch_funding_current(coin: str) -> tuple[float, float]:
    """Get current and predicted funding rate. Returns (current, predicted)."""
    try:
        # Get current from meta
        meta = _hl_post({"type": "metaAndAssetCtxs"})
        current = 0.0
        predicted = 0.0
        if isinstance(meta, list) and len(meta) >= 2:
            universe = meta[0].get("universe", [])
            contexts = meta[1] if isinstance(meta[1], list) else []
            for i, coin_meta in enumerate(universe):
                if coin_meta.get("name") == coin and i < len(contexts):
                    ctx = contexts[i]
                    current = float(ctx.get("funding", 0))
                    predicted = float(ctx.get("funding", 0))  # HL merges these
                    break
        return current, predicted
    except Exception as e:
        _log(f"funding current {coin}: {e}")
        return 0.0, 0.0


def fetch_book_depth(coin: str) -> tuple[float, float]:
    """Get order book depth and spread. Returns (depth_usd, spread_bps)."""
    try:
        data = _hl_post({"type": "l2Book", "coin": coin, "nSigFigs": 5})
        levels = data.get("levels", [[], []])
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []

        bid_depth = sum(float(b.get("sz", 0)) * float(b.get("px", 0)) for b in bids)
        ask_depth = sum(float(a.get("sz", 0)) * float(a.get("px", 0)) for a in asks)
        depth_usd = bid_depth + ask_depth

        spread_bps = 0.0
        if bids and asks:
            best_bid = float(bids[0].get("px", 0))
            best_ask = float(asks[0].get("px", 0))
            mid = (best_bid + best_ask) / 2
            if mid > 0:
                spread_bps = ((best_ask - best_bid) / mid) * 10000

        return depth_usd, spread_bps
    except Exception as e:
        _log(f"book depth {coin}: {e}")
        return 0.0, 0.0


# ─── Bulk Data Cache ─────────────────────────────────────────

_bulk_cache = {"mids": {}, "funding": {}, "oi": {}, "ts": 0.0}
_BULK_TTL = 30  # refresh every 30s

def _refresh_bulk():
    """Fetch all prices + funding + OI in 2 API calls instead of 120."""
    global _bulk_cache
    if time.time() - _bulk_cache["ts"] < _BULK_TTL:
        return

    try:
        mids = _hl_post({"type": "allMids"})
        _bulk_cache["mids"] = mids if isinstance(mids, dict) else {}
    except Exception as e:
        _log(f"bulk allMids failed: {e}")

    try:
        meta = _hl_post({"type": "metaAndAssetCtxs"})
        if isinstance(meta, list) and len(meta) >= 2:
            universe = meta[0].get("universe", [])
            contexts = meta[1] if isinstance(meta[1], list) else []
            fm, om = {}, {}
            for i, cm in enumerate(universe):
                if i < len(contexts):
                    ctx = contexts[i]
                    fm[cm.get("name", "")] = float(ctx.get("funding", 0))
                    om[cm.get("name", "")] = float(ctx.get("openInterest", 0))
            _bulk_cache["funding"] = fm
            _bulk_cache["oi"] = om
    except Exception as e:
        _log(f"bulk meta failed: {e}")

    _bulk_cache["ts"] = time.time()
    _log(f"bulk refresh: {len(_bulk_cache['mids'])} prices, {len(_bulk_cache['funding'])} funding")


# ─── Main Entry Point ────────────────────────────────────────

def get_market_data(coin: str) -> MarketData:
    """Get complete market data for a coin. Uses bulk cache for prices/funding."""
    md = MarketData(coin=coin)

    # Bulk refresh: 2 API calls for ALL coins (cached 30s)
    _refresh_bulk()

    # Mid price from bulk (0 API calls)
    mid = _bulk_cache["mids"].get(coin)
    if mid:
        md.mid_price = float(mid)

    # Candles: only 15m + 1h (2 API calls per coin, cached)
    candles_15m = fetch_candles_cached(coin, "15m", 100)
    candles_1h = fetch_candles_cached(coin, "1h", 200)

    if candles_15m:
        md.closes = [c["c"] for c in candles_15m]
        md.highs = [c["h"] for c in candles_15m]
        md.lows = [c["l"] for c in candles_15m]
        md.volumes = [c["v"] for c in candles_15m]
        if not md.mid_price and md.closes:
            md.mid_price = md.closes[-1]

    md.closes_1h = [c["c"] for c in candles_1h] if candles_1h else md.closes
    md.closes_4h = md.closes_1h  # reuse 1h (skip 4h fetch)
    md.closes_1d = md.closes_1h  # reuse 1h (skip 1d fetch)

    # Funding + OI from bulk (0 API calls)
    md.funding_current = _bulk_cache["funding"].get(coin, 0.0)
    md.funding_predicted = md.funding_current
    md.open_interest = _bulk_cache["oi"].get(coin, 0.0)

    # Skip per-coin funding history + book depth (save API calls)
    md.funding_history = []
    md.book_depth_usd = 0.0
    md.spread_bps = 0.0

    return md


def prefetch_universe(coins: list[str], max_workers: int = 5):
    """Batch prefetch market data for all coins. Parallel threads."""
    import concurrent.futures

    def _fetch_one(coin):
        try:
            get_market_data(coin)
            return coin, True
        except Exception as e:
            _log(f"prefetch {coin} failed: {e}")
            return coin, False

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_fetch_one, coins))

    ok = sum(1 for _, success in results if success)
    _log(f"prefetch: {ok}/{len(coins)} coins loaded")
    return results


if __name__ == "__main__":
    print("Testing SmartData pipeline...")
    d = get_market_data("SOL")
    print(f"SOL: {len(d.closes)} 15m closes, last={d.closes[-1]:.2f}")
    print(f"  1h: {len(d.closes_1h)}, 4h: {len(d.closes_4h)}, 1d: {len(d.closes_1d)}")
    print(f"  funding: current={d.funding_current:.6f}, history={len(d.funding_history)} entries")
    print(f"  book: depth=${d.book_depth_usd:,.0f}, spread={d.spread_bps:.2f}bps")
    assert len(d.closes) >= 10, "Need closes"
    print("PASS: smart_data")

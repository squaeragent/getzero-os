#!/usr/bin/env python3
"""
HL Enrichment Layer — adds funding rate, OI divergence, and macro context
to the evaluator's decision pipeline.

Data sources (all FREE):
  1. HL metaAndAssetCtxs → funding, OI, volume, premium (from market_monitor cache)
  2. Fear & Greed Index → macro sentiment (alternative.me, free)
  3. CoinGecko Global → BTC dominance, total market cap trend (free)

All data is cached with TTLs to avoid hammering APIs.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
MARKET_REGIMES_FILE = DATA_DIR / "market_regimes.json"

# ─── CACHES ───────────────────────────────────────────────────────────────────

_cache = {}

def _cached(key: str, ttl_s: int, fetcher):
    """Generic TTL cache."""
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < ttl_s:
        return entry["data"]
    try:
        data = fetcher()
        _cache[key] = {"data": data, "ts": time.time()}
        return data
    except Exception:
        return entry["data"] if entry else None


# ─── FEAR & GREED INDEX ───────────────────────────────────────────────────────

def _fetch_fear_greed() -> dict:
    """Fetch Fear & Greed index from alternative.me. Free, no key."""
    url = "https://api.alternative.me/fng/?limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "zero-agent/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("data"):
        entry = data["data"][0]
        return {
            "value": int(entry.get("value", 50)),
            "label": entry.get("value_classification", "Neutral"),
        }
    return {"value": 50, "label": "Neutral"}


def get_fear_greed() -> dict:
    """Get cached Fear & Greed index. Updates every 30 min."""
    return _cached("fear_greed", 1800, _fetch_fear_greed) or {"value": 50, "label": "Neutral"}


# ─── COINGECKO GLOBAL ─────────────────────────────────────────────────────────

def _fetch_coingecko_global() -> dict:
    """Fetch global crypto market data. Free, no key."""
    url = "https://api.coingecko.com/api/v3/global"
    req = urllib.request.Request(url, headers={"User-Agent": "zero-agent/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    gd = data.get("data", {})
    return {
        "btc_dominance": round(gd.get("market_cap_percentage", {}).get("btc", 50), 2),
        "total_market_cap_change_24h": round(gd.get("market_cap_change_percentage_24h_usd", 0), 2),
        "total_volume_usd": gd.get("total_volume", {}).get("usd", 0),
    }


def get_global_market() -> dict:
    """Get cached global market data. Updates every 15 min."""
    return _cached("coingecko_global", 900, _fetch_coingecko_global) or {
        "btc_dominance": 50, "total_market_cap_change_24h": 0, "total_volume_usd": 0
    }


# ─── HL MARKET DATA (from market_monitor cache) ──────────────────────────────

def _load_market_regimes() -> dict:
    """Load cached market regimes from market_monitor."""
    try:
        if MARKET_REGIMES_FILE.exists():
            data = json.loads(MARKET_REGIMES_FILE.read_text())
            return data.get("markets", {})
    except Exception:
        pass
    return {}


def get_market_data(coin: str) -> dict | None:
    """Get HL market data for a coin from market_monitor cache."""
    markets = _cached("market_regimes", 60, _load_market_regimes) or {}
    return markets.get(coin)


# ─── ENRICHMENT SIGNALS ──────────────────────────────────────────────────────

class EnrichmentSignal:
    """Enrichment analysis for a single entry decision."""

    def __init__(self, coin: str, direction: str):
        self.coin = coin
        self.direction = direction.upper()
        self.flags = []       # human-readable flags
        self.boost = 0.0      # positive = confirms, negative = warns
        self.block = False    # hard block
        self.block_reason = ""

    def analyze(self):
        """Run all enrichment checks."""
        self._check_funding()
        self._check_funding_trend()
        self._check_book_imbalance()
        self._check_oi_divergence()
        self._check_fear_greed()
        self._check_macro()
        return self

    def _check_funding(self):
        """Funding rate signal — contrarian indicator."""
        md = get_market_data(self.coin)
        if not md:
            return

        funding = md.get("funding_rate", 0)
        if funding is None:
            return

        # High positive funding = market crowded long
        if funding > 0.0001 and self.direction == "LONG":
            self.flags.append(f"⚠ funding={funding:.4%} (crowded long, contrarian SHORT)")
            self.boost -= 0.5
        elif funding > 0.0003 and self.direction == "LONG":
            self.flags.append(f"🛑 funding={funding:.4%} (extreme crowded long)")
            self.boost -= 1.0

        # High negative funding = market crowded short
        if funding < -0.0001 and self.direction == "SHORT":
            self.flags.append(f"⚠ funding={funding:.4%} (crowded short, contrarian LONG)")
            self.boost -= 0.5
        elif funding < -0.0003 and self.direction == "SHORT":
            self.flags.append(f"🛑 funding={funding:.4%} (extreme crowded short)")
            self.boost -= 1.0

        # Funding confirms direction
        if funding > 0.0001 and self.direction == "SHORT":
            self.flags.append(f"✅ funding={funding:.4%} (crowded long, SHORT confirmed)")
            self.boost += 0.3
        if funding < -0.0001 and self.direction == "LONG":
            self.flags.append(f"✅ funding={funding:.4%} (crowded short, LONG confirmed)")
            self.boost += 0.3

    def _check_funding_trend(self):
        """Funding rate trend over last 8h — momentum of sentiment."""
        ft = get_funding_trend(self.coin)
        if not ft:
            return
        trend = ft.get("trend", 0)
        avg = ft.get("average_8h", 0)

        # Funding trending up while going long = increasing crowd risk
        if trend > 0.0002 and self.direction == "LONG":
            self.flags.append(f"⚠ funding rising (trend={trend:.5f}), longs getting crowded")
            self.boost -= 0.3
        elif trend < -0.0002 and self.direction == "SHORT":
            self.flags.append(f"⚠ funding falling (trend={trend:.5f}), shorts getting crowded")
            self.boost -= 0.3

        # Funding reversing (was positive, now negative) = sentiment flip
        if avg > 0 and ft.get("current", 0) < 0:
            self.flags.append(f"✅ funding REVERSED (avg={avg:.5f}→curr={ft['current']:.5f})")
            if self.direction == "SHORT":
                self.boost += 0.4

    def _check_book_imbalance(self):
        """Order book bid/ask imbalance — near-term pressure."""
        book = get_book_imbalance(self.coin)
        imbalance = book.get("imbalance", 0)

        # Strong bid wall = near-term bullish pressure
        if imbalance > 0.3 and self.direction == "LONG":
            self.flags.append(f"✅ book bid-heavy (imb={imbalance:+.2f})")
            self.boost += 0.2
        elif imbalance > 0.3 and self.direction == "SHORT":
            self.flags.append(f"⚠ book bid-heavy (imb={imbalance:+.2f}), shorting into bids")
            self.boost -= 0.2

        # Strong ask wall = near-term bearish pressure
        if imbalance < -0.3 and self.direction == "SHORT":
            self.flags.append(f"✅ book ask-heavy (imb={imbalance:+.2f})")
            self.boost += 0.2
        elif imbalance < -0.3 and self.direction == "LONG":
            self.flags.append(f"⚠ book ask-heavy (imb={imbalance:+.2f}), buying into resistance")
            self.boost -= 0.2

    def _check_oi_divergence(self):
        """OI vs price direction — genuine trend vs squeeze."""
        md = get_market_data(self.coin)
        if not md:
            return

        change_pct = md.get("change_pct", 0)
        # We don't have OI change directly from market_regimes yet,
        # but we can flag volume anomalies
        volume = md.get("volume_24h", 0)
        oi = md.get("open_interest", 0)

        price = md.get("price", 0)
        oi_notional = oi * price if price > 0 else oi
        if oi_notional > 0 and volume > 0:
            vol_oi = volume / oi_notional
            if vol_oi > 3.0:
                self.flags.append(f"⚠ vol/OI={vol_oi:.1f}x (extreme turnover, volatile)")
                self.boost -= 0.3
            elif vol_oi < 0.1:
                self.flags.append(f"⚠ vol/OI={vol_oi:.2f}x (dead market, low liquidity)")
                self.boost -= 0.5

    def _check_fear_greed(self):
        """Macro sentiment filter."""
        fg = get_fear_greed()
        value = fg.get("value", 50)

        if value < 20:
            # Extreme fear — contrarian buy
            if self.direction == "LONG":
                self.flags.append(f"✅ fear/greed={value} EXTREME FEAR (contrarian LONG)")
                self.boost += 0.5
            else:
                self.flags.append(f"⚠ fear/greed={value} EXTREME FEAR (risky SHORT)")
                self.boost -= 0.3

        elif value > 80:
            # Extreme greed — contrarian sell
            if self.direction == "SHORT":
                self.flags.append(f"✅ fear/greed={value} EXTREME GREED (contrarian SHORT)")
                self.boost += 0.5
            else:
                self.flags.append(f"⚠ fear/greed={value} EXTREME GREED (risky LONG)")
                self.boost -= 0.3

    def _check_macro(self):
        """BTC dominance + total market cap trend."""
        gm = get_global_market()
        btc_dom = gm.get("btc_dominance", 50)
        mkt_change = gm.get("total_market_cap_change_24h", 0)

        # BTC dominance rising = risk-off for altcoins
        if self.coin != "BTC" and btc_dom > 60:
            self.flags.append(f"⚠ BTC dom={btc_dom}% (risk-off, altcoins fragile)")
            self.boost -= 0.2

        # Total market falling significantly
        if mkt_change < -3 and self.direction == "LONG":
            self.flags.append(f"⚠ market 24h={mkt_change:+.1f}% (macro bearish)")
            self.boost -= 0.3
        elif mkt_change > 3 and self.direction == "SHORT":
            self.flags.append(f"⚠ market 24h={mkt_change:+.1f}% (macro bullish)")
            self.boost -= 0.3

    def summary(self) -> str:
        """One-line summary for logging."""
        status = "BLOCK" if self.block else ("WARN" if self.boost < -0.5 else "OK")
        flags_str = " | ".join(self.flags) if self.flags else "no enrichment flags"
        return f"[ENRICH] {self.coin} {self.direction}: {status} (boost={self.boost:+.1f}) — {flags_str}"


# ─── HL FUNDING HISTORY ───────────────────────────────────────────────────────

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

def _fetch_funding_history(coin: str) -> list[float]:
    """Fetch last 8 hourly funding rates for a coin. FREE."""
    import urllib.request
    start_ms = int((time.time() - 8 * 3600) * 1000)
    payload = json.dumps({"type": "fundingHistory", "coin": coin, "startTime": start_ms}).encode()
    req = urllib.request.Request(HL_INFO_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [float(x.get("fundingRate", 0)) for x in data[-8:]]
    except Exception:
        return []


def get_funding_trend(coin: str) -> dict | None:
    """Get funding rate trend (last 8h). Cached 15 min per coin."""
    rates = _cached(f"funding_{coin}", 900, lambda: _fetch_funding_history(coin))
    if not rates or len(rates) < 2:
        return None
    avg = sum(rates) / len(rates)
    trend = rates[-1] - rates[0]  # positive = funding rising
    return {
        "current": rates[-1],
        "average_8h": avg,
        "trend": trend,  # positive = longs paying more, getting crowded
        "count": len(rates),
    }


# ─── HL ORDER BOOK IMBALANCE ─────────────────────────────────────────────────

def _fetch_book_imbalance(coin: str) -> dict:
    """Fetch L2 book and compute bid/ask imbalance. FREE."""
    import urllib.request
    payload = json.dumps({"type": "l2Book", "coin": coin}).encode()
    req = urllib.request.Request(HL_INFO_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        levels = data.get("levels", [[], []])
        bids = levels[0][:10] if len(levels) > 0 else []
        asks = levels[1][:10] if len(levels) > 1 else []

        bid_depth = sum(float(b.get("sz", 0)) * float(b.get("px", 0)) for b in bids)
        ask_depth = sum(float(a.get("sz", 0)) * float(a.get("px", 0)) for a in asks)
        total = bid_depth + ask_depth

        if total == 0:
            return {"imbalance": 0, "bid_depth": 0, "ask_depth": 0}

        # Positive = more bids (buyers), negative = more asks (sellers)
        imbalance = (bid_depth - ask_depth) / total
        return {
            "imbalance": round(imbalance, 3),
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
        }
    except Exception:
        return {"imbalance": 0, "bid_depth": 0, "ask_depth": 0}


def get_book_imbalance(coin: str) -> dict:
    """Get order book imbalance. Cached 2 min per coin."""
    return _cached(f"book_{coin}", 120, lambda: _fetch_book_imbalance(coin)) or {"imbalance": 0}


def check_entry(coin: str, direction: str) -> EnrichmentSignal:
    """Run all enrichment checks for an entry decision."""
    return EnrichmentSignal(coin, direction).analyze()

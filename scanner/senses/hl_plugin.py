"""
ZERO OS — Hyperliquid SensePlugin.

Fetches funding rates, open interest, volume, mark prices, and L2 order book
depth from the Hyperliquid API.
Extracted from scanner/agents/perception.py.
"""

from __future__ import annotations

import json
import time
import urllib.request

from scanner.core.interfaces import Observation
from scanner.senses.base import SensePlugin

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def _hl_post(payload: dict, timeout: int = 15) -> dict | list:
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _fetch_meta() -> dict[str, dict]:
    """Fetch metaAndAssetCtxs — returns {coin: {mark, oracle, funding, oi, volume, ...}}."""
    resp = _hl_post({"type": "metaAndAssetCtxs"})
    meta_list = resp[0]
    ctxs = resp[1]

    result = {}
    for i, ctx in enumerate(ctxs):
        coin = meta_list["universe"][i]["name"]
        mark = float(ctx.get("markPx") or 0)
        oracle = float(ctx.get("oraclePx") or 0)
        mid = float(ctx.get("midPx") or 0)
        funding = float(ctx.get("funding") or 0)
        oi = float(ctx.get("openInterest", 0))
        vol = float(ctx.get("dayNtlVlm", 0))
        spread_pct = (mark - oracle) / oracle * 100 if oracle > 0 else 0.0

        result[coin] = {
            "mark": mark,
            "oracle": oracle,
            "mid": mid,
            "funding": funding,
            "funding_pct": round(funding * 100, 6),
            "funding_ann": round(funding * 365 * 3 * 100, 1),
            "open_interest": round(oi, 2),
            "volume_24h": round(vol, 2),
            "spread_pct": round(spread_pct, 6),
            "spread_abs": round(abs(spread_pct), 6),
        }
    return result


def _fetch_l2_book(coin: str) -> dict:
    """Fetch L2 order book for a single coin."""
    raw = _hl_post({"type": "l2Book", "coin": coin})
    levels = raw.get("levels", [[], []])
    bids = [(float(l["px"]), float(l["sz"])) for l in (levels[0] if len(levels) > 0 else [])]
    asks = [(float(l["px"]), float(l["sz"])) for l in (levels[1] if len(levels) > 1 else [])]
    return {"bids": bids, "asks": asks}


class HyperliquidPlugin(SensePlugin):
    """Fetches market data from the Hyperliquid API."""

    name = "hyperliquid"

    def __init__(self, fetch_l2: bool = True, l2_delay: float = 0.15):
        self._fetch_l2 = fetch_l2
        self._l2_delay = l2_delay

    def fetch(self, coins: list[str]) -> list[Observation]:
        now = time.time()
        observations: list[Observation] = []

        # Fetch meta (mark prices, funding, OI, volume)
        meta = _fetch_meta()
        for coin in coins:
            data = meta.get(coin)
            if not data:
                continue

            observations.append(Observation(
                coin=coin, dimension="hl.mark_price", value=data["mark"],
                confidence=1.0, source="hyperliquid", timestamp=now,
            ))
            observations.append(Observation(
                coin=coin, dimension="hl.oracle_price", value=data["oracle"],
                confidence=1.0, source="hyperliquid", timestamp=now,
            ))
            observations.append(Observation(
                coin=coin, dimension="hl.funding_rate", value=data["funding"],
                confidence=1.0, source="hyperliquid", timestamp=now,
                metadata={"pct": data["funding_pct"], "annualized": data["funding_ann"]},
            ))
            observations.append(Observation(
                coin=coin, dimension="hl.open_interest", value=data["open_interest"],
                confidence=1.0, source="hyperliquid", timestamp=now,
            ))
            observations.append(Observation(
                coin=coin, dimension="hl.volume_24h", value=data["volume_24h"],
                confidence=1.0, source="hyperliquid", timestamp=now,
            ))
            observations.append(Observation(
                coin=coin, dimension="hl.spread_pct", value=data["spread_pct"],
                confidence=1.0, source="hyperliquid", timestamp=now,
            ))

        # Fetch L2 order books
        if self._fetch_l2:
            for coin in coins:
                try:
                    book = _fetch_l2_book(coin)
                    bid_depth = sum(sz for _, sz in book["bids"][:10])
                    ask_depth = sum(sz for _, sz in book["asks"][:10])
                    best_bid = book["bids"][0][0] if book["bids"] else 0.0
                    best_ask = book["asks"][0][0] if book["asks"] else 0.0

                    observations.append(Observation(
                        coin=coin, dimension="hl.bid_depth_10", value=bid_depth,
                        confidence=1.0, source="hyperliquid", timestamp=now,
                    ))
                    observations.append(Observation(
                        coin=coin, dimension="hl.ask_depth_10", value=ask_depth,
                        confidence=1.0, source="hyperliquid", timestamp=now,
                    ))
                    if best_bid > 0 and best_ask > 0:
                        book_spread = (best_ask - best_bid) / best_bid * 100
                        observations.append(Observation(
                            coin=coin, dimension="hl.book_spread_pct", value=round(book_spread, 6),
                            confidence=1.0, source="hyperliquid", timestamp=now,
                        ))
                except Exception:
                    pass
                time.sleep(self._l2_delay)

        return observations

    def health_check(self) -> dict:
        try:
            _hl_post({"type": "meta"}, timeout=5)
            return {"name": self.name, "status": "ok"}
        except Exception as e:
            return {"name": self.name, "status": "error", "error": str(e)}

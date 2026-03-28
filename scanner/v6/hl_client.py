#!/usr/bin/env python3
"""
HLClient — Hyperliquid HTTP adapter.

Extracted from executor.py (Session 8b) so controller.py can import it cleanly.
All HL communication lives here; controller.py handles all trading logic.

Usage:
    from scanner.v6.hl_client import HLClient, load_hl_meta, COIN_TO_ASSET, COIN_SZ_DECIMALS
"""

import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.config import HL_MAIN_ADDRESS, HL_INFO_URL, HL_EXCHANGE_URL
from scanner.exceptions import APIError, InsufficientFundsError, OrderError
from scanner.utils import make_logger

log = make_logger("HL")


# ─── COIN METADATA ────────────────────────────────────────────────────────────

COIN_TO_ASSET    : dict[str, int] = {}
COIN_SZ_DECIMALS : dict[str, int] = {}
COIN_MAX_LEV     : dict[str, int] = {}


def load_hl_meta() -> None:
    """Fetch HL meta: asset indices, sz decimals, max leverage."""
    global COIN_TO_ASSET, COIN_SZ_DECIMALS, COIN_MAX_LEV
    try:
        req = urllib.request.Request(
            HL_INFO_URL,
            data=json.dumps({"type": "meta"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        meta = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for i, u in enumerate(meta["universe"]):
            COIN_TO_ASSET[u["name"]]    = i
            COIN_SZ_DECIMALS[u["name"]] = u["szDecimals"]
            COIN_MAX_LEV[u["name"]]     = u.get("maxLeverage", 10)
        log(f"Loaded {len(COIN_TO_ASSET)} coins from HL meta")
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
        log(f"WARN: HL meta fetch failed: {e} — using hardcoded fallback")
        COIN_TO_ASSET.update({
            "BTC": 0,  "ETH": 1,  "SOL": 5,  "DOGE": 12, "AVAX": 6,
            "LINK": 18, "ARB": 11, "NEAR": 74, "SUI": 14,  "INJ": 13,
            "ADA": 7,  "BNB": 10, "XRP": 2,  "ONDO": 154, "TRUMP": 224,
        })
        COIN_SZ_DECIMALS.update({
            "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0, "AVAX": 2,
            "LINK": 1, "ARB": 1, "NEAR": 1, "SUI": 1,  "INJ": 1,
            "ADA": 0, "BNB": 3, "XRP": 1, "ONDO": 0, "TRUMP": 1,
        })


# ─── HYPERLIQUID CLIENT ───────────────────────────────────────────────────────

class HLClient:
    """Hyperliquid HTTP client — urllib + eth_account only (no SDK dep)."""

    def __init__(self, private_key: str, main_address: str):
        from eth_account import Account as EthAccount
        self.wallet       = EthAccount.from_key(private_key)
        self.address      = self.wallet.address
        self.main_address = main_address
        log(f"Wallet: {self.address} (main: {self.main_address})")

    # ── Internal ──────────────────────────────────────────────────────────

    def _info_post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            HL_INFO_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def _sign_and_send(self, action: dict) -> dict:
        from hyperliquid.utils.signing import sign_l1_action, get_timestamp_ms
        time.sleep(0.05)  # prevent nonce collision
        ts  = get_timestamp_ms()
        sig = sign_l1_action(self.wallet, action, None, ts, None, True)
        payload = json.dumps({"action": action, "nonce": ts, "signature": sig})
        req = urllib.request.Request(
            HL_EXCHANGE_URL,
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    @staticmethod
    def _gen_cloid() -> str:
        import uuid
        return "0x" + uuid.uuid4().hex

    # ── Account queries ───────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Total equity from HL. RAISES on API failure. Zero is never valid."""
        spot_usdc = 0.0
        try:
            spot = self._info_post({"type": "spotClearinghouseState", "user": self.main_address})
            for bal in spot.get("balances", []):
                if bal.get("coin") == "USDC":
                    spot_usdc = float(bal.get("total", 0))
                    break
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: spot balance fetch failed: {e} — using perp equity only")

        result     = self._info_post({"type": "clearinghouseState", "user": self.main_address})
        perp_equity = float(result.get("marginSummary", {}).get("accountValue", 0))

        if spot_usdc > 0:
            unrealized_pnl = sum(
                float(p.get("position", {}).get("unrealizedPnl", 0))
                for p in result.get("assetPositions", [])
            )
            equity = spot_usdc + unrealized_pnl
        elif perp_equity > 0:
            equity = perp_equity
        else:
            raise InsufficientFundsError(
                f"get_balance: no funds found (spot={spot_usdc}, perp={perp_equity})",
                required=0, available=0,
            )

        if equity <= 0:
            raise InsufficientFundsError(
                f"get_balance: equity is {equity} — refusing to return",
                required=0, available=equity,
            )
        return equity

    def get_positions(self) -> list:
        result = self._info_post({"type": "clearinghouseState", "user": self.main_address})
        return result.get("assetPositions", [])

    def get_open_orders(self) -> list:
        return self._info_post({"type": "openOrders", "user": self.main_address})

    def get_price(self, coin: str) -> float:
        """Get mid price. RAISES if price is zero or missing."""
        mids  = self._info_post({"type": "allMids"})
        price = float(mids.get(coin, 0))
        if price <= 0:
            raise ValueError(f"get_price({coin}): returned {price} — API degraded or coin delisted")
        return price

    def get_fee_rates(self) -> dict:
        try:
            resp  = self._info_post({"type": "userFees", "user": self.main_address})
            taker = float(resp.get("userCrossRate", 0.00045))
            maker = float(resp.get("userAddRate",   0.00015))
            return {"taker": taker, "maker": maker}
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: fee rate query failed: {e}")
            return {"taker": 0.00045, "maker": 0.00015}

    def get_predicted_funding(self, coin: str) -> float:
        try:
            resp = self._info_post({"type": "predictedFundings"})
            for item in resp:
                if item[0] == coin:
                    for venue in item[1]:
                        if venue[0] == "HlPerp":
                            return float(venue[1].get("fundingRate", 0))
            return 0.0
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: funding query failed for {coin}: {e}")
            return 0.0

    def get_l2_book(self, coin: str, depth: int = 5) -> dict:
        try:
            resp   = self._info_post({"type": "l2Book", "coin": coin})
            levels = resp.get("levels", [[], []])
            bids   = [(float(b["px"]), float(b["sz"])) for b in levels[0][:depth]]
            asks   = [(float(a["px"]), float(a["sz"])) for a in levels[1][:depth]]
            return {
                "bids":          bids,
                "asks":          asks,
                "bid_depth_usd": sum(px * sz for px, sz in bids),
                "ask_depth_usd": sum(px * sz for px, sz in asks),
            }
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: l2Book failed for {coin}: {e}")
            return {"bids": [], "asks": [], "bid_depth_usd": 0, "ask_depth_usd": 0, "api_error": str(e)}

    def get_rate_limit(self) -> dict:
        try:
            resp = self._info_post({"type": "userRateLimit", "user": self.main_address})
            return {
                "used":       resp.get("nRequestsUsed", 0),
                "cap":        resp.get("nRequestsCap", 10000),
                "cum_volume": float(resp.get("cumVlm", 0)),
            }
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: rate limit query failed: {e}")
            return {"used": 0, "cap": 10000, "cum_volume": 0}

    # ── Price / size helpers ──────────────────────────────────────────────

    @staticmethod
    def round_price(price: float) -> float:
        if price <= 0:       return 0
        if price >= 10000:   return round(price, 0)
        elif price >= 1000:  return round(price, 1)
        elif price >= 100:   return round(price, 2)
        elif price >= 10:    return round(price, 3)
        elif price >= 1:     return round(price, 4)
        elif price >= 0.1:   return round(price, 5)
        else:                return round(price, 6)

    @staticmethod
    def float_to_wire(x: float) -> str:
        rounded = round(x, 8)
        if abs(rounded) >= 1e15:
            return f"{int(rounded)}"
        s = f"{rounded:.8f}"
        return s.rstrip("0").rstrip(".")

    # ── Order placement ───────────────────────────────────────────────────

    def place_ioc_order(self, coin: str, is_buy: bool, size: float,
                        limit_price: float, reduce_only: bool = False) -> dict:
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}
        sz_dec = COIN_SZ_DECIMALS.get(coin, 2)
        cloid  = self._gen_cloid()
        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": self.float_to_wire(self.round_price(limit_price)),
                "s": self.float_to_wire(round(size, sz_dec)),
                "r": reduce_only,
                "t": {"limit": {"tif": "Ioc"}},
                "c": cloid,
            }],
            "grouping": "na",
        }
        result = self._sign_and_send(action)
        result["_cloid"] = cloid
        return result

    def place_gtc_order(self, coin: str, is_buy: bool, size: float,
                        limit_price: float, reduce_only: bool = False) -> dict:
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}
        sz_dec = COIN_SZ_DECIMALS.get(coin, 2)
        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": self.float_to_wire(self.round_price(limit_price)),
                "s": self.float_to_wire(round(size, sz_dec)),
                "r": reduce_only,
                "t": {"limit": {"tif": "Gtc"}},
            }],
            "grouping": "na",
        }
        return self._sign_and_send(action)

    def place_stop_loss(self, coin: str, is_buy: bool, size: float,
                        trigger_price: float) -> dict:
        """Place native stop-loss market trigger order on HL."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}
        sz_dec      = COIN_SZ_DECIMALS.get(coin, 2)
        sz_str      = self.float_to_wire(round(size, sz_dec))
        trigger_str = self.float_to_wire(self.round_price(trigger_price))
        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": trigger_str,
                "s": sz_str,
                "r": True,
                "t": {"trigger": {
                    "isMarket":  True,
                    "triggerPx": trigger_str,
                    "tpsl":      "sl",
                }},
            }],
            "grouping": "na",
        }
        return self._sign_and_send(action)

    def market_buy(self, coin: str, size: float, slippage: float = 0.01,
                   reduce_only: bool = False) -> dict:
        price = self.get_price(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        return self.place_ioc_order(coin, True, size,
                                    self.round_price(price * (1 + slippage)),
                                    reduce_only=reduce_only)

    def market_sell(self, coin: str, size: float, slippage: float = 0.01,
                    reduce_only: bool = False) -> dict:
        price = self.get_price(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        return self.place_ioc_order(coin, False, size,
                                    self.round_price(price * (1 - slippage)),
                                    reduce_only=reduce_only)

    def cancel_order(self, coin: str, oid: int) -> dict:
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}
        action = {
            "type":    "cancel",
            "cancels": [{"a": asset, "o": oid}],
        }
        return self._sign_and_send(action)

    def cancel_coin_stops(self, coin: str) -> None:
        """Cancel all open orders for a coin."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return
        orders    = self.get_open_orders()
        stop_oids = [o["oid"] for o in orders if o.get("coin") == coin]
        if not stop_oids:
            return
        action = {
            "type":    "cancel",
            "cancels": [{"a": asset, "o": oid} for oid in stop_oids],
        }
        try:
            self._sign_and_send(action)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            log(f"WARN: cancel stops for {coin}: {e}")

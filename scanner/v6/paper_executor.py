#!/usr/bin/env python3
"""
Paper Executor — virtual trading with identical HLClient interface.

Tracks positions, P&L, and stops in ~/.zeroos/state/paper_state.json.
Uses real prices from NVArena snapshot API. No real orders placed.
"""

import json
import math
import os
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.config import get_env


# ─── STATE ───────────────────────────────────────────────────────────────────

PAPER_STATE_DIR = Path("~/.zeroos/state").expanduser()
PAPER_STATE_FILE = PAPER_STATE_DIR / "paper_state.json"

DEFAULT_STATE = {
    "balance": 10000.0,
    "positions": {},      # coin → {direction, size, entry_price, size_usd}
    "stops": {},          # coin → {trigger_price, is_buy, size, oid}
    "trade_log": [],      # last 100 trades
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [PAPER] {msg}")


def _load_state() -> dict:
    if PAPER_STATE_FILE.exists():
        try:
            with open(PAPER_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_STATE)


def _save_state(state: dict):
    PAPER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PAPER_STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(PAPER_STATE_FILE)


# ─── PRICE SOURCE ────────────────────────────────────────────────────────────

_price_cache: dict[str, float] = {}


def _fetch_price(coin: str) -> float:
    """Fetch real price from Hyperliquid REST API (free, no rate limits)."""
    # Use HL allMids — free, fast, no API key needed
    # Do NOT call NVArena snapshot for prices — wastes credits and triggers rate limits
    try:
        hl_url = "https://api.hyperliquid.xyz/info"
        data = json.dumps({"type": "allMids"}).encode()
        req = urllib.request.Request(hl_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        mids = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if isinstance(mids, dict):
            price = float(mids.get(coin, 0))
            if price > 0:
                _price_cache[coin] = price
                return price
    except Exception as e:
        _log(f"WARN: HL price fetch failed for {coin}: {e}")

    # Last resort: cache
    cached = _price_cache.get(coin, 0)
    if cached > 0:
        return cached
    raise ValueError(f"No price available for {coin}")


# ─── PAPER EXECUTOR ─────────────────────────────────────────────────────────

class PaperExecutor:
    """Drop-in replacement for HLClient — all virtual, no real orders."""

    def __init__(self):
        self.state = _load_state()
        self.address = "0xPAPER_EXECUTOR"
        self.main_address = "0xPAPER_EXECUTOR"
        _log(f"Paper executor loaded: balance=${self.state['balance']:.2f}, "
             f"positions={len(self.state['positions'])}")

    def _save(self):
        _save_state(self.state)

    def load_state(self) -> dict:
        """Return a copy of the current paper state (balance, positions, stops, trade_log)."""
        return dict(self.state)

    def _next_oid(self) -> int:
        return int(time.time() * 1000) + (hash(uuid.uuid4()) % 10000)

    # ── Core Interface (matches HLClient) ────────────────────────────────

    def get_balance(self) -> float:
        """Virtual equity = balance + unrealized P&L."""
        upnl = 0.0
        for coin, pos in self.state["positions"].items():
            try:
                current = _fetch_price(coin)
                entry = pos["entry_price"]
                sz = pos["size"]
                if pos["direction"] == "LONG":
                    upnl += (current - entry) * sz
                else:
                    upnl += (entry - current) * sz
            except Exception:
                pass
        equity = self.state["balance"] + upnl
        if equity <= 0:
            raise ValueError(f"Paper equity is {equity}")
        return equity

    def get_positions(self) -> list:
        """Return positions in HL format: [{position: {coin, szi, entryPx, unrealizedPnl}}]."""
        result = []
        for coin, pos in self.state["positions"].items():
            try:
                current = _fetch_price(coin)
            except Exception:
                current = pos["entry_price"]
            sz = pos["size"]
            if pos["direction"] == "LONG":
                upnl = (current - pos["entry_price"]) * sz
                szi = sz
            else:
                upnl = (pos["entry_price"] - current) * sz
                szi = -sz
            result.append({
                "position": {
                    "coin": coin,
                    "szi": str(szi),
                    "entryPx": str(pos["entry_price"]),
                    "unrealizedPnl": str(round(upnl, 4)),
                    "positionValue": str(round(current * sz, 2)),
                    "leverage": {"type": "cross", "value": 3},
                }
            })
        return result

    def get_price(self, coin: str) -> float:
        """Fetch real price. Also checks stop triggers."""
        price = _fetch_price(coin)
        self._check_stops(coin, price)
        return price

    def get_open_orders(self) -> list:
        """Return virtual stop orders in HL format."""
        orders = []
        for coin, stop in self.state["stops"].items():
            orders.append({
                "coin": coin,
                "oid": stop["oid"],
                "side": "B" if stop["is_buy"] else "A",
                "sz": str(stop["size"]),
                "triggerPx": str(stop["trigger_price"]),
                "orderType": "Stop Loss",
            })
        return orders

    def place_ioc_order(self, coin: str, is_buy: bool, size: float,
                        limit_price: float, reduce_only: bool = False) -> dict:
        """Simulate IOC fill at current price with fee deduction."""
        try:
            current_price = _fetch_price(coin)
        except Exception:
            current_price = limit_price

        # Simulate slippage: fill at slightly worse price
        if is_buy:
            fill_price = min(limit_price, current_price * 1.0005)
        else:
            fill_price = max(limit_price, current_price * 0.9995)

        fill_price = self.round_price(fill_price)
        oid = self._next_oid()

        if reduce_only:
            self._process_close(coin, is_buy, size, fill_price)
        else:
            self._process_open(coin, is_buy, size, fill_price)

        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{
                        "filled": {
                            "totalSz": str(size),
                            "avgPx": str(fill_price),
                            "oid": oid,
                        }
                    }]
                }
            }
        }

    def place_stop_loss(self, coin: str, is_buy: bool, size: float,
                        trigger_price: float, limit_offset_pct: float = 0.02) -> dict:
        """Register a virtual stop-loss order."""
        oid = self._next_oid()
        self.state["stops"][coin] = {
            "trigger_price": trigger_price,
            "is_buy": is_buy,
            "size": size,
            "oid": oid,
        }
        self._save()
        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{
                        "resting": {
                            "oid": oid,
                        }
                    }]
                }
            }
        }

    def cancel_coin_stops(self, coin: str):
        """Remove virtual stops for a coin."""
        if coin in self.state["stops"]:
            del self.state["stops"][coin]
            self._save()

    def _get_price_or_fallback(self, coin: str) -> float:
        """Get price with fallback to position entry price for closes."""
        try:
            return self.get_price(coin)
        except ValueError:
            pos = self.state["positions"].get(coin)
            if pos:
                return pos["entry_price"]
            raise

    def market_buy(self, coin: str, size: float, slippage: float = 0.01,
                   reduce_only: bool = False) -> dict:
        price = self._get_price_or_fallback(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        limit = self.round_price(price * (1 + slippage))
        return self.place_ioc_order(coin, True, size, limit, reduce_only=reduce_only)

    def market_sell(self, coin: str, size: float, slippage: float = 0.01,
                    reduce_only: bool = False) -> dict:
        price = self._get_price_or_fallback(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        limit = self.round_price(price * (1 - slippage))
        return self.place_ioc_order(coin, False, size, limit, reduce_only=reduce_only)

    def get_fee_rates(self) -> dict:
        return {"taker": 0.00045, "maker": 0.00015}

    def get_predicted_funding(self, coin: str) -> float:
        return 0.0

    def get_rate_limit(self) -> dict:
        return {"used": 0, "cap": 10000, "cum_volume": 0}

    def get_l2_book(self, coin: str, depth: int = 5) -> dict:
        """Mock L2 book with high depth so trades always pass liquidity checks."""
        try:
            price = _fetch_price(coin)
        except Exception:
            price = 100.0
        bids = [(price * (1 - 0.001 * i), 100000.0) for i in range(1, depth + 1)]
        asks = [(price * (1 + 0.001 * i), 100000.0) for i in range(1, depth + 1)]
        bid_depth = sum(px * sz for px, sz in bids)
        ask_depth = sum(px * sz for px, sz in asks)
        return {"bids": bids, "asks": asks, "bid_depth_usd": bid_depth, "ask_depth_usd": ask_depth}

    @staticmethod
    def round_price(price: float) -> float:
        if price <= 0:
            return 0
        if price >= 10000:
            return round(price, 0)
        elif price >= 1000:
            return round(price, 1)
        elif price >= 100:
            return round(price, 2)
        elif price >= 10:
            return round(price, 3)
        elif price >= 1:
            return round(price, 4)
        elif price >= 0.1:
            return round(price, 5)
        else:
            return round(price, 6)

    @staticmethod
    def float_to_wire(x: float) -> str:
        rounded = round(x, 8)
        if abs(rounded) >= 1e15:
            return f"{int(rounded)}"
        s = f"{rounded:.8f}"
        return s.rstrip("0").rstrip(".")

    # ── Internal: Position Management ────────────────────────────────────

    def _process_open(self, coin: str, is_buy: bool, size: float, fill_price: float):
        """Record a new virtual position."""
        direction = "LONG" if is_buy else "SHORT"
        fee = fill_price * size * 0.00045  # taker fee
        self.state["balance"] -= fee

        self.state["positions"][coin] = {
            "direction": direction,
            "size": size,
            "entry_price": fill_price,
            "size_usd": round(fill_price * size, 2),
        }

        self._log_trade("open", coin, direction, size, fill_price, fee=fee)
        self._save()

    def _process_close(self, coin: str, is_buy: bool, size: float, fill_price: float):
        """Close a virtual position, compute P&L."""
        pos = self.state["positions"].get(coin)
        if not pos:
            return

        entry_price = pos["entry_price"]
        pos_size = pos["size"]
        direction = pos["direction"]

        # P&L: direction-aware
        if direction == "LONG":
            pnl_gross = (fill_price - entry_price) * pos_size
        else:
            pnl_gross = (entry_price - fill_price) * pos_size

        # Fee on close
        fee = fill_price * pos_size * 0.00045
        pnl_net = pnl_gross - fee

        self.state["balance"] += pnl_net
        del self.state["positions"][coin]

        # Remove any stops
        self.state["stops"].pop(coin, None)

        self._log_trade("close", coin, direction, pos_size, fill_price,
                         entry_price=entry_price, pnl=pnl_net, fee=fee)
        self._save()
        _log(f"Closed {direction} {coin}: entry=${entry_price:.4f} exit=${fill_price:.4f} "
             f"pnl=${pnl_net:.2f} (fee=${fee:.4f})")

    def _check_stops(self, coin: str, current_price: float):
        """Simulate stop trigger on price check."""
        stop = self.state["stops"].get(coin)
        if not stop:
            return

        triggered = False
        if stop["is_buy"] and current_price >= stop["trigger_price"]:
            triggered = True  # buying to close SHORT — price went up
        elif not stop["is_buy"] and current_price <= stop["trigger_price"]:
            triggered = True  # selling to close LONG — price went down

        if triggered:
            _log(f"STOP TRIGGERED: {coin} @ ${current_price:.4f} (trigger=${stop['trigger_price']:.4f})")
            pos = self.state["positions"].get(coin)
            if pos:
                is_buy_close = stop["is_buy"]
                self._process_close(coin, is_buy_close, stop["size"], current_price)

    def _log_trade(self, action: str, coin: str, direction: str, size: float,
                   price: float, entry_price: float = 0, pnl: float = 0, fee: float = 0):
        """Append to trade log (keep last 100)."""
        self.state["trade_log"].append({
            "ts": _now_iso(),
            "action": action,
            "coin": coin,
            "direction": direction,
            "size": size,
            "price": price,
            "entry_price": entry_price,
            "pnl": round(pnl, 4),
            "fee": round(fee, 4),
        })
        # Keep only last 100
        if len(self.state["trade_log"]) > 100:
            self.state["trade_log"] = self.state["trade_log"][-100:]

#!/usr/bin/env python3
"""
V6 Executor — Hyperliquid execution + Telegram + Supabase.

Reads:  scanner/v6/bus/approved.json   (risk-cleared entries)
        scanner/v6/bus/exits.json      (exit signals)
        scanner/v6/bus/positions.json  (open positions)
        scanner/v6/bus/allocation.json (portfolio weights → sizing)
Writes: scanner/v6/bus/positions.json  (updated after open/close)
        scanner/v6/bus/risk.json       (daily_loss_usd updates)
        scanner/v6/data/trades.jsonl   (local trade log)
        Supabase trades + positions tables
        Telegram alerts on open/close

Usage:
  python3 scanner/v6/executor.py           # single run
  python3 scanner/v6/executor.py --loop    # continuous 5s cycle
  python3 scanner/v6/executor.py --dry     # paper mode (no real orders)
"""

import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scanner.v6.config import (
    HL_MAIN_ADDRESS, HL_INFO_URL, HL_EXCHANGE_URL,
    APPROVED_FILE, EXITS_FILE, POSITIONS_FILE, RISK_FILE, HEARTBEAT_FILE,
    ALLOCATION_FILE, TRADES_FILE, BUS_DIR, DATA_DIR,
    MAX_POSITION_USD, MIN_POSITION_USD, STOP_LOSS_PCT, STRATEGY_VERSION,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN_ENV, get_env, get_stop_pct,
    get_dynamic_limits, FEE_RATE,
)

CYCLE_SECONDS = 5


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [EXEC] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as _e:
            pass
    return default


def save_json_atomic(path: Path, data: dict):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def load_json_locked(path: Path, default=None):
    """Read JSON with shared lock to prevent partial reads during writes."""
    import fcntl
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as _e:
        return default


def save_json_locked(path: Path, data: dict):
    """Write JSON with exclusive lock + fsync."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE, {})
    hb["executor"] = now_iso()
    save_json_atomic(HEARTBEAT_FILE, hb)


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_alert(message: str):
    """Send Telegram message. Never raises."""
    try:
        token = get_env(TELEGRAM_BOT_TOKEN_ENV)
        if not token:
            return
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     message,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"WARN: Telegram failed: {e}")



# (Supabase removed — all data served from local files via Intelligence API)


# ─── HYPERLIQUID METADATA ─────────────────────────────────────────────────────

COIN_TO_ASSET    : dict[str, int]   = {}
COIN_SZ_DECIMALS : dict[str, int]   = {}
COIN_MAX_LEV     : dict[str, int]   = {}


def load_hl_meta():
    """Fetch HL meta: asset indices, sz decimals, max leverage."""
    global COIN_TO_ASSET, COIN_SZ_DECIMALS, COIN_MAX_LEV
    try:
        req  = urllib.request.Request(
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
    except Exception as e:
        log(f"WARN: HL meta fetch failed: {e} — using hardcoded fallback")
        COIN_TO_ASSET.update({
            "BTC": 0, "ETH": 1, "SOL": 5, "DOGE": 12, "AVAX": 6,
            "LINK": 18, "ARB": 11, "NEAR": 74, "SUI": 14, "INJ": 13,
            "ADA": 7, "BNB": 10, "XRP": 2, "ONDO": 154, "TRUMP": 224,
        })
        COIN_SZ_DECIMALS.update({
            "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0, "AVAX": 2,
            "LINK": 1, "ARB": 1, "NEAR": 1, "SUI": 1, "INJ": 1,
            "ADA": 0, "BNB": 3, "XRP": 1, "ONDO": 0, "TRUMP": 1,
        })


# ─── HYPERLIQUID CLIENT ───────────────────────────────────────────────────────

class HLClient:
    """Hyperliquid HTTP client using only urllib + eth_account."""

    def __init__(self, private_key: str, main_address: str):
        from eth_account import Account as EthAccount
        self.wallet       = EthAccount.from_key(private_key)
        self.address      = self.wallet.address
        self.main_address = main_address
        log(f"Wallet: {self.address} (main: {self.main_address})")

    def _info_post(self, payload: dict) -> dict:
        req  = urllib.request.Request(
            HL_INFO_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def get_balance(self) -> float:
        """Total equity = spot USDC total + perp unrealized PnL.
        
        HL cross-margin: spot USDC 'hold' IS the perp collateral.
        So real equity = spot total (includes held) + perp uPnL only.
        DO NOT add perp accountValue — that double-counts the held USDC.
        
        RAISES on API failure. Zero is never a valid equity.
        """
        # Spot USDC total (includes the portion held as perp collateral)
        spot = self._info_post({"type": "spotClearinghouseState", "user": self.main_address})
        spot_usdc = 0.0
        for bal in spot.get("balances", []):
            if bal.get("coin") == "USDC":
                spot_usdc = float(bal.get("total", 0))
                break

        if spot_usdc <= 0:
            raise ValueError(f"get_balance: spot USDC is {spot_usdc} — API likely failed or account empty")

        # Perp unrealized PnL only (NOT accountValue)
        unrealized_pnl = 0.0
        result = self._info_post({"type": "clearinghouseState", "user": self.main_address})
        for pos in result.get("assetPositions", []):
            p = pos.get("position", {})
            unrealized_pnl += float(p.get("unrealizedPnl", 0))

        equity = spot_usdc + unrealized_pnl
        if equity <= 0:
            raise ValueError(f"get_balance: equity is {equity} (spot={spot_usdc}, uPnL={unrealized_pnl}) — refusing to return")

        return equity

    def get_positions(self) -> list:
        result = self._info_post({"type": "clearinghouseState", "user": self.main_address})
        return result.get("assetPositions", [])

    def get_open_orders(self) -> list:
        return self._info_post({"type": "openOrders", "user": self.main_address})

    def get_price(self, coin: str) -> float:
        """Get mid price. RAISES if price is zero or missing."""
        mids = self._info_post({"type": "allMids"})
        price = float(mids.get(coin, 0))
        if price <= 0:
            raise ValueError(f"get_price({coin}): returned {price} — API degraded or coin delisted")
        return price

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

    def place_ioc_order(self, coin: str, is_buy: bool, size: float, limit_price: float,
                        reduce_only: bool = False) -> dict:
        """Place IOC (Immediate-or-Cancel) order — fills at market or cancels."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}

        sz_dec  = COIN_SZ_DECIMALS.get(coin, 2)
        sz_str  = self.float_to_wire(round(size, sz_dec))
        px_str  = self.float_to_wire(self.round_price(limit_price))

        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": px_str,
                "s": sz_str,
                "r": reduce_only,
                "t": {"limit": {"tif": "Ioc"}},
            }],
            "grouping": "na",
        }
        return self._sign_and_send(action)

    def place_stop_loss(self, coin: str, is_buy: bool, size: float,
                        trigger_price: float) -> dict:
        """Place native stop-loss trigger order."""
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

    def cancel_coin_stops(self, coin: str):
        """Cancel all orders (stops/triggers) for a coin."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return
        orders     = self.get_open_orders()
        stop_oids  = [
            o["oid"] for o in orders
            if o.get("coin") == coin
        ]
        if not stop_oids:
            return
        action = {
            "type":    "cancel",
            "cancels": [{"a": asset, "o": oid} for oid in stop_oids],
        }
        try:
            self._sign_and_send(action)
        except Exception as e:
            log(f"WARN: cancel stops for {coin}: {e}")

    def market_buy(self, coin: str, size: float, slippage: float = 0.01) -> dict:
        price = self.get_price(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        return self.place_ioc_order(coin, True, size, self.round_price(price * (1 + slippage)))

    def market_sell(self, coin: str, size: float, slippage: float = 0.01,
                    reduce_only: bool = False) -> dict:
        price = self.get_price(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}
        return self.place_ioc_order(coin, False, size, self.round_price(price * (1 - slippage)),
                                    reduce_only=reduce_only)


# ─── POSITION SIZING ──────────────────────────────────────────────────────────

def compute_size_usd(trade: dict) -> float:
    """Compute position size using half-Kelly criterion.
    
    Kelly: f* = (p * b - q) / b where:
      p = win rate (from ENVY signal)
      q = 1 - p
      b = avg win / avg loss ratio (estimated from signal stats)
    Half-Kelly: f* / 2 (conservative — reduces variance)
    
    Size = equity * half_kelly_fraction, clamped to MIN/MAX.
    """
    allocation  = load_json(ALLOCATION_FILE, {}).get("allocations", {})
    coin        = trade.get("coin", "")
    weight      = allocation.get(coin, 0)

    # Equity-based sizing
    try:
        equity = float(load_json(BUS_DIR / "portfolio.json", {}).get("account_value", 0))
    except Exception as _e:
        equity = 0

    if not equity:
        log("WARN: No equity in portfolio.json — skipping trade")
        return 0  # refuse to size with no data

    # Dynamic limits from current equity
    limits = get_dynamic_limits(equity)
    min_pos = limits["min_position_usd"]
    max_pos = limits["max_position_usd"]

    if weight > 0:
        size_usd = equity * weight
    else:
        # Half-Kelly sizing
        win_rate = trade.get("win_rate", 50) / 100
        sharpe   = trade.get("sharpe", 1.5)
        b = max(1.0, 1.0 + sharpe * 0.3)
        p = max(0.01, min(0.99, win_rate))
        q = 1 - p
        kelly = (p * b - q) / b if b > 0 else 0
        half_kelly = max(0, kelly / 2)
        half_kelly = min(0.30, max(0.05, half_kelly))
        size_usd = equity * half_kelly
        log(f"  Kelly: p={p:.2f} b={b:.2f} f*={kelly:.3f} f*/2={half_kelly:.3f} → ${size_usd:.0f} (limits ${min_pos:.0f}-${max_pos:.0f})")

    return round(max(min_pos, min(max_pos, size_usd)), 2)


# ─── OPEN TRADE ───────────────────────────────────────────────────────────────

def open_trade(client: HLClient, trade: dict, dry: bool) -> bool:
    """Open a position from an approved entry. Returns True on success."""
    coin       = trade["coin"]
    direction  = trade["direction"]
    is_buy     = direction == "LONG"
    size_usd   = compute_size_usd(trade)
    signal_stop = trade.get("stop_loss_pct", 0)
    stop_pct   = get_stop_pct(coin, signal_stop)

    if dry:
        price     = client.get_price(coin)
        size_coins = round(size_usd / price, COIN_SZ_DECIMALS.get(coin, 2)) if price > 0 else 0
        log(f"  [DRY] Would open {direction} {coin}: ${size_usd:.0f} @ ~${price:,.4f}")
    else:
        price = client.get_price(coin)
        if price <= 0:
            log(f"  ERROR: no price for {coin}")
            return False

        raw_coins = size_usd / price
        decimals = COIN_SZ_DECIMALS.get(coin, 2)
        size_coins = math.floor(raw_coins * 10**decimals) / 10**decimals  # round DOWN, not nearest
        residual_usd = (raw_coins - size_coins) * price
        if residual_usd > 0.01:
            log(f"  Precision residual: ${residual_usd:.2f} ({(residual_usd/size_usd*100):.1f}% of position)")
        if size_coins <= 0:
            log(f"  ERROR: size_coins=0 for {coin} (size_usd=${size_usd:.2f}, price=${price})")
            return False

        log(f"  Opening {direction} {coin}: {size_coins} coins (${size_usd:.0f}) @ ${price:,.4f}")
        result = client.market_buy(coin, size_coins) if is_buy else client.market_sell(coin, size_coins)
        log(f"  Order result: {json.dumps(result)}")

        if result.get("status") == "err":
            log(f"  ERROR: order failed: {result.get('response')}")
            return False

        # Extract fill data from response
        fills = result.get("response", {}).get("data", {}).get("statuses", [{}])
        filled = fills[0].get("filled", {}) if fills else {}
        fill_px = float(filled.get("avgPx", 0))
        filled_sz = float(filled.get("totalSz", 0))
        hl_oid  = filled.get("oid", "")

        # GATE: reject if no fill
        if fill_px <= 0 or filled_sz <= 0:
            log(f"  🚨 NO FILL on entry for {coin}: fill_px={fill_px}, filled_sz={filled_sz}")
            send_alert(f"🚨 Entry order for {coin} {direction} got NO FILL. Order may be orphaned on HL.")
            return False

        # Check partial fill
        if abs(filled_sz - size_coins) > 0.0001:
            log(f"  ⚠️ PARTIAL FILL on entry: requested={size_coins}, filled={filled_sz}")
            send_alert(f"⚠️ PARTIAL FILL: {coin} {direction} requested={size_coins}, filled={filled_sz}")
            size_coins = filled_sz  # use actual filled size for all downstream calculations

        price = fill_px
        size_usd = price * filled_sz  # recalculate from actual fill, not requested

        # Place native stop-loss on HL
        stop_price = client.round_price(price * (1 - stop_pct) if is_buy else price * (1 + stop_pct))
        sl_result  = client.place_stop_loss(coin, not is_buy, size_coins, stop_price)
        sl_status = sl_result.get("status", "unknown")
        # Extract stop order ID
        sl_fills = sl_result.get("response", {}).get("data", {}).get("statuses", [{}])
        sl_oid = ""
        if sl_fills:
            resting = sl_fills[0].get("resting", {})
            sl_oid = str(resting.get("oid", ""))
        log(f"  Stop @ ${stop_price:,.4f} (oid={sl_oid}): {json.dumps(sl_result)}")
        if sl_status != "ok" or not sl_oid:
            log(f"  🚨 STOP LOSS FAILED: status={sl_status}, oid={sl_oid}")
            send_alert(f"🚨 STOP LOSS FAILED for {coin} {direction} @ ${stop_price:.2f}\nPosition is NAKED — no stop protection!")
            # Retry once
            try:
                time.sleep(1)
                sl_retry = client.place_stop_loss(coin, not is_buy, size_coins, stop_price)
                sl_retry_status = sl_retry.get("status", "unknown")
                sl_retry_fills = sl_retry.get("response", {}).get("data", {}).get("statuses", [{}])
                if sl_retry_fills:
                    resting = sl_retry_fills[0].get("resting", {})
                    sl_oid = str(resting.get("oid", ""))
                if sl_retry_status == "ok" and sl_oid:
                    log(f"  Stop retry succeeded: oid={sl_oid}")
                else:
                    log(f"  Stop retry also failed: {sl_retry}")
                    send_alert(f"🚨🚨 STOP RETRY FAILED for {coin}. NAKED POSITION. CLOSE MANUALLY.")
            except Exception as e:
                log(f"  Stop retry error: {e}")

    # Build position record
    pos_id    = f"{coin}_{direction}_{int(time.time())}"
    entry_time = now_iso()
    pos = {
        "id":              pos_id,
        "coin":            coin,
        "direction":       direction,
        "signal_name":     trade.get("signal_name", ""),
        "expression":      trade.get("expression", ""),
        "exit_expression": trade.get("exit_expression", ""),
        "max_hold_hours":  trade.get("max_hold_hours", 24),
        "entry_price":     price,
        "size_usd":        size_usd,
        "size_coins":      size_coins,
        "stop_loss_pct":   stop_pct,
        "stop_loss_price": client.round_price(price * (1 - stop_pct) if is_buy else price * (1 + stop_pct)),
        "entry_time":      entry_time,
        "sharpe":          trade.get("sharpe", 0),
        "win_rate":        trade.get("win_rate", 0),
        "composite_score": trade.get("composite_score", 0),
        "hl_order_id":     hl_oid if not dry else "dry",
        "sl_order_id":     sl_oid if not dry else "",
        "dry":             dry,
    }

    # Save to positions.json
    pdata = load_json_locked(POSITIONS_FILE, {})
    positions = pdata.get("positions", [])
    positions.append(pos)
    save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": positions})

    # Telegram alert
    emoji = "🟢" if is_buy else "🔴"
    send_alert(
        f"{emoji} <b>V6 OPEN {direction}</b> {coin}\n"
        f"Signal: {trade.get('signal_name', '')}\n"
        f"Price: ${price:,.4f}  Size: ${size_usd:.0f}\n"
        f"Stop: {stop_pct*100:.0f}%  Sharpe: {trade.get('sharpe', 0):.2f}  WR: {trade.get('win_rate', 0):.0f}%\n"
        + ("[DRY RUN]" if dry else "")
    )
    log(f"  Opened {direction} {coin} @ ${price:,.4f} (stop={stop_pct*100:.0f}%)")
    return True


# ─── CLOSE TRADE ──────────────────────────────────────────────────────────────

def close_trade(client: HLClient, pos: dict, exit_reason: str, dry: bool):
    """Close a position. Records P&L and sends alert."""
    coin       = pos["coin"]
    direction  = pos["direction"]
    is_long    = direction == "LONG"
    size_coins = pos.get("size_coins", 0)
    entry_price = pos.get("entry_price", 0)
    entry_time  = pos.get("entry_time", "")

    if dry:
        exit_price = client.get_price(coin)
        log(f"  [DRY] Would close {direction} {coin} @ ~${exit_price:,.4f}")
    else:
        # Cancel stops first
        client.cancel_coin_stops(coin)
        result = client.market_sell(coin, size_coins, reduce_only=True) if is_long else client.market_buy(coin, size_coins)
        log(f"  Close result: {json.dumps(result)}")

        fills   = result.get("response", {}).get("data", {}).get("statuses", [{}])
        filled  = fills[0].get("filled", {}) if fills else {}
        fill_px = float(filled.get("avgPx", 0))
        filled_sz = float(filled.get("totalSz", 0))
        if fill_px <= 0:
            log(f"  🚨 NO FILL PRICE from HL for {coin} close — cannot record trade")
            send_alert(f"🚨 NO FILL PRICE for {coin} close. Position may still be open. CHECK HL.")
            return  # refuse to record a trade without real fill data
        exit_price = fill_px

        # Check partial fill
        if filled_sz > 0 and abs(filled_sz - abs(size_coins)) > 0.0001:
            remaining = abs(size_coins) - filled_sz
            log(f"  PARTIAL FILL: filled {filled_sz}, remaining {remaining:.6f}")
            send_alert(f"⚠️ PARTIAL FILL on {coin} close: filled {filled_sz}/{abs(size_coins)}")
            # Retry the remainder
            try:
                retry = client.market_sell(coin, remaining, reduce_only=True) if is_long else client.market_buy(coin, remaining)
                retry_fills = retry.get("response", {}).get("data", {}).get("statuses", [{}])
                retry_filled = retry_fills[0].get("filled", {}) if retry_fills else {}
                if float(retry_filled.get("totalSz", 0)) > 0:
                    log(f"  Retry filled: {retry_filled.get('totalSz')} @ ${retry_filled.get('avgPx')}")
                else:
                    log(f"  RETRY FAILED — position may still be open on HL")
                    send_alert(f"🚨 FAILED to close remaining {remaining} {coin} — CHECK HL MANUALLY")
            except Exception as e:
                log(f"  RETRY ERROR: {e}")
                send_alert(f"🚨 RETRY ERROR closing {coin}: {e}")
        elif filled_sz == 0 and not dry:
            log(f"  CLOSE FAILED — no fill. Position still open on HL")
            send_alert(f"🚨 CLOSE FAILED for {coin} — no fill, position still open")
            return  # Don't record as closed

    # Compute P&L from actual coin movement (not entry size_usd which can be stale)
    actual_entry_notional = entry_price * abs(size_coins) if entry_price and size_coins else pos.get("size_usd", 0)
    actual_exit_notional = exit_price * abs(size_coins) if exit_price and size_coins else actual_entry_notional

    # Fees: HL taker = 0.035% of notional on each side
    fee_rate = FEE_RATE
    entry_fee = round(actual_entry_notional * fee_rate, 4)
    exit_fee = round(actual_exit_notional * fee_rate, 4)
    total_fees = round(entry_fee + exit_fee, 4)

    # P&L from actual price difference × actual coins (net of fees)
    if entry_price and exit_price and entry_price > 0 and size_coins:
        price_diff = exit_price - entry_price
        pnl_usd_gross = round(price_diff * abs(size_coins), 4) if is_long else round(-price_diff * abs(size_coins), 4)
        raw_pct = (exit_price - entry_price) / entry_price
        pnl_pct = raw_pct if is_long else -raw_pct
        pnl_usd = round(pnl_usd_gross - total_fees, 4)
    else:
        pnl_pct = 0
        pnl_usd_gross = 0
        pnl_usd = 0

    # Slippage: difference between mid price at order time and actual fill
    mid_at_close = 0
    try:
        mid_at_close = client.get_price(coin)
    except Exception as _e:
        pass  # swallowed: {_e}
    slippage_pct = round(abs(exit_price - mid_at_close) / mid_at_close * 100, 4) if mid_at_close > 0 and exit_price > 0 else 0

    exit_time = now_iso()
    trade_record = {
        **pos,
        "exit_price":     exit_price,
        "exit_time":      exit_time,
        "exit_reason":    exit_reason,
        "pnl_pct":        round(pnl_pct, 6),
        "pnl_usd":        pnl_usd,
        "pnl_usd_gross":  pnl_usd_gross,
        "fees_usd":       total_fees,
        "slippage_pct":   slippage_pct,
        "actual_notional": round(actual_exit_notional, 2),
        "won":            pnl_usd > 0,
    }

    # Append to trades.jsonl
    append_jsonl(TRADES_FILE, trade_record)

    # Update risk.json daily P&L (net — wins reduce, losses increase)
    if not dry:
        risk = load_json(RISK_FILE, {})
        # daily_pnl tracks net P&L (positive = winning day, negative = losing day)
        risk["daily_pnl_usd"] = round(risk.get("daily_pnl_usd", 0) + pnl_usd, 4)
        # daily_loss_usd tracks gross losses only (for conservative halt check)
        if pnl_usd < 0:
            risk["daily_loss_usd"] = round(risk.get("daily_loss_usd", 0) + abs(pnl_usd), 4)
        save_json_atomic(RISK_FILE, {**risk, "updated_at": now_iso()})

    # Telegram alert
    won     = pnl_usd > 0
    emoji   = "✅" if won else "❌"
    send_alert(
        f"{emoji} <b>V6 CLOSE {direction}</b> {coin}\n"
        f"Signal: {pos.get('signal_name', '')}\n"
        f"Entry: ${entry_price:,.4f}  Exit: ${exit_price:,.4f}\n"
        f"P&L: ${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%)\n"
        f"Reason: {exit_reason}"
        + ("  [DRY]" if dry else "")
    )
    log(f"  Closed {direction} {coin}: P&L=${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%) — {exit_reason}")
    return trade_record


# ─── MAIN CYCLE ───────────────────────────────────────────────────────────────

def run_once(client: HLClient, dry: bool):
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    approved  = load_json(APPROVED_FILE, {}).get("approved", [])
    exits     = load_json(EXITS_FILE,    {}).get("exits",    [])

    closed_ids = set()

    # ── Process exits ─────────────────────────────────────────────────────────
    if exits:
        pos_by_coin = {p["coin"]: p for p in positions}
        for ex in exits:
            coin = ex.get("coin", "")
            pos  = pos_by_coin.get(coin)
            if not pos:
                continue
            close_trade(client, pos, ex.get("reason", "exit_signal"), dry)
            closed_ids.add(pos.get("id", coin))

        # Clear exits
        save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": []})

    # Remove closed positions
    if closed_ids:
        remaining = [p for p in positions if p.get("id") not in closed_ids and p.get("coin") not in closed_ids]
        positions = remaining
        save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": positions})

    # ── Process approved entries ───────────────────────────────────────────────
    if approved:
        open_coins = {p["coin"] for p in positions}
        opened     = []
        for trade in approved:
            if trade["coin"] in open_coins:
                log(f"  SKIP: already have position on {trade['coin']}")
                continue
            success = open_trade(client, trade, dry)
            if success:
                open_coins.add(trade["coin"])
                opened.append(trade["coin"])

        # Clear approved
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": []})

    update_heartbeat()


def _reconcile_positions(client: "HLClient"):
    """Sync local positions.json with what HL actually has open.
    
    This prevents ghost positions (local thinks open, HL closed)
    and orphan positions (HL has open, local doesn't know).
    """
    try:
        hl_positions = client.get_positions()
    except Exception as e:
        log(f"WARN: reconciliation skipped — HL query failed: {e}")
        return

    # Build HL reality map: coin → {direction, size, entry_price}
    hl_map = {}
    for p in hl_positions:
        pos = p.get("position", {})
        sz = float(pos.get("szi", 0))
        if sz == 0:
            continue
        coin = pos["coin"]
        hl_map[coin] = {
            "coin":        coin,
            "direction":   "LONG" if sz > 0 else "SHORT",
            "size_coins":  abs(sz),
            "entry_price": float(pos.get("entryPx", 0)),
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
        }

    # Load local positions
    local_data = load_json_locked(POSITIONS_FILE, {})
    local_positions = local_data.get("positions", [])
    local_map = {p["coin"]: p for p in local_positions}

    changes = []

    # Check for ghosts (local has it, HL doesn't)
    for coin in list(local_map.keys()):
        if coin not in hl_map:
            changes.append(f"GHOST removed: {coin} {local_map[coin].get('direction')} (closed on HL)")

    # Check for orphans (HL has it, local doesn't)
    for coin, hl_pos in hl_map.items():
        if coin not in local_map:
            changes.append(f"ORPHAN adopted: {coin} {hl_pos['direction']} @ ${hl_pos['entry_price']:.2f}")

    # Check for direction mismatches
    for coin in set(local_map.keys()) & set(hl_map.keys()):
        if local_map[coin].get("direction") != hl_map[coin]["direction"]:
            changes.append(f"DIRECTION FIX: {coin} local={local_map[coin]['direction']} hl={hl_map[coin]['direction']}")

    if changes:
        log(f"  RECONCILIATION: {len(changes)} fixes")
        for c in changes:
            log(f"    {c}")

    # Rebuild positions from HL truth
    new_positions = []
    for coin, hl_pos in hl_map.items():
        # Preserve local metadata if it exists
        local = local_map.get(coin, {})
        new_positions.append({
            "coin":          coin,
            "direction":     hl_pos["direction"],
            "entry_price":   hl_pos["entry_price"],
            "size_coins":    hl_pos["size_coins"],
            "size_usd":      hl_pos["entry_price"] * hl_pos["size_coins"],
            "entry_time":    local.get("entry_time", now_iso()),
            "signal_name":   local.get("signal_name", "reconciled_from_hl"),
            "stop_loss_pct": local.get("stop_loss_pct", 0.05),
            "strategy_version": local.get("strategy_version", STRATEGY_VERSION),
            "sharpe":        local.get("sharpe"),
            "win_rate":      local.get("win_rate"),
        })

    save_json_locked(POSITIONS_FILE, {
        "updated_at": now_iso(),
        "positions": new_positions,
    })

    if not changes:
        log(f"  Positions synced: {len(new_positions)} match HL")

    # STOP ORDER VERIFICATION: check every open position has a stop on HL
    if new_positions:
        try:
            open_orders = client.get_open_orders()
            coins_with_stops = set()
            for order in open_orders:
                # HL returns orderType as None for trigger/stop orders
                # Any order for a coin with an open position is considered a stop
                coins_with_stops.add(order.get("coin"))

            for pos in new_positions:
                coin = pos["coin"]
                if coin not in coins_with_stops:
                    direction = pos["direction"]
                    entry = pos.get("entry_price", 0)
                    stop_pct = pos.get("stop_loss_pct", STOP_LOSS_PCT)
                    is_long = direction == "LONG"
                    log(f"  🚨 NAKED POSITION: {coin} {direction} — no stop order on HL!")
                    send_alert(f"🚨 NAKED POSITION: {coin} {direction} @ ${entry:.2f}\nNo stop loss on HL! Placing emergency stop.")
                    # Place emergency stop
                    try:
                        stop_price = client.round_price(entry * (1 - stop_pct) if is_long else entry * (1 + stop_pct))
                        size = pos.get("size_coins", 0)
                        if size > 0:
                            sl = client.place_stop_loss(coin, not is_long, size, stop_price)
                            log(f"  Emergency stop placed: {json.dumps(sl)}")
                        else:
                            log(f"  Cannot place emergency stop — size=0")
                            send_alert(f"🚨 Cannot place stop for {coin} — size unknown. CLOSE MANUALLY.")
                    except Exception as e:
                        log(f"  Emergency stop FAILED: {e}")
                        send_alert(f"🚨🚨 EMERGENCY STOP FAILED for {coin}: {e}\nCLOSE MANUALLY NOW.")
        except Exception as e:
            log(f"  WARN: stop verification failed: {e}")


def main():
    dry  = "--dry" in sys.argv
    loop = "--loop" in sys.argv

    if dry:
        log("=== V6 Executor (DRY RUN — no real orders) ===")
    else:
        log("=== V6 Executor ===")

    BUS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load HL metadata
    load_hl_meta()

    # Init HL client
    hl_key = get_env("HYPERLIQUID_SECRET_KEY") or get_env("HL_PRIVATE_KEY")
    if not hl_key:
        log("FATAL: HL_PRIVATE_KEY not set")
        sys.exit(1)

    client = HLClient(hl_key, HL_MAIN_ADDRESS)

    # Check balance
    try:
        equity = client.get_balance()
        log(f"Account equity: ${equity:,.2f}")
        save_json_atomic(BUS_DIR / "portfolio.json", {
            "updated_at":   now_iso(),
            "account_value": equity,
            "strategy_version": STRATEGY_VERSION,
        })
    except Exception as e:
        log(f"WARN: Could not fetch balance: {e}")

    # Reconcile positions with HL reality
    _reconcile_positions(client)

    run_once(client, dry)

    if loop:
        last_meta_refresh = time.time()
        META_REFRESH_INTERVAL = 600  # refresh HL metadata every 10 minutes

        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                # Refresh HL metadata periodically (new coins, size changes)
                if time.time() - last_meta_refresh >= META_REFRESH_INTERVAL:
                    load_hl_meta()
                    last_meta_refresh = time.time()

                # Update balance + reconcile every cycle
                try:
                    equity = client.get_balance()
                    save_json_atomic(BUS_DIR / "portfolio.json", {
                        "updated_at": now_iso(),
                        "account_value": equity,
                        "strategy_version": STRATEGY_VERSION,
                    })
                except Exception as e:
                    log(f"🚨 Balance fetch FAILED: {e} — skipping this cycle")
                    send_alert(f"🚨 HL API DOWN: get_balance failed: {e}\nSkipping trade cycle.")
                    continue  # skip this cycle entirely — no trading with unknown equity
                _reconcile_positions(client)
                run_once(client, dry)
            except Exception as e:
                log(f"ERROR in cycle: {e}")


if __name__ == "__main__":
    main()

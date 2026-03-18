#!/usr/bin/env python3
"""
ZERO OS — Hyperliquid Live Trade Executor
Reads paper scanner fires, mirrors highest-conviction signals as real perps trades.

Usage:
  python3 hyperliquid_executor.py           # dry run
  python3 hyperliquid_executor.py --live    # real money

Risk: $100 capital, $10 min order, max 5 positions, 1x leverage, 5% stop loss.
"""

import json
import os
import sys
import time
import math
import requests
from datetime import datetime, timezone
from pathlib import Path
from eth_account import Account as EthAccount

# Hyperliquid signing
from hyperliquid.utils.signing import (
    sign_l1_action, get_timestamp_ms,
    order_request_to_order_wire, action_hash,
    construct_phantom_agent, sign_inner
)

# ─── CONFIG ───
LIVE_MODE = "--live" in sys.argv
DATA_DIR = Path(__file__).parent / "data"
LIVE_DIR = DATA_DIR / "live"
LIVE_POSITIONS = LIVE_DIR / "positions.json"
LIVE_CLOSED = LIVE_DIR / "closed.jsonl"
LIVE_PORTFOLIO = LIVE_DIR / "portfolio.json"
LIVE_LOG = LIVE_DIR / "executor.log"
FIRES_LOG = DATA_DIR / "fires.jsonl"

HL_URL = "https://api.hyperliquid.xyz"

# Risk params — spot collateral mode (20x cross leverage)
# $115 spot USDC backs cross margin. Each $10 order uses ~$0.50 margin.
# We limit total notional exposure to $100 (effective ~1x on spot balance).
INITIAL_CAPITAL = 115.0
MAX_POSITION_USD = 15.0         # max $15 per position notional
MIN_POSITION_USD = 10.0         # Hyperliquid minimum
MAX_OPEN_POSITIONS = 8          # up to 8 positions at $10-15 each = $80-120 notional
MAX_TOTAL_NOTIONAL = 100.0      # total notional cap (keep effective leverage ~1x)
MAX_DAILY_LOSS_USD = 10.0       # $10 max daily loss, then stop
STOP_LOSS_PCT = 0.05            # 5% hard stop
TRAILING_STOP_TRIGGER = 0.02    # activate trailing stop after +2% gain
TRAILING_STOP_LOCK = 0.50       # lock in 50% of peak gain
LEVERAGE = 20                   # cross leverage (set by HL, we manage risk via position sizing)
MIN_SHARPE = 1.5                # matches paper engine threshold
MIN_WIN_RATE = 60.0             # require 60%+ win rate (filters ETH spam at 40%)
MAX_PER_COIN = 2                # max 2 live positions per coin

# Coin configs
COIN_TO_ASSET = {
    "BTC": 0, "ETH": 1, "SOL": 5, "DOGE": 17,
    "AVAX": 10, "LINK": 14, "ARB": 35, "NEAR": 33,
    "SUI": 54, "INJ": 37
}
COIN_SIZE_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0,
    "AVAX": 2, "LINK": 1, "ARB": 1, "NEAR": 1,
    "SUI": 1, "INJ": 1
}
COIN_PRICE_DECIMALS = {
    "BTC": 1, "ETH": 1, "SOL": 2, "DOGE": 5,
    "AVAX": 2, "LINK": 3, "ARB": 4, "NEAR": 3,
    "SUI": 4, "INJ": 3
}

# ─── HELPERS ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIVE_LOG, "a") as f:
        f.write(line + "\n")

def load_env():
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env

def load_json(path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


# ─── HYPERLIQUID CLIENT ───
class HLClient:
    def __init__(self, secret_key):
        self.wallet = EthAccount.from_key(secret_key)
        self.address = self.wallet.address
        log(f"Wallet: {self.address}")

    def info_post(self, payload):
        resp = requests.post(f"{HL_URL}/info", json=payload, timeout=10)
        return resp.json()

    def get_balance(self):
        """Get account equity from main account."""
        env = load_env()
        main_addr = env.get("HYPERLIQUID_MAIN_ADDRESS", self.address)
        result = self.info_post({"type": "clearinghouseState", "user": main_addr})
        margin = result.get("marginSummary", {})
        return float(margin.get("accountValue", 0))

    def get_spot_balance(self):
        env = load_env()
        main_addr = env.get("HYPERLIQUID_MAIN_ADDRESS", self.address)
        result = self.info_post({"type": "spotClearinghouseState", "user": main_addr})
        for b in result.get("balances", []):
            if b["coin"] == "USDC":
                return float(b.get("total", 0))
        return 0

    def get_positions(self):
        env = load_env()
        main_addr = env.get("HYPERLIQUID_MAIN_ADDRESS", self.address)
        result = self.info_post({"type": "clearinghouseState", "user": main_addr})
        return result.get("assetPositions", [])

    def get_price(self, coin):
        """Get mid price for a coin."""
        result = self.info_post({"type": "allMids"})
        return float(result.get(coin, 0))

    def _sign_and_send(self, action):
        timestamp = get_timestamp_ms()
        sig = sign_l1_action(self.wallet, action, None, timestamp, None, True)
        payload = {
            "action": action,
            "nonce": timestamp,
            "signature": sig,
        }
        resp = requests.post(f"{HL_URL}/exchange",
                            data=json.dumps(payload),
                            headers={"Content-Type": "application/json"},
                            timeout=10)
        return resp.json()

    def place_order(self, coin, is_buy, size, limit_price, reduce_only=False, order_type="Gtc"):
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}

        sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
        size_str = f"{size:.{sz_dec}f}"

        # Price must use 5 sig figs
        rounded_px = self.round_price(limit_price)
        if rounded_px >= 10000:
            price_str = f"{rounded_px:.0f}"
        elif rounded_px >= 1000:
            price_str = f"{rounded_px:.1f}"
        elif rounded_px >= 100:
            price_str = f"{rounded_px:.2f}"
        elif rounded_px >= 10:
            price_str = f"{rounded_px:.3f}"
        elif rounded_px >= 1:
            price_str = f"{rounded_px:.4f}"
        elif rounded_px >= 0.1:
            price_str = f"{rounded_px:.5f}"
        else:
            price_str = f"{rounded_px:.6f}"

        # Use "tif" format (not "tpc" — SDK bug in v0.22.0)
        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": price_str,
                "s": size_str,
                "r": reduce_only,
                "t": {"limit": {"tif": order_type}}
            }],
            "grouping": "na"
        }

        return self._sign_and_send(action)

    def round_price(self, price):
        """Round price to 5 significant figures (Hyperliquid requirement)."""
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

    def market_order(self, coin, is_buy, size, slippage_pct=0.01):
        """Market order via aggressive IOC limit with slippage."""
        price = self.get_price(coin)
        if price <= 0:
            return {"status": "err", "response": f"No price for {coin}"}

        if is_buy:
            limit_px = self.round_price(price * (1 + slippage_pct))
        else:
            limit_px = self.round_price(price * (1 - slippage_pct))

        return self.place_order(coin, is_buy, size, limit_px, order_type="Ioc")

    def close_position(self, coin, is_long, size):
        """Close a position via IOC market order."""
        return self.market_order(coin, not is_long, size)


# ─── EXECUTOR LOGIC ───
def get_new_fires(last_processed_time):
    """Read fires from scanner that are newer than last processed."""
    fires = []
    if not FIRES_LOG.exists():
        return fires
    with open(FIRES_LOG) as f:
        for line in f:
            try:
                fire = json.loads(line)
                fire_time = fire.get("time", "")
                if fire_time > last_processed_time:
                    fires.append(fire)
            except:
                continue
    return fires


def should_open(fire, positions, portfolio):
    """Check if a fire should result in a live trade."""
    # Quality filters
    if fire.get("sharpe", 0) < MIN_SHARPE:
        return False, "sharpe too low"
    if fire.get("win_rate", 0) < MIN_WIN_RATE:
        return False, "win rate too low"

    coin = fire["coin"]
    direction = fire["direction"]

    # Position limits
    if len(positions) >= MAX_OPEN_POSITIONS:
        return False, "max positions reached"

    # Total notional cap
    total_notional = sum(p.get("size_usd", 0) for p in positions)
    if total_notional >= MAX_TOTAL_NOTIONAL:
        return False, f"notional cap ${MAX_TOTAL_NOTIONAL:.0f} reached (${total_notional:.0f} deployed)"

    # Per-coin limit
    coin_count = sum(1 for p in positions if p["coin"] == coin)
    if coin_count >= MAX_PER_COIN:
        return False, f"max {MAX_PER_COIN} positions on {coin}"

    # No opposing positions
    for p in positions:
        if p["coin"] == coin and p["direction"] != direction:
            return False, f"opposing position on {coin}"

    # No duplicate coin+direction
    for p in positions:
        if p["coin"] == coin and p["direction"] == direction:
            return False, f"already {direction} on {coin}"

    # Daily loss check
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if portfolio.get("daily_reset") != today:
        portfolio["daily_loss"] = 0
        portfolio["daily_reset"] = today
    if portfolio.get("daily_loss", 0) >= MAX_DAILY_LOSS_USD:
        return False, "daily loss limit reached"

    return True, "ok"


def close_and_record(client, pos, current, pnl_pct, reason, portfolio):
    """Close a position on HL and record it."""
    coin = pos["coin"]
    is_long = pos["direction"] == "LONG"

    if LIVE_MODE:
        sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
        size = float(f"{pos['size_coins']:.{sz_dec}f}")
        result = client.close_position(coin, is_long, size)
        log(f"  Close result: {json.dumps(result)}")

    pnl_usd = pos["size_usd"] * pnl_pct
    portfolio["trades"] = portfolio.get("trades", 0) + 1
    if pnl_usd > 0:
        portfolio["wins"] = portfolio.get("wins", 0) + 1
    else:
        portfolio["daily_loss"] = portfolio.get("daily_loss", 0) + abs(pnl_usd)

    closed = {
        **pos,
        "exit_price": current,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason,
        "pnl_pct": round(pnl_pct * 100, 2),
        "pnl_usd": round(pnl_usd, 2)
    }
    # Remove peak_pnl from closed record (internal tracking field)
    closed.pop("peak_pnl_pct", None)
    append_jsonl(LIVE_CLOSED, closed)


def check_exits(client, positions, portfolio):
    """Check stop loss, trailing stop, and max hold for each position."""
    remaining = []
    for pos in positions:
        coin = pos["coin"]
        entry = pos["entry_price"]
        current = client.get_price(coin)
        if current <= 0:
            remaining.append(pos)
            continue

        is_long = pos["direction"] == "LONG"
        if is_long:
            pnl_pct = (current - entry) / entry
        else:
            pnl_pct = (entry - current) / entry

        # Track peak P&L for trailing stop
        peak = pos.get("peak_pnl_pct", 0)
        if pnl_pct > peak:
            pos["peak_pnl_pct"] = pnl_pct
            peak = pnl_pct

        # 1. Hard stop loss
        if pnl_pct <= -STOP_LOSS_PCT:
            log(f"🛑 STOP LOSS {coin} {pos['direction']} | ${entry:.4f} → ${current:.4f} | {pnl_pct*100:+.2f}%")
            close_and_record(client, pos, current, pnl_pct, "stop_loss", portfolio)
            continue

        # 2. Trailing stop: after +2% gain, close if price drops to 50% of peak
        if peak >= TRAILING_STOP_TRIGGER:
            trailing_floor = peak * TRAILING_STOP_LOCK  # e.g. peak 4% → floor 2%
            if pnl_pct <= trailing_floor:
                log(f"📉 TRAILING STOP {coin} {pos['direction']} | peak {peak*100:.2f}% → now {pnl_pct*100:+.2f}% (floor {trailing_floor*100:.2f}%)")
                close_and_record(client, pos, current, pnl_pct, "trailing_stop", portfolio)
                continue

        # 3. Max hold time
        entry_dt = datetime.fromisoformat(pos["entry_time"].replace("Z", "+00:00"))
        hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
        max_hold = pos.get("max_hold_hours", 48)
        if hours_held >= max_hold:
            log(f"⏰ MAX HOLD {coin} {pos['direction']} | {hours_held:.1f}h | {pnl_pct*100:+.2f}%")
            close_and_record(client, pos, current, pnl_pct, "max_hold", portfolio)
            continue

        remaining.append(pos)

    return remaining


def sync_positions_with_hl(client, positions):
    """Reconcile local position state with actual Hyperliquid positions."""
    hl_positions = client.get_positions()
    hl_map = {}
    for p in hl_positions:
        pos = p["position"]
        coin = pos["coin"]
        size = float(pos["szi"])
        if size != 0:
            hl_map[coin] = {
                "size": abs(size),
                "direction": "LONG" if size > 0 else "SHORT",
                "entry": float(pos["entryPx"]),
                "pnl": float(pos["unrealizedPnl"])
            }

    synced = []
    for pos in positions:
        coin = pos["coin"]
        if coin in hl_map:
            hl = hl_map[coin]
            # Verify direction matches
            if hl["direction"] == pos["direction"]:
                # Update size if HL disagrees (partial fill/liquidation)
                if abs(hl["size"] - pos["size_coins"]) > 0.0001:
                    log(f"🔄 SYNC {coin}: local size {pos['size_coins']} → HL size {hl['size']}")
                    pos["size_coins"] = hl["size"]
                    pos["size_usd"] = hl["size"] * hl["entry"]
                synced.append(pos)
                del hl_map[coin]
            else:
                log(f"⚠️ SYNC {coin}: direction mismatch local={pos['direction']} HL={hl['direction']}, dropping local")
                del hl_map[coin]
        else:
            # Position closed on HL but still in local state (liquidation?)
            log(f"⚠️ SYNC {coin}: position gone from HL, removing local tracker")

    # HL positions we don't have locally (manual trades?)
    for coin, hl in hl_map.items():
        log(f"⚠️ SYNC {coin}: found on HL but not tracked locally ({hl['direction']} {hl['size']})")

    return synced


def run():
    log(f"═══ ZERO OS EXECUTOR {'🔴 LIVE' if LIVE_MODE else '⚪ DRY RUN'} ═══")

    env = load_env()
    secret = env.get("HYPERLIQUID_SECRET_KEY")
    if not secret:
        log("ERROR: HYPERLIQUID_SECRET_KEY not found")
        sys.exit(1)

    client = HLClient(secret)

    # Store main address for balance lookups
    main_addr = env.get("HYPERLIQUID_MAIN_ADDRESS", "0xA5F25E3Bbf7a10EB61EEfA471B61E1dfa5777884")

    # Check balance
    perps_bal = client.get_balance()
    spot_bal = client.get_spot_balance()
    log(f"Balance: perps=${perps_bal:.2f}, spot=${spot_bal:.2f}")

    available = max(perps_bal, spot_bal)
    if available < 10 and LIVE_MODE:
        log(f"WARNING: Low balance (${available:.2f}). Need at least $10 for minimum order.")

    # Load state
    positions = load_json(LIVE_POSITIONS, [])
    portfolio = load_json(LIVE_PORTFOLIO, {
        "capital": INITIAL_CAPITAL,
        "started": datetime.now(timezone.utc).isoformat(),
        "trades": 0,
        "wins": 0,
        "daily_loss": 0,
        "daily_reset": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    })

    log(f"Open positions: {len(positions)}, Trades: {portfolio.get('trades', 0)}, Daily loss: ${portfolio.get('daily_loss', 0):.2f}")

    # Sync local state with Hyperliquid
    if LIVE_MODE and positions:
        positions = sync_positions_with_hl(client, positions)

    # Check exits (stop loss, trailing stop, max hold)
    positions = check_exits(client, positions, portfolio)

    # Get new fires from scanner
    last_time = portfolio.get("last_fire_time", "2000-01-01T00:00:00")
    fires = get_new_fires(last_time)

    if fires:
        # Sort by sharpe descending
        fires.sort(key=lambda x: x.get("sharpe", 0), reverse=True)
        log(f"New fires since last run: {len(fires)}")

        for fire in fires:
            portfolio["last_fire_time"] = fire.get("time", last_time)

            ok, reason = should_open(fire, positions, portfolio)
            if not ok:
                continue

            coin = fire["coin"]
            direction = fire["direction"]
            is_buy = direction == "LONG"
            price = client.get_price(coin)
            if price <= 0:
                log(f"  Skip {coin}: no price")
                continue

            # Size: Sharpe-weighted, $10-15 range
            sharpe = fire.get("sharpe", 2.0)
            size_mult = min(sharpe / 2.0, 1.5)  # 1.0x at 2.0, 1.5x at 3.0+
            size_usd = min(MIN_POSITION_USD * size_mult, MAX_POSITION_USD)
            size_usd = max(size_usd, MIN_POSITION_USD)  # floor at $10

            size_coins = size_usd / price
            sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
            size_coins = round(size_coins, sz_dec)

            # Verify order value meets $10 minimum
            order_value = size_coins * price
            if order_value < 10:
                log(f"  Skip {coin}: order value ${order_value:.2f} < $10 min")
                continue

            log(f"📈 {'LIVE' if LIVE_MODE else 'DRY'} OPEN {coin} {direction} | ${size_usd:.2f} ({size_coins} coins) @ ${price:,.4f} | Sharpe {sharpe:.2f}")

            if LIVE_MODE:
                result = client.market_order(coin, is_buy, size_coins)
                log(f"  Order result: {json.dumps(result)}")

                if result.get("status") != "ok":
                    log(f"  ❌ Order failed: {result}")
                    continue

                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                filled = False
                for s in statuses:
                    if "filled" in s:
                        filled = True
                        fill_px = float(s["filled"]["avgPx"])
                        log(f"  ✅ Filled @ ${fill_px:,.4f}")
                        price = fill_px
                    elif "resting" in s:
                        log(f"  ⏳ Resting (IOC should not rest)")
                    elif "error" in s:
                        log(f"  ❌ {s['error']}")

                if not filled:
                    log(f"  ❌ Not filled, skipping position track")
                    continue

            position = {
                "coin": coin,
                "direction": direction,
                "signal": fire.get("signal", ""),
                "entry_price": price,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "size_usd": round(size_usd, 2),
                "size_coins": size_coins,
                "sharpe": sharpe,
                "win_rate": fire.get("win_rate", 0),
                "max_hold_hours": fire.get("max_hold_hours", 48),
                "stop_loss": round(price * (1 - STOP_LOSS_PCT) if is_buy else price * (1 + STOP_LOSS_PCT), 6)
            }
            positions.append(position)
            log(f"  Stop loss @ ${position['stop_loss']:,.4f}")

    # Save state
    save_json(LIVE_POSITIONS, positions)
    save_json(LIVE_PORTFOLIO, portfolio)

    # Summary
    total_value = sum(p["size_usd"] for p in positions)
    log(f"Summary: {len(positions)} open positions, ${total_value:.2f} deployed, trades={portfolio.get('trades', 0)}")
    log("═══ DONE ═══")


if __name__ == "__main__":
    run()

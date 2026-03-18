#!/usr/bin/env python3
"""
ZERO OS — Agent 4: Execution Agent
Places orders on Hyperliquid, manages position lifecycle (open, stop, close),
reconciles with HL state each cycle.

Reads:
  scanner/bus/approved.json   — approved trades from Correlation Agent
  scanner/bus/risk.json       — risk state from Risk Agent (throttle, kill_all)
  scanner/config.yaml         — shared config

Writes:
  scanner/data/live/positions.json  — open positions
  scanner/data/live/closed.jsonl    — closed trade log
  scanner/data/live/portfolio.json  — portfolio state
  scanner/bus/heartbeat.json        — last-alive timestamp

Usage:
  python3 scanner/agents/execution_agent.py           # single run
  python3 scanner/agents/execution_agent.py --loop    # continuous 5-min cycle
  python3 scanner/agents/execution_agent.py --dry     # dry run (no orders)
"""

import json
import os
import sys
import time
import math
import requests
import yaml
from datetime import datetime, timezone
from pathlib import Path
from eth_account import Account as EthAccount
from hyperliquid.utils.signing import sign_l1_action, get_timestamp_ms, float_to_wire

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"

APPROVED_FILE = BUS_DIR / "approved.json"
RISK_FILE = BUS_DIR / "risk.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
CONFIG_FILE = SCANNER_DIR / "config.yaml"

POSITIONS_FILE = LIVE_DIR / "positions.json"
CLOSED_FILE = LIVE_DIR / "closed.jsonl"
PORTFOLIO_FILE = LIVE_DIR / "portfolio.json"
LOG_FILE = LIVE_DIR / "executor.log"

HL_URL = "https://api.hyperliquid.xyz"
CYCLE_SECONDS = 300  # 5 minutes

# ─── COIN TABLES ───
COIN_TO_ASSET = {
    "BTC": 0, "ETH": 1, "SOL": 5, "DOGE": 12,
    "AVAX": 6, "LINK": 18, "ARB": 11, "NEAR": 74,
    "SUI": 14, "INJ": 13
}
COIN_SIZE_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0,
    "AVAX": 2, "LINK": 1, "ARB": 1, "NEAR": 1,
    "SUI": 1, "INJ": 1
}

# ─── HELPERS ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [EXEC] {msg}"
    print(line)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
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


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f)
    return {}


def load_json(path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return default
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE, {})
    hb["execution"] = datetime.now(timezone.utc).isoformat()
    save_json(HEARTBEAT_FILE, hb)


# ─── HYPERLIQUID CLIENT ───
class HLClient:
    """Raw HTTP client for Hyperliquid — no SDK for order placement (tif/tpc bugs)."""

    def __init__(self, secret_key, main_address):
        self.wallet = EthAccount.from_key(secret_key)
        self.address = self.wallet.address
        self.main_address = main_address
        log(f"Wallet: {self.address} (main: {self.main_address})")

    def info_post(self, payload):
        resp = requests.post(f"{HL_URL}/info", json=payload, timeout=10)
        return resp.json()

    def get_balance(self):
        result = self.info_post({"type": "clearinghouseState", "user": self.main_address})
        margin = result.get("marginSummary", {})
        return float(margin.get("accountValue", 0))

    def get_positions(self):
        result = self.info_post({"type": "clearinghouseState", "user": self.main_address})
        return result.get("assetPositions", [])

    def get_open_orders(self):
        result = self.info_post({"type": "openOrders", "user": self.main_address})
        return result

    def get_price(self, coin):
        result = self.info_post({"type": "allMids"})
        return float(result.get(coin, 0))

    def get_all_prices(self):
        return self.info_post({"type": "allMids"})

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

    def _format_price(self, price):
        """Format a rounded price as string with appropriate decimals."""
        px = self.round_price(price)
        if px >= 10000:
            return f"{px:.0f}"
        elif px >= 1000:
            return f"{px:.1f}"
        elif px >= 100:
            return f"{px:.2f}"
        elif px >= 10:
            return f"{px:.3f}"
        elif px >= 1:
            return f"{px:.4f}"
        elif px >= 0.1:
            return f"{px:.5f}"
        else:
            return f"{px:.6f}"

    def _sign_and_send(self, action):
        time.sleep(0.05)  # prevent nonce collision on consecutive orders
        timestamp = get_timestamp_ms()
        sig = sign_l1_action(self.wallet, action, None, timestamp, None, True)
        payload = {
            "action": action,
            "nonce": timestamp,
            "signature": sig,
        }
        resp = requests.post(
            f"{HL_URL}/exchange",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return resp.json()

    def place_order(self, coin, is_buy, size, limit_price, reduce_only=False,
                    order_type="Gtc"):
        """Place a limit/IOC order using tif format (not tpc — SDK bug)."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}

        sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
        size_rounded = round(size, sz_dec)
        size_str = float_to_wire(size_rounded)
        price_str = float_to_wire(self.round_price(limit_price))

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
            "grouping": "na",
        }
        return self._sign_and_send(action)

    def place_trigger_order(self, coin, is_buy, size, trigger_price, tpsl="sl"):
        """Place a native stop-loss / take-profit trigger order on HL servers."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}

        sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
        size_str = float_to_wire(round(size, sz_dec))
        trigger_str = float_to_wire(self.round_price(trigger_price))

        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": trigger_str,
                "s": size_str,
                "r": True,
                "t": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": trigger_str,
                        "tpsl": tpsl,
                    }
                },
            }],
            "grouping": "na",
        }
        return self._sign_and_send(action)

    def cancel_all_triggers(self, coin):
        """Cancel all trigger orders for a coin."""
        asset = COIN_TO_ASSET.get(coin)
        if asset is None:
            return {"status": "err", "response": f"Unknown coin: {coin}"}
        orders = self.get_open_orders()
        trigger_oids = []
        for o in orders:
            if o.get("coin") == coin and o.get("orderType", "").startswith("Stop"):
                trigger_oids.append(o["oid"])
        if not trigger_oids:
            return {"status": "ok", "response": "no triggers to cancel"}
        action = {
            "type": "cancel",
            "cancels": [{"a": asset, "o": oid} for oid in trigger_oids],
        }
        return self._sign_and_send(action)

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


# ─── EXECUTION LOGIC ───

def read_approved():
    """Read approved trades from Correlation Agent bus file."""
    data = load_json(APPROVED_FILE, {})
    return data.get("approved", [])


def read_risk():
    """Read risk state from Risk Agent bus file."""
    return load_json(RISK_FILE, {"throttle": 1.0, "kill_all": False})


def compute_size(trade, cfg, throttle):
    """Compute position size in USD, applying throttle from risk agent."""
    min_usd = cfg.get("min_position_usd", 30)
    max_usd = cfg.get("max_position_usd", 50)
    min_sharpe = cfg.get("min_sharpe", 1.5)

    sharpe = trade.get("sharpe", 1.5)
    # Scale: min_sharpe → min_usd, min_sharpe+1.0 → max_usd
    size_usd = min_usd + (sharpe - min_sharpe) * (max_usd - min_usd)
    size_usd = max(min_usd, min(max_usd, size_usd))

    # Apply risk throttle
    size_usd *= throttle

    return round(size_usd, 2)


def should_open(trade, positions, cfg):
    """Check if an approved trade should be opened."""
    coin = trade["coin"]
    direction = trade["direction"]
    min_sharpe = cfg.get("min_sharpe", 1.5)
    min_wr = cfg.get("min_win_rate", 60)
    max_pos = cfg.get("max_positions", 3)
    max_per_coin = cfg.get("max_per_coin", 1)
    max_notional = cfg.get("max_notional", 100)

    if trade.get("sharpe", 0) < min_sharpe:
        return False, f"sharpe {trade.get('sharpe', 0):.2f} < {min_sharpe}"
    if trade.get("win_rate", 0) < min_wr:
        return False, f"win_rate {trade.get('win_rate', 0):.1f}% < {min_wr}%"

    if len(positions) >= max_pos:
        return False, f"max positions ({max_pos})"

    total_notional = sum(p.get("size_usd", 0) for p in positions)
    if total_notional >= max_notional:
        return False, f"notional cap ${max_notional}"

    coin_count = sum(1 for p in positions if p["coin"] == coin)
    if coin_count >= max_per_coin:
        return False, f"max {max_per_coin} on {coin}"

    for p in positions:
        if p["coin"] == coin and p["direction"] != direction:
            return False, f"opposing position on {coin}"
        if p["coin"] == coin and p["direction"] == direction:
            return False, f"already {direction} on {coin}"

    return True, "ok"


def place_stop_loss(client, coin, direction, size_coins, entry_price, stop_pct, dry):
    """Place a native stop-loss trigger order on HL."""
    is_long = direction == "LONG"
    if is_long:
        stop_price = client.round_price(entry_price * (1 - stop_pct))
        is_buy = False  # sell to close long
    else:
        stop_price = client.round_price(entry_price * (1 + stop_pct))
        is_buy = True  # buy to close short

    log(f"  Stop loss: {coin} trigger @ ${stop_price:,.4f} ({'buy' if is_buy else 'sell'} {size_coins})")

    if not dry:
        result = client.place_trigger_order(coin, is_buy, size_coins, stop_price, tpsl="sl")
        log(f"  Stop result: {json.dumps(result)}")
        return result, stop_price
    return {"status": "ok", "response": "dry"}, stop_price


def open_trade(client, trade, positions, cfg, throttle, dry):
    """Open a new position from an approved trade."""
    coin = trade["coin"]
    direction = trade["direction"]
    is_buy = direction == "LONG"

    size_usd = compute_size(trade, cfg, throttle)

    # Check remaining notional cap
    max_notional = cfg.get("max_notional", 100)
    current_notional = sum(p.get("size_usd", 0) for p in positions)
    remaining = max_notional - current_notional
    if size_usd > remaining:
        if remaining >= 10:  # HL minimum
            size_usd = remaining
        else:
            log(f"  Skip {coin}: notional cap (${current_notional:.0f}/${max_notional})")
            return None

    price = client.get_price(coin)
    if price <= 0:
        log(f"  Skip {coin}: no price")
        return None

    sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
    size_coins = round(size_usd / price, sz_dec)
    order_value = size_coins * price
    if order_value < 10:
        log(f"  Skip {coin}: order ${order_value:.2f} < $10 min")
        return None

    log(f"{'DRY' if dry else 'LIVE'} OPEN {coin} {direction} | ${size_usd:.2f} ({size_coins}) @ ${price:,.4f} | sharpe={trade.get('sharpe', 0):.2f}")

    fill_price = price
    if not dry:
        result = client.market_order(coin, is_buy, size_coins)
        log(f"  Order result: {json.dumps(result)}")

        if result.get("status") != "ok":
            log(f"  Order failed: {result}")
            return None

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        filled = False
        for s in statuses:
            if "filled" in s:
                filled = True
                fill_price = float(s["filled"]["avgPx"])
                log(f"  Filled @ ${fill_price:,.4f}")
            elif "error" in s:
                log(f"  Error: {s['error']}")

        if not filled:
            log(f"  Not filled, skipping")
            return None

    # Place native stop loss on HL
    stop_pct = cfg.get("stop_loss_pct", 0.05)
    _, stop_price = place_stop_loss(client, coin, direction, size_coins, fill_price, stop_pct, dry)

    position = {
        "coin": coin,
        "direction": direction,
        "signal": trade.get("signal", ""),
        "entry_price": fill_price,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "size_usd": round(size_usd, 2),
        "size_coins": size_coins,
        "sharpe": trade.get("sharpe", 0),
        "win_rate": trade.get("win_rate", 0),
        "max_hold_hours": 48,
        "stop_loss": stop_price,
        "stop_type": "native",  # HL server-side trigger
        "peak_pnl_pct": 0.0,
    }
    return position


def close_and_record(client, pos, current_price, pnl_pct, reason, portfolio, dry):
    """Close a position and record to closed.jsonl."""
    coin = pos["coin"]
    is_long = pos["direction"] == "LONG"
    sz_dec = COIN_SIZE_DECIMALS.get(coin, 2)
    size = round(pos["size_coins"], sz_dec)

    if not dry:
        # Cancel any existing triggers for this coin first
        client.cancel_all_triggers(coin)
        result = client.close_position(coin, is_long, size)
        log(f"  Close result: {json.dumps(result)}")

    pnl_usd = pos["size_usd"] * pnl_pct
    portfolio["trades"] = portfolio.get("trades", 0) + 1
    if pnl_usd > 0:
        portfolio["wins"] = portfolio.get("wins", 0) + 1
    else:
        portfolio["daily_loss"] = portfolio.get("daily_loss", 0) + abs(pnl_usd)

    closed = {
        "coin": pos["coin"],
        "direction": pos["direction"],
        "signal": pos.get("signal", ""),
        "entry_price": pos["entry_price"],
        "entry_time": pos["entry_time"],
        "size_usd": pos["size_usd"],
        "size_coins": pos["size_coins"],
        "exit_price": current_price,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason,
        "pnl_pct": round(pnl_pct * 100, 2),
        "pnl_usd": round(pnl_usd, 2),
    }
    append_jsonl(CLOSED_FILE, closed)
    log(f"  Closed {coin} {pos['direction']} | {reason} | {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")


def check_trailing_stops(client, positions, portfolio, cfg, prices, dry):
    """Check trailing stop logic for positions where HL native stop is the hard stop.
    If trailing stop triggers, close position and cancel the native stop."""
    trailing_trigger = cfg.get("trailing_stop_trigger", 0.02)
    trailing_lock = cfg.get("trailing_stop_lock", 0.50)
    remaining = []

    for pos in positions:
        coin = pos["coin"]
        entry = pos["entry_price"]
        current = float(prices.get(coin, 0))
        if current <= 0:
            remaining.append(pos)
            continue

        is_long = pos["direction"] == "LONG"
        pnl_pct = (current - entry) / entry if is_long else (entry - current) / entry

        # Update peak
        peak = pos.get("peak_pnl_pct", 0)
        if pnl_pct > peak:
            pos["peak_pnl_pct"] = pnl_pct
            peak = pnl_pct

        # Trailing stop: after trigger% gain, close if drops to lock% of peak
        if peak >= trailing_trigger:
            floor = peak * trailing_lock
            if pnl_pct <= floor:
                log(f"TRAILING STOP {coin} {pos['direction']} | peak {peak*100:.2f}% -> {pnl_pct*100:+.2f}% (floor {floor*100:.2f}%)")
                close_and_record(client, pos, current, pnl_pct, "trailing_stop", portfolio, dry)
                continue

        # Max hold time
        entry_dt = datetime.fromisoformat(pos["entry_time"])
        hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
        max_hold = pos.get("max_hold_hours", 48)
        if hours_held >= max_hold:
            log(f"MAX HOLD {coin} {pos['direction']} | {hours_held:.1f}h | {pnl_pct*100:+.2f}%")
            close_and_record(client, pos, current, pnl_pct, "max_hold", portfolio, dry)
            continue

        remaining.append(pos)

    return remaining


def kill_all_positions(client, positions, portfolio, prices, dry):
    """Emergency: close ALL positions immediately (risk kill switch)."""
    log("KILL ALL — Risk Agent triggered emergency close")
    for pos in positions:
        coin = pos["coin"]
        entry = pos["entry_price"]
        current = float(prices.get(coin, 0))
        if current <= 0:
            current = entry  # fallback
        is_long = pos["direction"] == "LONG"
        pnl_pct = (current - entry) / entry if is_long else (entry - current) / entry
        close_and_record(client, pos, current, pnl_pct, "risk_kill", portfolio, dry)
    return []


def sync_positions_with_hl(client, positions):
    """Reconcile local state with actual Hyperliquid positions."""
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
                "pnl": float(pos["unrealizedPnl"]),
            }

    synced = []
    for pos in positions:
        coin = pos["coin"]
        if coin in hl_map:
            hl = hl_map[coin]
            if hl["direction"] == pos["direction"]:
                if abs(hl["size"] - pos["size_coins"]) > 0.0001:
                    log(f"SYNC {coin}: local {pos['size_coins']} -> HL {hl['size']}")
                    pos["size_coins"] = hl["size"]
                    pos["size_usd"] = hl["size"] * hl["entry"]
                synced.append(pos)
                del hl_map[coin]
            else:
                log(f"SYNC {coin}: direction mismatch local={pos['direction']} HL={hl['direction']}, dropping")
                del hl_map[coin]
        else:
            log(f"SYNC {coin}: gone from HL, removing local tracker")

    for coin, hl in hl_map.items():
        log(f"SYNC {coin}: on HL but not tracked ({hl['direction']} {hl['size']})")

    return synced


def update_portfolio(portfolio, positions, balance):
    """Update portfolio state."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if portfolio.get("daily_reset") != today:
        portfolio["daily_loss"] = 0
        portfolio["daily_reset"] = today

    portfolio["balance"] = balance
    portfolio["open_positions"] = len(positions)
    portfolio["total_notional"] = round(sum(p.get("size_usd", 0) for p in positions), 2)
    portfolio["last_update"] = datetime.now(timezone.utc).isoformat()


def run_cycle(client, cfg, dry):
    """Single execution cycle."""
    log(f"--- Execution cycle {'(DRY)' if dry else '(LIVE)'} ---")

    # Load state
    positions = load_json(POSITIONS_FILE, [])
    portfolio = load_json(PORTFOLIO_FILE, {
        "capital": cfg.get("capital", 115),
        "started": datetime.now(timezone.utc).isoformat(),
        "trades": 0, "wins": 0, "daily_loss": 0,
        "daily_reset": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    # Read risk state
    risk = read_risk()
    throttle = risk.get("throttle", 1.0)
    kill_all = risk.get("kill_all", False)

    log(f"Positions: {len(positions)} | Throttle: {throttle} | Kill: {kill_all}")

    # Fetch all prices once
    prices = client.get_all_prices()

    # 1. Kill switch
    if kill_all:
        positions = kill_all_positions(client, positions, portfolio, prices, dry)
        save_json(POSITIONS_FILE, positions)
        save_json(PORTFOLIO_FILE, portfolio)
        update_heartbeat()
        return

    # 2. Sync with HL
    if not dry and positions:
        positions = sync_positions_with_hl(client, positions)

    # 3. Check trailing stops & max hold (hard stop is on HL server)
    positions = check_trailing_stops(client, positions, portfolio, cfg, prices, dry)

    # 4. Open new positions from approved trades
    if throttle > 0:
        approved = read_approved()
        # Sort by sharpe descending — best signals first
        approved.sort(key=lambda t: t.get("sharpe", 0), reverse=True)

        # Track which trades we've already processed (by coin+direction)
        processed = load_json(PORTFOLIO_FILE, {}).get("processed_signals", [])

        for trade in approved:
            key = f"{trade['coin']}_{trade['direction']}_{trade.get('signal', '')}"
            if key in processed:
                continue

            ok, reason = should_open(trade, positions, cfg)
            if not ok:
                log(f"  Skip {trade['coin']} {trade['direction']}: {reason}")
                processed.append(key)
                continue

            pos = open_trade(client, trade, positions, cfg, throttle, dry)
            if pos:
                positions.append(pos)
                processed.append(key)

        # Keep processed list bounded (last 200)
        portfolio["processed_signals"] = processed[-200:]
    else:
        log("Throttle=0, no new trades")

    # 5. Update portfolio & balance
    balance = 0
    if not dry:
        try:
            balance = client.get_balance()
        except Exception as e:
            log(f"Balance fetch error: {e}")
    update_portfolio(portfolio, positions, balance)

    # Save
    save_json(POSITIONS_FILE, positions)
    save_json(PORTFOLIO_FILE, portfolio)
    update_heartbeat()

    total_notional = sum(p.get("size_usd", 0) for p in positions)
    log(f"Summary: {len(positions)} positions, ${total_notional:.2f} notional, trades={portfolio.get('trades', 0)}")


def main():
    dry = "--dry" in sys.argv
    loop = "--loop" in sys.argv

    log(f"=== ZERO OS Execution Agent {'DRY' if dry else 'LIVE'} ===")

    env = load_env()
    secret = env.get("HYPERLIQUID_SECRET_KEY")
    main_addr = env.get("HYPERLIQUID_MAIN_ADDRESS")
    if not secret:
        log("ERROR: HYPERLIQUID_SECRET_KEY not found in ~/.config/openclaw/.env")
        sys.exit(1)
    if not main_addr:
        log("ERROR: HYPERLIQUID_MAIN_ADDRESS not found in ~/.config/openclaw/.env")
        sys.exit(1)

    cfg = load_config()
    client = HLClient(secret, main_addr)

    if loop:
        log(f"Looping every {CYCLE_SECONDS}s")
        while True:
            try:
                run_cycle(client, cfg, dry)
            except Exception as e:
                log(f"Cycle error: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(client, cfg, dry)


if __name__ == "__main__":
    main()

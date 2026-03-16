#!/usr/bin/env python3
"""
ZERO OS — Hyperliquid Trade Executor
Bridges the signal scanner to real Hyperliquid perps trading.

Usage:
  # Dry run (default) — logs what would happen
  python3 hyperliquid_executor.py

  # Live trading
  python3 hyperliquid_executor.py --live

Setup:
  1. Go to https://app.hyperliquid.xyz/API
  2. Generate an API wallet (separate from main wallet)
  3. Approve the API wallet and fund it
  4. Add to ~/.config/openclaw/.env:
     export HYPERLIQUID_SECRET_KEY="0x..."
     export HYPERLIQUID_ACCOUNT_ADDRESS="0x..."  # main wallet address
  5. Deposit USDC to Hyperliquid (Arbitrum bridge)

Safety:
  - Max position size: $15 (15% of $100)
  - Max open positions: 5
  - Hard stop loss: -5% per trade
  - Max daily loss: -$10 (10% of capital)
  - Leverage: 1x (no leverage)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ───
LIVE_MODE = "--live" in sys.argv
DATA_DIR = Path(__file__).parent / "data"
LIVE_DIR = DATA_DIR / "live"
LIVE_POSITIONS = LIVE_DIR / "positions.json"
LIVE_CLOSED = LIVE_DIR / "closed.jsonl"
LIVE_PORTFOLIO = LIVE_DIR / "portfolio.json"
LIVE_LOG = LIVE_DIR / "executor.log"

# Risk params for $100 account
INITIAL_CAPITAL = 100.0
MAX_POSITION_PCT = 0.15        # 15% per trade ($15 max)
MAX_OPEN_POSITIONS = 5
MAX_DAILY_LOSS_PCT = 0.10      # 10% max daily loss
STOP_LOSS_PCT = 0.05           # 5% hard stop per trade
LEVERAGE = 1                   # no leverage
MIN_SHARPE = 1.5               # minimum signal quality
MIN_WIN_RATE = 50.0            # minimum win rate

# Hyperliquid coin → asset index mapping (perps)
# Run info.meta() to get full list; these are the common ones
COIN_TO_ASSET = {
    "BTC": 0, "ETH": 1, "SOL": 5, "DOGE": 17,
    "AVAX": 10, "LINK": 14, "ARB": 35, "NEAR": 33,
    "SUI": 54, "INJ": 37
}

# Size decimals per coin (Hyperliquid requires specific precision)
COIN_SIZE_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0,
    "AVAX": 2, "LINK": 1, "ARB": 0, "NEAR": 1,
    "SUI": 1, "INJ": 1
}

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIVE_LOG, "a") as f:
        f.write(line + "\n")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_env():
    """Load Hyperliquid credentials from .env"""
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

def get_client():
    """Initialize Hyperliquid SDK client."""
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    env = load_env()
    secret = env.get("HYPERLIQUID_SECRET_KEY")
    account = env.get("HYPERLIQUID_ACCOUNT_ADDRESS")

    if not secret or not account:
        log("ERROR: HYPERLIQUID_SECRET_KEY and HYPERLIQUID_ACCOUNT_ADDRESS required in ~/.config/openclaw/.env")
        sys.exit(1)

    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    exchange = Exchange(account, secret, constants.MAINNET_API_URL)

    return info, exchange, account

def load_live_portfolio():
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if LIVE_PORTFOLIO.exists():
        with open(LIVE_PORTFOLIO) as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "started": now_iso(),
        "trades": 0,
        "wins": 0,
        "daily_loss": 0,
        "daily_reset": datetime.now(timezone.utc).strftime("%Y-%m-%d")
    }

def save_live_portfolio(p):
    with open(LIVE_PORTFOLIO, "w") as f:
        json.dump(p, f, indent=2)

def load_live_positions():
    if LIVE_POSITIONS.exists():
        with open(LIVE_POSITIONS) as f:
            return json.load(f)
    return []

def save_live_positions(positions):
    with open(LIVE_POSITIONS, "w") as f:
        json.dump(positions, f, indent=2)

def append_closed(record):
    with open(LIVE_CLOSED, "a") as f:
        f.write(json.dumps(record) + "\n")

def get_mid_price(info, coin):
    """Get current mid price from Hyperliquid orderbook."""
    asset = COIN_TO_ASSET.get(coin)
    if asset is None:
        return None
    try:
        book = info.l2_snapshot(coin)
        if book and "levels" in book:
            bids = book["levels"][0]
            asks = book["levels"][1]
            if bids and asks:
                return (float(bids[0]["px"]) + float(asks[0]["px"])) / 2
    except Exception as e:
        log(f"Price fetch failed for {coin}: {e}")
    return None

def place_order(exchange, coin, is_buy, size_usd, price):
    """Place a market order on Hyperliquid."""
    asset = COIN_TO_ASSET.get(coin)
    if asset is None:
        log(f"ERROR: {coin} not in asset mapping")
        return None

    # Calculate size in coin units
    size_coins = size_usd / price
    decimals = COIN_SIZE_DECIMALS.get(coin, 2)
    size_coins = round(size_coins, decimals)

    if size_coins <= 0:
        log(f"ERROR: size too small for {coin}: {size_coins}")
        return None

    # Use IOC market order (immediate fill)
    # Price with 0.5% slippage tolerance
    limit_price = price * (1.005 if is_buy else 0.995)
    limit_price = round(limit_price, 6)

    log(f"{'LIVE' if LIVE_MODE else 'DRY'} ORDER: {coin} {'BUY' if is_buy else 'SELL'} {size_coins} @ ~${limit_price:,.2f} (${size_usd:.2f})")

    if not LIVE_MODE:
        return {"status": "dry_run", "filled_price": price}

    try:
        result = exchange.order(
            coin,
            is_buy,
            size_coins,
            limit_price,
            {"limit": {"tif": "Ioc"}},
            reduce_only=False
        )
        log(f"Order result: {json.dumps(result)}")
        return result
    except Exception as e:
        log(f"Order failed: {e}")
        return None

def close_position(exchange, info, coin, is_long, size_usd, entry_price):
    """Close a position — sell if long, buy if short."""
    current_price = get_mid_price(info, coin)
    if not current_price:
        log(f"Can't close {coin} — no price data")
        return None, None

    is_buy = not is_long  # reverse direction to close
    result = place_order(exchange, coin, is_buy, size_usd, current_price)
    return result, current_price

def process_signals(info, exchange, portfolio, positions):
    """Read paper scanner fires and decide which to execute live."""
    fires_file = DATA_DIR / "fires.jsonl"
    if not fires_file.exists():
        return positions

    # Get latest fires (last 15 min)
    fires = []
    cutoff = time.time() - 900  # 15 min ago
    for line in fires_file.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            f = json.loads(line)
            fire_time = datetime.fromisoformat(f["time"]).timestamp()
            if fire_time > cutoff:
                fires.append(f)
        except:
            continue

    if not fires:
        return positions

    # Filter by quality
    qualified = [f for f in fires
                 if f.get("sharpe", 0) >= MIN_SHARPE
                 and f.get("win_rate", 0) >= MIN_WIN_RATE
                 and f.get("coin") in COIN_TO_ASSET]

    # Sort by Sharpe
    qualified.sort(key=lambda x: x.get("sharpe", 0), reverse=True)

    # Don't open if at position limit
    open_coins = {(p["coin"], p["direction"]) for p in positions}

    for fire in qualified:
        if len(positions) >= MAX_OPEN_POSITIONS:
            break

        key = (fire["coin"], fire["direction"])
        if key in open_coins:
            continue

        # Don't open opposing position on same coin
        opposite = (fire["coin"], "SHORT" if fire["direction"] == "LONG" else "LONG")
        if opposite in open_coins:
            log(f"SKIP {fire['coin']} {fire['direction']} — opposing position open")
            continue

        # Check daily loss limit
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if portfolio.get("daily_reset") != today:
            portfolio["daily_loss"] = 0
            portfolio["daily_reset"] = today

        if portfolio["daily_loss"] >= INITIAL_CAPITAL * MAX_DAILY_LOSS_PCT:
            log(f"DAILY LOSS LIMIT HIT: ${portfolio['daily_loss']:.2f}")
            break

        # Size the position
        size = min(portfolio["capital"] * MAX_POSITION_PCT, portfolio["capital"] * 0.5)
        if size < 5:
            log("Insufficient capital")
            break

        # Get current price from Hyperliquid
        price = get_mid_price(info, fire["coin"])
        if not price:
            continue

        # Place order
        is_buy = fire["direction"] == "LONG"
        result = place_order(exchange, fire["coin"], is_buy, size, price)

        if result:
            fill_price = result.get("filled_price", price) if isinstance(result, dict) else price
            portfolio["capital"] -= size

            position = {
                "coin": fire["coin"],
                "signal_name": fire["signal"],
                "rarity": fire.get("rarity", "unknown"),
                "direction": fire["direction"],
                "entry_price": fill_price,
                "entry_time": now_iso(),
                "exit_expression": fire.get("exit_expression", ""),
                "max_hold_hours": fire.get("max_hold_hours", 48),
                "size": round(size, 2),
                "sharpe": fire.get("sharpe", 0),
                "win_rate": fire.get("win_rate", 0),
                "stop_loss_price": fill_price * (1 - STOP_LOSS_PCT) if is_buy else fill_price * (1 + STOP_LOSS_PCT)
            }
            positions.append(position)
            open_coins.add(key)
            log(f"OPENED {fire['coin']} {fire['direction']} | ${size:.2f} @ ${fill_price:,.2f} | SL: ${position['stop_loss_price']:,.2f}")

    return positions

def check_exits(info, exchange, portfolio, positions):
    """Check stop losses and exit conditions on open positions."""
    remaining = []
    for pos in positions:
        price = get_mid_price(info, pos["coin"])
        if not price:
            remaining.append(pos)
            continue

        # Check stop loss
        is_long = pos["direction"] == "LONG"
        hit_stop = (is_long and price <= pos.get("stop_loss_price", 0)) or \
                   (not is_long and price >= pos.get("stop_loss_price", float("inf")))

        # Check max hold time
        entry_time = datetime.fromisoformat(pos["entry_time"])
        hours_held = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
        timed_out = hours_held >= pos.get("max_hold_hours", 48)

        if hit_stop or timed_out:
            reason = "stop_loss" if hit_stop else "timeout"
            result, exit_price = close_position(exchange, info, pos["coin"], is_long, pos["size"], pos["entry_price"])

            if exit_price:
                if is_long:
                    pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
                else:
                    pnl_pct = ((pos["entry_price"] - exit_price) / pos["entry_price"]) * 100

                pnl_dollars = pos["size"] * (pnl_pct / 100)
                portfolio["capital"] += pos["size"] + pnl_dollars
                portfolio["trades"] += 1
                portfolio["daily_loss"] += max(0, -pnl_dollars)
                if pnl_pct > 0:
                    portfolio["wins"] += 1

                record = {
                    "coin": pos["coin"],
                    "direction": pos["direction"],
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_dollars": round(pnl_dollars, 2),
                    "hours_held": round(hours_held, 1),
                    "exit_reason": reason,
                    "time": now_iso(),
                    "live": LIVE_MODE
                }
                append_closed(record)
                log(f"CLOSED {pos['coin']} {pos['direction']} | {pnl_pct:+.2f}% | ${pnl_dollars:+.2f} | {reason}")
            else:
                remaining.append(pos)  # couldn't close, keep
        else:
            remaining.append(pos)

    return remaining

def run():
    log(f"{'='*50}")
    log(f"ZERO OS Executor — {'LIVE' if LIVE_MODE else 'DRY RUN'}")
    log(f"{'='*50}")

    if LIVE_MODE:
        info, exchange, account = get_client()
        # Check account balance
        try:
            state = info.user_state(account)
            margin = state.get("marginSummary", {})
            balance = float(margin.get("accountValue", 0))
            log(f"Hyperliquid balance: ${balance:,.2f}")
        except Exception as e:
            log(f"WARNING: Could not fetch balance: {e}")
    else:
        info, exchange = None, None
        log("Dry run — no orders will be placed")

    portfolio = load_live_portfolio()
    positions = load_live_positions()

    log(f"Portfolio: ${portfolio['capital']:.2f} | Open: {len(positions)} | Closed: {portfolio['trades']}")

    # Check exits first
    if LIVE_MODE:
        positions = check_exits(info, exchange, portfolio, positions)

    # Process new signals (need info client for price lookups)
    if info:
        positions = process_signals(info, exchange, portfolio, positions)
    else:
        log("Dry run — skipping signal processing (no exchange client)")

    # Save
    save_live_portfolio(portfolio)
    save_live_positions(positions)

    log(f"Done. Capital: ${portfolio['capital']:.2f} | Positions: {len(positions)}")

if __name__ == "__main__":
    run()

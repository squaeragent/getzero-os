#!/usr/bin/env python3
"""
ZERO OS — Live Signal Scanner
Runs every 15 minutes. Fetches indicators for all coins,
evaluates signal pack entry/exit expressions, logs fires.
Tracks open positions and closes when exit triggers.

Data stored in scanner/data/:
  - signals_cache/{COIN}.yaml   — cached signal packs per coin
  - fires.jsonl                 — every signal fire event
  - positions.json              — currently open paper positions
  - closed.jsonl                — closed positions with P&L
  - portfolio.json              — portfolio state
"""

import json
import os
import sys
import time
import re
import math
import yaml
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
DATA_DIR = Path(__file__).parent / "data"
SIGNALS_DIR = DATA_DIR / "signals_cache"
FIRES_LOG = DATA_DIR / "fires.jsonl"
POSITIONS_FILE = DATA_DIR / "positions.json"
CLOSED_LOG = DATA_DIR / "closed.jsonl"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"

# Top coins to scan (expand later)
SCAN_COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"]

# Portfolio config
INITIAL_CAPITAL = 10000.0
POSITION_SIZE_PCT = 0.05  # 5% base per trade (scales with Sharpe)
MAX_OPEN_POSITIONS = 10
MAX_PER_COIN = 5  # max open trades on any single coin
MIN_SHARPE = 2.0  # raised from 1.5

# ─── API ───
def get_api_key():
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("ENVY_API_KEY="):
                val = line.split("=", 1)[1]
                return val.strip().strip('"').strip("'")
    raise RuntimeError("ENVY_API_KEY not found in ~/.config/openclaw/.env")

API_KEY = get_api_key()

def api_get(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read().decode()
        # Signal packs return YAML, not JSON
        if data.strip().startswith("#") or data.strip().startswith("coin:"):
            return yaml.safe_load(data)
        return json.loads(data)

# ─── SIGNAL CACHE ───
def load_signals(coin):
    """Load cached signals for a coin, fetch if missing."""
    cache_file = SIGNALS_DIR / f"{coin}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    print(f"  Fetching signal packs for {coin}...")
    all_signals = []
    for pack_type in ["common", "rare", "trump"]:
        try:
            data = api_get("/paid/signals/pack", {"coin": coin, "type": pack_type})
            if isinstance(data, dict) and "signals" in data:
                for sig in data["signals"]:
                    sig["_pack"] = pack_type
                    sig["_coin"] = coin
                    all_signals.append(sig)
            time.sleep(0.5)  # rate limit courtesy
        except Exception as e:
            print(f"    Warning: failed to fetch {pack_type} pack for {coin}: {e}")

    # Cache
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(all_signals, f, indent=2)
    print(f"  Cached {len(all_signals)} signals for {coin}")
    return all_signals

# ─── EXPRESSION EVALUATOR ───
def evaluate_expression(expr, indicators):
    """Evaluate a signal expression against current indicator values.
    Handles: AND, OR, >=, <=, <, >, +, *, (), scored expressions.
    """
    if not expr or not indicators:
        return False

    try:
        # Replace indicator codes with their values
        safe_expr = expr

        # Handle scored expressions: ((INDICATOR op VALUE) * WEIGHT)
        # These use multiplication for weighting, need special handling

        # Sort indicator names by length (longest first) to avoid partial replacements
        sorted_names = sorted(indicators.keys(), key=len, reverse=True)
        for name in sorted_names:
            val = indicators.get(name)
            if val is not None:
                safe_expr = safe_expr.replace(name, str(float(val)))

        # Check if any indicator names remain (missing data)
        remaining = re.findall(r'[A-Z][A-Z0-9_]{3,}', safe_expr)
        if remaining:
            return False  # Missing indicator data

        # Replace AND/OR with Python operators
        safe_expr = safe_expr.replace(" AND ", " and ")
        safe_expr = safe_expr.replace(" OR ", " or ")

        # Security: only allow safe characters
        allowed = set("0123456789.+-*/<>=() andor\n\t ")
        if not all(c in allowed for c in safe_expr.lower()):
            return False

        result = eval(safe_expr)
        return bool(result)
    except Exception:
        return False

# ─── INDICATOR FETCH ───
def fetch_indicators(coins_batch):
    """Fetch all indicators for a batch of coins (max 10)."""
    try:
        data = api_get("/paid/indicators/snapshot", {"coins": ",".join(coins_batch)})
        if not data.get("snapshot"):
            return {}

        result = {}
        for coin, ind_list in data["snapshot"].items():
            if not isinstance(ind_list, list):
                continue
            indicators = {}
            for ind in ind_list:
                indicators[ind["indicatorCode"]] = ind["value"]
            result[coin] = indicators
        return result
    except Exception as e:
        print(f"  Error fetching indicators: {e}")
        return {}

# ─── PORTFOLIO ───
def load_portfolio():
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {"capital": INITIAL_CAPITAL, "started": now_iso(), "trades": 0, "wins": 0}

def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

def load_positions():
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return []

def save_positions(positions):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")

# ─── MAIN SCAN ───
def scan():
    print(f"\n{'='*60}")
    print(f"ZERO OS Signal Scanner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Load portfolio & positions
    portfolio = load_portfolio()
    positions = load_positions()

    # Fetch current indicators for all coins
    print(f"\nFetching indicators for {len(SCAN_COINS)} coins...")
    all_indicators = {}
    for i in range(0, len(SCAN_COINS), 10):
        batch = SCAN_COINS[i:i+10]
        indicators = fetch_indicators(batch)
        all_indicators.update(indicators)
        if i + 10 < len(SCAN_COINS):
            time.sleep(0.5)

    print(f"  Got data for {len(all_indicators)} coins")

    # Check exit conditions on open positions
    closed_this_run = []
    remaining_positions = []
    for pos in positions:
        coin = pos["coin"]
        indicators = all_indicators.get(coin, {})
        price = indicators.get("CLOSE_PRICE_15M", pos.get("entry_price", 0))

        # Check max hold time
        entry_time = datetime.fromisoformat(pos["entry_time"])
        hours_held = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
        max_hold = pos.get("max_hold_hours", 48)

        exit_triggered = evaluate_expression(pos.get("exit_expression", ""), indicators)
        timed_out = hours_held >= max_hold

        if exit_triggered or timed_out:
            # Close position
            pnl_pct = 0
            if pos["direction"] == "LONG":
                pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]) * 100
            else:
                pnl_pct = ((pos["entry_price"] - price) / pos["entry_price"]) * 100

            pnl_dollars = pos["size"] * (pnl_pct / 100)
            portfolio["capital"] += pos["size"] + pnl_dollars
            portfolio["trades"] += 1
            if pnl_pct > 0:
                portfolio["wins"] += 1

            close_record = {
                "coin": coin,
                "signal": pos["signal_name"],
                "rarity": pos.get("rarity", "unknown"),
                "direction": pos["direction"],
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "entry_time": pos["entry_time"],
                "exit_time": now_iso(),
                "hours_held": round(hours_held, 1),
                "exit_reason": "signal" if exit_triggered else "timeout",
                "pnl_pct": round(pnl_pct, 4),
                "pnl_dollars": round(pnl_dollars, 2),
                "size": pos["size"],
                "portfolio_after": round(portfolio["capital"], 2)
            }
            append_jsonl(CLOSED_LOG, close_record)
            closed_this_run.append(close_record)
            print(f"  CLOSED {coin} {pos['direction']} | {pnl_pct:+.2f}% | ${pnl_dollars:+.2f} | reason: {close_record['exit_reason']}")
        else:
            remaining_positions.append(pos)

    # Scan for new signal fires
    fires_this_run = []
    for coin in SCAN_COINS:
        indicators = all_indicators.get(coin)
        if not indicators:
            continue

        signals = load_signals(coin)
        for sig in signals:
            entry_expr = sig.get("expression", "")
            if not entry_expr:
                continue

            # Check if signal fires
            if evaluate_expression(entry_expr, indicators):
                fire = {
                    "coin": coin,
                    "signal": sig.get("name", "unknown"),
                    "rarity": sig.get("rarity", "unknown"),
                    "direction": sig.get("signal_type", "LONG"),
                    "sharpe": sig.get("sharpe", 0),
                    "win_rate": sig.get("win_rate", 0),
                    "max_drawdown": sig.get("max_drawdown", 0),
                    "total_return": sig.get("total_return", 0),
                    "expression": entry_expr,
                    "exit_expression": sig.get("exit_expression", ""),
                    "max_hold_hours": sig.get("max_hold_hours", 48),
                    "price": indicators.get("CLOSE_PRICE_15M", 0),
                    "time": now_iso()
                }
                fires_this_run.append(fire)
                append_jsonl(FIRES_LOG, fire)

    print(f"\n  Signals fired: {len(fires_this_run)}")
    for f in fires_this_run[:10]:  # show first 10
        print(f"    🔥 {f['coin']} {f['direction']} | {f['signal'][:50]} | Sharpe {f['sharpe']:.2f} | WR {f['win_rate']:.0f}%")

    # Open new paper positions from best fires
    if fires_this_run and len(remaining_positions) < MAX_OPEN_POSITIONS:
        # Sort by Sharpe ratio, pick best
        fires_this_run.sort(key=lambda x: x.get("sharpe", 0), reverse=True)

        # Don't double-enter same coin+direction
        open_keys = {(p["coin"], p["direction"]) for p in remaining_positions}
        coin_trades_this_scan = {}

        # Count existing open positions per coin
        coin_open_count = {}
        for p in remaining_positions:
            coin_open_count[p["coin"]] = coin_open_count.get(p["coin"], 0) + 1

        for fire in fires_this_run:
            if len(remaining_positions) >= MAX_OPEN_POSITIONS:
                break
            key = (fire["coin"], fire["direction"])
            if key in open_keys:
                continue

            # Max 2 entries per coin per scan (prevents spam)
            ct = coin_trades_this_scan.get(fire["coin"], 0)
            if ct >= 2:
                continue

            # Max open trades per coin across all time
            if coin_open_count.get(fire["coin"], 0) >= MAX_PER_COIN:
                continue

            # No opposing positions on same coin
            opposite = (fire["coin"], "SHORT" if fire["direction"] == "LONG" else "LONG")
            if opposite in open_keys:
                continue

            # Minimum quality
            sharpe = fire.get("sharpe", 0)
            if sharpe < MIN_SHARPE:
                continue
            if fire.get("win_rate", 0) < 50:
                continue

            # Sharpe-weighted position sizing: base 5%, up to 10% for Sharpe 3.0+
            size_mult = min(sharpe / 2.0, 2.0)  # 1.0x at Sharpe 2.0, 1.5x at 3.0, 2.0x cap at 4.0+
            size = portfolio["capital"] * POSITION_SIZE_PCT * size_mult
            if size < 10:
                continue  # too small

            portfolio["capital"] -= size
            position = {
                "coin": fire["coin"],
                "signal_name": fire["signal"],
                "rarity": fire["rarity"],
                "direction": fire["direction"],
                "entry_price": fire["price"],
                "entry_time": fire["time"],
                "exit_expression": fire["exit_expression"],
                "max_hold_hours": fire["max_hold_hours"],
                "size": round(size, 2),
                "sharpe": fire["sharpe"],
                "win_rate": fire["win_rate"]
            }
            remaining_positions.append(position)
            open_keys.add(key)
            coin_trades_this_scan[fire["coin"]] = ct + 1
            coin_open_count[fire["coin"]] = coin_open_count.get(fire["coin"], 0) + 1
            print(f"    📈 OPENED {fire['coin']} {fire['direction']} | ${size:.2f} @ ${fire['price']:,.2f} | Sharpe {fire['sharpe']:.2f} | size_mult {size_mult:.1f}x")

    # Save state
    save_positions(remaining_positions)
    save_portfolio(portfolio)

    # Summary
    total_in_positions = sum(p["size"] for p in remaining_positions)
    total_value = portfolio["capital"] + total_in_positions
    win_rate = (portfolio["wins"] / portfolio["trades"] * 100) if portfolio["trades"] > 0 else 0

    print(f"\n{'─'*40}")
    print(f"  Portfolio: ${total_value:,.2f} (cash: ${portfolio['capital']:,.2f})")
    print(f"  Open positions: {len(remaining_positions)}")
    print(f"  Trades closed: {portfolio['trades']} (WR: {win_rate:.0f}%)")
    if closed_this_run:
        print(f"  Closed this run: {len(closed_this_run)}")
    print(f"{'─'*40}\n")

def export_snapshot():
    """Export current state as a single JSON for the website API."""
    portfolio = load_portfolio()
    positions = load_positions()

    closed = []
    if CLOSED_LOG.exists():
        for line in CLOSED_LOG.read_text().strip().split("\n"):
            if line:
                try: closed.append(json.loads(line))
                except: pass

    fires = []
    if FIRES_LOG.exists():
        for line in FIRES_LOG.read_text().strip().split("\n"):
            if line:
                try: fires.append(json.loads(line))
                except: pass

    total_in = sum(p.get("size", 0) for p in positions)
    total_value = portfolio["capital"] + total_in
    started = portfolio.get("started", now_iso())
    days = max(1, int((time.time() - datetime.fromisoformat(started).timestamp()) / 86400) + 1)

    snapshot = {
        "live": True,
        "updated": now_iso(),
        "summary": {
            "startingCapital": 10000,
            "currentValue": round(total_value, 2),
            "cash": round(portfolio["capital"], 2),
            "pnl": round(total_value - 10000, 2),
            "pnlPct": round((total_value - 10000) / 100, 2),
            "totalTrades": portfolio["trades"],
            "winRate": round(portfolio["wins"] / portfolio["trades"] * 100, 1) if portfolio["trades"] > 0 else None,
            "openPositions": len(positions),
            "daysRunning": days,
            "started": started
        },
        "positions": positions,
        "closed": closed[-50:],
        "recentFires": fires[-20:]
    }

    out = Path(__file__).parent.parent / "public" / "api" / "portfolio.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(f"  Exported snapshot to {out}")

def push_to_repo():
    """Git commit and push the portfolio snapshot."""
    import subprocess
    repo = Path(__file__).parent.parent
    try:
        subprocess.run(["git", "add", "public/api/portfolio.json"], cwd=repo, capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "commit", "-m", f"scanner: update portfolio snapshot"],
            cwd=repo, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            subprocess.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, timeout=30)
            print("  Pushed snapshot to repo")
        else:
            print("  No changes to push")
    except Exception as e:
        print(f"  Git push failed: {e}")

if __name__ == "__main__":
    scan()
    export_snapshot()
    push_to_repo()

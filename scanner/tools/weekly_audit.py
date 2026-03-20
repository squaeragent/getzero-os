#!/usr/bin/env python3
"""
Weekly Self-Audit — Data Collection Script
Collects ALL quantitative data from the audit spec and outputs a JSON report.
Does NOT make judgment calls (those are for the AI agent).

Usage:
  python3 scanner/tools/weekly_audit.py [--output /path/to/output.json]
"""

import json
import os
import sys
import subprocess
import argparse
import urllib.request
import urllib.error
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─── Paths ────────────────────────────────────────────────────────────────────
SCANNER_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = SCANNER_DIR.parent
AUDIT_DIR = SCANNER_DIR / "data" / "audit"

LIVE_POSITIONS = SCANNER_DIR / "data" / "live" / "positions.json"
LIVE_PORTFOLIO = SCANNER_DIR / "data" / "live" / "portfolio.json"
V6_POSITIONS = SCANNER_DIR / "v6" / "bus" / "positions.json"
V6_HEARTBEAT = SCANNER_DIR / "v6" / "bus" / "heartbeat.json"
V6_RISK = SCANNER_DIR / "v6" / "bus" / "risk.json"
V6_EQUITY_HISTORY = SCANNER_DIR / "v6" / "bus" / "equity_history.jsonl"
V6_SHARPE_HISTORY = SCANNER_DIR / "v6" / "bus" / "sharpe_history.json"
V6_IMMUNE_STATE = SCANNER_DIR / "v6" / "bus" / "immune_state.json"
CLOSED_TRADES = SCANNER_DIR / "data" / "live" / "closed.jsonl"
V6_CLOSED_TRADES = SCANNER_DIR / "v6" / "bus" / "closed.jsonl"
CONFIG_FILE = SCANNER_DIR / "config.yaml"
SUPERVISOR_LOG = SCANNER_DIR / "v6" / "supervisor.log"
SIGNAL_OUTCOMES = SCANNER_DIR / "memory" / "signal_outcomes.jsonl"

# ─── Constants ────────────────────────────────────────────────────────────────
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
MAIN_WALLET = "0xA5F25E3Bbf7a10EB61EEfA471B61E1dfa5777884"
ENVY_BASE_URL = "https://gate.getzero.dev/api/claw"

# ─── Env loading ──────────────────────────────────────────────────────────────

def load_env():
    """Load env vars from ~/.config/openclaw/.env into os.environ."""
    env_path = Path.home() / ".config" / "openclaw" / ".env"
    if not env_path.exists():
        log(f"WARNING: {env_path} not found")
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_env(key):
    return os.environ.get(key, "")


# ─── Helpers ──────────────────────────────────────────────────────────────────

ERRORS = []

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [AUDIT] {msg}", flush=True)


def safe(fn, label):
    """Run fn, return result. On exception, log and return None."""
    try:
        return fn()
    except Exception as e:
        ERRORS.append({"check": label, "error": str(e)})
        log(f"ERROR in {label}: {e}")
        return None


def load_json_file(path, default=None):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def load_jsonl(path, max_lines=0):
    """Load JSONL file. max_lines=0 means all."""
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if max_lines > 0:
        return records[-max_lines:]
    return records


def hl_post(payload, timeout=15):
    """POST to Hyperliquid info API."""
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def envy_get(path, params=None, timeout=15):
    """GET from ENVY API."""
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    api_key = get_env("ENVY_API_KEY")
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def load_config():
    """Parse scanner/config.yaml (simple YAML, no dependency needed)."""
    if not CONFIG_FILE.exists():
        return {}
    cfg = {}
    with open(CONFIG_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            # strip inline comments
            if "#" in v:
                v = v[:v.index("#")].strip()
            # try numeric
            try:
                v = int(v)
            except ValueError:
                try:
                    v = float(v)
                except ValueError:
                    pass
            cfg[k] = v
    return cfg


def file_info(path):
    """Return dict with size, mtime, exists, valid_json for a file."""
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    info = {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "age_seconds": (datetime.now(timezone.utc) - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)).total_seconds(),
    }
    if path.suffix == ".json":
        try:
            with open(path) as f:
                json.load(f)
            info["valid_json"] = True
        except (json.JSONDecodeError, Exception):
            info["valid_json"] = False
    elif path.suffix == ".jsonl":
        try:
            with open(path) as f:
                first = f.readline().strip()
                if first:
                    json.loads(first)
                    info["valid_json"] = True
                else:
                    info["valid_json"] = False
        except Exception:
            info["valid_json"] = False
    return info


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: Core State Integrity
# ═══════════════════════════════════════════════════════════════════════════════

def check_1_1_position_sync():
    """1.1 Position sync — HL vs local, exact match on coin/direction/size."""
    # Get HL positions
    result = hl_post({"type": "clearinghouseState", "user": MAIN_WALLET})
    hl_positions_raw = result.get("assetPositions", [])
    hl_positions = []
    for ap in hl_positions_raw:
        pos = ap.get("position", {})
        szi = float(pos.get("szi", "0"))
        if szi == 0:
            continue
        hl_positions.append({
            "coin": pos.get("coin", ""),
            "direction": "LONG" if szi > 0 else "SHORT",
            "size_coins": abs(szi),
            "entry_price": float(pos.get("entryPx", "0")),
            "unrealized_pnl": float(pos.get("unrealizedPnl", "0")),
            "leverage_type": pos.get("leverage", {}).get("type", ""),
            "leverage_value": float(pos.get("leverage", {}).get("value", "0")),
            "margin_used": float(pos.get("marginUsed", "0")),
            "return_on_equity": float(pos.get("returnOnEquity", "0")),
            "liquidation_px": pos.get("liquidationPx"),
        })

    # Get local positions (v6)
    v6_data = load_json_file(V6_POSITIONS, {})
    local_positions = v6_data.get("positions", [])
    local_updated_at = v6_data.get("updated_at", "")

    # Also get live positions
    live_positions = load_json_file(LIVE_POSITIONS, [])
    if isinstance(live_positions, dict):
        live_positions = live_positions.get("positions", [])

    # Build comparison
    hl_map = {p["coin"]: p for p in hl_positions}
    local_map = {p.get("coin"): p for p in local_positions}
    live_map = {p.get("coin"): p for p in live_positions}

    mismatches = []
    all_coins = set(list(hl_map.keys()) + list(local_map.keys()))
    for coin in sorted(all_coins):
        hl = hl_map.get(coin)
        loc = local_map.get(coin)
        entry = {"coin": coin}
        if hl and not loc:
            entry["issue"] = "HL_ONLY"
            entry["hl"] = hl
        elif loc and not hl:
            entry["issue"] = "LOCAL_ONLY"
            entry["local"] = loc
        else:
            entry["hl_direction"] = hl["direction"]
            entry["local_direction"] = loc.get("direction")
            entry["hl_size"] = hl["size_coins"]
            entry["local_size"] = loc.get("size_coins")
            entry["direction_match"] = hl["direction"] == loc.get("direction")
            entry["size_match"] = abs(hl["size_coins"] - (loc.get("size_coins") or 0)) < 1e-8
            if not entry["direction_match"] or not entry["size_match"]:
                entry["issue"] = "MISMATCH"
            else:
                entry["issue"] = None
        mismatches.append(entry)

    return {
        "hl_positions": hl_positions,
        "local_positions": local_positions,
        "local_updated_at": local_updated_at,
        "live_positions_count": len(live_positions),
        "comparison": mismatches,
        "hl_account_value": float(result.get("marginSummary", {}).get("accountValue", "0")),
        "hl_total_margin_used": float(result.get("marginSummary", {}).get("totalMarginUsed", "0")),
        "hl_withdrawable": float(result.get("marginSummary", {}).get("totalRawUsd", "0")),
    }


def check_1_2_stop_sync():
    """1.2 Stop sync — stop exists per position, oid match, trigger/limit offset."""
    # Get frontend open orders (includes trigger orders)
    orders = hl_post({"type": "frontendOpenOrders", "user": MAIN_WALLET})

    # Get positions for comparison
    v6_data = load_json_file(V6_POSITIONS, {})
    positions = v6_data.get("positions", [])
    position_coins = {p.get("coin") for p in positions}

    # Parse stop orders
    stop_orders = []
    for o in orders:
        if o.get("orderType") in ("Stop Market", "Stop Limit", "Take Profit Market", "Take Profit Limit"):
            stop_orders.append(o)
        elif "children" in o:
            # TP/SL children
            for child in o.get("children", []):
                if child.get("orderType") in ("Stop Market", "Stop Limit"):
                    stop_orders.append(child)

    # Check coverage
    stop_coins = set()
    order_details = []
    for o in stop_orders:
        coin = o.get("coin", "")
        stop_coins.add(coin)
        trigger_px = float(o.get("triggerPx", "0"))
        limit_px = float(o.get("limitPx", "0"))
        side = o.get("side", "")
        order_details.append({
            "coin": coin,
            "oid": o.get("oid"),
            "order_type": o.get("orderType"),
            "side": side,
            "trigger_px": trigger_px,
            "limit_px": limit_px,
            "sz": o.get("sz"),
            "trigger_limit_offset_pct": abs(trigger_px - limit_px) / trigger_px * 100 if trigger_px else 0,
        })

    naked = [c for c in position_coins if c not in stop_coins]
    orphan_stops = [c for c in stop_coins if c not in position_coins]

    return {
        "all_orders": orders,
        "stop_orders": order_details,
        "naked_positions": naked,
        "orphan_stop_orders": orphan_stops,
        "position_coins": sorted(position_coins),
        "stop_coins": sorted(stop_coins),
    }


def check_1_3_portfolio_state():
    """1.3 Portfolio state — capital within 5% of equity, freshness."""
    portfolio = load_json_file(LIVE_PORTFOLIO, {})
    v6_risk = load_json_file(V6_RISK, {})

    # Get real equity from HL
    spot = hl_post({"type": "spotClearinghouseState", "user": MAIN_WALLET})
    hl_usdc = 0
    for b in spot.get("balances", []):
        if b.get("coin") == "USDC":
            hl_usdc = float(b.get("total", 0))

    perps = hl_post({"type": "clearinghouseState", "user": MAIN_WALLET})
    hl_account_value = float(perps.get("marginSummary", {}).get("accountValue", "0"))

    config = load_config()
    config_capital = config.get("capital", 750)

    local_capital = portfolio.get("capital", 0)
    local_balance = portfolio.get("balance", 0)
    last_update = portfolio.get("last_update", "")

    freshness_seconds = None
    if last_update:
        try:
            lu = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
            freshness_seconds = (datetime.now(timezone.utc) - lu).total_seconds()
        except Exception:
            pass

    return {
        "config_capital": config_capital,
        "local_capital": local_capital,
        "local_balance": local_balance,
        "hl_perp_account_value": hl_account_value,
        "hl_spot_usdc": hl_usdc,
        "hl_total_equity": hl_account_value + hl_usdc,
        "capital_vs_equity_pct": abs(local_capital - hl_account_value) / hl_account_value * 100 if hl_account_value else None,
        "last_update": last_update,
        "freshness_seconds": freshness_seconds,
        "portfolio_trades": portfolio.get("trades", 0),
        "portfolio_wins": portfolio.get("wins", 0),
        "open_positions": portfolio.get("open_positions", 0),
        "total_notional": portfolio.get("total_notional", 0),
        "peak_equity": v6_risk.get("peak_equity", 0),
        "drawdown_pct": v6_risk.get("drawdown_pct", 0),
    }


def check_1_4_file_integrity():
    """1.4 File integrity — parseable JSON, not empty, timestamps."""
    files_to_check = [
        LIVE_POSITIONS, LIVE_PORTFOLIO,
        V6_POSITIONS, V6_HEARTBEAT, V6_RISK,
        V6_EQUITY_HISTORY, V6_SHARPE_HISTORY, V6_IMMUNE_STATE,
        SCANNER_DIR / "v6" / "bus" / "strategies.json",
        SCANNER_DIR / "v6" / "bus" / "approved.json",
        SCANNER_DIR / "v6" / "bus" / "entries.json",
        SCANNER_DIR / "v6" / "bus" / "allocation.json",
        SCANNER_DIR / "v6" / "bus" / "coin_streaks.json",
    ]
    results = {}
    for fp in files_to_check:
        results[fp.name] = file_info(fp)
    return results


def check_1_5_process_health():
    """1.5 Process health — PIDs, last cycle times from logs."""
    # Check running processes
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
    ps_output = result.stdout

    components = {
        "supervisor": "v6/supervisor",
        "executor": "v6/executor",
        "immune": "v6/immune",
        "evaluator": "v6/evaluator",
        "ws_stream": "ws_stream",
    }
    process_status = {}
    for name, pattern in components.items():
        matches = [l for l in ps_output.splitlines() if pattern in l and "grep" not in l]
        if matches:
            # Extract PID from first match
            parts = matches[0].split()
            pid = parts[1] if len(parts) > 1 else "?"
            process_status[name] = {"running": True, "pid": pid, "count": len(matches)}
        else:
            process_status[name] = {"running": False, "pid": None, "count": 0}

    # Check heartbeats
    heartbeats = load_json_file(V6_HEARTBEAT, {})
    now = datetime.now(timezone.utc)
    heartbeat_ages = {}
    for comp, ts_str in heartbeats.items():
        try:
            last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (now - last).total_seconds()
            heartbeat_ages[comp] = {"last": ts_str, "age_seconds": age, "stale": age > 300}
        except Exception:
            heartbeat_ages[comp] = {"last": ts_str, "age_seconds": None, "stale": True}

    # Last lines of supervisor log
    last_log_lines = []
    if SUPERVISOR_LOG.exists():
        try:
            with open(SUPERVISOR_LOG) as f:
                lines = f.readlines()
                last_log_lines = [l.strip() for l in lines[-20:]]
        except Exception:
            pass

    return {
        "processes": process_status,
        "heartbeats": heartbeat_ages,
        "supervisor_log_tail": last_log_lines,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: Risk & Exposure
# ═══════════════════════════════════════════════════════════════════════════════

def check_2_1_leverage_audit():
    """2.1 Leverage audit — actual leverage per position from HL."""
    result = hl_post({"type": "clearinghouseState", "user": MAIN_WALLET})
    positions = result.get("assetPositions", [])
    account_value = float(result.get("marginSummary", {}).get("accountValue", "0"))

    leverage_data = []
    total_notional = 0
    for ap in positions:
        pos = ap.get("position", {})
        szi = float(pos.get("szi", "0"))
        if szi == 0:
            continue
        entry_px = float(pos.get("entryPx", "0"))
        notional = abs(szi) * entry_px
        total_notional += notional
        leverage_data.append({
            "coin": pos.get("coin", ""),
            "size": abs(szi),
            "entry_px": entry_px,
            "notional_usd": notional,
            "leverage_type": pos.get("leverage", {}).get("type", ""),
            "leverage_value": float(pos.get("leverage", {}).get("value", "0")),
            "position_leverage": notional / account_value if account_value else 0,
            "margin_used": float(pos.get("marginUsed", "0")),
        })

    return {
        "positions": leverage_data,
        "account_value": account_value,
        "total_notional": total_notional,
        "aggregate_leverage": total_notional / account_value if account_value else 0,
    }


def check_2_2_capital_floor():
    """2.2 Capital floor — equity, peak, floor at 60%, headroom."""
    result = hl_post({"type": "clearinghouseState", "user": MAIN_WALLET})
    account_value = float(result.get("marginSummary", {}).get("accountValue", "0"))

    risk = load_json_file(V6_RISK, {})
    peak_equity = risk.get("peak_equity", 0)
    config = load_config()
    config_capital = config.get("capital", 750)

    floor_pct = 0.60
    floor_value = config_capital * floor_pct
    headroom = account_value - floor_value

    return {
        "current_equity": account_value,
        "peak_equity": peak_equity,
        "config_capital": config_capital,
        "floor_pct": floor_pct,
        "floor_value": floor_value,
        "headroom": headroom,
        "capital_floor_hit": risk.get("capital_floor_hit", False),
        "drawdown_from_peak_pct": (peak_equity - account_value) / peak_equity * 100 if peak_equity else 0,
    }


def check_2_3_daily_loss():
    """2.3 Daily loss — worst day P&L from closed trades."""
    # Load closed trades from both sources
    trades = load_jsonl(CLOSED_TRADES)
    v6_trades = load_jsonl(V6_CLOSED_TRADES)
    all_trades = trades + v6_trades

    if not all_trades:
        return {"no_trades": True}

    # Group by day
    daily_pnl = {}
    for t in all_trades:
        exit_time = t.get("exit_time", "")
        if not exit_time:
            continue
        try:
            day = exit_time[:10]  # YYYY-MM-DD
            pnl = float(t.get("pnl_usd", 0))
            daily_pnl.setdefault(day, 0)
            daily_pnl[day] += pnl
        except Exception:
            continue

    if not daily_pnl:
        return {"no_daily_data": True}

    worst_day = min(daily_pnl.items(), key=lambda x: x[1])
    best_day = max(daily_pnl.items(), key=lambda x: x[1])

    return {
        "daily_pnl": dict(sorted(daily_pnl.items())),
        "worst_day": {"date": worst_day[0], "pnl_usd": worst_day[1]},
        "best_day": {"date": best_day[0], "pnl_usd": best_day[1]},
        "total_days": len(daily_pnl),
        "total_pnl": sum(daily_pnl.values()),
    }


def check_2_4_position_count():
    """2.4 Position count vs MAX_POSITIONS."""
    config = load_config()
    max_positions = config.get("max_positions", 3)

    v6_data = load_json_file(V6_POSITIONS, {})
    positions = v6_data.get("positions", [])

    result = hl_post({"type": "clearinghouseState", "user": MAIN_WALLET})
    hl_count = sum(1 for ap in result.get("assetPositions", [])
                   if float(ap.get("position", {}).get("szi", "0")) != 0)

    return {
        "max_positions": max_positions,
        "local_count": len(positions),
        "hl_count": hl_count,
        "at_limit": len(positions) >= max_positions,
        "over_limit": len(positions) > max_positions,
    }


def check_2_5_funding_exposure():
    """2.5 Funding exposure per position — query HL funding rates."""
    # Get predicted fundings
    fundings = hl_post({"type": "predictedFundings"})

    v6_data = load_json_file(V6_POSITIONS, {})
    positions = v6_data.get("positions", [])

    # Build funding rate map
    funding_map = {}
    if isinstance(fundings, list):
        for item in fundings:
            if isinstance(item, list) and len(item) == 2:
                venue_data = item[1]
                if isinstance(venue_data, dict):
                    coin = venue_data.get("coin", "")
                    rate = venue_data.get("fundingRate", "0")
                    funding_map[coin] = float(rate)
            elif isinstance(item, dict):
                coin = item.get("coin", "")
                rate = item.get("fundingRate", "0")
                funding_map[coin] = float(rate)

    # Also try metaAndAssetCtxs for current funding
    meta_ctx = hl_post({"type": "metaAndAssetCtxs"})
    current_funding_map = {}
    if isinstance(meta_ctx, list) and len(meta_ctx) >= 2:
        universe = meta_ctx[0].get("universe", [])
        ctxs = meta_ctx[1]
        for i, ctx in enumerate(ctxs):
            if i < len(universe):
                coin = universe[i].get("name", "")
                current_funding_map[coin] = {
                    "funding": float(ctx.get("funding", "0")),
                    "open_interest": float(ctx.get("openInterest", "0")),
                    "mark_px": float(ctx.get("markPx", "0")),
                    "oracle_px": float(ctx.get("oraclePx", "0")),
                }

    exposure = []
    for pos in positions:
        coin = pos.get("coin", "")
        direction = pos.get("direction", "")
        size_usd = pos.get("size_usd", 0)
        rate = funding_map.get(coin, 0)
        current = current_funding_map.get(coin, {})
        # Funding is paid by longs when rate > 0, by shorts when rate < 0
        # Annualized = rate * 3 * 365 (HL pays every 8h = 3x/day)
        annual_rate = rate * 3 * 365
        hourly_cost = size_usd * rate / 8  # cost per hour
        daily_cost = size_usd * rate * 3   # cost per day
        exposure.append({
            "coin": coin,
            "direction": direction,
            "size_usd": size_usd,
            "predicted_funding_rate": rate,
            "annualized_rate_pct": annual_rate * 100,
            "daily_funding_cost_usd": daily_cost,
            "hourly_funding_cost_usd": hourly_cost,
            "paying_funding": (direction == "LONG" and rate > 0) or (direction == "SHORT" and rate < 0),
            "current_funding": current.get("funding", 0),
            "mark_price": current.get("mark_px", 0),
            "oracle_price": current.get("oracle_px", 0),
        })

    return {
        "positions": exposure,
        "predicted_funding_rates_sample": dict(list(funding_map.items())[:10]),
        "total_daily_funding_cost": sum(e["daily_funding_cost_usd"] for e in exposure),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: Execution Quality
# ═══════════════════════════════════════════════════════════════════════════════

def check_3_1_order_type_distribution():
    """3.1 Order type distribution — GTC vs IOC from trade log."""
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    # Also check signal outcomes for order type info
    outcomes = load_jsonl(SIGNAL_OUTCOMES)

    # Count exit reasons as proxy for order types
    exit_reasons = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "total_trades": len(trades),
        "exit_reason_distribution": exit_reasons,
        "signal_outcomes_count": len(outcomes),
    }


def check_3_2_fee_analysis():
    """3.2 Fee analysis — total fees, average rate."""
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    total_fees = 0
    total_volume = 0
    fee_records = []
    for t in trades:
        size_usd = float(t.get("size_usd", 0))
        fee = float(t.get("fee", 0))
        total_volume += size_usd
        total_fees += fee
        if fee != 0:
            fee_records.append({"coin": t.get("coin"), "fee": fee, "size_usd": size_usd})

    # Estimate fees from HL if not in trade records (0.035% taker, 0.01% maker typical)
    estimated_taker_fees = total_volume * 0.00035
    estimated_maker_fees = total_volume * 0.0001

    return {
        "total_recorded_fees": total_fees,
        "total_volume": total_volume,
        "avg_fee_rate": total_fees / total_volume if total_volume else 0,
        "estimated_taker_fees": estimated_taker_fees,
        "estimated_maker_fees": estimated_maker_fees,
        "n_trades_with_fees": len(fee_records),
        "n_total_trades": len(trades),
    }


def check_3_3_slippage():
    """3.3 Slippage — entry price vs intended price from trade records."""
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    slippage_data = []
    for t in trades:
        entry_px = float(t.get("entry_price", 0))
        intended_px = float(t.get("intended_price", 0)) or float(t.get("signal_price", 0))
        if entry_px and intended_px:
            slip = abs(entry_px - intended_px) / intended_px * 100
            slippage_data.append({
                "coin": t.get("coin"),
                "direction": t.get("direction"),
                "entry_px": entry_px,
                "intended_px": intended_px,
                "slippage_pct": slip,
            })

    return {
        "trades_with_slippage_data": len(slippage_data),
        "avg_slippage_pct": sum(s["slippage_pct"] for s in slippage_data) / len(slippage_data) if slippage_data else None,
        "max_slippage_pct": max((s["slippage_pct"] for s in slippage_data), default=None),
        "slippage_records": slippage_data[-20:],  # Last 20
        "total_trades": len(trades),
    }


def check_3_4_signal_rejection_rate():
    """3.4 Signal rejection rate from evaluator/risk logs."""
    outcomes = load_jsonl(SIGNAL_OUTCOMES)

    # Count fired vs rejected
    fired = 0
    rejected = 0
    reasons = {}
    for o in outcomes:
        if o.get("action") == "fire" or o.get("fired"):
            fired += 1
        elif o.get("action") == "reject" or o.get("rejected"):
            rejected += 1
            reason = o.get("reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1

    # Also check risk guard approved/rejected
    approved = load_json_file(SCANNER_DIR / "v6" / "bus" / "approved.json", {})

    # Check supervisor log for rejections
    rejection_log_count = 0
    if SUPERVISOR_LOG.exists():
        try:
            with open(SUPERVISOR_LOG) as f:
                for line in f:
                    if "REJECT" in line or "reject" in line:
                        rejection_log_count += 1
        except Exception:
            pass

    return {
        "signal_outcomes_total": len(outcomes),
        "fired": fired,
        "rejected": rejected,
        "rejection_rate_pct": rejected / (fired + rejected) * 100 if (fired + rejected) else None,
        "rejection_reasons": reasons,
        "supervisor_rejection_mentions": rejection_log_count,
    }


def check_3_5_fill_verification():
    """3.5 Fill verification — check recent trades were actually filled on HL."""
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    # Basic stats
    filled = [t for t in trades if t.get("exit_price") and float(t.get("exit_price", 0)) > 0]
    unfilled = [t for t in trades if not t.get("exit_price") or float(t.get("exit_price", 0)) == 0]

    return {
        "total_trades": len(trades),
        "filled_count": len(filled),
        "unfilled_count": len(unfilled),
        "fill_rate_pct": len(filled) / len(trades) * 100 if trades else 100,
        "last_10_trades": trades[-10:] if trades else [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4: Strategy Performance
# ═══════════════════════════════════════════════════════════════════════════════

def check_4_1_sharpe_gap():
    """4.1 Sharpe gap — ENVY backtested vs realized."""
    sharpe_history = load_json_file(V6_SHARPE_HISTORY, {})
    runs = sharpe_history.get("runs", [])

    # Latest ENVY Sharpe per coin
    latest_envy_sharpes = {}
    if runs:
        latest_run = runs[-1]
        latest_envy_sharpes = latest_run.get("sharpes", {})

    # Realized Sharpe from closed trades
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    per_coin_pnl = {}
    for t in trades:
        coin = t.get("coin", "")
        pnl_pct = float(t.get("pnl_pct", 0))
        per_coin_pnl.setdefault(coin, []).append(pnl_pct)

    realized_sharpes = {}
    for coin, returns in per_coin_pnl.items():
        if len(returns) >= 3:
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            std = variance ** 0.5
            realized_sharpes[coin] = mean / std if std > 0 else 0

    # Compare
    gaps = {}
    for coin in set(list(latest_envy_sharpes.keys()) + list(realized_sharpes.keys())):
        envy = latest_envy_sharpes.get(coin)
        realized = realized_sharpes.get(coin)
        if envy is not None and realized is not None:
            gaps[coin] = {
                "envy_sharpe": envy,
                "realized_sharpe": round(realized, 4),
                "gap": round(envy - realized, 4),
            }
        elif envy is not None:
            gaps[coin] = {"envy_sharpe": envy, "realized_sharpe": None, "gap": None}

    return {
        "latest_envy_sharpes": latest_envy_sharpes,
        "realized_sharpes": realized_sharpes,
        "gaps": gaps,
        "trades_per_coin": {c: len(v) for c, v in per_coin_pnl.items()},
    }


def check_4_5_envy_api_health():
    """4.5 ENVY API health — test call."""
    api_key = get_env("ENVY_API_KEY")
    if not api_key:
        return {"error": "ENVY_API_KEY not set"}

    try:
        result = envy_get("/paid/indicators/snapshot", {"coins": "BTC", "indicators": "RSI_24H"})
        return {
            "status": "ok",
            "response_keys": list(result.keys()) if isinstance(result, dict) else str(type(result)),
            "success": result.get("success", None) if isinstance(result, dict) else None,
        }
    except urllib.error.HTTPError as e:
        return {"status": "error", "http_code": e.code, "reason": str(e.reason)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5: Trade Review
# ═══════════════════════════════════════════════════════════════════════════════

def check_5_trade_review():
    """5.x Trade review — closed trades analysis."""
    trades = load_jsonl(CLOSED_TRADES) + load_jsonl(V6_CLOSED_TRADES)

    if not trades:
        return {"no_trades": True}

    # Per-signal performance
    per_signal = {}
    for t in trades:
        sig = t.get("signal", t.get("signal_name", "unknown"))
        per_signal.setdefault(sig, {"count": 0, "wins": 0, "total_pnl": 0, "pnl_list": []})
        per_signal[sig]["count"] += 1
        pnl = float(t.get("pnl_usd", 0))
        per_signal[sig]["total_pnl"] += pnl
        per_signal[sig]["pnl_list"].append(pnl)
        if pnl > 0:
            per_signal[sig]["wins"] += 1

    for sig, data in per_signal.items():
        data["win_rate"] = data["wins"] / data["count"] * 100 if data["count"] else 0
        data["avg_pnl"] = data["total_pnl"] / data["count"] if data["count"] else 0
        del data["pnl_list"]

    # Overall stats
    total_pnl = sum(float(t.get("pnl_usd", 0)) for t in trades)
    wins = sum(1 for t in trades if float(t.get("pnl_usd", 0)) > 0)
    losses = sum(1 for t in trades if float(t.get("pnl_usd", 0)) < 0)
    breakeven = sum(1 for t in trades if float(t.get("pnl_usd", 0)) == 0)

    # Avg win/loss size
    win_sizes = [float(t.get("pnl_usd", 0)) for t in trades if float(t.get("pnl_usd", 0)) > 0]
    loss_sizes = [float(t.get("pnl_usd", 0)) for t in trades if float(t.get("pnl_usd", 0)) < 0]

    return {
        "total_trades": len(trades),
        "total_pnl_usd": total_pnl,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_pct": wins / len(trades) * 100 if trades else 0,
        "avg_win_usd": sum(win_sizes) / len(win_sizes) if win_sizes else 0,
        "avg_loss_usd": sum(loss_sizes) / len(loss_sizes) if loss_sizes else 0,
        "profit_factor": sum(win_sizes) / abs(sum(loss_sizes)) if loss_sizes else float("inf"),
        "per_signal": per_signal,
        "last_5_trades": trades[-5:] if trades else [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART 6: Regression Checks
# ═══════════════════════════════════════════════════════════════════════════════

def check_6_regression():
    """6.x Regression checks — grep source code to verify critical fixes are present."""
    checks = {}

    def grep_file(filepath, pattern, label):
        """Check if pattern exists in file."""
        try:
            fp = SCANNER_DIR / filepath
            if not fp.exists():
                return {"present": False, "reason": f"file not found: {filepath}"}
            content = fp.read_text()
            found = bool(re.search(pattern, content))
            return {"present": found, "file": filepath, "pattern": pattern}
        except Exception as e:
            return {"present": False, "error": str(e)}

    # 6.1 POST not GET for HL info calls
    checks["hl_uses_post_not_get"] = grep_file(
        "v6/executor.py",
        r'(urllib\.request\.Request|requests\.post|\.post\(|data=.*json\.dumps)',
        "HL API uses POST"
    )

    # 6.2 Atomic/safe write pattern
    checks["safe_json_write"] = grep_file(
        "v6/bus_io.py",
        r'(save_json_atomic|\.tmp|fsync|replace)',
        "Atomic JSON writes"
    )

    # 6.3 Desync detector present
    checks["desync_detector"] = grep_file(
        "v6/executor.py",
        r'(DESYNC|desync)',
        "Desync detection"
    )

    # 6.4 frontendOpenOrders (not openOrders) used for trigger orders
    checks["frontend_open_orders"] = grep_file(
        "tools/fix_stops.py",
        r'frontendOpenOrders',
        "Uses frontendOpenOrders for trigger orders"
    )

    # 6.5 Stop offset check (limit != trigger, has slippage buffer)
    checks["stop_offset_present"] = grep_file(
        "v6/executor.py",
        r'(offset|slippage|0\.98|1\.02|limit.*trigger|trigger.*limit)',
        "Stop order has price offset"
    )

    # 6.6 Place-before-cancel ordering for stops
    checks["place_before_cancel"] = grep_file(
        "v6/executor.py",
        r'(place.*cancel|new.*before.*old|place_stop.*cancel)',
        "Place-before-cancel stop ordering"
    )

    # 6.7 Immune system running
    checks["immune_system_exists"] = grep_file(
        "v6/immune.py",
        r'(class|def main|def run_cycle)',
        "Immune system implementation"
    )

    # 6.8 Risk guard checks capital floor
    checks["risk_guard_capital_floor"] = grep_file(
        "v6/risk_guard.py",
        r'(capital_floor|CAPITAL.*floor|floor.*CAPITAL)',
        "Risk guard capital floor check"
    )

    # 6.9 Equity history recording
    checks["equity_history_recording"] = grep_file(
        "v6/bus_io.py",
        r'equity_history',
        "Equity history recording"
    )
    if not checks["equity_history_recording"]["present"]:
        # Try other files
        for candidate in ["v6/risk_guard.py", "v6/executor.py", "v6/supervisor.py"]:
            result = grep_file(candidate, r'equity_history', "Equity history recording")
            if result["present"]:
                checks["equity_history_recording"] = result
                break

    return checks


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def run_audit():
    """Run all audit checks and return the full report."""
    now = datetime.now(timezone.utc)
    report = {
        "audit_timestamp": now.isoformat(),
        "audit_date": now.strftime("%Y-%m-%d"),
        "scanner_dir": str(SCANNER_DIR),
        "wallet": MAIN_WALLET,
        "errors": [],
    }

    checks = [
        ("1_1_position_sync", check_1_1_position_sync),
        ("1_2_stop_sync", check_1_2_stop_sync),
        ("1_3_portfolio_state", check_1_3_portfolio_state),
        ("1_4_file_integrity", check_1_4_file_integrity),
        ("1_5_process_health", check_1_5_process_health),
        ("2_1_leverage_audit", check_2_1_leverage_audit),
        ("2_2_capital_floor", check_2_2_capital_floor),
        ("2_3_daily_loss", check_2_3_daily_loss),
        ("2_4_position_count", check_2_4_position_count),
        ("2_5_funding_exposure", check_2_5_funding_exposure),
        ("3_1_order_type_distribution", check_3_1_order_type_distribution),
        ("3_2_fee_analysis", check_3_2_fee_analysis),
        ("3_3_slippage", check_3_3_slippage),
        ("3_4_signal_rejection_rate", check_3_4_signal_rejection_rate),
        ("3_5_fill_verification", check_3_5_fill_verification),
        ("4_1_sharpe_gap", check_4_1_sharpe_gap),
        ("4_5_envy_api_health", check_4_5_envy_api_health),
        ("5_trade_review", check_5_trade_review),
        ("6_regression", check_6_regression),
    ]

    for name, fn in checks:
        log(f"Running {name}...")
        result = safe(fn, name)
        report[name] = result

    report["errors"] = ERRORS
    return report


def main():
    parser = argparse.ArgumentParser(description="Weekly self-audit data collection")
    parser.add_argument("--output", type=str, help="Output JSON path (default: scanner/data/audit/YYYY-MM-DD.json)")
    args = parser.parse_args()

    load_env()

    report = run_audit()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = AUDIT_DIR / f"{report['audit_date']}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    log(f"Audit report written to {output_path}")
    log(f"Errors: {len(ERRORS)}")
    print(str(output_path))


if __name__ == "__main__":
    main()

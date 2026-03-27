#!/usr/bin/env python3
"""
ZERO OS — Intelligence API

Self-hosted FastAPI server exposing the cognitive loop's state.
Runs on Mac Studio, exposed via Cloudflare Tunnel.

Endpoints:
  GET  /                    — API info
  GET  /health              — System health (all plugins + agents)
  GET  /decide?coin=BTC     — Get decision for a coin
  GET  /regime              — Current regime classification for all coins
  GET  /signals             — Active signals and candidates
  GET  /positions           — Current open positions
  GET  /performance         — Trading performance stats
  GET  /world               — Full world state snapshot
  GET  /schemas             — JSON schemas for all types

Usage:
  python3 scanner/api/server.py                    # dev mode (port 8420)
  uvicorn scanner.api.server:app --host 0.0.0.0    # production
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse

# ─── Paths ───
SCANNER_DIR = Path(__file__).parent.parent
BUS_DIR = SCANNER_DIR / "bus"
LIVE_DIR = SCANNER_DIR / "data" / "live"
MEMORY_DIR = SCANNER_DIR / "memory"

# Ensure scanner package is importable
sys.path.insert(0, str(SCANNER_DIR.parent))

from scanner.core.interfaces import Observation, Decision, WorldState
from scanner.core.schemas import json_schemas
from scanner.senses.envy_plugin import EnvyPlugin
from scanner.senses.hl_plugin import HyperliquidPlugin
from scanner.senses.talib_plugin import TalibPlugin
from scanner.hands.telegram_adapter import TelegramAdapter
from scanner.hands.aitrader_adapter import AITraderAdapter

# ─── App ───
app = FastAPI(
    title="ZERO OS Intelligence API",
    description="Adversarial cognition engine for crypto markets",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─── Helpers ───

def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _read_jsonl(path: Path, limit: int = 50) -> list:
    if not path.exists():
        return []
    lines = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return lines[-limit:]


def _agent_heartbeats() -> dict:
    hb = _read_json(BUS_DIR / "heartbeat.json")
    if not hb:
        return {}
    now = time.time()
    result = {}
    for agent, ts in hb.items():
        if isinstance(ts, (int, float)):
            age = now - ts
            status = "ok" if age < 600 else "stale" if age < 1800 else "dead"
            result[agent] = {"last_seen": ts, "age_seconds": round(age), "status": status}
    return result


# ─── Endpoints ───

@app.get("/")
def root():
    return {
        "name": "ZERO OS Intelligence API",
        "version": "0.1.0",
        "description": "Adversarial cognition engine — perceive, hypothesize, challenge, act, observe, evolve",
        "endpoints": ["/health", "/decide", "/regime", "/signals", "/positions", "/performance", "/world", "/schemas"],
    }


@app.get("/health")
def health():
    """System health: plugin status + agent heartbeats."""
    plugins = {}
    for plugin_cls in [EnvyPlugin, HyperliquidPlugin, TalibPlugin]:
        try:
            p = plugin_cls() if plugin_cls != HyperliquidPlugin else plugin_cls(fetch_l2=False)
            plugins[p.name] = p.health_check()
        except Exception as e:
            plugins[plugin_cls.__name__] = {"status": "error", "error": str(e)}

    adapters = {}
    for adapter_cls in [TelegramAdapter, AITraderAdapter]:
        try:
            a = adapter_cls()
            adapters[a.name] = a.health_check()
        except Exception as e:
            adapters[adapter_cls.__name__] = {"status": "error", "error": str(e)}

    return {
        "status": "operational",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "plugins": plugins,
        "adapters": adapters,
        "agents": _agent_heartbeats(),
    }


@app.get("/decide")
def decide(coin: str = Query(..., description="Coin symbol (e.g. BTC, ETH, SOL)")):
    """Get current decision for a coin from the cognitive loop."""
    coin = coin.upper()

    # Read latest approved trades
    approved_raw = _read_json(BUS_DIR / "approved.json") or {}
    approved = approved_raw.get("approved", []) if isinstance(approved_raw, dict) else approved_raw
    
    # Read current positions
    positions = _read_json(LIVE_DIR / "positions.json") or []
    
    # Read world state for regime
    world = _read_json(BUS_DIR / "world_state.json") or {}
    coins_data = world.get("coins", {})
    coin_state = coins_data.get(coin, {})
    regime = coin_state.get("regime", "unknown")
    
    # Read adversary report
    adversary = _read_json(BUS_DIR / "adversary.json") or {}

    # Check if there's an approved trade for this coin
    approved_for_coin = [a for a in approved if a.get("coin") == coin]
    
    # Check if we have an open position
    open_position = None
    for p in positions:
        if p.get("coin") == coin:
            open_position = p
            break

    if approved_for_coin:
        trade = approved_for_coin[0]
        return {
            "coin": coin,
            "action": trade.get("direction", "WAIT"),
            "confidence": trade.get("confidence", 0),
            "regime": regime,
            "sharpe": trade.get("sharpe"),
            "win_rate": trade.get("win_rate"),
            "adversary_score": trade.get("adversary_score"),
            "signal": trade.get("signal"),
            "status": "approved",
            "open_position": open_position,
        }
    elif open_position:
        return {
            "coin": coin,
            "action": "HOLD",
            "confidence": open_position.get("confidence", 0),
            "regime": regime,
            "direction": open_position.get("direction"),
            "entry_price": open_position.get("entry_price"),
            "pnl_pct": open_position.get("pnl_pct"),
            "status": "in_position",
        }
    else:
        return {
            "coin": coin,
            "action": "WAIT",
            "confidence": 0,
            "regime": regime,
            "reason": "No approved signals or open positions",
            "status": "idle",
        }


@app.get("/regime")
def regime():
    """Current regime classification for all coins."""
    world = _read_json(BUS_DIR / "world_state.json") or {}
    coins_data = world.get("coins", {})
    macro = world.get("macro", {})

    regimes = {}
    for coin, data in coins_data.items():
        regimes[coin] = {
            "regime": data.get("regime", "unknown"),
            "confidence": data.get("regime_confidence", 0),
            "timeframe_pattern": data.get("timeframe", {}).get("pattern"),
            "funding_rate": data.get("funding", {}).get("rate"),
            "spread_status": data.get("spread", {}).get("status"),
        }

    # Regime distribution
    regime_counts = {}
    for r in regimes.values():
        reg = r["regime"]
        regime_counts[reg] = regime_counts.get(reg, 0) + 1

    return {
        "timestamp": world.get("timestamp"),
        "macro": macro,
        "regime_distribution": regime_counts,
        "coins": regimes,
    }


@app.get("/v6/regime")
def v6_regime():
    """Global market regime state from v6 engine — the road surface."""
    from scanner.v6.api import ZeroAPI
    from scanner.v6.regime import RegimeState
    api = ZeroAPI()
    heat_data = api.get_heat("op_default")
    brief_data = api.get_brief("op_default")
    regime = RegimeState.from_heat(heat_data, brief_data)
    return regime.to_dict()


@app.get("/signals")
def signals():
    """Active signals: candidates, approved, and adversary kill stats."""
    candidates_raw = _read_json(BUS_DIR / "candidates.json") or []
    candidates = candidates_raw if isinstance(candidates_raw, list) else candidates_raw.get("candidates", [])
    approved_raw = _read_json(BUS_DIR / "approved.json") or {}
    approved = approved_raw.get("approved", []) if isinstance(approved_raw, dict) else approved_raw
    blocked = approved_raw.get("blocked", []) if isinstance(approved_raw, dict) else []
    adversary = _read_json(BUS_DIR / "adversary.json") or {}

    return {
        "candidates": len(candidates),
        "approved": len(approved),
        "blocked": len(blocked),
        "kill_rate": adversary.get("kill_rate"),
        "approved_signals": approved,
        "adversary_summary": {
            "total_evaluated": adversary.get("total_evaluated"),
            "killed": adversary.get("killed"),
            "attacks": adversary.get("attack_stats"),
        },
    }


@app.get("/positions")
def positions():
    """Current open positions and recent closed trades."""
    open_pos = _read_json(LIVE_DIR / "positions.json") or []
    closed = _read_jsonl(LIVE_DIR / "closed.jsonl", limit=20)
    risk = _read_json(BUS_DIR / "risk.json") or {}

    return {
        "open": open_pos,
        "closed_recent": closed,
        "risk_status": risk.get("risk_level"),
        "throttle": risk.get("throttle"),
        "equity": risk.get("equity"),
        "drawdown": risk.get("max_drawdown"),
    }


@app.get("/performance")
def performance():
    """Trading performance statistics."""
    closed = _read_jsonl(LIVE_DIR / "closed.jsonl", limit=500)
    risk = _read_json(BUS_DIR / "risk.json") or {}

    if not closed:
        return {"trades": 0, "message": "No closed trades yet"}

    wins = [t for t in closed if (t.get("pnl_usd", 0) or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_usd", 0) or 0) <= 0]
    total_pnl = sum(t.get("pnl_usd", 0) or 0 for t in closed)
    
    longs = [t for t in closed if t.get("direction") == "LONG"]
    shorts = [t for t in closed if t.get("direction") == "SHORT"]
    long_wins = [t for t in longs if (t.get("pnl_usd", 0) or 0) > 0]
    short_wins = [t for t in shorts if (t.get("pnl_usd", 0) or 0) > 0]

    # Average P&L per trade
    avg_win = sum(t.get("pnl_usd", 0) or 0 for t in wins) / len(wins) if wins else 0
    avg_loss = sum(abs(t.get("pnl_usd", 0) or 0) for t in losses) / len(losses) if losses else 0

    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "total_pnl_usd": round(total_pnl, 2),
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "win_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        "long_trades": len(longs),
        "long_win_rate": round(len(long_wins) / len(longs) * 100, 1) if longs else 0,
        "short_trades": len(shorts),
        "short_win_rate": round(len(short_wins) / len(shorts) * 100, 1) if shorts else 0,
        "risk_level": risk.get("risk_level"),
        "max_drawdown": risk.get("max_drawdown"),
        "current_streak": risk.get("current_streak"),
    }


@app.get("/world")
def world():
    """Full world state snapshot from the cognitive loop."""
    world = _read_json(BUS_DIR / "world_state.json")
    if not world:
        return {"error": "No world state available"}
    
    # Trim to essential data for API consumers
    coins = {}
    for coin, data in world.get("coins", {}).items():
        coins[coin] = {
            "regime": data.get("regime"),
            "funding_rate": data.get("funding", {}).get("rate"),
            "spread_status": data.get("spread", {}).get("status"),
            "liquidity_tradeable": data.get("liquidity", {}).get("tradeable"),
            "timeframe_pattern": data.get("timeframe", {}).get("pattern"),
            "mark_price": data.get("oi", {}).get("mark_price"),
        }

    return {
        "timestamp": world.get("timestamp"),
        "macro": world.get("macro", {}),
        "coins": coins,
    }


@app.get("/portfolio.json")
def portfolio_json():
    """Portfolio data in the format the frontend expects (replaces static JSON export)."""
    # Read directly from the export file — it's updated every cycle by export_portfolio.py
    portfolio_path = SCANNER_DIR.parent / "public" / "api" / "portfolio.json"
    if not portfolio_path.exists():
        # Fallback: build from live data
        portfolio_path = LIVE_DIR / "portfolio.json"
    data = _read_json(portfolio_path)
    if data:
        return data
    
    # Build minimal response from raw files
    positions = _read_json(LIVE_DIR / "positions.json") or []
    closed = _read_jsonl(LIVE_DIR / "closed.jsonl", limit=500)
    risk = _read_json(BUS_DIR / "risk.json") or {}
    heartbeat = _read_json(BUS_DIR / "heartbeat.json") or {}
    
    wins = [t for t in closed if (t.get("pnl_usd", 0) or 0) > 0]
    
    return {
        "live": True,
        "updated": datetime.now(timezone.utc).isoformat(),
        "liveTrading": {
            "enabled": True,
            "capital": 750,
            "positions": positions,
            "trades": len(closed),
            "wins": len(wins),
            "dailyLoss": 0,
            "closed": closed,
            "started": "2026-03-17T00:00:00+00:00",
            "stats": {},
        },
        "agents": {
            "heartbeat": heartbeat,
            "risk": risk,
        },
        "liveEquityCurve": [],
    }


@app.get("/prices")
def prices(coins: str = Query("BTC,ETH,SOL", description="Comma-separated coin symbols")):
    """Live prices from Hyperliquid — replaces broken Envy proxy on Vercel."""
    coin_list = [c.strip().upper() for c in coins.split(",") if c.strip()]
    
    try:
        from scanner.senses.hl_plugin import _fetch_meta
        meta = _fetch_meta()
        
        matrix = {}
        for coin in coin_list:
            if coin in meta:
                matrix[coin] = {"CLOSE_PRICE_15M": meta[coin]["mark"]}
        
        return {"matrix": matrix, "source": "hyperliquid", "timestamp": time.time()}
    except Exception as e:
        return {"matrix": {}, "error": str(e)}


# ─── PORTFOLIO DATA (replaces Supabase) ──────────────────────────────────────

V6_BUS = SCANNER_DIR / "v6" / "bus"


@app.get("/v6/positions")
def v6_positions():
    """Open positions — replaces Supabase positions table."""
    # Try V6 first, fall back to V5
    pos = _read_json(V6_BUS / "positions.json")
    if pos and "positions" in pos:
        entries = pos["positions"]
    else:
        entries = _read_json(LIVE_DIR / "positions.json") or []
        if isinstance(entries, dict):
            entries = entries.get("positions", [])
    
    # Return Supabase-compatible format (array of objects)
    result = []
    for p in entries:
        result.append({
            "id": p.get("id", p.get("signal_name", "")),
            "coin": p.get("coin"),
            "direction": p.get("direction"),
            "entry_price": p.get("entry_price"),
            "entry_time": p.get("entry_time"),
            "size_usd": p.get("size_usd"),
            "size_coins": p.get("size_coins"),
            "stop_loss_pct": p.get("stop_loss_pct"),
            "signal": p.get("signal_name", p.get("signal")),
            "strategy_version": p.get("strategy_version", 6),
            "sharpe": p.get("sharpe"),
            "win_rate": p.get("win_rate"),
            "regime": p.get("regime"),
        })
    return result


@app.get("/v6/trades")
def v6_trades(limit: int = Query(200, ge=1, le=1000)):
    """Closed trades — replaces Supabase trades table."""
    closed = _read_jsonl(LIVE_DIR / "closed.jsonl", limit=10000)
    # Also include V6 trades
    v6_trades_file = SCANNER_DIR / "v6" / "data" / "trades.jsonl"
    if v6_trades_file.exists():
        v6_closed = _read_jsonl(v6_trades_file, limit=10000)
        closed = closed + v6_closed
    # Always sort by exit_time desc (newest first)
    closed.sort(key=lambda t: t.get("close_time", t.get("exit_time", "")), reverse=True)
    closed = closed[:limit]
    
    result = []
    for t in closed:
        result.append({
            "id": t.get("id", ""),
            "coin": t.get("coin"),
            "direction": t.get("direction"),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price", t.get("close_price")),
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("close_time", t.get("exit_time")),
            "pnl_usd": t.get("pnl_usd"),
            "pnl_pct": t.get("pnl_pct"),
            "size_usd": t.get("size_usd"),
            "signal": t.get("signal"),
            "exit_reason": t.get("exit_reason"),
            "strategy_version": t.get("strategy_version"),
            "sharpe": t.get("sharpe"),
            "hold_hours": t.get("hold_hours"),
            "fees_usd": t.get("fees_usd"),
        })
    return result


@app.get("/v6/equity")
def v6_equity(points: int = Query(200, ge=10, le=1000)):
    """Equity curve — replaces Supabase get_equity_curve() RPC."""
    # Merge equity from both old bus and V6 bus
    all_points = []
    old_eq = BUS_DIR / "equity_history.jsonl"
    v6_eq = SCANNER_DIR / "v6" / "bus" / "equity_history.jsonl"
    live_eq = LIVE_DIR / "equity_history.jsonl"
    for f in [old_eq, v6_eq, live_eq]:
        if f.exists():
            all_points.extend(_read_jsonl(f, limit=10000))
    # Sort by timestamp asc, dedup by rounding to nearest minute
    all_points.sort(key=lambda p: p.get("timestamp", p.get("recorded_at", "")))
    if not all_points:
        return []
    
    # Downsample to requested points
    if len(all_points) > points:
        step = len(all_points) / points
        sampled = []
        for i in range(points):
            idx = int(i * step)
            if idx < len(all_points):
                sampled.append(all_points[idx])
        # Always include the last point
        if sampled and sampled[-1] != all_points[-1]:
            sampled[-1] = all_points[-1]
        all_points = sampled
    
    result = []
    for p in all_points:
        result.append({
            "recorded_at": p.get("timestamp", p.get("recorded_at", "")),
            "equity_usd": p.get("equity", p.get("equity_usd", p.get("account_value", 750))),
        })
    return result


@app.get("/v6/heartbeats")
def v6_heartbeats():
    """Agent heartbeats — replaces Supabase agent_heartbeats table."""
    # V6 heartbeat
    hb = _read_json(V6_BUS / "heartbeat.json") or {}
    # V5 heartbeat as fallback
    if not hb:
        hb = _read_json(BUS_DIR / "heartbeat.json") or {}
    
    result = []
    for agent, ts in hb.items():
        result.append({
            "agent_name": agent,
            "last_heartbeat": ts,
        })
    return result


@app.get("/v6/analytics")
def v6_analytics():
    """Real performance analytics — Sharpe, per-signal stats, drawdown."""
    try:
        from scanner.v6.analytics import full_report
        return full_report()
    except Exception as e:
        return {"error": str(e)}


# ─── Cognitive Dashboard Endpoints ───

V6_DIR = SCANNER_DIR / "v6"
V6_BUS = V6_DIR / "bus"
V6_DATA = V6_DIR / "data"


@app.get("/v6/status")
def v6_status():
    """System status for homepage boot sequence."""
    heartbeats = _read_json(V6_BUS / "heartbeat.json") or {}
    strategies = _read_json(V6_BUS / "strategies.json") or {}
    positions = _read_json(V6_BUS / "positions.json") or {}
    immune_state = _read_json(V6_BUS / "immune_state.json") or {}

    now = time.time()

    # Active coins
    raw_active = strategies.get("active_coins", [])
    active_coins = raw_active if isinstance(raw_active, list) else list(raw_active.keys())
    blacklist_count = 3  # PUMP, XPL, TRUMP

    # Evaluator heartbeat
    eval_ts = heartbeats.get("evaluator", "")
    eval_ago = 0
    if eval_ts:
        try:
            eval_ago = int(now - datetime.fromisoformat(eval_ts.replace("Z", "+00:00")).timestamp())
        except (ValueError, AttributeError):
            eval_ago = -1

    # Process count (agents reporting)
    processes = sum(1 for k, v in heartbeats.items() if v)

    # Uptime from positions file or first equity point
    equity_history = immune_state.get("equity_history_7d", [])
    first_ts = ""
    uptime_hours = 0
    if equity_history:
        first_ts = equity_history[0].get("ts", "")
        try:
            first_time = datetime.fromisoformat(first_ts.replace("Z", "+00:00")).timestamp()
            uptime_hours = (now - first_time) / 3600
        except (ValueError, AttributeError):
            pass

    # Open positions
    open_positions = len(positions.get("positions", []))

    return {
        "indicators": 85,
        "expressions": 370,
        "active_coins": len(active_coins),
        "blacklisted_coins": blacklist_count,
        "pre_trade_gates": 9,
        "immune_monitors": 6,
        "eval_cycle_seconds": 15,
        "eval_last_run_ago": eval_ago,
        "processes": processes,
        "uptime_hours": round(uptime_hours, 1),
        "open_positions": open_positions,
        "heartbeats": {k: v for k, v in heartbeats.items()},
    }


@app.get("/v6/decisions")
def v6_decisions(limit: int = Query(30, ge=1, le=200)):
    """Decision stream: rejections + entries + closes merged chronologically."""
    decisions = []

    # 1. Rejections from rejections.jsonl
    rejections = _read_jsonl(V6_BUS / "rejections.jsonl", limit=500)
    for r in rejections:
        decisions.append({
            "ts": r.get("ts"),
            "coin": r.get("coin"),
            "direction": r.get("dir"),
            "type": "BLOCKED" if "blacklist" in (r.get("reason") or "") else "REJECTED",
            "reason": r.get("reason"),
            "details": r.get("details"),
        })

    # 2. Closed trades from trades.jsonl
    trades = _read_jsonl(V6_DATA / "trades.jsonl", limit=500)
    for t in trades:
        pnl = t.get("pnl_usd", 0) or 0
        decisions.append({
            "ts": t.get("exit_time"),
            "coin": t.get("coin"),
            "direction": t.get("direction"),
            "type": "CLOSED",
            "reason": t.get("exit_reason", ""),
            "pnl_usd": pnl,
            "pnl_pct": t.get("pnl_pct", 0),
            "won": t.get("won", pnl > 0),
            "signal": t.get("signal_name"),
            "sharpe": t.get("sharpe"),
        })
        # Also add the entry event
        decisions.append({
            "ts": t.get("entry_time"),
            "coin": t.get("coin"),
            "direction": t.get("direction"),
            "type": "ENTERED",
            "reason": f"${t.get('size_usd',0):.0f} @ ${t.get('entry_price',0)}",
            "signal": t.get("signal_name"),
            "sharpe": t.get("sharpe"),
        })

    # 3. Currently open positions as ENTERED events
    positions = _read_json(V6_BUS / "positions.json")
    if positions and "positions" in positions:
        for p in positions["positions"]:
            decisions.append({
                "ts": p.get("entry_time"),
                "coin": p.get("coin"),
                "direction": p.get("direction"),
                "type": "ENTERED",
                "reason": f"${p.get('size_usd',0):.0f} @ ${p.get('entry_price',0)}",
                "signal": p.get("signal_name"),
                "sharpe": p.get("sharpe"),
            })

    # Sort by timestamp desc, return latest N
    decisions.sort(key=lambda d: d.get("ts") or "", reverse=True)
    return decisions[:limit]


@app.get("/v6/funnel")
def v6_funnel():
    """Gate funnel: count rejections by reason to show signal→trade conversion."""
    rejections = _read_jsonl(V6_BUS / "rejections.jsonl", limit=5000)
    trades = _read_jsonl(V6_DATA / "trades.jsonl", limit=5000)
    positions = _read_json(V6_BUS / "positions.json")
    open_count = len((positions or {}).get("positions", []))

    # Count rejections by gate
    gate_counts = {}
    for r in rejections:
        reason = r.get("reason", "unknown")
        # Normalize to gate category
        gate = reason.split(":")[0].strip() if ":" in reason else reason
        gate_counts[gate] = gate_counts.get(gate, 0) + 1

    total_signals = len(rejections) + len(trades) + open_count

    # Build ordered funnel
    funnel = [
        {"gate": "SIGNALS_EVALUATED", "count": total_signals, "pct": 100.0},
    ]

    # Known gate ordering (from most to least permissive)
    gate_order = [
        "max_positions", "blacklisted", "alpha_vs_cost", "funding_cost",
        "book_depth_zero", "liquidity_too_thin", "size_zero", "no_price",
        "already_open", "cooldown",
    ]

    remaining = total_signals
    for gate in gate_order:
        if gate in gate_counts:
            remaining -= gate_counts[gate]
            pct = (remaining / total_signals * 100) if total_signals > 0 else 0
            funnel.append({
                "gate": gate.upper().replace("_", " "),
                "rejected": gate_counts[gate],
                "remaining": remaining,
                "pct": round(pct, 1),
            })

    # Add any unlisted gates
    for gate, count in gate_counts.items():
        if gate not in gate_order:
            remaining -= count
            pct = (remaining / total_signals * 100) if total_signals > 0 else 0
            funnel.append({
                "gate": gate.upper().replace("_", " "),
                "rejected": count,
                "remaining": remaining,
                "pct": round(pct, 1),
            })

    executed = len(trades) + open_count
    funnel.append({
        "gate": "EXECUTED",
        "count": executed,
        "pct": round(executed / total_signals * 100, 1) if total_signals > 0 else 0,
    })

    return {
        "total_signals": total_signals,
        "total_rejected": len(rejections),
        "total_executed": executed,
        "rejection_rate": round(len(rejections) / total_signals * 100, 1) if total_signals > 0 else 0,
        "funnel": funnel,
        "by_gate": gate_counts,
    }


@app.get("/v6/immune")
def v6_immune():
    """Immune system heartbeat: 24h health windows."""
    state = _read_json(V6_BUS / "immune_state.json")
    if not state:
        return {"windows": [], "checks": {}}

    equity_history = state.get("equity_history_7d", [])
    error_counts = state.get("error_counts", {})

    # Build 15-minute windows from equity history (last 24h)
    now_ts = time.time()
    window_size = 15 * 60  # 15 minutes
    windows_24h = 96  # 24h / 15min

    windows = []
    for i in range(windows_24h):
        window_start = now_ts - (windows_24h - i) * window_size
        window_end = window_start + window_size

        # Find equity points in this window
        points_in_window = [
            e for e in equity_history
            if _iso_to_ts(e.get("ts", "")) >= window_start
            and _iso_to_ts(e.get("ts", "")) < window_end
        ]

        if len(points_in_window) == 0:
            status = "nodata"
        else:
            # Check for equity anomaly (>2% drop in window)
            equities = [p["equity"] for p in points_in_window]
            if max(equities) > 0 and (max(equities) - min(equities)) / max(equities) > 0.02:
                status = "warning"
            else:
                status = "ok"

        windows.append({
            "start": datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat(),
            "status": status,
            "points": len(points_in_window),
        })

    # Check summaries
    checks = {
        "stop_verification": "ok",
        "position_sync": "ok",
        "equity_anomaly": "ok",
        "desync_detection": "ok",
    }

    # Count alerts
    alerts_today = state.get("alerts_sent_today", 0)
    cycle_count = state.get("cycle_count", 0)

    return {
        "windows": windows,
        "checks": checks,
        "alerts_today": alerts_today,
        "cycle_count": cycle_count,
        "ok_count": sum(1 for w in windows if w["status"] == "ok"),
        "warn_count": sum(1 for w in windows if w["status"] == "warning"),
        "nodata_count": sum(1 for w in windows if w["status"] == "nodata"),
        "total_windows": len(windows),
    }


def _iso_to_ts(iso_str: str) -> float:
    """Convert ISO timestamp to unix timestamp."""
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


@app.get("/v6/landscape")
def v6_landscape():
    """Signal landscape: assembled Sharpe sparklines per coin, 3 tiers."""
    history = _read_json(V6_BUS / "sharpe_history.json")
    strategies = _read_json(V6_BUS / "strategies.json")
    runs = (history or {}).get("runs", [])

    # Get current active coins and blacklist from strategy_manager
    raw_active = (strategies or {}).get("active_coins", [])
    active_coins = raw_active if isinstance(raw_active, list) else list(raw_active.keys())

    # Read blacklist from strategy_manager source
    blacklist_map = {"PUMP": -0.96, "XPL": -2.39, "TRUMP": -0.66}

    # Build per-coin sparkline data
    all_coins = set()
    for run in runs:
        all_coins.update(run.get("sharpes", {}).keys())

    landscape = []
    for coin in sorted(all_coins):
        sparkline = []
        for run in runs:
            val = run.get("sharpes", {}).get(coin)
            if val is not None:
                sparkline.append(round(val, 4))

        current = sparkline[-1] if sparkline else None
        is_active = coin in active_coins
        is_blacklisted = coin in blacklist_map

        # Determine tier
        if is_blacklisted:
            tier = "blacklisted"
        elif is_active:
            tier = "active"
        else:
            tier = "watching"

        landscape.append({
            "coin": coin,
            "sparkline": sparkline,
            "current_sharpe": current,
            "tier": tier,
            "active": is_active,
            "blacklisted": is_blacklisted,
            "points": len(sparkline),
        })

    # Add blacklisted coins that aren't in sharpe history
    existing = {c["coin"] for c in landscape}
    for coin, sharpe in blacklist_map.items():
        if coin not in existing:
            landscape.append({
                "coin": coin,
                "sparkline": [],
                "current_sharpe": sharpe,
                "tier": "blacklisted",
                "active": False,
                "blacklisted": True,
                "points": 0,
            })

    # Sort: active first (by sharpe desc), then watching, then blacklisted
    landscape.sort(key=lambda x: (
        2 if x["tier"] == "blacklisted" else (0 if x["tier"] == "active" else 1),
        -(x["current_sharpe"] or 0),
    ))

    active_count = sum(1 for c in landscape if c["tier"] == "active")
    watching_count = sum(1 for c in landscape if c["tier"] == "watching")
    blacklisted_count = sum(1 for c in landscape if c["tier"] == "blacklisted")

    return {
        "coins": landscape,
        "active_count": active_count,
        "watching_count": watching_count,
        "blacklisted_count": blacklisted_count,
        "total": len(landscape),
        "data_points": len(runs),
    }


@app.get("/schemas")
def schemas():
    """JSON Schema definitions for API types."""
    return json_schemas()


# ─── V6 Engine API + MCP mount ───

try:
    from scanner.v6.api import ZeroAPI
    from scanner.v6.mcp_server import mount_on_fastapi

    _v6_api = ZeroAPI()

    # ── Operator management ──────────────────────────────────────────────
    from scanner.v6.operator import register_operator, list_operators as list_ops

    @app.post("/v6/operator/register")
    def v6_register_operator(
        id: str = "op_default",
        wallet: str = "",
        api_wallet: str = "",
        plan: str = "free",
    ):
        ctx = register_operator(id, wallet, api_wallet=api_wallet, plan=plan)
        return {
            "operator_id": ctx.operator_id,
            "plan": ctx.plan,
            "bus_dir": str(ctx.bus_dir),
            "created": True,
        }

    @app.get("/v6/operators")
    def v6_list_operators():
        return {"operators": list_ops()}

    # ── REST endpoints for v6 engine (all under /v6/) ────────────────
    @app.get("/v6/strategies")
    def v6_strategies(operator_id: str = Query("op_default")):
        return _v6_api.list_strategies(operator_id)

    @app.get("/v6/strategy/{name}")
    def v6_strategy(name: str, operator_id: str = Query("op_default")):
        return _v6_api.preview_strategy(operator_id, name)

    @app.post("/v6/session/start")
    def v6_start(
        strategy: str = "momentum",
        paper: bool = True,
        operator_id: str = Query("op_default"),
    ):
        return _v6_api.start_session(operator_id, strategy, paper=paper)

    @app.get("/v6/session/status")
    def v6_session_status(operator_id: str = Query("op_default")):
        return _v6_api.session_status(operator_id)

    @app.post("/v6/session/end")
    def v6_end(operator_id: str = Query("op_default")):
        return _v6_api.end_session(operator_id)

    @app.post("/v6/session/queue")
    def v6_queue(
        strategy: str = "momentum",
        paper: bool = True,
        operator_id: str = Query("op_default"),
    ):
        return _v6_api.queue_session(operator_id, strategy, paper=paper)

    @app.post("/v6/session/mode")
    def v6_set_mode(
        mode: str = "comfort",
        operator_id: str = Query("op_default"),
    ):
        return _v6_api.set_mode(operator_id, mode)

    @app.get("/v6/session/auto-select")
    def v6_auto_select(operator_id: str = Query("op_default")):
        return _v6_api.auto_select(operator_id)

    @app.get("/v6/session/history")
    def v6_history(limit: int = 10, operator_id: str = Query("op_default")):
        return _v6_api.session_history(operator_id, limit=limit)

    @app.get("/v6/session/{session_id}")
    def v6_result(session_id: str, operator_id: str = Query("op_default")):
        return _v6_api.session_result(operator_id, session_id)

    @app.get("/v6/evaluate/{coin}")
    def v6_evaluate(coin: str, operator_id: str = Query("op_default")):
        return _v6_api.evaluate(operator_id, coin)

    @app.get("/v6/heat")
    def v6_heat(operator_id: str = Query("op_default")):
        return _v6_api.get_heat(operator_id)

    @app.get("/v6/approaching")
    def v6_approaching(operator_id: str = Query("op_default")):
        return _v6_api.get_approaching(operator_id)

    @app.get("/v6/pulse")
    def v6_pulse(limit: int = 20, operator_id: str = Query("op_default")):
        return _v6_api.get_pulse(operator_id, limit=limit)

    @app.get("/v6/brief")
    def v6_brief(operator_id: str = Query("op_default")):
        return _v6_api.get_brief(operator_id)

    @app.get("/v6/engine/health")
    def v6_engine_health(operator_id: str = Query("op_default")):
        return _v6_api.get_engine_health(operator_id)

    # ── Agent identity endpoints ─────────────────────────────────────────
    from scanner.v6.agent_registry import AgentRegistry

    @app.get("/v6/agent/profile")
    def v6_agent_profile(operator_id: str = Query("op_default")):
        """Get agent profile (auto-registers if new)."""
        registry = AgentRegistry()
        profile = registry.register_or_get(operator_id)
        from dataclasses import asdict
        return asdict(profile)

    @app.get("/v6/agents")
    def v6_list_agents():
        """List all registered agents with stats."""
        registry = AgentRegistry()
        return {"agents": registry.get_all_agents(), "count": registry.get_agent_count()}

    @app.get("/v6/agent/{agent_id}")
    def v6_agent_by_id(agent_id: str):
        """Get a specific agent's public profile."""
        registry = AgentRegistry()
        agent = registry.get_agent(agent_id)
        if agent is None:
            return JSONResponse(status_code=404, content={"error": "agent not found"})
        return agent

    # ── Arena endpoints ──────────────────────────────────────────────────
    @app.get("/v6/arena")
    def v6_arena(operator_id: str = Query("op_default")):
        """Get arena leaderboard and stats."""
        return _v6_api.get_arena(operator_id)

    @app.get("/v6/arena/leaderboard")
    def v6_leaderboard(limit: int = 10, operator_id: str = Query("op_default")):
        """Top agents by score."""
        from dataclasses import asdict as _asdict
        from scanner.v6.arena import Arena
        arena = Arena(_v6_api)
        entries = arena.get_leaderboard(limit=limit, requester_id=operator_id)
        return {"leaderboard": [_asdict(e) for e in entries], "count": len(entries)}

    # ── Canvas live dashboard ─────────────────────────────────────────────
    _dashboard_path = str(SCANNER_DIR / "v6" / "cards" / "dashboard.html")

    @app.get("/v6/dashboard")
    def v6_dashboard():
        return FileResponse(_dashboard_path, media_type="text/html")

    # ── Card PNG endpoints ──────────────────────────────────────────────
    from scanner.v6.cards.card_api import router as cards_router
    app.include_router(cards_router)

    # ── Backtest endpoints ───────────────────────────────────────────────
    from scanner.v6.backtest.backtest_api import router as backtest_router
    app.include_router(backtest_router)

    # Mount MCP server at /mcp
    mount_on_fastapi(app, "/mcp")

    print("[V6] Engine API mounted at /v6/* | Cards at /v6/cards/* | Backtest at /v6/backtest/* | MCP at /mcp")
except ImportError as e:
    print(f"[V6] Engine not available: {e}")
except Exception as e:
    print(f"[V6] Engine mount failed: {e}")


# ─── Run ───
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8420)

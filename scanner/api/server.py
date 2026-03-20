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


@app.get("/schemas")
def schemas():
    """JSON Schema definitions for API types."""
    return json_schemas()


# ─── Run ───
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8420)

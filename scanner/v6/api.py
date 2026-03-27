#!/usr/bin/env python3
"""
ZERO Engine API — the ONE function layer.

Every external access path (MCP, REST, Telegram Bot) calls these functions.
Every function takes operator_id as first parameter (V1: ignored, multi-operator ready).
No business logic here — just call engine methods and format responses.

Usage:
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    result = api.evaluate("op_123", "BTC")
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scanner.v6.config import BUS_DIR
from scanner.v6.monitor import Monitor
from scanner.v6.session import SessionManager
from scanner.v6.operator import (
    OperatorContext,
    resolve_operator,
    plan_allows_strategy,
    get_allowed_strategies,
)
from scanner.v6.strategy_loader import (
    load_strategy,
    load_all_strategies,
    list_strategies,
    StrategyConfig,
    _REGIME_MAP,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ZeroAPI:
    """
    Unified API for the ZERO trading engine.

    Every method takes operator_id as first parameter.
    operator_id resolves to OperatorContext with isolated bus_dir.
    """

    def __init__(self, bus_dir: Path | None = None):
        self._default_bus_dir = bus_dir or BUS_DIR
        # Cache: operator_id → (Monitor, SessionManager)
        self._instances: dict[str, tuple[Monitor, SessionManager]] = {}
        # Shared monitor for market data (evaluations are stateless)
        self._shared_monitor: Monitor | None = None

    # ── HELPERS ──────────────────────────────────────────────────────────

    def _resolve(self, operator_id: str) -> OperatorContext:
        """Resolve operator_id to OperatorContext."""
        return resolve_operator(operator_id)

    def _get_instances(self, operator_id: str) -> tuple[Monitor, SessionManager]:
        """Get or create Monitor + SessionManager for this operator."""
        ctx = self._resolve(operator_id)
        cache_key = str(ctx.bus_dir)  # cache by bus_dir, not operator_id
        if cache_key not in self._instances:
            ctx.ensure_bus_dir()
            monitor = Monitor(bus_dir=ctx.bus_dir)
            session_mgr = SessionManager(bus_dir=ctx.bus_dir)
            self._instances[cache_key] = (monitor, session_mgr)
        return self._instances[cache_key]

    def _get_monitor(self, operator_id: str) -> Monitor:
        """Get or create a Monitor for this operator's bus_dir."""
        return self._get_instances(operator_id)[0]

    def _get_session_mgr(self, operator_id: str) -> SessionManager:
        """Get or create a SessionManager for this operator's bus_dir."""
        return self._get_instances(operator_id)[1]

    # ── SESSION (8 tools) ────────────────────────────────────────────────

    def list_strategies(self, operator_id: str) -> dict:
        """List all 9 strategies with tier and unlock requirements."""
        strategies = load_all_strategies()
        items = []
        for name, cfg in strategies.items():
            items.append({
                "name": cfg.name,
                "display": cfg.display,
                "tier": cfg.tier,
                "unlock_score": cfg.unlock.score_minimum,
                "risk_level": _risk_level(cfg),
                "max_positions": cfg.risk.max_positions,
                "position_size_pct": cfg.risk.position_size_pct,
                "stop_loss_pct": cfg.risk.stop_loss_pct,
                "consensus_threshold": cfg.evaluation.consensus_threshold,
                "directions": cfg.evaluation.directions,
                "regimes": cfg.evaluation.min_regime,
            })
        items.sort(key=lambda x: x["unlock_score"])
        return {"strategies": items, "count": len(items)}

    def preview_strategy(self, operator_id: str, strategy_name: str) -> dict:
        """Preview a strategy: details, risk math, current market match."""
        try:
            cfg = load_strategy(strategy_name)
        except (FileNotFoundError, ValueError) as e:
            return {"error": f"Strategy not found: {strategy_name}", "available": list_strategies()}

        max_exposure = cfg.risk.max_positions * cfg.risk.position_size_pct + cfg.risk.reserve_pct
        max_drawdown = cfg.risk.max_positions * cfg.risk.stop_loss_pct

        return {
            "name": cfg.name,
            "display": cfg.display,
            "tier": cfg.tier,
            "risk": {
                "max_positions": cfg.risk.max_positions,
                "position_size_pct": cfg.risk.position_size_pct,
                "stop_loss_pct": cfg.risk.stop_loss_pct,
                "reserve_pct": cfg.risk.reserve_pct,
                "max_exposure_pct": round(max_exposure, 1),
                "max_drawdown_pct": round(max_drawdown, 1),
                "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
                "max_hold_hours": cfg.risk.max_hold_hours,
            },
            "evaluation": {
                "consensus_threshold": cfg.evaluation.consensus_threshold,
                "directions": cfg.evaluation.directions,
                "regimes": cfg.evaluation.min_regime,
                "scope": cfg.evaluation.scope,
            },
            "session": {
                "duration_hours": cfg.session.duration_hours,
            },
            "unlock": {
                "score_minimum": cfg.unlock.score_minimum,
            },
        }

    def start_session(self, operator_id: str, strategy: str, paper: bool = True) -> dict:
        """Start a trading session. Returns session ID and confirmation."""
        # Plan gating
        ctx = self._resolve(operator_id)
        if not plan_allows_strategy(ctx.plan, strategy):
            allowed = sorted(get_allowed_strategies(ctx.plan))
            return {
                "error": f"Strategy '{strategy}' requires a higher plan. Your plan: {ctx.plan}",
                "allowed_strategies": allowed,
                "plan": ctx.plan,
            }

        sm = self._get_session_mgr(operator_id)

        # Check if a session is already active
        active = sm.active_session
        if active is not None:
            return {
                "error": "Session already active",
                "active_session": {
                    "session_id": active.id,
                    "strategy": active.strategy_config.display,
                    "state": active.state,
                },
            }

        try:
            session = sm.start_session(strategy_name=strategy, paper=paper)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            return {"error": str(e)}

        return {
            "session_id": session.id,
            "strategy": session.strategy_config.name,
            "strategy_display": session.strategy_config.display,
            "duration_hours": session.strategy_config.session.duration_hours,
            "paper": session.paper,
            "status": "active",
            "ends_at": session.ends_at.isoformat() if session.ends_at else None,
        }

    def session_status(self, operator_id: str) -> dict:
        """Get active session state + P&L."""
        sm = self._get_session_mgr(operator_id)
        status = sm.get_status()
        if not status["active"]:
            return {"active": False, "session": None}

        session_data = status["session"]
        # Add position data
        positions = self._read_positions(operator_id)
        session_data["open_positions"] = len(positions)
        session_data["positions"] = positions
        return {"active": True, "session": session_data}

    def end_session(self, operator_id: str) -> dict:
        """End the active session early. Returns result card."""
        sm = self._get_session_mgr(operator_id)
        session = sm.active_session
        if session is None:
            return {"error": "No active session"}

        try:
            result = sm.end_session_early(session)
            return {
                "session_id": result.session_id,
                "strategy": result.strategy,
                "strategy_display": result.strategy_display,
                "trade_count": result.trade_count,
                "wins": result.wins,
                "losses": result.losses,
                "total_pnl_usd": round(result.total_pnl_usd, 2),
                "total_pnl_pct": round(result.total_pnl_pct, 2),
                "duration": str(result.duration_actual),
                "paper": result.paper,
                "narrative": result.narrative_text,
            }
        except Exception as e:
            return {"error": str(e)}

    def queue_session(self, operator_id: str, strategy: str, paper: bool = True) -> dict:
        """Queue a session to start after current one ends."""
        sm = self._get_session_mgr(operator_id)
        result = sm.queue_session(strategy, paper=paper)
        return {"queued": True, **result}

    def session_history(self, operator_id: str, limit: int = 10) -> dict:
        """Get past session results."""
        sm = self._get_session_mgr(operator_id)
        history = sm.get_history(limit=limit)
        return {"sessions": history, "count": len(history)}

    def session_result(self, operator_id: str, session_id: str) -> dict:
        """Get full result card for a specific session."""
        sm = self._get_session_mgr(operator_id)
        result = sm.get_result(session_id)
        if result is None:
            return {"error": f"Session {session_id} not found"}
        return result

    # ── INTELLIGENCE (5 tools) ───────────────────────────────────────────

    def evaluate(self, operator_id: str, coin: str) -> dict:
        """Evaluate a coin through 7 intelligence layers."""
        monitor = self._get_monitor(operator_id)
        try:
            monitor.cache.refresh()
            result = monitor.evaluate_coin(coin.upper())
            return {
                "coin": result.coin,
                "price": result.price,
                "consensus": result.consensus,
                "conviction": round(result.conviction, 4),
                "direction": result.direction,
                "regime": result.regime,
                "layers": [
                    {
                        "layer": lr.layer,
                        "passed": lr.passed,
                        "value": lr.value if _is_serializable(lr.value) else str(lr.value),
                        "detail": lr.detail,
                    }
                    for lr in result.layers
                ],
                "data_fresh": result.data_complete,
                "timestamp": result.timestamp,
            }
        except Exception as e:
            return {"error": str(e), "coin": coin.upper()}

    def get_heat(self, operator_id: str) -> dict:
        """Get all coins sorted by conviction (heat map)."""
        monitor = self._get_monitor(operator_id)
        results = monitor.get_heat_state()
        return {
            "coins": results,
            "count": len(results),
            "timestamp": _now_iso(),
        }

    def get_approaching(self, operator_id: str) -> dict:
        """Get coins near consensus threshold with bottleneck analysis."""
        monitor = self._get_monitor(operator_id)
        approaching = monitor.get_approaching()
        return {
            "approaching": approaching,
            "count": len(approaching),
            "timestamp": _now_iso(),
        }

    def get_pulse(self, operator_id: str, limit: int = 20) -> dict:
        """Get recent market events and decisions."""
        monitor = self._get_monitor(operator_id)
        events = monitor.get_pulse(limit=limit)
        return {
            "events": events,
            "count": len(events),
            "timestamp": _now_iso(),
        }

    def get_brief(self, operator_id: str) -> dict:
        """Generate overnight briefing."""
        monitor = self._get_monitor(operator_id)
        return monitor.get_brief()

    # ── PROGRESSION (4 tools) — Phase 4 stubs ────────────────────────────

    def get_score(self, operator_id: str) -> dict:
        """Get operator score. Phase 4 — returns placeholder."""
        return {
            "score": 0.0,
            "class": "unranked",
            "tier": "apprentice",
            "dimensions": {
                "performance": 0.0,
                "discipline": 0.0,
                "protection": 0.0,
                "consistency": 0.0,
                "adaptation": 0.0,
            },
            "phase": "coming in Phase 4",
        }

    def get_achievements(self, operator_id: str) -> dict:
        """Get operator achievements. Phase 4 — returns placeholder."""
        return {"achievements": [], "count": 0, "phase": "coming in Phase 4"}

    def get_streak(self, operator_id: str) -> dict:
        """Get operator streaks. Phase 4 — returns placeholder."""
        return {
            "daily_streak": 0,
            "session_streak": 0,
            "immune_uptime_pct": 100.0,
            "phase": "coming in Phase 4",
        }

    def get_reputation(self, operator_id: str) -> dict:
        """Get operator reputation. Phase 4 — returns placeholder."""
        return {
            "stars": 0,
            "dimensions": {
                "accuracy": 0.0,
                "discipline": 0.0,
                "longevity": 0.0,
                "diversity": 0.0,
                "contribution": 0.0,
            },
            "phase": "coming in Phase 4",
        }

    # ── COMPETITION (3 tools) — Phase 4 stubs ────────────────────────────

    def get_arena(self, operator_id: str) -> dict:
        """Get arena leaderboard. Phase 4 — returns placeholder."""
        return {"leaderboard": [], "season": None, "phase": "coming in Phase 4"}

    def get_rivalry(self, operator_id: str) -> dict:
        """Get rivalry stats. Phase 4 — returns placeholder."""
        return {"rival": None, "h2h": None, "phase": "coming in Phase 4"}

    def get_chain(self, operator_id: str) -> dict:
        """Get active chain progress. Phase 4 — returns placeholder."""
        return {"active_chain": None, "longest": 0, "phase": "coming in Phase 4"}

    # ── ACCOUNT (2 tools) — Phase 4 stubs ────────────────────────────────

    def get_credits(self, operator_id: str) -> dict:
        """Get operator credits. Phase 4 — returns placeholder."""
        return {"balance": 0, "history": [], "phase": "coming in Phase 4"}

    def get_energy(self, operator_id: str) -> dict:
        """Get operator energy. Phase 4 — returns placeholder."""
        return {"energy_pct": 100.0, "recovery_at": None, "phase": "coming in Phase 4"}

    # ── ENGINE HEALTH (1 bonus tool) ─────────────────────────────────────

    def get_engine_health(self, operator_id: str) -> dict:
        """Get engine health metrics."""
        ctx = self._resolve(operator_id)
        monitor = self._get_monitor(operator_id)
        metrics = monitor.last_cycle_metrics
        # Read heartbeat
        hb_file = ctx.bus_dir / "heartbeat.json"
        heartbeat = {}
        if hb_file.exists():
            try:
                heartbeat = json.loads(hb_file.read_text())
            except Exception:
                pass
        # Read immune state
        immune_file = ctx.bus_dir / "immune_state.json"
        immune = {}
        if immune_file.exists():
            try:
                immune = json.loads(immune_file.read_text())
            except Exception:
                pass
        return {
            "status": "operational",
            "last_cycle": metrics.to_dict() if metrics else None,
            "heartbeat": heartbeat,
            "immune": {
                "active": bool(immune),
                "last_check": immune.get("last_check"),
                "stops_verified": immune.get("stops_verified", 0),
            },
            "timestamp": _now_iso(),
        }

    # ── PRIVATE HELPERS ──────────────────────────────────────────────────

    def _read_positions(self, operator_id: str = "op_default") -> list[dict]:
        """Read current positions from bus."""
        ctx = self._resolve(operator_id)
        positions_file = ctx.bus_dir / "positions.json"
        if not positions_file.exists():
            return []
        try:
            data = json.loads(positions_file.read_text())
            if isinstance(data, dict):
                return data.get("positions", [])
            return data if isinstance(data, list) else []
        except Exception:
            return []


# ── Helpers ──────────────────────────────────────────────────────────────────

def _risk_level(cfg: StrategyConfig) -> str:
    """Classify strategy risk from params."""
    max_dd = cfg.risk.max_positions * cfg.risk.stop_loss_pct
    if max_dd == 0:
        return "none"
    if max_dd <= 8:
        return "conservative"
    if max_dd <= 15:
        return "moderate"
    if max_dd <= 24:
        return "aggressive"
    return "extreme"


def _is_serializable(val) -> bool:
    """Check if a value is JSON-serializable."""
    try:
        json.dumps(val)
        return True
    except (TypeError, ValueError):
        return False

#!/usr/bin/env python3
"""
ZERO MCP Server — thin adapter layer.

23 tools mapping MCP protocol to ZeroAPI functions.
The MCP server is a DOORWAY, not a room. No business logic here.

Transport: Streamable HTTP (mounted on existing FastAPI at /mcp)
Auth: Bearer token (JWT in V2, static token in V1)

Usage:
    # As standalone stdio server (for local agents)
    python -m scanner.v6.mcp_server

    # Mounted on FastAPI (for remote agents via getzero.dev/mcp)
    from scanner.v6.mcp_server import mcp
    # See mount_on_fastapi() below
"""

from __future__ import annotations

import os
from typing import Optional

from fastmcp import FastMCP

from scanner.v6.api import ZeroAPI
from scanner.v6.auth import check_tool_tier, TOOL_TIERS

# ── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    "zero",
    instructions=(
        "ZERO trading engine. 7 intelligence layers evaluate 40+ markets. "
        "97% of setups rejected. The 3% that pass become trades. "
        "Use zero_evaluate to check a coin. Use zero_start_session to deploy."
    ),
)

# Singleton API instance
_api = ZeroAPI()

# V1: single operator, use static token or default
_DEFAULT_OPERATOR = "op_default"
_DEFAULT_PLAN = "scale"  # V1: full access


def _get_operator_id() -> str:
    """V1: returns default. V2: extract from MCP auth context."""
    return _DEFAULT_OPERATOR


def _get_plan() -> str:
    """V1: returns default plan. V2: extract from MCP auth context."""
    return _DEFAULT_PLAN


def _gate(tool_name: str) -> dict | None:
    """Check tier gating for a tool. Returns error dict if blocked, None if allowed."""
    return check_tool_tier(tool_name, _get_plan())


# ── SESSION TOOLS (8) ────────────────────────────────────────────────────────

@mcp.tool()
def zero_list_strategies() -> dict:
    """List all 9 trading strategies with tier, unlock requirements, and risk parameters."""
    return _api.list_strategies(_get_operator_id())


@mcp.tool()
def zero_preview_strategy(strategy: str) -> dict:
    """Preview a strategy: full risk math, evaluation criteria, and session parameters."""
    return _api.preview_strategy(_get_operator_id(), strategy)


@mcp.tool()
def zero_start_session(strategy: str, paper: bool = True) -> dict:
    """Deploy a trading session with a specific strategy. Paper mode by default."""
    return _api.start_session(_get_operator_id(), strategy, paper=paper)


@mcp.tool()
def zero_session_status() -> dict:
    """Get active session state: strategy, duration, P&L, open positions."""
    return _api.session_status(_get_operator_id())


@mcp.tool()
def zero_end_session() -> dict:
    """End the active session early. Returns result card with narrative."""
    return _api.end_session(_get_operator_id())


@mcp.tool()
def zero_queue_session(strategy: str, paper: bool = True) -> dict:
    """Queue a session to start after the current one completes."""
    return _api.queue_session(_get_operator_id(), strategy, paper=paper)


@mcp.tool()
def zero_session_history(limit: int = 10) -> dict:
    """Get past session results: strategy, trades, P&L, narrative."""
    return _api.session_history(_get_operator_id(), limit=limit)


@mcp.tool()
def zero_session_result(session_id: str) -> dict:
    """Get full result card for a specific completed session."""
    return _api.session_result(_get_operator_id(), session_id)


# ── AUTO-PILOT (1) ──────────────────────────────────────────────────────────

@mcp.tool()
def zero_auto_select() -> dict:
    """Let the engine choose the best strategy for current conditions.

    Uses regime analysis, operator history, and backtest data
    to pick the optimal strategy. Shows reasoning.
    """
    return _api.auto_select(_get_operator_id())


# ── DRIVE MODE (1) ──────────────────────────────────────────────────────────

@mcp.tool()
def zero_set_mode(mode: str) -> dict:
    """Set the drive mode for the active session.

    Modes control how you experience trading:
    - comfort: autonomous, minimal alerts (entry/exit/brief only)
    - sport: autonomous with full narration (approaching, heat shifts, regime)
    - track: manual approval required for every trade

    Does NOT change strategy logic — only push frequency and approval flow.
    """
    return _api.set_mode(_get_operator_id(), mode)


# ── INTELLIGENCE TOOLS (5) ──────────────────────────────────────────────────

@mcp.tool()
def zero_evaluate(coin: str) -> dict:
    """Evaluate a coin through 7 intelligence layers. Returns consensus, conviction, direction, and per-layer detail."""
    return _api.evaluate(_get_operator_id(), coin)


@mcp.tool()
def zero_get_heat() -> dict:
    """Get all coins sorted by conviction — the heat map. Highest conviction first."""
    return _api.get_heat(_get_operator_id())


@mcp.tool()
def zero_get_approaching() -> dict:
    """Get coins near consensus threshold with bottleneck analysis and conviction velocity. Shows what's forming and how fast."""
    from scanner.v6.conviction_history import ConvictionTracker
    result = _api.get_approaching(_get_operator_id())
    tracker = ConvictionTracker()
    for coin_entry in result.get("approaching", []):
        coin = coin_entry.get("coin", "")
        data = tracker.get_coin_data(coin)
        coin_entry["velocity"] = data["velocity"]
        coin_entry["velocity_label"] = data["velocity_label"]
        coin_entry["time_to_threshold"] = data["time_to_threshold"]
    return result


@mcp.tool()
def zero_get_pulse(limit: int = 20) -> dict:
    """Get recent market events: entries, exits, approaching signals, rejections."""
    return _api.get_pulse(_get_operator_id(), limit=limit)


@mcp.tool()
def zero_get_regime() -> dict:
    """Get the global market regime state.

    Shows: dominant direction, coin distribution, fear/greed,
    funding bias, volatility. This is the "road surface" —
    how the market feels right now.
    """
    from scanner.v6.regime import RegimeState
    heat_data = _api.get_heat(_get_operator_id())
    brief_data = _api.get_brief(_get_operator_id())
    regime = RegimeState.from_heat(heat_data, brief_data)
    return regime.to_dict()


@mcp.tool()
def zero_get_brief() -> dict:
    """Generate overnight briefing: positions, signals, approaching coins, fear & greed."""
    return _api.get_brief(_get_operator_id())


# ── PATTERN RECOGNITION (1) ─────────────────────────────────────────────────

@mcp.tool()
def zero_get_insights() -> dict:
    """Get personalized trading insights based on your history.

    Discovers patterns: regime affinity, strategy edge, time patterns.
    Needs 5+ completed sessions to generate insights.
    """
    return _api.get_insights(_get_operator_id())


# ── PROGRESSION TOOLS (4) ───────────────────────────────────────────────────

@mcp.tool()
def zero_get_score() -> dict:
    """Get your 5-dimension operator score.
    Performance, discipline, protection, consistency, adaptation.
    Class: novice → apprentice → operator → veteran → elite.
    """
    return _api.get_score(_get_operator_id())


@mcp.tool()
def zero_get_achievements() -> dict:
    """Get your earned milestones and progress toward unearned ones."""
    return _api.get_achievements(_get_operator_id())


@mcp.tool()
def zero_get_streak() -> dict:
    """Get your current streak and all-time best.
    Badges: bronze (3), silver (5), gold (10), diamond (20).
    """
    return _api.get_streak(_get_operator_id())


@mcp.tool()
def zero_get_reputation() -> dict:
    """Get your full reputation: score + streak + milestones + stats."""
    return _api.get_reputation(_get_operator_id())


# ── COMPETITION TOOLS (3) ───────────────────────────────────────────────────

@mcp.tool()
def zero_get_arena() -> dict:
    """Get arena leaderboard and your ranking.

    Shows: top 10 agents by score, your rank, network stats.
    """
    return _api.get_arena(_get_operator_id())


@mcp.tool()
def zero_get_rivalry() -> dict:
    """Get your closest rival -- the agent ranked just above you.

    Shows their stats vs yours. Beat them to move up.
    """
    return _api.get_rivalry(_get_operator_id())


@mcp.tool()
def zero_get_chain() -> dict:
    """Get active chain progress and longest chain. (Phase 4 — placeholder)"""
    return _api.get_chain(_get_operator_id())


# ── ACCOUNT TOOLS (2) — Phase 4 ─────────────────────────────────────────────

@mcp.tool()
def zero_get_credits() -> dict:
    """Get credit balance and earning history. (Phase 4 — placeholder)"""
    return _api.get_credits(_get_operator_id())


@mcp.tool()
def zero_get_energy() -> dict:
    """Get energy percentage, recovery time, and projected. (Phase 4 — placeholder)"""
    return _api.get_energy(_get_operator_id())


# ── AGENT IDENTITY (1) ─────────────────────────────────────────────────────

@mcp.tool()
def zero_get_profile() -> dict:
    """Get your agent's public profile and stats.

    Returns: agent ID, public URL, score, class, milestones,
    streak, sessions, win rate, best strategy.
    Share your profile: getzero.dev/agent/{id}
    """
    from dataclasses import asdict
    from scanner.v6.agent_registry import AgentRegistry
    registry = AgentRegistry()
    profile = registry.register_or_get(_get_operator_id())
    return asdict(profile)


# ── ENGINE HEALTH (1 bonus) ─────────────────────────────────────────────────

@mcp.tool()
def zero_get_engine_health() -> dict:
    """Get engine health: cycle time, data freshness, immune status, memory usage."""
    return _api.get_engine_health(_get_operator_id())


# ── MOUNT ON FASTAPI ─────────────────────────────────────────────────────────

def mount_on_fastapi(app, path: str = "/mcp"):
    """Mount the MCP server on an existing FastAPI app at the given path."""
    mcp_app = mcp.http_app(path=path, transport="streamable-http")
    app.mount(path, mcp_app)


# ── STANDALONE ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")

#!/usr/bin/env python3
"""
Agent Reputation — 5-dimension trust score (0-5 stars).

Dimensions:
  1. Uptime (25%)       — session hours / 720h target
  2. Reliability (25%)  — completion rate, crash/early-end penalties
  3. Immune Health (20%) — failure rate, clean streak, uptime penalty
  4. Contribution (15%) — trade volume, scout sessions, coin breadth
  5. Tenure (15%)       — days active, consistency

Usage:
  python -m scanner.v6.reputation             # all sim agents
  python -m scanner.v6.reputation --agent zr_phantom   # one agent
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SIM_DIR = Path.home() / ".zeroos" / "sim"


# ─── THRESHOLDS ──────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, v))


def _bracket(value: float, thresholds: list[tuple[float, float]], default: float) -> float:
    """Return score for the first bracket where value < upper bound."""
    for upper, score in thresholds:
        if value < upper:
            return score
    return default


# ─── DIMENSION 1: UPTIME (25%) ──────────────────────────────────────────────

def compute_uptime(agent_data: dict) -> float:
    hours = agent_data.get("session_hours_last_30d", 0.0)
    ratio = hours / 720.0
    pct = ratio * 100.0
    return _bracket(pct, [
        (5, 0.5), (15, 1.5), (30, 2.5), (50, 3.5), (70, 4.0), (85, 4.5),
    ], 5.0)


# ─── DIMENSION 2: RELIABILITY (25%) ─────────────────────────────────────────

def compute_reliability(agent_data: dict) -> float:
    total = agent_data.get("sessions_total", 1)
    completed = agent_data.get("sessions_completed", 0)
    crashed = agent_data.get("sessions_crashed", 0)
    early_end_pct = agent_data.get("early_end_pct", 0.0)

    rate = completed / max(total, 1) * 100.0
    base = _bracket(rate, [(50, 1.0), (70, 2.0), (85, 3.0), (95, 4.0)], 5.0)

    crash_penalty = crashed * -0.5
    early_penalty = _bracket(early_end_pct, [
        (1, 0.0), (5, -0.1), (15, -0.3), (30, -0.8),
    ], -1.5)

    return _clamp(base + crash_penalty + early_penalty)


# ─── DIMENSION 3: IMMUNE HEALTH (20%) ───────────────────────────────────────

def compute_immune(agent_data: dict) -> float:
    checks = agent_data.get("immune_checks", 0)
    failures = agent_data.get("immune_failures", 0)
    clean_streak_days = agent_data.get("clean_streak_days", 0)
    immune_uptime_pct = agent_data.get("immune_uptime_pct", 100.0)

    failure_rate = failures / max(checks, 1) * 100.0
    base = _bracket(failure_rate, [
        (0.001, 5.0), (0.01, 4.5), (0.1, 3.5), (0.5, 2.5), (1.0, 1.5),
    ], 0.5)

    clean_bonus = _bracket(clean_streak_days, [
        (7, 0.0), (30, 0.2), (90, 0.4), (180, 0.6),
    ], 0.8)

    uptime_penalty = _bracket(100.0 - immune_uptime_pct, [
        (0.001, 0.0), (1, -0.1), (5, -0.3), (10, -0.8),
    ], -1.5)

    return _clamp(base + clean_bonus + uptime_penalty)


# ─── DIMENSION 4: CONTRIBUTION (15%) ────────────────────────────────────────

def compute_contribution(agent_data: dict) -> float:
    trade_count = agent_data.get("trade_count_30d", 0)
    scout_sessions = agent_data.get("scout_sessions_30d", 0)
    coins_traded = agent_data.get("coins_traded_count", 0)

    volume = _bracket(trade_count, [
        (50, 1.0), (200, 2.0), (500, 3.0), (1000, 4.0),
    ], 5.0)

    scout_bonus = min(scout_sessions * 0.3, 1.5)

    breadth_bonus = _bracket(coins_traded, [
        (5, 0.0), (15, 0.2), (30, 0.4),
    ], 0.6)

    return _clamp(volume + scout_bonus + breadth_bonus)


# ─── DIMENSION 5: TENURE (15%) ──────────────────────────────────────────────

def compute_tenure(agent_data: dict) -> float:
    days_active = agent_data.get("tenure_days", 0)
    consistency = agent_data.get("consistency_ratio", 1.0)

    tenure_score = _bracket(days_active, [
        (7, 1.0), (30, 2.0), (90, 3.0), (180, 4.0),
    ], 5.0)

    consistency_penalty = _bracket(consistency, [
        (0.5, -1.0), (0.7, -0.5), (0.9, -0.2),
    ], 0.0)

    return _clamp(tenure_score + consistency_penalty)


# ─── OVERALL ─────────────────────────────────────────────────────────────────

def compute_reputation(agent_data: dict) -> dict:
    """Compute all 5 reputation dimensions + overall score."""
    uptime = compute_uptime(agent_data)
    reliability = compute_reliability(agent_data)
    immune = compute_immune(agent_data)
    contribution = compute_contribution(agent_data)
    tenure = compute_tenure(agent_data)

    overall = (
        uptime * 0.25
        + reliability * 0.25
        + immune * 0.20
        + contribution * 0.15
        + tenure * 0.15
    )

    filled = int(round(overall))
    stars_display = "★" * filled + "☆" * (5 - filled)

    return {
        "agent_id": agent_data.get("agent_id", "unknown"),
        "dimensions": {
            "uptime": round(uptime, 2),
            "reliability": round(reliability, 2),
            "immune_health": round(immune, 2),
            "contribution": round(contribution, 2),
            "tenure": round(tenure, 2),
        },
        "overall": round(overall, 2),
        "stars_display": f"{stars_display} ({overall:.1f})",
    }


def format_reputation(result: dict) -> str:
    """Format reputation result for CLI display."""
    d = result["dimensions"]
    lines = [
        f"  Agent: {result['agent_id']}",
        f"  Rating: {result['stars_display']}",
        f"",
        f"  Uptime          {d['uptime']:.1f} / 5.0  (25%)",
        f"  Reliability     {d['reliability']:.1f} / 5.0  (25%)",
        f"  Immune Health   {d['immune_health']:.1f} / 5.0  (20%)",
        f"  Contribution    {d['contribution']:.1f} / 5.0  (15%)",
        f"  Tenure          {d['tenure']:.1f} / 5.0  (15%)",
        f"",
        f"  Overall         {result['overall']:.2f} / 5.00",
    ]
    return "\n".join(lines)


# ─── DATA EXTRACTION ────────────────────────────────────────────────────────

def extract_agent_data(agent_dir: Path) -> dict:
    """Build agent_data dict from sim session.json."""
    session_file = agent_dir / "session.json"
    if not session_file.exists():
        return {"agent_id": agent_dir.name}

    with open(session_file) as f:
        session = json.load(f)

    now = datetime.now(timezone.utc)
    agent_id = session.get("agent_id", agent_dir.name)
    started = datetime.fromisoformat(session["started_at"])
    expires = datetime.fromisoformat(session["expires_at"])
    status = session.get("status", "unknown")
    trades = session.get("trades", [])

    # Tenure: days since first session start
    tenure_days = max((now - started).total_seconds() / 86400, 0)

    # Session hours (this session's active hours, capped at 30d)
    session_end = min(now, expires) if status == "active" else expires
    session_hours = max((session_end - started).total_seconds() / 3600, 0)

    # Trade analysis
    trade_count = len([t for t in trades if t.get("action") == "open"])
    coins = set(t.get("coin", "") for t in trades)
    coins.discard("")
    close_trades = [t for t in trades if t.get("action") == "close"]
    stop_losses = [t for t in close_trades if t.get("reason") == "stop_loss"]

    # Completion: active = in progress, check if expired
    is_completed = status == "completed" or (status == "active" and now < expires)
    is_crashed = status == "crashed"
    early_ends = len(stop_losses)
    early_end_pct = (early_ends / max(len(close_trades), 1)) * 100 if close_trades else 0.0

    # Eval count as proxy for immune checks
    eval_count = session.get("eval_count", 0)

    # Consistency: fraction of days with activity
    if tenure_days > 0:
        trade_days = set()
        for t in trades:
            ts = t.get("ts", "")
            if ts:
                trade_days.add(ts[:10])
        consistency = len(trade_days) / max(tenure_days, 1)
    else:
        consistency = 1.0

    return {
        "agent_id": agent_id,
        "session_hours_last_30d": session_hours,
        "sessions_total": 1,
        "sessions_completed": 1 if is_completed else 0,
        "sessions_crashed": 1 if is_crashed else 0,
        "early_end_pct": early_end_pct,
        "immune_checks": eval_count,
        "immune_failures": 0,
        "clean_streak_days": tenure_days,
        "immune_uptime_pct": 100.0 if status == "active" else 95.0,
        "trade_count_30d": trade_count,
        "scout_sessions_30d": 0,
        "coins_traded_count": len(coins),
        "tenure_days": tenure_days,
        "consistency_ratio": min(consistency, 1.0),
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent Reputation — 5-dimension trust score")
    parser.add_argument("--agent", type=str, help="Show specific agent (e.g. zr_phantom)")
    args = parser.parse_args()

    if not SIM_DIR.exists():
        print(f"No sim directory found at {SIM_DIR}")
        sys.exit(1)

    if args.agent:
        agent_dir = SIM_DIR / args.agent
        if not agent_dir.exists():
            print(f"Agent not found: {args.agent}")
            sys.exit(1)
        data = extract_agent_data(agent_dir)
        result = compute_reputation(data)
        print(format_reputation(result))
    else:
        agents = sorted(d for d in SIM_DIR.iterdir() if d.is_dir())
        if not agents:
            print("No agents found.")
            sys.exit(0)

        print(f"{'Agent':<14} {'Rating':<16} {'Up':>4} {'Rel':>4} {'Imm':>4} {'Con':>4} {'Ten':>4} {'Overall':>7}")
        print("─" * 72)
        for agent_dir in agents:
            data = extract_agent_data(agent_dir)
            r = compute_reputation(data)
            d = r["dimensions"]
            filled = int(round(r["overall"]))
            stars = "★" * filled + "☆" * (5 - filled)
            print(
                f"{r['agent_id']:<14} {stars:<16}"
                f" {d['uptime']:>4.1f} {d['reliability']:>4.1f}"
                f" {d['immune_health']:>4.1f} {d['contribution']:>4.1f}"
                f" {d['tenure']:>4.1f}  {r['overall']:>5.2f}"
            )


if __name__ == "__main__":
    main()

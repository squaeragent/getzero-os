# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
zero_score.py — Score System v2

5 dimensions, Bayesian convergence, time decay, confidence bands.
The score is the LANGUAGE of the platform.

DIMENSIONS (weights):
  PERFORMANCE  (25%) — risk-adjusted returns
  DISCIPLINE   (25%) — patience, selectivity, strategy adherence
  PROTECTION   (20%) — drawdown control, immune health, loss management
  CONSISTENCY  (15%) — stability across sessions, low variance
  ADAPTATION   (15%) — intelligent mode/strategy use, pattern learning

OVERALL = perf×0.25 + disc×0.25 + prot×0.20 + cons×0.15 + adapt×0.15
"""

import json
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [score] [{ts}] {msg}", flush=True)

STATE_DIR = Path.home() / ".zeroos" / "state"

# ─── DIMENSION WEIGHTS ──────────────────────────────────────────────────────

_WEIGHTS = {
    "performance": 0.25,
    "discipline":  0.25,
    "protection":  0.20,
    "consistency": 0.15,
    "adaptation":  0.15,
}

BAYESIAN_PRIOR = 5.0
CONVERGENCE_TRADES = 100
DECAY_HALFLIFE_DAYS = 14
DECAY_GRACE_DAYS = 3
MIN_TRADES_VISIBLE = 20
MIN_DAYS_VISIBLE = 7
TRUST_BONUS = 0.05
DIVERSITY_BONUS_MAX = 0.15
ROLLING_WINDOW_DAYS = 90


# ─── INTERPOLATION HELPERS ──────────────────────────────────────────────────

def _interpolate(value: float, thresholds: list[tuple[float, float]]) -> float:
    """Linearly interpolate between threshold breakpoints.

    thresholds: list of (input_value, output_value) sorted by input_value ascending.
    Values below the first threshold clamp to first output.
    Values above the last threshold clamp to last output.
    Duplicate x-values create step transitions — at the exact x, the later y wins.
    """
    if not thresholds:
        return 0.0
    if value < thresholds[0][0]:
        return thresholds[0][1]
    if value >= thresholds[-1][0]:
        return thresholds[-1][1]
    for i in range(len(thresholds) - 1):
        x0, y0 = thresholds[i]
        x1, y1 = thresholds[i + 1]
        if x0 == x1:
            continue  # skip step-transition pairs, handled by next segment
        if x0 <= value < x1:
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return thresholds[-1][1]


def _filter_rolling_window(trades: list[dict], days: int = ROLLING_WINDOW_DAYS) -> list[dict]:
    """Return only trades from the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for t in trades:
        ts_str = t.get("exit_time") or t.get("entry_time") or ""
        if not ts_str:
            result.append(t)  # keep trades with no timestamp
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts >= cutoff:
                result.append(t)
        except Exception:
            result.append(t)
    return result


# ─── DIMENSION 1: PERFORMANCE (25%) ────────────────────────────────────────

def _performance_score(session_returns: list[float], max_drawdown_pct: float,
                       profit_factor: float, gross_losses: float) -> float | None:
    """Risk-adjusted returns.

    Minimum data: 3 completed sessions.

    return_score thresholds:
        -5%→0.0, -2%→2.0, 0%→4.0, +1%→5.5, +2%→6.5, +4%→7.5, +8%→8.5, +15%→9.5, +25%→10.0

    drawdown_penalty thresholds:
        0-2%→0.0, 2-5%→-0.3, 5-10%→-0.8, 10-20%→-1.5, 20-30%→-2.5, 30%+→-4.0

    profit_factor_bonus thresholds:
        <0.5→-1.0, 0.5-1.0→0.0, 1.0-1.5→+0.3, 1.5-2.0→+0.5, 2.0-3.0→+0.8, 3.0+→+1.0

    Guard: if gross_losses == 0, treat profit_factor as 10.0 (capped at +1.0 bonus).
    """
    if len(session_returns) < 3:
        return None

    avg_return = sum(session_returns) / len(session_returns)

    return_score = _interpolate(avg_return * 100, [
        (-5.0, 0.0), (-2.0, 2.0), (0.0, 4.0), (1.0, 5.5),
        (2.0, 6.5), (4.0, 7.5), (8.0, 8.5), (15.0, 9.5), (25.0, 10.0),
    ])

    drawdown_penalty = _interpolate(max_drawdown_pct, [
        (0.0, 0.0), (2.0, 0.0), (5.0, -0.3), (10.0, -0.8),
        (20.0, -1.5), (30.0, -2.5), (100.0, -4.0),
    ])

    # Guard: no losses means perfect profit factor
    effective_pf = 10.0 if gross_losses == 0 else profit_factor
    profit_factor_bonus = _interpolate(effective_pf, [
        (0.0, -1.0), (0.5, -1.0), (0.5, 0.0), (1.0, 0.0),
        (1.5, 0.3), (2.0, 0.5), (3.0, 0.8), (100.0, 1.0),
    ])
    # Clamp bonus for the no-loss guard case
    if gross_losses == 0:
        profit_factor_bonus = min(profit_factor_bonus, 1.0)

    raw = return_score + drawdown_penalty + profit_factor_bonus
    return max(0.0, min(10.0, raw))


# ─── DIMENSION 2: DISCIPLINE (25%) ─────────────────────────────────────────

def _discipline_score(rejection_rate: float, session_completion_rate: float,
                      sessions_per_week: float) -> float | None:
    """Patience, selectivity, strategy adherence.

    Minimum data: 2 completed sessions.

    rejection_score thresholds:
        <80%→3.0, 80-90%→5.0, 90-95%→7.0, 95-98%→8.5, 98-99%→9.5, 99%+→10.0

    completion_score thresholds:
        <50%→2.0, 50-70%→4.0, 70-85%→6.0, 85-95%→8.0, 95%+→9.5

    frequency_score thresholds:
        0→2.0, 0.5-1→5.0, 2-4→8.0, 5-7→7.0, 8+→5.0

    DISCIPLINE = rejection×0.4 + completion×0.3 + frequency×0.3
    """
    rejection_pct = rejection_rate * 100

    rejection_score = _interpolate(rejection_pct, [
        (0.0, 3.0), (80.0, 3.0), (80.0, 5.0), (90.0, 5.0),
        (90.0, 7.0), (95.0, 7.0), (95.0, 8.5), (98.0, 8.5),
        (98.0, 9.5), (99.0, 9.5), (99.0, 10.0), (100.0, 10.0),
    ])

    completion_pct = session_completion_rate * 100

    completion_score = _interpolate(completion_pct, [
        (0.0, 2.0), (50.0, 2.0), (50.0, 4.0), (70.0, 4.0),
        (70.0, 6.0), (85.0, 6.0), (85.0, 8.0), (95.0, 8.0),
        (95.0, 9.5), (100.0, 9.5),
    ])

    frequency_score = _interpolate(sessions_per_week, [
        (0.0, 2.0), (0.5, 5.0), (1.0, 5.0), (2.0, 8.0),
        (4.0, 8.0), (5.0, 7.0), (7.0, 7.0), (8.0, 5.0), (20.0, 5.0),
    ])

    return rejection_score * 0.4 + completion_score * 0.3 + frequency_score * 0.3


# ─── DIMENSION 3: PROTECTION (20%) ─────────────────────────────────────────

def _protection_score(immune_uptime_pct: float, immune_failures: int,
                      immune_saves: int, max_single_loss_pct: float) -> float | None:
    """Drawdown control, immune health, loss management.

    Minimum data: 1 completed session + immune data.

    uptime_score thresholds:
        <90%→2.0, 90-95%→5.0, 95-99%→7.0, 99-99.9%→9.0, 100%→10.0

    failure_penalty = immune_failures × -1.0
    save_bonus = min(immune_saves × 0.1, 1.0)

    max_loss_score thresholds:
        <1%→10.0, 1-2%→8.0, 2-3%→6.5, 3-5%→5.0, 5-10%→3.0, 10%+→1.0

    PROTECTION = clamp((uptime×0.3) + (max_loss×0.4) + save_bonus + failure_penalty, 0, 10)
    """
    uptime_score = _interpolate(immune_uptime_pct, [
        (0.0, 2.0), (90.0, 2.0), (90.0, 5.0), (95.0, 5.0),
        (95.0, 7.0), (99.0, 7.0), (99.0, 9.0), (99.9, 9.0),
        (99.9, 10.0), (100.0, 10.0),
    ])

    failure_penalty = immune_failures * -1.0
    save_bonus = min(immune_saves * 0.1, 1.0)

    max_loss_score = _interpolate(max_single_loss_pct, [
        (0.0, 10.0), (1.0, 10.0), (1.0, 8.0), (2.0, 8.0),
        (2.0, 6.5), (3.0, 6.5), (3.0, 5.0), (5.0, 5.0),
        (5.0, 3.0), (10.0, 3.0), (10.0, 1.0), (100.0, 1.0),
    ])

    raw = (uptime_score * 0.3) + (max_loss_score * 0.4) + save_bonus + failure_penalty
    return max(0.0, min(10.0, raw))


# ─── DIMENSION 4: CONSISTENCY (15%) ────────────────────────────────────────

def _consistency_score(session_return_stddev: float, win_rate: float,
                       losing_streak_max: int) -> float | None:
    """Stability across sessions, low variance.

    Minimum data: 5 completed sessions.

    variance_score thresholds (stddev %):
        <1%→9.5, 1-2%→8.0, 2-4%→6.5, 4-6%→5.0, 6-10%→3.5, 10%+→2.0

    win_rate_score thresholds:
        <30%→2.0, 30-40%→4.0, 40-50%→5.5, 50-60%→7.0, 60-70%→8.0, 70-80%→9.0, 80%+→9.5

    streak_penalty thresholds:
        0-1→0.0, 2→-0.3, 3→-0.8, 4-5→-1.5, 6+→-2.5

    CONSISTENCY = clamp((variance×0.5) + (win_rate×0.4) + streak_penalty, 0, 10)
    """
    variance_score = _interpolate(session_return_stddev * 100, [
        (0.0, 9.5), (1.0, 9.5), (2.0, 8.0), (4.0, 6.5),
        (6.0, 5.0), (10.0, 3.5), (100.0, 2.0),
    ])

    win_rate_pct = win_rate * 100
    win_rate_score = _interpolate(win_rate_pct, [
        (0.0, 2.0), (30.0, 2.0), (30.0, 4.0), (40.0, 4.0),
        (40.0, 5.5), (50.0, 5.5), (50.0, 7.0), (60.0, 7.0),
        (60.0, 8.0), (70.0, 8.0), (70.0, 9.0), (80.0, 9.0),
        (80.0, 9.5), (100.0, 9.5),
    ])

    streak_penalty = _interpolate(float(losing_streak_max), [
        (0.0, 0.0), (1.0, 0.0), (2.0, -0.3), (3.0, -0.8),
        (4.0, -1.5), (5.0, -1.5), (6.0, -2.5), (100.0, -2.5),
    ])

    raw = (variance_score * 0.5) + (win_rate_score * 0.4) + streak_penalty
    return max(0.0, min(10.0, raw))


# ─── DIMENSION 5: ADAPTATION (15%) ─────────────────────────────────────────

def _adaptation_score(strategy_market_match_rate: float, unique_strategies_used: int,
                      score_trend_30d: str) -> float | None:
    """Intelligent mode/strategy use, pattern learning.

    Minimum data: 5 completed sessions across 2+ strategies.

    match_score thresholds:
        <30%→2.0, 30-50%→4.0, 50-65%→6.0, 65-80%→7.5, 80%+→9.0

    diversity_score thresholds:
        1 strategy→4.0, 2→6.0, 3→7.5, 4-5→8.5, 6+→9.5

    improvement_score:
        declining→3.0, flat→5.0, slight_up→7.0, strong_up→9.0

    ADAPTATION = match×0.4 + diversity×0.3 + improvement×0.3
    """
    match_pct = strategy_market_match_rate * 100
    match_score = _interpolate(match_pct, [
        (0.0, 2.0), (30.0, 2.0), (30.0, 4.0), (50.0, 4.0),
        (50.0, 6.0), (65.0, 6.0), (65.0, 7.5), (80.0, 7.5),
        (80.0, 9.0), (100.0, 9.0),
    ])

    diversity_score = _interpolate(float(unique_strategies_used), [
        (1.0, 4.0), (2.0, 6.0), (3.0, 7.5), (4.0, 8.5),
        (5.0, 8.5), (6.0, 9.5), (100.0, 9.5),
    ])

    improvement_map = {
        "declining": 3.0,
        "flat": 5.0,
        "slight_up": 7.0,
        "strong_up": 9.0,
    }
    improvement_score = improvement_map.get(score_trend_30d, 5.0)

    return match_score * 0.4 + diversity_score * 0.3 + improvement_score * 0.3


# ─── EXTRACT INPUTS FROM TRADES + AGENT ─────────────────────────────────────

def _extract_session_returns(trades: list[dict]) -> list[float]:
    """Group trades into sessions and return per-session return percentages."""
    sessions: dict[str, list[float]] = {}
    for t in trades:
        sid = t.get("session_id", "default")
        sessions.setdefault(sid, []).append(t.get("pnl_pct", 0.0))
    return [sum(pnls) for pnls in sessions.values()] if sessions else []


def _extract_max_drawdown(trades: list[dict]) -> float:
    """Compute max drawdown percentage from sequential trades."""
    if not trades:
        return 0.0
    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.get("pnl_pct", 0.0)
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    return max_dd  # pnl_pct is already in percentage points


def _extract_profit_factor(trades: list[dict]) -> tuple[float, float]:
    """Return (profit_factor, gross_losses)."""
    gross_profit = sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) > 0)
    gross_loss = abs(sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) < 0))
    if gross_loss == 0:
        return (10.0, 0.0)
    return (gross_profit / gross_loss, gross_loss)


def _extract_win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
    return wins / len(trades)


def _extract_losing_streak(trades: list[dict]) -> int:
    max_streak = 0
    current = 0
    for t in trades:
        if t.get("pnl_pct", 0) < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _extract_max_single_loss(trades: list[dict]) -> float:
    """Return max single loss as positive percentage."""
    if not trades:
        return 0.0
    losses = [abs(t.get("pnl_pct", 0)) for t in trades if t.get("pnl_pct", 0) < 0]
    return max(losses) if losses else 0.0


def _extract_unique_strategies(trades: list[dict]) -> int:
    strategies = set()
    for t in trades:
        s = t.get("strategy", t.get("mode", "default"))
        strategies.add(s)
    return max(len(strategies), 1)


def _extract_score_trend(history: list[float]) -> str:
    """Determine 30-day score trend from historical scores."""
    if len(history) < 2:
        return "flat"
    recent = history[-min(len(history), 7):]
    older = history[:-len(recent)] if len(history) > len(recent) else recent
    if not older:
        return "flat"
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    diff = recent_avg - older_avg
    if diff > 1.0:
        return "strong_up"
    elif diff > 0.3:
        return "slight_up"
    elif diff < -0.3:
        return "declining"
    return "flat"


def _count_completed_sessions(trades: list[dict]) -> int:
    """Count distinct completed sessions."""
    sessions = set()
    for t in trades:
        sid = t.get("session_id", "default")
        sessions.add(sid)
    return len(sessions)


# ─── SCORE ENGINE ────────────────────────────────────────────────────────────

def compute_score(trades: list[dict], agent: dict) -> dict:
    """Compute the full ZERO Score with all 5 dimensions.

    Handles partial data: only include non-None dimensions, redistribute
    weights proportionally. 90-day rolling window. Bayesian convergence
    (prior=5.0, converges at 100 trades). Time decay (14-day halflife
    after 3-day grace).
    """
    # 90-day rolling window
    trades = _filter_rolling_window(trades)
    trade_count = len(trades)
    completed_sessions = _count_completed_sessions(trades)

    # Extract common inputs
    session_returns = _extract_session_returns(trades)
    max_dd_pct = _extract_max_drawdown(trades)
    pf, gross_losses = _extract_profit_factor(trades)
    win_rate = _extract_win_rate(trades)
    losing_streak = _extract_losing_streak(trades)
    max_single_loss = _extract_max_single_loss(trades)
    unique_strats = _extract_unique_strategies(trades)

    # Session return stats
    if session_returns:
        sr_stddev = math.sqrt(sum((r - sum(session_returns)/len(session_returns))**2
                                  for r in session_returns) / len(session_returns))
    else:
        sr_stddev = 0.0

    # Agent-level inputs
    rejection_rate = agent.get("rejection_rate", 0.95)
    session_completion_rate = agent.get("session_completion_rate", 0.9)
    sessions_per_week = agent.get("sessions_per_week", 3.0)
    immune_uptime_pct = agent.get("immune_uptime_pct", 99.0)
    immune_failures = agent.get("immune_failures", 0)
    immune_saves = agent.get("immune_saves", 0)
    strategy_match_rate = agent.get("strategy_market_match_rate", 0.5)
    score_history = agent.get("score_history", [])
    score_trend = _extract_score_trend(score_history)

    # Compute each dimension (may return None if insufficient data)
    dimensions = {}

    # PERFORMANCE: min 3 sessions
    if completed_sessions >= 3:
        dimensions["performance"] = round(
            _performance_score(session_returns, max_dd_pct, pf, gross_losses), 1)
    else:
        dimensions["performance"] = None

    # DISCIPLINE: min 2 sessions
    if completed_sessions >= 2:
        dimensions["discipline"] = round(
            _discipline_score(rejection_rate, session_completion_rate, sessions_per_week), 1)
    else:
        dimensions["discipline"] = None

    # PROTECTION: min 1 session + immune data
    has_immune = any(k in agent for k in ("immune_uptime_pct", "immune_failures", "immune_saves"))
    if completed_sessions >= 1 and has_immune:
        dimensions["protection"] = round(
            _protection_score(immune_uptime_pct, immune_failures, immune_saves, max_single_loss), 1)
    else:
        dimensions["protection"] = None

    # CONSISTENCY: min 5 sessions
    if completed_sessions >= 5:
        dimensions["consistency"] = round(
            _consistency_score(sr_stddev, win_rate, losing_streak), 1)
    else:
        dimensions["consistency"] = None

    # ADAPTATION: min 5 sessions across 2+ strategies
    if completed_sessions >= 5 and unique_strats >= 2:
        dimensions["adaptation"] = round(
            _adaptation_score(strategy_match_rate, unique_strats, score_trend), 1)
    else:
        dimensions["adaptation"] = None

    # Weighted total — redistribute weights for non-None dimensions only
    active_dims = {k: v for k, v in dimensions.items() if v is not None}
    if active_dims:
        total_weight = sum(_WEIGHTS[k] for k in active_dims)
        raw_total = sum(active_dims[k] * _WEIGHTS[k] / total_weight for k in active_dims)
    else:
        raw_total = BAYESIAN_PRIOR

    # Bayesian convergence (prior=5.0, converges at 100 trades)
    if trade_count < CONVERGENCE_TRADES:
        alpha = trade_count / CONVERGENCE_TRADES
        total = alpha * raw_total + (1 - alpha) * BAYESIAN_PRIOR
    else:
        total = raw_total

    # Trust bonus for verified agents
    if agent.get("wallet_verified", False):
        total *= (1 + TRUST_BONUS)

    total = round(min(10.0, max(0.0, total)), 1)

    # Confidence band (tighter with more trades)
    confidence = min(1.0, trade_count / CONVERGENCE_TRADES)
    band_width = (1 - confidence) * 2.0  # ±2.0 at 0 trades, ±0 at 100
    lower_bound = round(max(0, total - band_width), 1)

    # Time decay (14-day halflife after 3-day grace)
    last_trade_time = None
    for t in reversed(trades):
        ts = t.get("exit_time") or t.get("entry_time")
        if ts:
            try:
                last_trade_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                pass
            break

    decay_factor = 1.0
    if last_trade_time:
        days_inactive = (datetime.now(timezone.utc) - last_trade_time).total_seconds() / 86400
        if days_inactive > DECAY_GRACE_DAYS:
            decay_factor = 0.5 ** ((days_inactive - DECAY_GRACE_DAYS) / DECAY_HALFLIFE_DAYS)

    if decay_factor < 1.0:
        total = round(total * decay_factor, 1)
        lower_bound = round(lower_bound * decay_factor, 1)

    # Visibility check
    agent_days = agent.get("uptime_days", 0)
    visible = trade_count >= MIN_TRADES_VISIBLE and agent_days >= MIN_DAYS_VISIBLE

    # Calibration progress
    calibration = min(1.0, trade_count / MIN_TRADES_VISIBLE)

    return {
        "score": total,
        "components": dimensions,
        "lower_bound": lower_bound,
        "confidence": round(confidence, 2),
        "trade_count": trade_count,
        "decay_factor": round(decay_factor, 3),
        "visible": visible,
        "calibrating": not visible,
        "calibration_progress": round(calibration, 2),
        "verified": agent.get("wallet_verified", False),
    }


def compute_operator_score(agent_scores: list[dict]) -> dict:
    """Weighted average of agent scores × diversity bonus."""
    if not agent_scores:
        return {"score": 0, "agent_count": 0}

    total_weight = 0
    weighted_sum = 0
    for a in agent_scores:
        s = a.get("score", 0)
        weight = max(s, 1)
        weighted_sum += s * weight
        total_weight += weight

    avg = weighted_sum / max(total_weight, 1)

    agent_count = len(agent_scores)
    diversity = min(DIVERSITY_BONUS_MAX, (agent_count - 1) * 0.05)

    active = sum(1 for a in agent_scores if a.get("decay_factor", 1) > 0.9)
    uptime_factor = active / max(agent_count, 1)

    final = round(min(10.0, avg * (1 + diversity) * uptime_factor), 1)

    return {
        "score": final,
        "agent_count": agent_count,
        "active_agents": active,
        "diversity_bonus": round(diversity, 2),
        "best_agent": max(agent_scores, key=lambda a: a.get("score", 0)),
    }


# ─── SCORE DISPLAY ──────────────────────────────────────────────────────────

def format_score_display(result: dict) -> str:
    """Format score for CLI display. Shows dimensions but not weights."""
    if result.get("calibrating"):
        pct = int(result.get("calibration_progress", 0) * 100)
        return (
            f"  zero score: calibrating [{pct}%]\n"
            f"  {'█' * (pct // 5)}{'░' * (20 - pct // 5)}\n"
            f"  need {MIN_TRADES_VISIBLE} trades + {MIN_DAYS_VISIBLE} days. keep running.\n"
        )

    score = result["score"]
    components = result["components"]
    lower = result["lower_bound"]
    verified = "✓" if result.get("verified") else ""
    decay = result.get("decay_factor", 1)

    lines = [f"  zero score: {score} {verified}"]

    if lower < score:
        lines.append(f"  confidence range: {lower} — {score}")

    bar_width = 30
    filled = int(score / 10 * bar_width)
    lines.append(f"  {'█' * filled}{'░' * (bar_width - filled)}")
    lines.append("")

    lines.append("  breakdown:")
    labels = {
        "performance": "performance",
        "discipline":  "discipline",
        "protection":  "protection",
        "consistency": "consistency",
        "adaptation":  "adaptation",
    }
    qualitative = {
        (9, 11): "excellent",
        (7, 9): "good",
        (5, 7): "developing",
        (3, 5): "needs work",
        (0, 3): "critical",
    }

    for key, label in labels.items():
        val = components.get(key)
        if val is None:
            lines.append(f"    {label:14s}   —   insufficient data")
            continue
        qual = "unknown"
        for (lo, hi), q in qualitative.items():
            if lo <= val < hi:
                qual = q
                break
        bar = "█" * int(val) + "░" * (10 - int(val))
        lines.append(f"    {label:14s} {val:4.1f}  {bar}  {qual}")

    if decay < 0.95:
        lines.append(f"\n  ⚠ score decaying — last trade {int((1 - decay) * DECAY_HALFLIFE_DAYS)}+ days ago")

    return "\n".join(lines)


# ─── SKILL DECOMPOSITION ────────────────────────────────────────────────────

def get_weakest_component(result: dict) -> dict:
    """Identify weakest dimension with actionable advice."""
    components = result.get("components", {})
    active = {k: v for k, v in components.items() if v is not None}
    if not active:
        return {"component": "unknown", "score": 0, "fix": "keep trading"}

    weakest = min(active, key=lambda k: active[k])
    score = active[weakest]

    fixes = {
        "performance": "focus on risk management. smaller drawdowns = higher performance score.",
        "discipline":  "don't override the machine. let it reject signals. stability > speed.",
        "protection":  "ensure every position has a stop. never disable the immune system.",
        "consistency": "trade in all conditions, not just trending. diversify across regimes.",
        "adaptation":  "use multiple strategies. match strategy to market regime. learn from patterns.",
    }

    return {
        "component": weakest,
        "score": score,
        "fix": fixes.get(weakest, "keep trading"),
    }


# ─── PERSISTENCE ─────────────────────────────────────────────────────────────

def save_score(result: dict, agent_id: str = "default"):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"score_{agent_id}.json"
    path.write_text(json.dumps({**result, "updated_at": datetime.now(timezone.utc).isoformat()}))

def load_score(agent_id: str = "default") -> dict | None:
    path = STATE_DIR / f"score_{agent_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


# ─── COMPAT LAYER (for score_cmd.py) ─────────────────────────────────────────

def _rest_fetch(table: str, query: str) -> list[dict]:
    """Fetch from Supabase REST API."""
    import os
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", ""))
    if not url or not key:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}?{query}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def score_from_db() -> dict:
    """Compute score from trades in local jsonl file."""
    trades_file = Path.home() / "getzero-os" / "scanner" / "v6" / "bus" / "trades.jsonl"
    trades = []
    if trades_file.exists():
        for line in trades_file.read_text().strip().split("\n"):
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except Exception:
                    pass

    agent = {
        "uptime_days": 45,
        "rejection_rate": 0.95,
        "session_completion_rate": 0.9,
        "sessions_per_week": 3.0,
        "immune_uptime_pct": 99.0,
        "immune_saves": 0,
        "immune_failures": 0,
        "strategy_market_match_rate": 0.5,
        "score_history": [],
        "wallet_verified": False,
    }

    result = compute_score(trades, agent)
    weakest = get_weakest_component(result)

    return {
        "score": result["score"],
        "effective_score": result["score"],
        "components": result["components"],
        "lower_bound": result["lower_bound"],
        "confidence": result["confidence"],
        "trade_count": result["trade_count"],
        "days_active": agent["uptime_days"],
        "decay_state": "active" if result["decay_factor"] > 0.95 else "decaying",
        "decay_factor": result["decay_factor"],
        "weakest": weakest["component"],
        "rank_label": f"top {max(10, 100 - int(result['score'] * 10))}%",
        "verified": result["verified"],
        "calibrating": result["calibrating"],
    }


def format_terminal(result: dict) -> str:
    """Format for CLI output."""
    return format_score_display(result)


def generate_insight(result: dict, trades: list[dict] = None) -> str:
    """Generate actionable insight."""
    weakest = get_weakest_component(result)
    return weakest.get("fix", "")


def save_snapshot(result: dict) -> bool:
    """Save score snapshot."""
    try:
        save_score(result)
        return True
    except Exception:
        return False


def check_achievements(history: list[dict], score: float) -> list[dict]:
    """Check for new achievements."""
    achievements = []
    if score >= 8.0:
        achievements.append({"name": "◆ elite", "key": "elite_score"})
    if score >= 7.0:
        achievements.append({"name": "◆ veteran", "key": "veteran_score"})
    return achievements


def get_history() -> list[dict]:
    """Load score history."""
    path = STATE_DIR / "score_history.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []

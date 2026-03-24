# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
zero_score.py — Session 5: ZERO Score

5 components, Bayesian convergence, time decay, confidence bands.
The score is the LANGUAGE of the platform.
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

# ─── COMPONENT WEIGHTS (INTERNAL — NEVER SHOWN) ──────────────────────────────

_WEIGHTS = {
    "immune":      0.25,
    "discipline":  0.25,
    "performance": 0.20,
    "consistency": 0.20,
    "resilience":  0.10,
}

BAYESIAN_PRIOR = 5.0
CONVERGENCE_TRADES = 100
DECAY_HALFLIFE_DAYS = 14
MIN_TRADES_VISIBLE = 20
MIN_DAYS_VISIBLE = 7
TRUST_BONUS = 0.05  # 5% for verified
DIVERSITY_BONUS_MAX = 0.15  # up to 15% for operator


# ─── COMPONENT CALCULATORS ───────────────────────────────────────────────────

def _immune_score(trades: list[dict], agent: dict) -> float:
    """Stop verification + audit + saves + uptime."""
    stop_rate = agent.get("stop_verification_rate", 1.0)
    saves = agent.get("immune_saves", 0)
    checks = max(agent.get("immune_checks", 1), 1)
    save_rate = saves / checks
    uptime_pct = min(agent.get("uptime_days", 0) / 90, 1.0)

    # Perfect stops + high uptime + few saves needed = high score
    raw = stop_rate * 4 + (1 - min(save_rate * 10, 1)) * 3 + uptime_pct * 3
    return min(10.0, max(0.0, raw))


def _discipline_score(trades: list[dict], agent: dict) -> float:
    """Rejection rate + overrides + config stability."""
    total_evals = agent.get("total_evaluations", 1)
    total_trades = max(len(trades), 1)
    rejection_rate = 1 - (total_trades / max(total_evals, total_trades))

    overrides = agent.get("manual_overrides", 0)
    config_changes = agent.get("config_changes", 0)

    # High rejection rate + few overrides + stable config = disciplined
    rej_score = min(rejection_rate * 10, 9.5)
    override_penalty = min(overrides * 0.5, 3)
    config_penalty = min(config_changes * 0.2, 2)

    return max(0.0, min(10.0, rej_score - override_penalty - config_penalty))


def _performance_score(trades: list[dict]) -> float:
    """Sortino ratio + max drawdown penalty."""
    if not trades:
        return BAYESIAN_PRIOR

    returns = [t.get("pnl_pct", 0) for t in trades]
    if not returns:
        return BAYESIAN_PRIOR

    avg_return = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    downside_dev = math.sqrt(sum(r**2 for r in downside) / max(len(downside), 1)) if downside else 0.001

    sortino = avg_return / max(downside_dev, 0.001)

    # Max drawdown penalty
    peak = 0
    max_dd = 0
    cumulative = 0
    for r in returns:
        cumulative += r
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    dd_penalty = min(max_dd * 20, 4)

    # Scale sortino to 0-10 range
    raw = min(10, max(0, sortino * 2 + 5)) - dd_penalty
    return max(0.0, min(10.0, raw))


def _consistency_score(trades: list[dict]) -> float:
    """Positive day rate + distribution + WR stability."""
    if len(trades) < 5:
        return BAYESIAN_PRIOR

    # Group by day
    daily_pnl: dict[str, float] = {}
    for t in trades:
        day = t.get("entry_time", "")[:10] or t.get("exit_time", "")[:10]
        if day:
            daily_pnl[day] = daily_pnl.get(day, 0) + t.get("pnl_pct", 0)

    if not daily_pnl:
        return BAYESIAN_PRIOR

    positive_days = sum(1 for v in daily_pnl.values() if v > 0)
    total_days = len(daily_pnl)
    pos_rate = positive_days / total_days

    # Win rate stability (rolling WR shouldn't vary wildly)
    wins = [1 if t.get("pnl_pct", 0) > 0 else 0 for t in trades]
    if len(wins) >= 10:
        window = max(len(wins) // 4, 5)
        chunks = [wins[i:i+window] for i in range(0, len(wins) - window + 1, window)]
        chunk_wrs = [sum(c) / len(c) for c in chunks if c]
        if len(chunk_wrs) >= 2:
            wr_std = math.sqrt(sum((w - sum(chunk_wrs)/len(chunk_wrs))**2 for w in chunk_wrs) / len(chunk_wrs))
        else:
            wr_std = 0
    else:
        wr_std = 0.1

    stability_score = max(0, 10 - wr_std * 30)
    pos_day_score = pos_rate * 10

    return min(10.0, (pos_day_score * 0.6 + stability_score * 0.4))


def _resilience_score(trades: list[dict]) -> float:
    """Recovery speed + regime transition behavior."""
    if len(trades) < 10:
        return BAYESIAN_PRIOR

    # Recovery: how quickly does P&L recover after a loss?
    recovery_times = []
    in_drawdown = False
    dd_start_idx = 0

    for i, t in enumerate(trades):
        pnl = t.get("pnl_pct", 0)
        if pnl < 0 and not in_drawdown:
            in_drawdown = True
            dd_start_idx = i
        elif pnl > 0 and in_drawdown:
            recovery_times.append(i - dd_start_idx)
            in_drawdown = False

    if not recovery_times:
        return 7.0  # No drawdowns = pretty good

    avg_recovery = sum(recovery_times) / len(recovery_times)
    # 1 trade recovery = excellent (10), 5+ = poor
    recovery_score = max(0, 10 - avg_recovery * 2)

    # Regime transition: trades during regime changes
    regime_trades = [t for t in trades if t.get("regime_changes", 0) > 0]
    if regime_trades:
        regime_wr = sum(1 for t in regime_trades if t.get("pnl_pct", 0) > 0) / len(regime_trades)
        regime_score = regime_wr * 10
    else:
        regime_score = 5.0

    return min(10.0, recovery_score * 0.6 + regime_score * 0.4)


# ─── SCORE ENGINE ─────────────────────────────────────────────────────────────

def compute_score(trades: list[dict], agent: dict) -> dict:
    """Compute the full ZERO Score with all components."""
    trade_count = len(trades)

    # Components
    components = {
        "immune":      round(_immune_score(trades, agent), 1),
        "discipline":  round(_discipline_score(trades, agent), 1),
        "performance": round(_performance_score(trades), 1),
        "consistency": round(_consistency_score(trades), 1),
        "resilience":  round(_resilience_score(trades), 1),
    }

    # Weighted total
    raw_total = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)

    # Bayesian convergence
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

    # Time decay
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
        if days_inactive > 3:  # Grace period
            decay_factor = 0.5 ** ((days_inactive - 3) / DECAY_HALFLIFE_DAYS)

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
        "components": components,
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

    # Weighted average (higher scores weight more)
    total_weight = 0
    weighted_sum = 0
    for a in agent_scores:
        s = a.get("score", 0)
        weight = max(s, 1)
        weighted_sum += s * weight
        total_weight += weight

    avg = weighted_sum / max(total_weight, 1)

    # Diversity bonus: more agents = bonus (up to 15%)
    agent_count = len(agent_scores)
    diversity = min(DIVERSITY_BONUS_MAX, (agent_count - 1) * 0.05)

    # Uptime factor
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


# ─── SCORE DISPLAY (Tier 2 — operator sees scores, not weights) ──────────────

def format_score_display(result: dict) -> str:
    """Format score for CLI display. Shows components but not weights."""
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
    lines.append(f"  {'█' * int(score / 10 * bar_width)}{'░' * (bar_width - int(score / 10 * bar_width))}")
    lines.append("")

    # Components (names + scores, NO weights)
    lines.append("  breakdown:")
    labels = {
        "immune":      "immune",
        "discipline":  "discipline",
        "performance": "performance",
        "consistency": "consistency",
        "resilience":  "resilience",
    }
    qualitative = {
        (9, 11): "excellent",
        (7, 9): "good",
        (5, 7): "developing",
        (3, 5): "needs work",
        (0, 3): "critical",
    }

    for key, label in labels.items():
        val = components.get(key, 0)
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


# ─── SKILL DECOMPOSITION (from compounding upgrade 7) ────────────────────────

def get_weakest_component(result: dict) -> dict:
    """Identify weakest component with actionable advice."""
    components = result.get("components", {})
    if not components:
        return {"component": "unknown", "score": 0, "fix": "keep trading"}

    weakest = min(components, key=lambda k: components[k])
    score = components[weakest]

    fixes = {
        "immune":      "ensure every position has a stop. never disable the immune system.",
        "discipline":  "don't override the machine. let it reject signals. stability > speed.",
        "performance": "focus on risk management. smaller drawdowns = higher performance score.",
        "consistency": "trade in all conditions, not just trending. diversify across regimes.",
        "resilience":  "exit faster during regime transitions. recovery speed matters.",
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
        "stop_verification_rate": 1.0,
        "immune_saves": 0,
        "immune_checks": len(trades) * 10,
        "total_evaluations": max(len(trades) * 50, 1),
        "manual_overrides": 0,
        "config_changes": 0,
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

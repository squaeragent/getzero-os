"""
Operator progression system — streaks, milestones, score.
All calculations from local session_history. No network required.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StreakInfo:
    current: int = 0            # consecutive profitable sessions
    best: int = 0               # all-time best streak
    streak_type: str = "none"   # "winning", "losing", "none"
    badge: Optional[str] = None # "bronze" (3), "silver" (5), "gold" (10), "diamond" (20)
    sessions_to_next: int = 0   # sessions until next badge


@dataclass
class Milestone:
    id: str
    name: str
    description: str
    achieved: bool = False
    achieved_at: Optional[str] = None
    progress: float = 0.0       # 0.0-1.0 for incomplete milestones


@dataclass
class ScoreCard:
    """5-dimension scoring."""
    performance: float = 0.0    # 0-100
    discipline: float = 0.0     # 0-100
    protection: float = 0.0     # 0-100
    consistency: float = 0.0    # 0-100
    adaptation: float = 0.0     # 0-100
    total: float = 0.0          # weighted average
    class_name: str = "novice"  # novice/apprentice/operator/veteran/elite


@dataclass
class Reputation:
    """Aggregate reputation from all dimensions."""
    score: ScoreCard = field(default_factory=ScoreCard)
    streak: StreakInfo = field(default_factory=StreakInfo)
    milestones_earned: int = 0
    milestones_total: int = 0
    sessions_completed: int = 0
    total_trades: int = 0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    favorite_strategy: str = "none"


# ── Badge thresholds ──────────────────────────────────────────────────────────

BADGE_THRESHOLDS = [
    (20, "diamond"),
    (10, "gold"),
    (5, "silver"),
    (3, "bronze"),
]


def _badge_for_streak(streak: int) -> Optional[str]:
    for threshold, badge in BADGE_THRESHOLDS:
        if streak >= threshold:
            return badge
    return None


def _sessions_to_next_badge(streak: int) -> int:
    for threshold, _ in reversed(BADGE_THRESHOLDS):
        if streak < threshold:
            return threshold - streak
    return 0  # already at diamond


# ── Milestone definitions ─────────────────────────────────────────────────────

def _max_consecutive_profitable(history: list) -> int:
    """Count longest consecutive profitable sessions."""
    best = 0
    current = 0
    for s in history:
        if s.get("total_pnl_pct", s.get("pnl", 0)) > 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _total_trades(history: list) -> int:
    return sum(s.get("trade_count", s.get("trades", 0)) for s in history)


def _unique_strategies(history: list) -> set:
    return {s.get("strategy") for s in history if s.get("strategy")}


def _win_rate(history: list) -> float:
    total = _total_trades(history)
    if total < 1:
        return 0.0
    wins = sum(s.get("wins", 0) for s in history)
    return (wins / total) * 100


def _has_profitable(history: list) -> bool:
    return any(s.get("total_pnl_pct", s.get("pnl", 0)) > 0 for s in history)


def _has_losing(history: list) -> bool:
    return any(s.get("total_pnl_pct", s.get("pnl", 0)) < 0 for s in history)


def _has_mode(history: list, mode: str) -> bool:
    return any(s.get("mode") == mode for s in history)


def _is_night_session(session: dict) -> bool:
    """Check if session ran entirely overnight (start after 22:00, end before 08:00)."""
    started = session.get("started_at", "")
    completed = session.get("completed_at", "")
    if not started or not completed:
        return False
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = datetime.fromisoformat(completed)
        return start_dt.hour >= 22 and end_dt.hour < 8
    except (ValueError, TypeError):
        return False


def _has_night_profit(history: list) -> bool:
    return any(
        _is_night_session(s) and s.get("total_pnl_pct", s.get("pnl", 0)) > 0
        for s in history
    )


def _survived_circuit(history: list) -> bool:
    """Check if any session had a circuit breaker event and ended profitable."""
    for s in history:
        had_circuit = s.get("circuit_breaker", False) or s.get("had_circuit_breaker", False)
        profitable = s.get("total_pnl_pct", s.get("pnl", 0)) > 0
        if had_circuit and profitable:
            return True
    return False


MILESTONE_DEFS = [
    # First steps
    {"id": "first_session", "name": "Ignition", "desc": "deployed your first session",
     "check": lambda h: len(h) >= 1, "progress": lambda h: min(1.0, len(h) / 1)},
    {"id": "first_profit", "name": "First Blood", "desc": "first profitable session",
     "check": _has_profitable, "progress": lambda h: 1.0 if _has_profitable(h) else 0.0},
    {"id": "first_loss", "name": "Battle Scars", "desc": "first losing session (stops worked)",
     "check": _has_losing, "progress": lambda h: 1.0 if _has_losing(h) else 0.0},
    # Volume
    {"id": "10_sessions", "name": "Getting Started", "desc": "completed 10 sessions",
     "check": lambda h: len(h) >= 10, "progress": lambda h: min(1.0, len(h) / 10)},
    {"id": "50_sessions", "name": "Seasoned", "desc": "completed 50 sessions",
     "check": lambda h: len(h) >= 50, "progress": lambda h: min(1.0, len(h) / 50)},
    {"id": "100_trades", "name": "Century", "desc": "100 trades executed",
     "check": lambda h: _total_trades(h) >= 100,
     "progress": lambda h: min(1.0, _total_trades(h) / 100)},
    # Strategy diversity
    {"id": "3_strategies", "name": "Versatile", "desc": "used 3 different strategies",
     "check": lambda h: len(_unique_strategies(h)) >= 3,
     "progress": lambda h: min(1.0, len(_unique_strategies(h)) / 3)},
    {"id": "all_strategies", "name": "Full Roster", "desc": "used all 9 strategies",
     "check": lambda h: len(_unique_strategies(h)) >= 9,
     "progress": lambda h: min(1.0, len(_unique_strategies(h)) / 9)},
    # Streaks
    {"id": "streak_3", "name": "Hot Hand", "desc": "3 profitable sessions in a row",
     "check": lambda h: _max_consecutive_profitable(h) >= 3,
     "progress": lambda h: min(1.0, _max_consecutive_profitable(h) / 3)},
    {"id": "streak_10", "name": "Unstoppable", "desc": "10 profitable sessions in a row",
     "check": lambda h: _max_consecutive_profitable(h) >= 10,
     "progress": lambda h: min(1.0, _max_consecutive_profitable(h) / 10)},
    # Performance
    {"id": "wr_70", "name": "Sharpshooter", "desc": "70%+ win rate over 20+ trades",
     "check": lambda h: _total_trades(h) >= 20 and _win_rate(h) >= 70,
     "progress": lambda h: min(1.0, _total_trades(h) / 20) * 0.5 + (min(1.0, _win_rate(h) / 70) * 0.5) if _total_trades(h) > 0 else 0.0},
    {"id": "survived_circuit", "name": "Survivor", "desc": "survived a circuit breaker and came back profitable",
     "check": _survived_circuit, "progress": lambda h: 1.0 if _survived_circuit(h) else 0.0},
    # Modes
    {"id": "tried_sport", "name": "Engaged", "desc": "used sport mode",
     "check": lambda h: _has_mode(h, "sport"),
     "progress": lambda h: 1.0 if _has_mode(h, "sport") else 0.0},
    {"id": "tried_track", "name": "Driver", "desc": "used track mode (manual approval)",
     "check": lambda h: _has_mode(h, "track"),
     "progress": lambda h: 1.0 if _has_mode(h, "track") else 0.0},
    # Special
    {"id": "night_trader", "name": "Night Owl", "desc": "profitable session that ran entirely overnight",
     "check": _has_night_profit, "progress": lambda h: 1.0 if _has_night_profit(h) else 0.0},
]


# ── Scoring formulas ──────────────────────────────────────────────────────────

def _calc_performance(history: list) -> float:
    """Performance score: win rate + PnL bonus."""
    if not history:
        return 0.0
    wr = _win_rate(history)
    base = wr
    total_pnl_pct = sum(s.get("total_pnl_pct", s.get("pnl", 0)) for s in history)
    pnl_bonus = min(20, total_pnl_pct * 5)
    return max(0.0, min(100.0, base + pnl_bonus))


def _calc_discipline(history: list) -> float:
    """Discipline score: session completion + stop adherence."""
    if not history:
        return 0.0
    completed = sum(1 for s in history if s.get("completed_at") or s.get("state") == "completed")
    abandoned = sum(1 for s in history if s.get("state") == "abandoned" or s.get("abandoned", False))
    total = completed + abandoned
    completion_pct = (completed / total * 100) if total > 0 else 100.0

    # Stop adherence: approximate from losses that hit stop vs total losses
    total_losses = sum(s.get("losses", 0) for s in history)
    stops_triggered = sum(s.get("stops_triggered", s.get("losses", 0)) for s in history)
    stop_adherence = (stops_triggered / total_losses * 100) if total_losses > 0 else 100.0

    return max(0.0, min(100.0, (completion_pct + stop_adherence) / 2))


def _calc_protection(history: list) -> float:
    """Protection score: drawdown control + loss control."""
    if not history:
        return 0.0
    max_dd = max((abs(s.get("max_drawdown_pct", 0)) for s in history), default=0)
    max_dd_score = max(0, 100 - max_dd * 5)

    losses = [s.get("total_pnl_pct", s.get("pnl", 0)) for s in history
              if s.get("total_pnl_pct", s.get("pnl", 0)) < 0]
    if losses:
        avg_loss = abs(statistics.mean(losses))
        avg_loss_controlled = 100 if avg_loss < 5 else 50
    else:
        avg_loss_controlled = 100

    return max(0.0, min(100.0, (max_dd_score + avg_loss_controlled) / 2))


def _calc_consistency(history: list) -> float:
    """Consistency score: low variance = high consistency."""
    if len(history) < 2:
        return 50.0 if history else 0.0
    pnls = [s.get("total_pnl_pct", s.get("pnl", 0)) for s in history]
    try:
        pnl_std = statistics.stdev(pnls)
    except statistics.StatisticsError:
        return 50.0
    return max(0.0, min(100.0, 100 - pnl_std * 20))


def _calc_adaptation(history: list) -> float:
    """Adaptation score: strategy diversity + regime matching."""
    if not history:
        return 0.0
    strategy_count = len(_unique_strategies(history))
    diversity_score = (strategy_count / 9) * 50

    # Regime matching: sessions where strategy matched regime
    matched = 0
    total = 0
    for s in history:
        regime = s.get("regime")
        if regime:
            total += 1
            if s.get("regime_matched", False):
                matched += 1
    regime_score = (matched / total * 50) if total > 0 else 25  # default 25 if no regime data

    return max(0.0, min(100.0, diversity_score + regime_score))


CLASS_BOUNDARIES = [
    (80, "elite"),
    (60, "veteran"),
    (40, "operator"),
    (20, "apprentice"),
    (0, "novice"),
]


def _class_for_score(total: float) -> str:
    for threshold, name in CLASS_BOUNDARIES:
        if total >= threshold:
            return name
    return "novice"


# ── ProgressionEngine ─────────────────────────────────────────────────────────

class ProgressionEngine:
    """Calculates all progression metrics from session history."""

    def __init__(self, api, operator_id: str = "op_default"):
        self.api = api
        self.operator_id = operator_id

    def _get_history(self) -> list:
        """Fetch full session history."""
        result = self.api.session_history(self.operator_id, limit=9999)
        return result.get("sessions", [])

    def get_streak(self) -> StreakInfo:
        """Calculate current streak from session_history."""
        history = self._get_history()
        if not history:
            return StreakInfo()

        # Sessions are most-recent-first typically; we need chronological
        # Check if sorted by started_at, reverse if needed
        sessions = list(history)

        # Count current streak from most recent session backwards
        current_winning = 0
        current_losing = 0
        for s in sessions:
            pnl = s.get("total_pnl_pct", s.get("pnl", 0))
            if pnl > 0:
                current_winning += 1
            else:
                break

        if current_winning == 0:
            for s in sessions:
                pnl = s.get("total_pnl_pct", s.get("pnl", 0))
                if pnl <= 0:
                    current_losing += 1
                else:
                    break

        current = current_winning if current_winning > 0 else current_losing
        streak_type = "winning" if current_winning > 0 else ("losing" if current_losing > 0 else "none")

        # Best all-time winning streak (chronological scan)
        best = _max_consecutive_profitable(reversed(sessions))

        badge = _badge_for_streak(current if streak_type == "winning" else 0)
        winning_for_badge = current if streak_type == "winning" else 0
        sessions_to_next = _sessions_to_next_badge(winning_for_badge)

        return StreakInfo(
            current=current,
            best=best,
            streak_type=streak_type,
            badge=badge,
            sessions_to_next=sessions_to_next,
        )

    def get_milestones(self) -> list[Milestone]:
        """Check all milestones against session history."""
        history = self._get_history()
        milestones = []
        for mdef in MILESTONE_DEFS:
            achieved = mdef["check"](history)
            progress = mdef["progress"](history) if not achieved else 1.0

            # Try to find when achieved (first session that would have tipped it)
            achieved_at = None
            if achieved and history:
                # Use the most recent session's completed_at as approximation
                achieved_at = history[0].get("completed_at")

            milestones.append(Milestone(
                id=mdef["id"],
                name=mdef["name"],
                description=mdef["desc"],
                achieved=achieved,
                achieved_at=achieved_at,
                progress=round(progress, 2),
            ))
        return milestones

    def get_achievements(self) -> list[Milestone]:
        """Milestones that are achieved=True."""
        return [m for m in self.get_milestones() if m.achieved]

    def get_score(self) -> ScoreCard:
        """Calculate 5-dimension score."""
        history = self._get_history()

        perf = _calc_performance(history)
        disc = _calc_discipline(history)
        prot = _calc_protection(history)
        cons = _calc_consistency(history)
        adapt = _calc_adaptation(history)

        total = (
            perf * 0.30 +
            disc * 0.20 +
            prot * 0.20 +
            cons * 0.15 +
            adapt * 0.15
        )
        total = round(total, 1)

        return ScoreCard(
            performance=round(perf, 1),
            discipline=round(disc, 1),
            protection=round(prot, 1),
            consistency=round(cons, 1),
            adaptation=round(adapt, 1),
            total=total,
            class_name=_class_for_score(total),
        )

    def get_reputation(self) -> Reputation:
        """Full reputation combining score + streak + milestones + stats."""
        history = self._get_history()
        score = self.get_score()
        streak = self.get_streak()
        milestones = self.get_milestones()

        earned = sum(1 for m in milestones if m.achieved)
        total_ms = len(milestones)
        total_trades = _total_trades(history)

        # Best and worst trade PnL (session-level approximation)
        pnls = [s.get("total_pnl_pct", s.get("pnl", 0)) for s in history]
        best_pnl = max(pnls) if pnls else 0.0
        worst_pnl = min(pnls) if pnls else 0.0

        # Favorite strategy
        strat_counts: dict[str, int] = {}
        for s in history:
            strat = s.get("strategy", "unknown")
            strat_counts[strat] = strat_counts.get(strat, 0) + 1
        favorite = max(strat_counts, key=strat_counts.get) if strat_counts else "none"

        return Reputation(
            score=score,
            streak=streak,
            milestones_earned=earned,
            milestones_total=total_ms,
            sessions_completed=len(history),
            total_trades=total_trades,
            best_trade_pnl=round(best_pnl, 2),
            worst_trade_pnl=round(worst_pnl, 2),
            favorite_strategy=favorite,
        )

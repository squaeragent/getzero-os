#!/usr/bin/env python3
"""
Pattern recognition from operator session history.
Learns what works for this specific operator.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SessionComparison:
    """How this session compares to operator's history."""
    session_number: int        # e.g., "momentum session #8"
    strategy: str
    pnl: float
    pnl_vs_average: float     # +1.30 means $1.30 above average
    wr: float
    wr_vs_average: float      # +6.0 means 6% above average
    is_best: bool             # is this the best session for this strategy?
    is_worst: bool
    rank: str                 # "top 20%", "average", "bottom 20%"
    narrative: str            # personalized one-line insight

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyProfile:
    """Operator's profile with a specific strategy."""
    strategy: str
    sessions: int
    total_trades: int
    avg_pnl: float
    avg_wr: float
    best_pnl: float
    worst_pnl: float
    preferred_regime: str     # regime where this strategy worked best
    worst_regime: str         # regime where it worked worst

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OperatorInsight:
    """Deep insight about operator's trading patterns."""
    insight_type: str         # "regime_affinity", "time_pattern", "strategy_edge"
    title: str                # "You're a Fear Trader"
    description: str          # "Your best results come when fear & greed < 30"
    confidence: float         # 0.0-1.0
    data_points: int          # sessions supporting this insight
    recommendation: str       # "Consider degen during extreme fear periods"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_pnl(s: dict) -> float:
    return s.get("total_pnl_usd", 0.0)


def _session_wr(s: dict) -> float:
    trades = s.get("trade_count", 0)
    wins = s.get("wins", 0)
    if trades == 0:
        return 0.0
    return (wins / trades) * 100.0


def _session_strategy(s: dict) -> str:
    return s.get("strategy", "unknown")


def _session_regime(s: dict) -> str:
    return s.get("regime", "unknown")


def _session_start_hour(s: dict) -> int:
    started = s.get("started_at", "")
    if not started:
        return 12
    try:
        dt = datetime.fromisoformat(started)
        return dt.hour
    except (ValueError, TypeError):
        return 12


def _session_duration_hours(s: dict) -> float:
    secs = s.get("duration_actual_s", 0)
    return secs / 3600.0 if secs else 0.0


def _rank_label(position: int, total: int) -> str:
    """Convert position/total to rank label."""
    if total <= 1:
        return "first session"
    pct = (position - 1) / (total - 1)  # 0.0 = best, 1.0 = worst
    if pct <= 0.2:
        return "top 20%"
    if pct >= 0.8:
        return "bottom 20%"
    return "average"


# ── Pattern Engine ────────────────────────────────────────────────────────────

class PatternEngine:
    """Analyzes operator history for patterns and insights."""

    def __init__(self, api):
        self.api = api

    def _get_history(self, operator_id: str, limit: int = 100) -> list[dict]:
        result = self.api.session_history(operator_id, limit=limit)
        return result.get("sessions", [])

    def compare_session(self, operator_id: str, session: dict) -> SessionComparison:
        """Compare a completed session to operator's history."""
        history = self._get_history(operator_id)
        strategy = _session_strategy(session)
        pnl = _session_pnl(session)
        wr = _session_wr(session)

        # Filter history for same strategy
        strat_history = [s for s in history if _session_strategy(s) == strategy]
        session_number = len(strat_history)

        if not strat_history:
            return SessionComparison(
                session_number=1,
                strategy=strategy,
                pnl=pnl,
                pnl_vs_average=0.0,
                wr=wr,
                wr_vs_average=0.0,
                is_best=True,
                is_worst=True,
                rank="first session",
                narrative=f"first {strategy} session. tracking.",
            )

        # Averages
        avg_pnl = sum(_session_pnl(s) for s in strat_history) / len(strat_history)
        avg_wr = sum(_session_wr(s) for s in strat_history) / len(strat_history)

        # Best/worst
        best_pnl = max(_session_pnl(s) for s in strat_history)
        worst_pnl = min(_session_pnl(s) for s in strat_history)
        is_best = pnl > best_pnl
        is_worst = pnl < worst_pnl

        # Rank by PnL
        all_pnls = sorted([_session_pnl(s) for s in strat_history] + [pnl], reverse=True)
        position = all_pnls.index(pnl) + 1
        rank = _rank_label(position, len(all_pnls))

        pnl_vs_average = round(pnl - avg_pnl, 2)
        wr_vs_average = round(wr - avg_wr, 1)

        narrative = self._generate_narrative(
            strategy, session_number, pnl, pnl_vs_average,
            wr_vs_average, is_best, is_worst, rank, best_pnl,
            strat_history,
        )

        return SessionComparison(
            session_number=session_number + 1,
            strategy=strategy,
            pnl=round(pnl, 2),
            pnl_vs_average=pnl_vs_average,
            wr=round(wr, 1),
            wr_vs_average=wr_vs_average,
            is_best=is_best,
            is_worst=is_worst,
            rank=rank,
            narrative=narrative,
        )

    def get_strategy_profile(self, operator_id: str, strategy: str) -> Optional[StrategyProfile]:
        """Get operator's profile with a specific strategy. Needs 3+ sessions."""
        history = self._get_history(operator_id)
        strat_sessions = [s for s in history if _session_strategy(s) == strategy]

        if len(strat_sessions) < 3:
            return None

        pnls = [_session_pnl(s) for s in strat_sessions]
        wrs = [_session_wr(s) for s in strat_sessions]
        total_trades = sum(s.get("trade_count", 0) for s in strat_sessions)

        # Regime analysis
        regime_pnls: dict[str, list[float]] = {}
        for s in strat_sessions:
            regime = _session_regime(s)
            regime_pnls.setdefault(regime, []).append(_session_pnl(s))

        preferred_regime = "unknown"
        worst_regime = "unknown"
        if regime_pnls:
            regime_avgs = {r: sum(ps) / len(ps) for r, ps in regime_pnls.items()}
            preferred_regime = max(regime_avgs, key=regime_avgs.get)
            worst_regime = min(regime_avgs, key=regime_avgs.get)

        return StrategyProfile(
            strategy=strategy,
            sessions=len(strat_sessions),
            total_trades=total_trades,
            avg_pnl=round(sum(pnls) / len(pnls), 2),
            avg_wr=round(sum(wrs) / len(wrs), 1),
            best_pnl=round(max(pnls), 2),
            worst_pnl=round(min(pnls), 2),
            preferred_regime=preferred_regime,
            worst_regime=worst_regime,
        )

    def get_insights(self, operator_id: str) -> list[OperatorInsight]:
        """Generate personalized insights from history. Needs 5+ sessions."""
        history = self._get_history(operator_id)
        if len(history) < 5:
            return []

        insights: list[OperatorInsight] = []
        insights.extend(self._detect_regime_affinity(history))
        insights.extend(self._detect_strategy_edge(history))
        insights.extend(self._detect_time_pattern(history))
        insights.extend(self._detect_hold_duration(history))
        insights.extend(self._detect_loss_recovery(history))

        # Sort by confidence descending
        insights.sort(key=lambda i: i.confidence, reverse=True)
        return insights

    def get_recommendation(self, operator_id: str) -> dict:
        """Personalized recommendation based on patterns."""
        history = self._get_history(operator_id)
        if len(history) < 3:
            return {
                "recommendation": None,
                "reason": "need more sessions",
                "sessions_completed": len(history),
                "sessions_needed": 3,
            }

        # Best strategy by average PnL
        strat_pnls: dict[str, list[float]] = {}
        for s in history:
            strat = _session_strategy(s)
            strat_pnls.setdefault(strat, []).append(_session_pnl(s))

        strat_avgs = {
            strat: sum(ps) / len(ps)
            for strat, ps in strat_pnls.items()
            if len(ps) >= 2
        }

        if not strat_avgs:
            return {
                "recommendation": None,
                "reason": "need more variety",
                "sessions_completed": len(history),
            }

        best_strat = max(strat_avgs, key=strat_avgs.get)
        worst_strat = min(strat_avgs, key=strat_avgs.get)

        return {
            "recommendation": best_strat,
            "avg_pnl": round(strat_avgs[best_strat], 2),
            "avoid": worst_strat if strat_avgs[worst_strat] < 0 else None,
            "sessions_analyzed": len(history),
        }

    # ── Insight Detectors ─────────────────────────────────────────────────

    def _detect_regime_affinity(self, history: list[dict]) -> list[OperatorInsight]:
        """Does operator perform better in certain regimes?"""
        regime_wrs: dict[str, list[float]] = {}
        for s in history:
            regime = _session_regime(s)
            if regime == "unknown":
                continue
            regime_wrs.setdefault(regime, []).append(_session_wr(s))

        if len(regime_wrs) < 2:
            return []

        regime_avgs = {r: sum(ws) / len(ws) for r, ws in regime_wrs.items() if len(ws) >= 2}
        if len(regime_avgs) < 2:
            return []

        best_regime = max(regime_avgs, key=regime_avgs.get)
        worst_regime = min(regime_avgs, key=regime_avgs.get)
        spread = regime_avgs[best_regime] - regime_avgs[worst_regime]

        if spread < 15:
            return []

        data_points = sum(len(ws) for ws in regime_wrs.values())
        confidence = min(1.0, spread / 40.0) * min(1.0, data_points / 10)

        return [OperatorInsight(
            insight_type="regime_affinity",
            title=f"you're a {best_regime} market trader",
            description=(
                f"WR: {regime_avgs[best_regime]:.0f}% in {best_regime}, "
                f"{regime_avgs[worst_regime]:.0f}% in {worst_regime}."
            ),
            confidence=round(confidence, 2),
            data_points=data_points,
            recommendation=f"favor sessions during {best_regime} conditions.",
        )]

    def _detect_strategy_edge(self, history: list[dict]) -> list[OperatorInsight]:
        """Does operator have an edge with specific strategies?"""
        strat_wrs: dict[str, list[float]] = {}
        for s in history:
            strat = _session_strategy(s)
            strat_wrs.setdefault(strat, []).append(_session_wr(s))

        if len(strat_wrs) < 2:
            return []

        strat_avgs = {st: sum(ws) / len(ws) for st, ws in strat_wrs.items() if len(ws) >= 2}
        if len(strat_avgs) < 2:
            return []

        overall_avg = sum(strat_avgs.values()) / len(strat_avgs)
        best_strat = max(strat_avgs, key=strat_avgs.get)
        edge = strat_avgs[best_strat] - overall_avg

        if edge < 10:
            return []

        data_points = len(strat_wrs[best_strat])
        confidence = min(1.0, edge / 25.0) * min(1.0, data_points / 8)

        return [OperatorInsight(
            insight_type="strategy_edge",
            title=f"{best_strat} is your edge",
            description=(
                f"{strat_avgs[best_strat]:.0f}% WR vs {overall_avg:.0f}% average."
            ),
            confidence=round(confidence, 2),
            data_points=data_points,
            recommendation=f"lean into {best_strat} when conditions allow.",
        )]

    def _detect_time_pattern(self, history: list[dict]) -> list[OperatorInsight]:
        """Do sessions started at certain times perform better?"""
        # Group by time bucket: morning (6-12), afternoon (12-18), evening (18-24), night (0-6)
        buckets: dict[str, list[float]] = {
            "morning": [], "afternoon": [], "evening": [], "night": [],
        }
        for s in history:
            hour = _session_start_hour(s)
            pnl = _session_pnl(s)
            if 6 <= hour < 12:
                buckets["morning"].append(pnl)
            elif 12 <= hour < 18:
                buckets["afternoon"].append(pnl)
            elif 18 <= hour < 24:
                buckets["evening"].append(pnl)
            else:
                buckets["night"].append(pnl)

        filled = {b: ps for b, ps in buckets.items() if len(ps) >= 2}
        if len(filled) < 2:
            return []

        bucket_avgs = {b: sum(ps) / len(ps) for b, ps in filled.items()}
        best_bucket = max(bucket_avgs, key=bucket_avgs.get)
        worst_bucket = min(bucket_avgs, key=bucket_avgs.get)

        if bucket_avgs[best_bucket] <= 0:
            return []

        spread = bucket_avgs[best_bucket] - bucket_avgs[worst_bucket]
        if spread < 1.0:  # need $1+ difference
            return []

        data_points = len(filled[best_bucket])
        confidence = min(1.0, spread / 5.0) * min(1.0, data_points / 5)

        return [OperatorInsight(
            insight_type="time_pattern",
            title=f"your {best_bucket} sessions outperform",
            description=(
                f"{best_bucket} avg: ${bucket_avgs[best_bucket]:+.2f}. "
                f"{worst_bucket} avg: ${bucket_avgs[worst_bucket]:+.2f}."
            ),
            confidence=round(confidence, 2),
            data_points=sum(len(ps) for ps in filled.values()),
            recommendation=f"consider starting sessions in the {best_bucket}.",
        )]

    def _detect_hold_duration(self, history: list[dict]) -> list[OperatorInsight]:
        """Does operator benefit from longer or shorter sessions?"""
        short_pnls: list[float] = []  # < 36h
        long_pnls: list[float] = []   # >= 36h

        for s in history:
            dur = _session_duration_hours(s)
            pnl = _session_pnl(s)
            if dur <= 0:
                continue
            if dur < 36:
                short_pnls.append(pnl)
            else:
                long_pnls.append(pnl)

        if len(short_pnls) < 2 or len(long_pnls) < 2:
            return []

        short_avg = sum(short_pnls) / len(short_pnls)
        long_avg = sum(long_pnls) / len(long_pnls)
        spread = abs(short_avg - long_avg)

        if spread < 1.0:
            return []

        better = "shorter" if short_avg > long_avg else "longer"
        better_avg = max(short_avg, long_avg)
        worse_avg = min(short_avg, long_avg)

        data_points = len(short_pnls) + len(long_pnls)
        confidence = min(1.0, spread / 5.0) * min(1.0, data_points / 8)

        if better == "shorter":
            rec = "consider degen (24h) over momentum (48h)."
        else:
            rec = "consider defense (168h) for longer hold windows."

        return [OperatorInsight(
            insight_type="hold_duration",
            title=f"{better} sessions work better for you",
            description=(
                f"{better} avg: ${better_avg:+.2f}. "
                f"other avg: ${worse_avg:+.2f}."
            ),
            confidence=round(confidence, 2),
            data_points=data_points,
            recommendation=rec,
        )]

    def _detect_loss_recovery(self, history: list[dict]) -> list[OperatorInsight]:
        """How does operator perform after a loss?"""
        if len(history) < 5:
            return []

        # History is newest-first from API, reverse for chronological
        chronological = list(reversed(history))
        post_loss_pnls: list[float] = []
        normal_pnls: list[float] = []

        for i in range(1, len(chronological)):
            prev_pnl = _session_pnl(chronological[i - 1])
            curr_pnl = _session_pnl(chronological[i])
            if prev_pnl < 0:
                post_loss_pnls.append(curr_pnl)
            else:
                normal_pnls.append(curr_pnl)

        if len(post_loss_pnls) < 2 or len(normal_pnls) < 2:
            return []

        post_loss_avg = sum(post_loss_pnls) / len(post_loss_pnls)
        normal_avg = sum(normal_pnls) / len(normal_pnls)

        spread = post_loss_avg - normal_avg
        if abs(spread) < 0.5:
            return []

        data_points = len(post_loss_pnls)
        confidence = min(1.0, abs(spread) / 3.0) * min(1.0, data_points / 5)

        if spread > 0:
            title = "you recover well after losses"
            desc = f"sessions after a loss: ${post_loss_avg:+.2f} avg."
            rec = "your discipline holds. keep trading through drawdowns."
        else:
            title = "losses affect your next session"
            desc = (
                f"after a loss, avg drops to ${post_loss_avg:+.2f} "
                f"(normal: ${normal_avg:+.2f})."
            )
            rec = "consider defense after a losing session."

        return [OperatorInsight(
            insight_type="loss_recovery",
            title=title,
            description=desc,
            confidence=round(confidence, 2),
            data_points=data_points,
            recommendation=rec,
        )]

    # ── Narrative Generation ──────────────────────────────────────────────

    def _generate_narrative(
        self, strategy: str, session_number: int,
        pnl: float, pnl_vs_avg: float, wr_vs_avg: float,
        is_best: bool, is_worst: bool, rank: str,
        prev_best: float, history: list[dict],
    ) -> str:
        """Generate personalized narrative for session result."""
        if is_best:
            return (
                f"your best {strategy} session. "
                f"${pnl:+.2f} beats your previous best of ${prev_best:+.2f}."
            )

        if is_worst:
            return (
                f"your worst {strategy} session. "
                f"${pnl:+.2f}. review conditions before next deploy."
            )

        if pnl_vs_avg > 0 and wr_vs_avg > 0:
            return (
                f"above average on both P&L and win rate. "
                f"{strategy} suits current conditions."
            )

        if pnl_vs_avg > 0:
            return f"P&L above average by ${pnl_vs_avg:+.2f}. solid {strategy} session."

        if rank == "top 20%":
            return f"top 20% of your {strategy} sessions."

        if pnl_vs_avg < 0 and len(history) > 5:
            return (
                f"below average by ${abs(pnl_vs_avg):.2f}. "
                f"check if regime favors {strategy} right now."
            )

        return f"{strategy} session #{session_number + 1}. tracking."

#!/usr/bin/env python3
"""
Tests for PatternEngine — session comparison, insights, and narrative generation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.pattern_engine import (
    PatternEngine,
    SessionComparison,
    StrategyProfile,
    OperatorInsight,
    _session_pnl,
    _session_wr,
    _rank_label,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session(
    strategy: str = "momentum",
    pnl: float = 2.0,
    trades: int = 5,
    wins: int = 3,
    regime: str = "trending",
    started_at: str = "2026-03-20T14:00:00+00:00",
    duration_s: float = 172800,
) -> dict:
    return {
        "session_id": f"sess_{id(strategy)}",
        "strategy": strategy,
        "total_pnl_usd": pnl,
        "trade_count": trades,
        "wins": wins,
        "losses": trades - wins,
        "regime": regime,
        "started_at": started_at,
        "duration_actual_s": duration_s,
    }


def _make_api(sessions: list[dict]) -> MagicMock:
    api = MagicMock()
    api.session_history.return_value = {"sessions": sessions}
    return api


OP = "op_test"


# ══════════════════════════════════════════════════════════════════════════════
# compare_session
# ══════════════════════════════════════════════════════════════════════════════

class TestCompareSession:

    def test_first_session(self):
        """First session for a strategy should be marked as both best and worst."""
        api = _make_api([])
        engine = PatternEngine(api)
        session = _make_session(pnl=3.0)
        result = engine.compare_session(OP, session)
        assert isinstance(result, SessionComparison)
        assert result.session_number == 1
        assert result.is_best is True
        assert result.is_worst is True
        assert result.rank == "first session"
        assert "first" in result.narrative

    def test_with_5_sessions_history(self):
        """With 5 prior sessions, comparison should have valid averages."""
        history = [
            _make_session(pnl=1.0, wins=2, trades=5),
            _make_session(pnl=2.0, wins=3, trades=5),
            _make_session(pnl=3.0, wins=4, trades=5),
            _make_session(pnl=-1.0, wins=1, trades=5),
            _make_session(pnl=0.5, wins=2, trades=5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        # New session with +4.0 (better than all)
        session = _make_session(pnl=4.0, wins=4, trades=5)
        result = engine.compare_session(OP, session)
        assert result.session_number == 6
        assert result.is_best is True
        assert result.pnl_vs_average > 0

    def test_with_20_sessions_history(self):
        """Large history should still work correctly."""
        history = [_make_session(pnl=float(i), wins=3, trades=5) for i in range(20)]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=25.0, wins=4, trades=5)
        result = engine.compare_session(OP, session)
        assert result.session_number == 21
        assert result.is_best is True

    def test_is_best_detection(self):
        """Session with PnL above all prior should be flagged as best."""
        history = [
            _make_session(pnl=1.0),
            _make_session(pnl=2.0),
            _make_session(pnl=3.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=5.0)
        result = engine.compare_session(OP, session)
        assert result.is_best is True
        assert result.is_worst is False
        assert "best" in result.narrative

    def test_is_worst_detection(self):
        """Session with PnL below all prior should be flagged as worst."""
        history = [
            _make_session(pnl=1.0),
            _make_session(pnl=2.0),
            _make_session(pnl=3.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=-5.0)
        result = engine.compare_session(OP, session)
        assert result.is_worst is True
        assert result.is_best is False
        assert "worst" in result.narrative

    def test_rank_top_20(self):
        """Session in top 20% by PnL should have 'top 20%' rank."""
        history = [_make_session(pnl=float(i)) for i in range(10)]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=15.0)  # best
        result = engine.compare_session(OP, session)
        assert result.rank == "top 20%"

    def test_rank_bottom_20(self):
        """Session in bottom 20% by PnL should have 'bottom 20%' rank."""
        history = [_make_session(pnl=float(i)) for i in range(10)]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=-10.0)  # worst
        result = engine.compare_session(OP, session)
        assert result.rank == "bottom 20%"

    def test_filters_by_strategy(self):
        """Comparison should only consider sessions with the same strategy."""
        history = [
            _make_session(strategy="momentum", pnl=10.0),
            _make_session(strategy="degen", pnl=100.0),
            _make_session(strategy="defense", pnl=50.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(strategy="momentum", pnl=5.0)
        result = engine.compare_session(OP, session)
        # Should only compare to the 1 momentum session
        assert result.session_number == 2
        assert result.is_worst is True  # 5 < 10


class TestNarrativeGeneration:

    def test_narrative_best(self):
        history = [_make_session(pnl=2.0)]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=5.0)
        result = engine.compare_session(OP, session)
        assert "best" in result.narrative

    def test_narrative_above_average(self):
        history = [
            _make_session(pnl=1.0, wins=2, trades=5),
            _make_session(pnl=2.0, wins=3, trades=5),
            _make_session(pnl=3.0, wins=4, trades=5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        # Above average but not best (3.5 > avg 2.0, but < max 3.0... no, 3.5 > 3.0)
        # Make it between avg and max
        session = _make_session(pnl=2.5, wins=4, trades=5)
        result = engine.compare_session(OP, session)
        assert "above average" in result.narrative or "P&L above" in result.narrative

    def test_narrative_below_average(self):
        history = [_make_session(pnl=float(i + 1), wins=3, trades=5) for i in range(6)]
        api = _make_api(history)
        engine = PatternEngine(api)
        session = _make_session(pnl=0.5, wins=1, trades=5)
        result = engine.compare_session(OP, session)
        assert "below average" in result.narrative or "worst" in result.narrative

    def test_narrative_too_early(self):
        api = _make_api([])
        engine = PatternEngine(api)
        session = _make_session(pnl=1.0)
        result = engine.compare_session(OP, session)
        assert "first" in result.narrative or "tracking" in result.narrative


# ══════════════════════════════════════════════════════════════════════════════
# get_strategy_profile
# ══════════════════════════════════════════════════════════════════════════════

class TestStrategyProfile:

    def test_returns_none_with_insufficient_data(self):
        """Strategy profile needs 3+ sessions. Should return None with < 3."""
        history = [
            _make_session(strategy="momentum", pnl=1.0),
            _make_session(strategy="momentum", pnl=2.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        result = engine.get_strategy_profile(OP, "momentum")
        assert result is None

    def test_returns_profile_with_3_sessions(self):
        """Should return profile with 3+ sessions."""
        history = [
            _make_session(strategy="momentum", pnl=1.0, wins=2, trades=5, regime="trending"),
            _make_session(strategy="momentum", pnl=3.0, wins=4, trades=5, regime="trending"),
            _make_session(strategy="momentum", pnl=-1.0, wins=1, trades=5, regime="reverting"),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        result = engine.get_strategy_profile(OP, "momentum")
        assert result is not None
        assert isinstance(result, StrategyProfile)
        assert result.strategy == "momentum"
        assert result.sessions == 3
        assert result.avg_pnl == 1.0
        assert result.best_pnl == 3.0
        assert result.worst_pnl == -1.0
        assert result.preferred_regime == "trending"
        assert result.worst_regime == "reverting"

    def test_ignores_other_strategies(self):
        """Profile for 'momentum' should not include 'degen' sessions."""
        history = [
            _make_session(strategy="momentum", pnl=1.0),
            _make_session(strategy="degen", pnl=10.0),
            _make_session(strategy="degen", pnl=20.0),
            _make_session(strategy="degen", pnl=30.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        assert engine.get_strategy_profile(OP, "momentum") is None
        degen_profile = engine.get_strategy_profile(OP, "degen")
        assert degen_profile is not None
        assert degen_profile.sessions == 3


# ══════════════════════════════════════════════════════════════════════════════
# get_insights
# ══════════════════════════════════════════════════════════════════════════════

class TestGetInsights:

    def test_needs_5_sessions(self):
        """Should return empty insights with < 5 sessions."""
        history = [_make_session() for _ in range(4)]
        api = _make_api(history)
        engine = PatternEngine(api)
        result = engine.get_insights(OP)
        assert result == []

    def test_empty_history_returns_no_insights(self):
        """Empty history should return no insights."""
        api = _make_api([])
        engine = PatternEngine(api)
        result = engine.get_insights(OP)
        assert result == []

    def test_generates_insights_with_history(self):
        """With enough diverse history, should generate at least some insights."""
        history = [
            # Trending sessions - good WR
            _make_session(strategy="momentum", pnl=3.0, wins=4, trades=5, regime="trending",
                         started_at="2026-03-20T20:00:00+00:00"),
            _make_session(strategy="momentum", pnl=2.5, wins=4, trades=5, regime="trending",
                         started_at="2026-03-21T21:00:00+00:00"),
            _make_session(strategy="momentum", pnl=4.0, wins=5, trades=6, regime="trending",
                         started_at="2026-03-22T19:00:00+00:00"),
            # Reverting sessions - bad WR
            _make_session(strategy="degen", pnl=-1.0, wins=1, trades=5, regime="reverting",
                         started_at="2026-03-23T08:00:00+00:00"),
            _make_session(strategy="degen", pnl=-2.0, wins=0, trades=5, regime="reverting",
                         started_at="2026-03-24T09:00:00+00:00"),
            _make_session(strategy="degen", pnl=-1.5, wins=1, trades=5, regime="reverting",
                         started_at="2026-03-25T10:00:00+00:00"),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine.get_insights(OP)
        assert isinstance(insights, list)
        # Should find regime affinity and/or strategy edge
        types = {i.insight_type for i in insights}
        assert len(insights) > 0
        for insight in insights:
            assert isinstance(insight, OperatorInsight)
            assert 0.0 <= insight.confidence <= 1.0
            assert insight.data_points > 0

    def test_insights_sorted_by_confidence(self):
        """Insights should be returned sorted by confidence descending."""
        history = [
            _make_session(strategy="momentum", pnl=5.0, wins=5, trades=5, regime="trending",
                         started_at="2026-03-20T20:00:00+00:00"),
            _make_session(strategy="momentum", pnl=4.0, wins=4, trades=5, regime="trending",
                         started_at="2026-03-21T21:00:00+00:00"),
            _make_session(strategy="degen", pnl=-3.0, wins=0, trades=5, regime="reverting",
                         started_at="2026-03-22T08:00:00+00:00"),
            _make_session(strategy="degen", pnl=-2.0, wins=1, trades=5, regime="reverting",
                         started_at="2026-03-23T09:00:00+00:00"),
            _make_session(strategy="momentum", pnl=3.0, wins=4, trades=5, regime="trending",
                         started_at="2026-03-24T22:00:00+00:00"),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine.get_insights(OP)
        if len(insights) >= 2:
            for i in range(len(insights) - 1):
                assert insights[i].confidence >= insights[i + 1].confidence


# ══════════════════════════════════════════════════════════════════════════════
# Insight Detectors
# ══════════════════════════════════════════════════════════════════════════════

class TestRegimeAffinity:

    def test_detects_regime_affinity(self):
        """Should detect when operator is significantly better in one regime."""
        history = [
            # Trending: 80% WR
            _make_session(regime="trending", wins=4, trades=5),
            _make_session(regime="trending", wins=4, trades=5),
            _make_session(regime="trending", wins=4, trades=5),
            # Reverting: 20% WR (spread = 60% > 15% threshold)
            _make_session(regime="reverting", wins=1, trades=5),
            _make_session(regime="reverting", wins=1, trades=5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_regime_affinity(history)
        assert len(insights) == 1
        assert insights[0].insight_type == "regime_affinity"
        assert "trending" in insights[0].title

    def test_no_insight_below_threshold(self):
        """Should not generate insight if WR spread < 15%."""
        history = [
            _make_session(regime="trending", wins=3, trades=5),
            _make_session(regime="trending", wins=3, trades=5),
            _make_session(regime="reverting", wins=3, trades=5),
            _make_session(regime="reverting", wins=2, trades=5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_regime_affinity(history)
        assert len(insights) == 0


class TestStrategyEdge:

    def test_detects_strategy_edge(self):
        """Should detect when one strategy has 10%+ WR edge."""
        history = [
            _make_session(strategy="momentum", wins=4, trades=5),
            _make_session(strategy="momentum", wins=4, trades=5),
            _make_session(strategy="degen", wins=1, trades=5),
            _make_session(strategy="degen", wins=1, trades=5),
            _make_session(strategy="momentum", wins=5, trades=5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_strategy_edge(history)
        assert len(insights) == 1
        assert insights[0].insight_type == "strategy_edge"
        assert "momentum" in insights[0].title


class TestTimePattern:

    def test_detects_time_pattern(self):
        """Should detect when sessions at certain times perform better."""
        history = [
            # Evening sessions: positive
            _make_session(started_at="2026-03-20T20:00:00+00:00", pnl=5.0),
            _make_session(started_at="2026-03-21T21:00:00+00:00", pnl=4.0),
            _make_session(started_at="2026-03-22T19:00:00+00:00", pnl=6.0),
            # Morning sessions: negative
            _make_session(started_at="2026-03-23T08:00:00+00:00", pnl=-2.0),
            _make_session(started_at="2026-03-24T09:00:00+00:00", pnl=-3.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_time_pattern(history)
        assert len(insights) == 1
        assert insights[0].insight_type == "time_pattern"
        assert "evening" in insights[0].title

    def test_no_insight_when_similar(self):
        """No time pattern insight if PnL spread < $1."""
        history = [
            _make_session(started_at="2026-03-20T20:00:00+00:00", pnl=1.0),
            _make_session(started_at="2026-03-21T21:00:00+00:00", pnl=1.2),
            _make_session(started_at="2026-03-22T08:00:00+00:00", pnl=0.8),
            _make_session(started_at="2026-03-23T09:00:00+00:00", pnl=0.9),
            _make_session(started_at="2026-03-24T10:00:00+00:00", pnl=1.1),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_time_pattern(history)
        assert len(insights) == 0


class TestHoldDuration:

    def test_detects_hold_duration(self):
        """Should detect when shorter/longer sessions perform better."""
        history = [
            # Short sessions (< 36h = 129600s): good
            _make_session(duration_s=86400, pnl=5.0),   # 24h
            _make_session(duration_s=43200, pnl=4.0),   # 12h
            # Long sessions (>= 36h): bad
            _make_session(duration_s=172800, pnl=-1.0),  # 48h
            _make_session(duration_s=259200, pnl=-2.0),  # 72h
            _make_session(duration_s=604800, pnl=-0.5),  # 168h
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_hold_duration(history)
        assert len(insights) == 1
        assert insights[0].insight_type == "hold_duration"
        assert "shorter" in insights[0].title


class TestLossRecovery:

    def test_detects_recovery(self):
        """Should detect pattern in performance after losses."""
        # Chronological order (API returns newest-first, engine reverses)
        history = [
            # Newest first (as API returns)
            _make_session(pnl=3.0),   # session 5 (after loss)
            _make_session(pnl=-2.0),  # session 4 (loss)
            _make_session(pnl=4.0),   # session 3 (after loss)
            _make_session(pnl=-1.0),  # session 2 (loss)
            _make_session(pnl=2.0),   # session 1
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_loss_recovery(history)
        # Post-loss sessions: sess 3 (+4.0) and sess 5 (+3.0) → avg 3.5
        # Normal sessions: sess 4 (-2.0) and sess 2 (-1.0) → wait, let me recalculate
        # Chronological: [2.0, -1.0, 4.0, -2.0, 3.0]
        # i=1: prev=2.0 (win), curr=-1.0 → normal: [-1.0]
        # i=2: prev=-1.0 (loss), curr=4.0 → post_loss: [4.0]
        # i=3: prev=4.0 (win), curr=-2.0 → normal: [-1.0, -2.0]
        # i=4: prev=-2.0 (loss), curr=3.0 → post_loss: [4.0, 3.0]
        # post_loss avg: 3.5, normal avg: -1.5, spread: 5.0 > 0.5
        assert len(insights) == 1
        assert insights[0].insight_type == "loss_recovery"
        assert "recover" in insights[0].title

    def test_needs_enough_data(self):
        """Should not generate insight with < 5 sessions."""
        history = [
            _make_session(pnl=-1.0),
            _make_session(pnl=2.0),
            _make_session(pnl=-0.5),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        insights = engine._detect_loss_recovery(history)
        assert len(insights) == 0


# ══════════════════════════════════════════════════════════════════════════════
# get_recommendation
# ══════════════════════════════════════════════════════════════════════════════

class TestGetRecommendation:

    def test_needs_3_sessions(self):
        history = [_make_session() for _ in range(2)]
        api = _make_api(history)
        engine = PatternEngine(api)
        result = engine.get_recommendation(OP)
        assert result["recommendation"] is None
        assert "need more" in result["reason"]

    def test_recommends_best_strategy(self):
        history = [
            _make_session(strategy="momentum", pnl=5.0),
            _make_session(strategy="momentum", pnl=4.0),
            _make_session(strategy="degen", pnl=-1.0),
            _make_session(strategy="degen", pnl=-2.0),
        ]
        api = _make_api(history)
        engine = PatternEngine(api)
        result = engine.get_recommendation(OP)
        assert result["recommendation"] == "momentum"
        assert result["avoid"] == "degen"


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_session_pnl(self):
        assert _session_pnl({"total_pnl_usd": 3.5}) == 3.5
        assert _session_pnl({}) == 0.0

    def test_session_wr(self):
        assert _session_wr({"trade_count": 10, "wins": 7}) == 70.0
        assert _session_wr({"trade_count": 0, "wins": 0}) == 0.0

    def test_rank_label(self):
        assert _rank_label(1, 10) == "top 20%"
        assert _rank_label(10, 10) == "bottom 20%"
        assert _rank_label(5, 10) == "average"
        assert _rank_label(1, 1) == "first session"


# ══════════════════════════════════════════════════════════════════════════════
# Dataclass serialization
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialization:

    def test_session_comparison_to_dict(self):
        sc = SessionComparison(
            session_number=5, strategy="momentum", pnl=3.0,
            pnl_vs_average=1.0, wr=70.0, wr_vs_average=5.0,
            is_best=False, is_worst=False, rank="top 20%",
            narrative="solid session.",
        )
        d = sc.to_dict()
        assert d["strategy"] == "momentum"
        assert d["pnl"] == 3.0
        assert d["rank"] == "top 20%"

    def test_strategy_profile_to_dict(self):
        sp = StrategyProfile(
            strategy="momentum", sessions=5, total_trades=25,
            avg_pnl=2.0, avg_wr=65.0, best_pnl=5.0, worst_pnl=-1.0,
            preferred_regime="trending", worst_regime="reverting",
        )
        d = sp.to_dict()
        assert d["sessions"] == 5

    def test_operator_insight_to_dict(self):
        oi = OperatorInsight(
            insight_type="regime_affinity",
            title="you're a trending trader",
            description="WR: 74% in trending, 48% in reverting.",
            confidence=0.85, data_points=12,
            recommendation="favor trending conditions.",
        )
        d = oi.to_dict()
        assert d["confidence"] == 0.85
        assert d["insight_type"] == "regime_affinity"


# ══════════════════════════════════════════════════════════════════════════════
# Insight card rendering (template existence)
# ══════════════════════════════════════════════════════════════════════════════

class TestInsightCardTemplate:

    def test_template_exists(self):
        """insight_card.html template should exist."""
        from scanner.v6.cards.renderer import TEMPLATES_DIR
        template = TEMPLATES_DIR / "insight_card.html"
        assert template.exists()

    def test_template_has_placeholders(self):
        """Template should contain expected placeholders."""
        from scanner.v6.cards.renderer import TEMPLATES_DIR
        html = (TEMPLATES_DIR / "insight_card.html").read_text()
        assert "{{title}}" in html
        assert "{{description}}" in html
        assert "{{recommendation}}" in html
        assert "{{confidence_pct}}" in html
        assert "{{data_points}}" in html

    def test_preprocess_insight_card(self):
        """Preprocessing should compute confidence_pct and confidence_display."""
        from scanner.v6.cards.renderer import _preprocess
        data = {
            "title": "TEST",
            "insight_type": "strategy_edge",
            "description": "test desc",
            "recommendation": "do this",
            "confidence": 0.75,
            "data_points": 10,
        }
        result = _preprocess("insight_card", data)
        assert result["confidence_pct"] == "75"
        assert result["confidence_display"] == "75%"

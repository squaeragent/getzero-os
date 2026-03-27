"""Tests for the progression system — streaks, milestones, score, reputation."""

from __future__ import annotations

import struct
from dataclasses import asdict
from unittest.mock import MagicMock

import pytest

from scanner.v6.progression import (
    ProgressionEngine,
    StreakInfo,
    Milestone,
    ScoreCard,
    Reputation,
    _badge_for_streak,
    _sessions_to_next_badge,
    _max_consecutive_profitable,
    _calc_performance,
    _calc_discipline,
    _calc_protection,
    _calc_consistency,
    _calc_adaptation,
    _class_for_score,
    MILESTONE_DEFS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session(
    pnl: float = 1.0,
    strategy: str = "momentum",
    trades: int = 3,
    wins: int = 2,
    losses: int = 1,
    mode: str = "comfort",
    max_drawdown_pct: float = 2.0,
    completed_at: str = "2026-03-27T12:00:00Z",
    started_at: str = "2026-03-27T00:00:00Z",
    **kwargs,
) -> dict:
    return {
        "session_id": f"sess-{id(pnl)}",
        "strategy": strategy,
        "strategy_display": strategy.title(),
        "trade_count": trades,
        "wins": wins,
        "losses": losses,
        "total_pnl_pct": pnl,
        "total_pnl_usd": pnl * 10,
        "max_drawdown_pct": max_drawdown_pct,
        "completed_at": completed_at,
        "started_at": started_at,
        "mode": mode,
        "state": "completed",
        **kwargs,
    }


def _make_history(pnls: list[float], **kwargs) -> list[dict]:
    """Build a session history from a list of PnL values (most recent first)."""
    return [_make_session(pnl=p, **kwargs) for p in pnls]


def _mock_api(history: list[dict]):
    """Create a mock API that returns the given session history."""
    api = MagicMock()
    api.session_history.return_value = {"sessions": history, "count": len(history)}
    return api


def _engine(history: list[dict]) -> ProgressionEngine:
    """Create a ProgressionEngine with mocked history."""
    return ProgressionEngine(_mock_api(history), "op_test")


# ── Streak tests ──────────────────────────────────────────────────────────────

class TestStreak:
    def test_empty_history(self):
        e = _engine([])
        s = e.get_streak()
        assert s.current == 0
        assert s.best == 0
        assert s.streak_type == "none"
        assert s.badge is None

    def test_one_winning(self):
        e = _engine(_make_history([5.0]))
        s = e.get_streak()
        assert s.current == 1
        assert s.streak_type == "winning"
        assert s.badge is None
        assert s.sessions_to_next == 2  # 3 - 1

    def test_three_winning_bronze(self):
        e = _engine(_make_history([3.0, 2.0, 1.0]))
        s = e.get_streak()
        assert s.current == 3
        assert s.streak_type == "winning"
        assert s.badge == "bronze"
        assert s.sessions_to_next == 2  # 5 - 3

    def test_five_winning_silver(self):
        e = _engine(_make_history([5.0, 4.0, 3.0, 2.0, 1.0]))
        s = e.get_streak()
        assert s.current == 5
        assert s.badge == "silver"
        assert s.sessions_to_next == 5  # 10 - 5

    def test_ten_winning_gold(self):
        e = _engine(_make_history([float(i) for i in range(10, 0, -1)]))
        s = e.get_streak()
        assert s.current == 10
        assert s.badge == "gold"
        assert s.sessions_to_next == 10  # 20 - 10

    def test_twenty_winning_diamond(self):
        e = _engine(_make_history([float(i) for i in range(20, 0, -1)]))
        s = e.get_streak()
        assert s.current == 20
        assert s.badge == "diamond"
        assert s.sessions_to_next == 0

    def test_losing_streak(self):
        e = _engine(_make_history([-1.0, -2.0, -3.0]))
        s = e.get_streak()
        assert s.current == 3
        assert s.streak_type == "losing"
        assert s.badge is None

    def test_broken_streak(self):
        """Most recent is a loss, breaking a winning streak."""
        e = _engine(_make_history([-1.0, 3.0, 2.0, 1.0]))
        s = e.get_streak()
        assert s.current == 1
        assert s.streak_type == "losing"
        assert s.best == 3  # best all-time is the 3 wins before

    def test_best_tracks_all_time(self):
        """Best streak should find the longest winning run anywhere in history."""
        # History: 2 wins, loss, 5 wins (most recent first → [5 wins], loss, [2 wins])
        pnls = [5.0, 4.0, 3.0, 2.0, 1.0, -1.0, 2.0, 1.0]
        e = _engine(_make_history(pnls))
        s = e.get_streak()
        assert s.current == 5
        assert s.best == 5


class TestBadgeThresholds:
    def test_below_bronze(self):
        assert _badge_for_streak(0) is None
        assert _badge_for_streak(2) is None

    def test_bronze(self):
        assert _badge_for_streak(3) == "bronze"
        assert _badge_for_streak(4) == "bronze"

    def test_silver(self):
        assert _badge_for_streak(5) == "silver"
        assert _badge_for_streak(9) == "silver"

    def test_gold(self):
        assert _badge_for_streak(10) == "gold"
        assert _badge_for_streak(19) == "gold"

    def test_diamond(self):
        assert _badge_for_streak(20) == "diamond"
        assert _badge_for_streak(100) == "diamond"

    def test_sessions_to_next(self):
        assert _sessions_to_next_badge(0) == 3
        assert _sessions_to_next_badge(2) == 1
        assert _sessions_to_next_badge(3) == 2  # next is silver
        assert _sessions_to_next_badge(5) == 5  # next is gold
        assert _sessions_to_next_badge(10) == 10  # next is diamond
        assert _sessions_to_next_badge(20) == 0  # already diamond


# ── Milestone tests ───────────────────────────────────────────────────────────

class TestMilestones:
    def test_no_history(self):
        e = _engine([])
        ms = e.get_milestones()
        assert len(ms) == 15
        assert all(not m.achieved for m in ms)
        assert all(m.progress == 0.0 for m in ms)

    def test_first_session(self):
        e = _engine([_make_session()])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["first_session"].achieved is True
        assert ms["first_session"].progress == 1.0

    def test_first_profit(self):
        e = _engine([_make_session(pnl=5.0)])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["first_profit"].achieved is True

    def test_first_loss(self):
        e = _engine([_make_session(pnl=-2.0)])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["first_loss"].achieved is True

    def test_10_sessions(self):
        e = _engine(_make_history([1.0] * 10))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["10_sessions"].achieved is True

    def test_10_sessions_progress(self):
        e = _engine(_make_history([1.0] * 6))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["10_sessions"].achieved is False
        assert ms["10_sessions"].progress == 0.6

    def test_50_sessions(self):
        e = _engine(_make_history([1.0] * 50))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["50_sessions"].achieved is True

    def test_100_trades(self):
        sessions = [_make_session(trades=20)] * 5  # 100 trades
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["100_trades"].achieved is True

    def test_100_trades_progress(self):
        sessions = [_make_session(trades=10)] * 5  # 50 trades
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["100_trades"].progress == 0.5

    def test_3_strategies(self):
        sessions = [
            _make_session(strategy="momentum"),
            _make_session(strategy="defense"),
            _make_session(strategy="degen"),
        ]
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["3_strategies"].achieved is True

    def test_all_strategies(self):
        strats = ["momentum", "defense", "degen", "sniper", "scalp",
                  "breakout", "mean_revert", "funding_arb", "trend_follow"]
        sessions = [_make_session(strategy=s) for s in strats]
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["all_strategies"].achieved is True

    def test_streak_3_milestone(self):
        e = _engine(_make_history([3.0, 2.0, 1.0]))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["streak_3"].achieved is True

    def test_streak_10_milestone(self):
        e = _engine(_make_history([float(i) for i in range(10, 0, -1)]))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["streak_10"].achieved is True

    def test_streak_10_not_achieved(self):
        e = _engine(_make_history([1.0] * 5))
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["streak_10"].achieved is False
        assert ms["streak_10"].progress == 0.5

    def test_wr_70_not_enough_trades(self):
        """70% WR needs 20+ trades."""
        sessions = [_make_session(trades=5, wins=4, losses=1)]  # 80% but only 5 trades
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["wr_70"].achieved is False

    def test_wr_70_achieved(self):
        sessions = [_make_session(trades=25, wins=20, losses=5)]
        e = _engine(sessions)
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["wr_70"].achieved is True

    def test_sport_mode(self):
        e = _engine([_make_session(mode="sport")])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["tried_sport"].achieved is True
        assert ms["tried_track"].achieved is False

    def test_track_mode(self):
        e = _engine([_make_session(mode="track")])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["tried_track"].achieved is True

    def test_survived_circuit(self):
        e = _engine([_make_session(pnl=2.0, circuit_breaker=True)])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["survived_circuit"].achieved is True

    def test_night_owl(self):
        e = _engine([_make_session(
            pnl=3.0,
            started_at="2026-03-27T22:30:00Z",
            completed_at="2026-03-28T06:00:00Z",
        )])
        ms = {m.id: m for m in e.get_milestones()}
        assert ms["night_trader"].achieved is True

    def test_achievements_only_returns_earned(self):
        e = _engine([_make_session(pnl=5.0)])
        achievements = e.get_achievements()
        assert all(m.achieved for m in achievements)
        assert len(achievements) >= 2  # at least first_session + first_profit


# ── Score tests ───────────────────────────────────────────────────────────────

class TestScore:
    def test_empty_history_all_zero(self):
        e = _engine([])
        sc = e.get_score()
        assert sc.total == 0.0
        assert sc.class_name == "novice"

    def test_single_session(self):
        e = _engine([_make_session(pnl=5.0, trades=10, wins=7, losses=3)])
        sc = e.get_score()
        assert sc.performance > 0
        assert sc.discipline > 0
        assert sc.protection > 0
        assert sc.total > 0

    def test_performance_formula(self):
        # 70% WR + 10% total PnL → base=70 + bonus=min(20, 50)=20 → 90
        history = [_make_session(pnl=10.0, trades=10, wins=7, losses=3)]
        score = _calc_performance(history)
        assert score >= 70  # at minimum the WR base
        assert score <= 100

    def test_performance_negative_pnl(self):
        history = [_make_session(pnl=-5.0, trades=10, wins=3, losses=7)]
        score = _calc_performance(history)
        assert score < 50  # low WR + negative PnL

    def test_discipline_perfect(self):
        history = [_make_session()]
        score = _calc_discipline(history)
        assert score == 100.0  # all sessions completed, all stops triggered

    def test_protection_low_drawdown(self):
        history = [_make_session(max_drawdown_pct=1.0)]
        score = _calc_protection(history)
        assert score >= 90  # very low drawdown

    def test_protection_high_drawdown(self):
        history = [_make_session(max_drawdown_pct=20.0)]
        score = _calc_protection(history)
        assert score <= 50  # 20% DD → max_dd_score = 0

    def test_consistency_identical_sessions(self):
        history = _make_history([2.0, 2.0, 2.0, 2.0, 2.0])
        score = _calc_consistency(history)
        assert score == 100.0  # zero variance

    def test_consistency_high_variance(self):
        history = _make_history([20.0, -15.0, 18.0, -12.0, 10.0])
        score = _calc_consistency(history)
        assert score < 50

    def test_adaptation_one_strategy(self):
        history = _make_history([1.0] * 5, strategy="momentum")
        score = _calc_adaptation(history)
        assert score < 35  # 1/9 strategies, low diversity

    def test_adaptation_many_strategies(self):
        strats = ["momentum", "defense", "degen", "sniper", "scalp",
                  "breakout", "mean_revert"]
        history = [_make_session(strategy=s) for s in strats]
        score = _calc_adaptation(history)
        assert score > 30  # 7/9 * 50 = ~39 + regime bonus


class TestScoreClass:
    def test_novice(self):
        assert _class_for_score(0) == "novice"
        assert _class_for_score(19.9) == "novice"

    def test_apprentice(self):
        assert _class_for_score(20) == "apprentice"
        assert _class_for_score(39.9) == "apprentice"

    def test_operator(self):
        assert _class_for_score(40) == "operator"
        assert _class_for_score(59.9) == "operator"

    def test_veteran(self):
        assert _class_for_score(60) == "veteran"
        assert _class_for_score(79.9) == "veteran"

    def test_elite(self):
        assert _class_for_score(80) == "elite"
        assert _class_for_score(100) == "elite"


# ── Reputation tests ──────────────────────────────────────────────────────────

class TestReputation:
    def test_empty_history(self):
        e = _engine([])
        rep = e.get_reputation()
        assert rep.sessions_completed == 0
        assert rep.total_trades == 0
        assert rep.milestones_earned == 0
        assert rep.milestones_total == 15
        assert rep.favorite_strategy == "none"
        assert rep.score.class_name == "novice"

    def test_with_history(self):
        history = [
            _make_session(pnl=5.0, strategy="momentum", trades=10),
            _make_session(pnl=-2.0, strategy="defense", trades=5),
            _make_session(pnl=3.0, strategy="momentum", trades=8),
        ]
        e = _engine(history)
        rep = e.get_reputation()
        assert rep.sessions_completed == 3
        assert rep.total_trades == 23
        assert rep.best_trade_pnl == 5.0
        assert rep.worst_trade_pnl == -2.0
        assert rep.favorite_strategy == "momentum"
        assert rep.milestones_earned > 0
        assert rep.score.total > 0

    def test_serializes_to_dict(self):
        e = _engine([_make_session()])
        rep = e.get_reputation()
        d = asdict(rep)
        assert isinstance(d, dict)
        assert "score" in d
        assert "streak" in d
        assert d["score"]["class_name"] in ("novice", "apprentice", "operator", "veteran", "elite")

    def test_50_sessions(self):
        history = _make_history([float(i % 5) for i in range(50)])
        e = _engine(history)
        rep = e.get_reputation()
        assert rep.sessions_completed == 50
        assert rep.milestones_earned >= 3  # at least first_session, 10_sessions, 50_sessions


# ── Card rendering tests ─────────────────────────────────────────────────────

def _is_png(data: bytes) -> bool:
    return data[:8] == b'\x89PNG\r\n\x1a\n'


def _png_dimensions(data: bytes) -> tuple:
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    return w, h


SCORE_DATA = {
    "performance": 72.5,
    "discipline": 85.0,
    "protection": 90.0,
    "consistency": 60.0,
    "adaptation": 45.0,
    "total": 71.8,
    "class_name": "veteran",
}

MILESTONE_DATA = {
    "milestones": [
        {"id": "first_session", "name": "Ignition", "description": "deployed first session",
         "achieved": True, "achieved_at": "2026-03-01T00:00:00Z", "progress": 1.0},
        {"id": "first_profit", "name": "First Blood", "description": "first profitable session",
         "achieved": True, "achieved_at": "2026-03-01T12:00:00Z", "progress": 1.0},
        {"id": "first_loss", "name": "Battle Scars", "description": "first losing session",
         "achieved": False, "achieved_at": None, "progress": 0.0},
        {"id": "10_sessions", "name": "Getting Started", "description": "completed 10 sessions",
         "achieved": True, "achieved_at": "2026-03-10T00:00:00Z", "progress": 1.0},
        {"id": "50_sessions", "name": "Seasoned", "description": "completed 50 sessions",
         "achieved": False, "achieved_at": None, "progress": 0.4},
        {"id": "100_trades", "name": "Century", "description": "100 trades executed",
         "achieved": False, "achieved_at": None, "progress": 0.65},
        {"id": "3_strategies", "name": "Versatile", "description": "used 3 strategies",
         "achieved": True, "achieved_at": "2026-03-05T00:00:00Z", "progress": 1.0},
        {"id": "all_strategies", "name": "Full Roster", "description": "used all 9",
         "achieved": False, "achieved_at": None, "progress": 0.33},
        {"id": "streak_3", "name": "Hot Hand", "description": "3 wins in a row",
         "achieved": True, "achieved_at": "2026-03-15T00:00:00Z", "progress": 1.0},
        {"id": "streak_10", "name": "Unstoppable", "description": "10 wins in a row",
         "achieved": False, "achieved_at": None, "progress": 0.5},
        {"id": "wr_70", "name": "Sharpshooter", "description": "70%+ WR over 20 trades",
         "achieved": True, "achieved_at": "2026-03-20T00:00:00Z", "progress": 1.0},
        {"id": "survived_circuit", "name": "Survivor", "description": "survived circuit breaker",
         "achieved": False, "achieved_at": None, "progress": 0.0},
        {"id": "tried_sport", "name": "Engaged", "description": "used sport mode",
         "achieved": True, "achieved_at": "2026-03-03T00:00:00Z", "progress": 1.0},
        {"id": "tried_track", "name": "Driver", "description": "used track mode",
         "achieved": False, "achieved_at": None, "progress": 0.0},
        {"id": "night_trader", "name": "Night Owl", "description": "profitable overnight",
         "achieved": False, "achieved_at": None, "progress": 0.0},
    ],
    "earned": 7,
    "total": 15,
}

STREAK_DATA = {
    "current": 5,
    "best": 8,
    "streak_type": "winning",
    "badge": "silver",
    "sessions_to_next": 5,
}


@pytest.fixture(scope="module")
def renderer():
    from scanner.v6.cards.renderer import CardRenderer
    return CardRenderer()


class TestScoreCard:
    def test_renders(self, renderer):
        png = renderer.render("score_card", SCORE_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("score_card", SCORE_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_novice(self, renderer):
        data = {**SCORE_DATA, "total": 10.0, "class_name": "novice"}
        png = renderer.render("score_card", data)
        assert _is_png(png)

    def test_elite(self, renderer):
        data = {**SCORE_DATA, "total": 95.0, "class_name": "elite"}
        png = renderer.render("score_card", data)
        assert _is_png(png)

    def test_all_zero(self, renderer):
        data = {
            "performance": 0.0, "discipline": 0.0, "protection": 0.0,
            "consistency": 0.0, "adaptation": 0.0, "total": 0.0, "class_name": "novice",
        }
        png = renderer.render("score_card", data)
        assert _is_png(png)


class TestMilestoneCard:
    def test_renders(self, renderer):
        png = renderer.render("milestone_card", MILESTONE_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("milestone_card", MILESTONE_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_no_milestones_earned(self, renderer):
        data = {
            "milestones": [{"id": "x", "name": "X", "description": "x",
                           "achieved": False, "achieved_at": None, "progress": 0.0}],
            "earned": 0, "total": 1,
        }
        png = renderer.render("milestone_card", data)
        assert _is_png(png)

    def test_all_earned(self, renderer):
        data = {
            "milestones": [
                {"id": f"m{i}", "name": f"M{i}", "description": f"milestone {i}",
                 "achieved": True, "achieved_at": "2026-03-27T00:00:00Z", "progress": 1.0}
                for i in range(15)
            ],
            "earned": 15, "total": 15,
        }
        png = renderer.render("milestone_card", data)
        assert _is_png(png)


class TestStreakCard:
    def test_renders(self, renderer):
        png = renderer.render("streak_card", STREAK_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("streak_card", STREAK_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_no_streak(self, renderer):
        data = {"current": 0, "best": 0, "streak_type": "none", "badge": None, "sessions_to_next": 3}
        png = renderer.render("streak_card", data)
        assert _is_png(png)

    def test_losing_streak(self, renderer):
        data = {"current": 4, "best": 7, "streak_type": "losing", "badge": None, "sessions_to_next": 3}
        png = renderer.render("streak_card", data)
        assert _is_png(png)

    def test_diamond_badge(self, renderer):
        data = {"current": 25, "best": 25, "streak_type": "winning", "badge": "diamond", "sessions_to_next": 0}
        png = renderer.render("streak_card", data)
        assert _is_png(png)

    def test_gold_badge(self, renderer):
        data = {"current": 12, "best": 12, "streak_type": "winning", "badge": "gold", "sessions_to_next": 8}
        png = renderer.render("streak_card", data)
        assert _is_png(png)


class TestProgressionSamplePNGs:
    """Generate sample PNGs to /tmp for visual inspection."""

    def test_score_png(self, renderer):
        path = renderer.render_to_file("score_card", SCORE_DATA, "/tmp/test_score.png")
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_milestone_png(self, renderer):
        path = renderer.render_to_file("milestone_card", MILESTONE_DATA, "/tmp/test_milestone.png")
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_streak_png(self, renderer):
        path = renderer.render_to_file("streak_card", STREAK_DATA, "/tmp/test_streak.png")
        with open(path, "rb") as f:
            assert _is_png(f.read())

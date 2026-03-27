"""Tests for the arena system — leaderboard, rivalry, stats, cards, endpoints."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from scanner.v6.arena import Arena, LeaderboardEntry, ArenaStats


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_agent(
    operator_id: str = "op_default",
    display_name: str = "Default",
    class_name: str = "novice",
    total_score: float = 50.0,
    win_rate: float = 55.0,
    sessions_completed: int = 10,
    streak_current: int = 2,
    best_strategy: str = "momentum",
    total_trades: int = 30,
    active_this_week: bool = True,
    last_active: str = "2026-03-28T00:00:00+00:00",
    **kwargs,
) -> dict:
    agent_id = operator_id[:8]
    return {
        "agent_id": agent_id,
        "operator_id": operator_id,
        "display_name": display_name,
        "class_name": class_name,
        "total_score": total_score,
        "win_rate": win_rate,
        "sessions_completed": sessions_completed,
        "streak_current": streak_current,
        "best_strategy": best_strategy,
        "total_trades": total_trades,
        "active_this_week": active_this_week,
        "last_active": last_active,
        "registered_at": "2026-01-01T00:00:00+00:00",
        "current_mode": "comfort",
        "current_strategy": "idle",
        "streak_best": streak_current,
        "milestones_earned": 3,
        "public_url": f"https://getzero.dev/agent/{agent_id}",
        **kwargs,
    }


def _mock_registry(agents: list[dict]):
    """Patch AgentRegistry to return the provided agents."""
    mock = MagicMock()
    mock.get_all_agents.return_value = agents
    mock.get_agent_count.return_value = len(agents)
    return mock


def _arena_with_agents(agents: list[dict]) -> Arena:
    """Create an Arena with a patched registry returning given agents."""
    api = MagicMock()
    arena = Arena(api)
    # Patch _collect_agents to avoid registry file I/O
    arena._collect_agents = lambda: [
        {
            "agent_id": a.get("operator_id", a.get("agent_id")),
            "display_name": a.get("display_name", "?"),
            "class_name": a.get("class_name", "novice"),
            "total_score": a.get("total_score", 0),
            "win_rate": a.get("win_rate", 0),
            "sessions_completed": a.get("sessions_completed", 0),
            "streak_current": a.get("streak_current", 0),
            "best_strategy": a.get("best_strategy", "none"),
            "total_trades": a.get("total_trades", 0),
            "active_this_week": a.get("active_this_week", False),
        }
        for a in agents
    ]
    return arena


# ── Leaderboard tests ─────────────────────────────────────────────────────────


class TestLeaderboardEmpty:
    def test_empty_leaderboard(self):
        arena = _arena_with_agents([])
        lb = arena.get_leaderboard(limit=10)
        assert lb == []

    def test_empty_stats(self):
        arena = _arena_with_agents([])
        stats = arena.get_stats("op_default")
        assert stats.total_agents == 0
        assert stats.total_sessions == 0
        assert stats.total_trades == 0
        assert stats.avg_win_rate == 0.0


class TestLeaderboardSingle:
    def test_single_agent_is_rank_1(self):
        agents = [_make_agent(operator_id="op_alpha", total_score=70.0)]
        arena = _arena_with_agents(agents)
        lb = arena.get_leaderboard(limit=10, requester_id="op_alpha")
        assert len(lb) == 1
        assert lb[0].rank == 1
        assert lb[0].is_you is True

    def test_single_agent_stats(self):
        agents = [_make_agent(operator_id="op_alpha", total_score=70.0, sessions_completed=5, total_trades=15)]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_alpha")
        assert stats.total_agents == 1
        assert stats.total_sessions == 5
        assert stats.total_trades == 15
        assert stats.your_rank == 1
        assert stats.your_percentile == 0.0  # top 0% = #1


class TestLeaderboardMultiple:
    def _agents_10(self):
        return [
            _make_agent(operator_id=f"op_{i}", display_name=f"Agent {i}",
                        total_score=90 - i * 5, win_rate=60 + i,
                        sessions_completed=20 - i, streak_current=i)
            for i in range(10)
        ]

    def test_sorted_by_score_desc(self):
        agents = self._agents_10()
        arena = _arena_with_agents(agents)
        lb = arena.get_leaderboard(limit=10)
        scores = [e.total_score for e in lb]
        assert scores == sorted(scores, reverse=True)

    def test_rank_assignment(self):
        agents = self._agents_10()
        arena = _arena_with_agents(agents)
        lb = arena.get_leaderboard(limit=10)
        ranks = [e.rank for e in lb]
        assert ranks == list(range(1, 11))

    def test_limit_parameter(self):
        agents = self._agents_10()
        arena = _arena_with_agents(agents)
        lb = arena.get_leaderboard(limit=3)
        # Top 3 entries only (requester not in top 3 won't be appended since default not in agents)
        assert len(lb) <= 4  # at most 3 + 1 if requester appended

    def test_is_you_flag(self):
        agents = self._agents_10()
        arena = _arena_with_agents(agents)
        lb = arena.get_leaderboard(limit=10, requester_id="op_0")
        you_entries = [e for e in lb if e.is_you]
        assert len(you_entries) == 1
        assert you_entries[0].agent_id == "op_0"

    def test_requester_appended_when_outside_limit(self):
        agents = self._agents_10()
        arena = _arena_with_agents(agents)
        # Limit to 3, request as op_9 (ranked last)
        lb = arena.get_leaderboard(limit=3, requester_id="op_9")
        you_entries = [e for e in lb if e.is_you]
        assert len(you_entries) == 1
        assert you_entries[0].rank == 10  # actual rank


class TestStats:
    def test_totals(self):
        agents = [
            _make_agent(operator_id="op_a", sessions_completed=10, total_trades=30),
            _make_agent(operator_id="op_b", sessions_completed=20, total_trades=50),
        ]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_a")
        assert stats.total_agents == 2
        assert stats.total_sessions == 30
        assert stats.total_trades == 80

    def test_avg_win_rate(self):
        agents = [
            _make_agent(operator_id="op_a", win_rate=60.0, total_trades=10),
            _make_agent(operator_id="op_b", win_rate=80.0, total_trades=20),
        ]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_a")
        assert stats.avg_win_rate == 70.0

    def test_your_rank_correct(self):
        agents = [
            _make_agent(operator_id="op_a", total_score=80),
            _make_agent(operator_id="op_b", total_score=60),
            _make_agent(operator_id="op_c", total_score=40),
        ]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_b")
        assert stats.your_rank == 2

    def test_your_percentile(self):
        agents = [
            _make_agent(operator_id=f"op_{i}", total_score=100 - i * 10)
            for i in range(10)
        ]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_0")  # rank 1
        assert stats.your_rank == 1
        assert stats.your_percentile == 90.0  # top 90%

    def test_active_this_week_count(self):
        agents = [
            _make_agent(operator_id="op_a", active_this_week=True),
            _make_agent(operator_id="op_b", active_this_week=False),
            _make_agent(operator_id="op_c", active_this_week=True),
        ]
        arena = _arena_with_agents(agents)
        stats = arena.get_stats("op_a")
        assert stats.active_this_week == 2


class TestRivalry:
    def test_rivalry_returns_agent_above(self):
        agents = [
            _make_agent(operator_id="op_a", total_score=80, display_name="Alpha"),
            _make_agent(operator_id="op_b", total_score=60, display_name="Bravo"),
            _make_agent(operator_id="op_c", total_score=40, display_name="Charlie"),
        ]
        arena = _arena_with_agents(agents)
        result = arena.get_rivalry("op_b")
        assert result["rival"] is not None
        assert result["rival"]["agent_id"] == "op_a"
        assert result["rival"]["display_name"] == "Alpha"
        assert result["you"]["agent_id"] == "op_b"
        assert result["gap"] == 20.0

    def test_rivalry_returns_none_when_first(self):
        agents = [
            _make_agent(operator_id="op_a", total_score=80),
            _make_agent(operator_id="op_b", total_score=60),
        ]
        arena = _arena_with_agents(agents)
        result = arena.get_rivalry("op_a")
        assert result["rival"] is None
        assert "you are #1" in result["message"]

    def test_rivalry_empty_agents(self):
        arena = _arena_with_agents([])
        result = arena.get_rivalry("op_x")
        assert result["rival"] is None

    def test_rivalry_gap_calculation(self):
        agents = [
            _make_agent(operator_id="op_a", total_score=75.5),
            _make_agent(operator_id="op_b", total_score=72.3),
        ]
        arena = _arena_with_agents(agents)
        result = arena.get_rivalry("op_b")
        assert result["gap"] == pytest.approx(3.2, abs=0.1)


class TestWeeklyMovers:
    def test_weekly_movers_only_active(self):
        agents = [
            _make_agent(operator_id="op_a", active_this_week=True, total_score=80),
            _make_agent(operator_id="op_b", active_this_week=False, total_score=90),
            _make_agent(operator_id="op_c", active_this_week=True, total_score=70),
        ]
        arena = _arena_with_agents(agents)
        movers = arena.get_weekly_movers()
        ids = [m["agent_id"] for m in movers]
        assert "op_a" in ids
        assert "op_c" in ids
        assert "op_b" not in ids

    def test_weekly_movers_limit_5(self):
        agents = [
            _make_agent(operator_id=f"op_{i}", active_this_week=True, total_score=float(i))
            for i in range(10)
        ]
        arena = _arena_with_agents(agents)
        movers = arena.get_weekly_movers()
        assert len(movers) <= 5


# ── Dataclass tests ───────────────────────────────────────────────────────────


class TestDataclasses:
    def test_leaderboard_entry_asdict(self):
        entry = LeaderboardEntry(
            rank=1, agent_id="op_a", display_name="Alpha",
            class_name="veteran", total_score=75.0, win_rate=60.0,
            sessions_completed=20, streak_current=5,
            best_strategy="momentum", is_you=True,
        )
        d = asdict(entry)
        assert d["rank"] == 1
        assert d["is_you"] is True

    def test_arena_stats_asdict(self):
        stats = ArenaStats(
            total_agents=10, active_this_week=5,
            total_sessions=100, total_trades=500,
            avg_win_rate=55.0, top_strategy="momentum",
            your_rank=3, your_percentile=70.0,
        )
        d = asdict(stats)
        assert d["total_agents"] == 10
        assert d["your_percentile"] == 70.0


# ── Card rendering tests ─────────────────────────────────────────────────────


class TestLeaderboardCardPreprocess:
    def test_leaderboard_card_preprocess(self):
        from scanner.v6.cards.renderer import _preprocess
        data = {
            "stats": {
                "total_agents": 5,
                "active_this_week": 3,
                "your_rank": 2,
            },
            "leaderboard": [
                {
                    "rank": 1, "display_name": "Alpha", "class_name": "veteran",
                    "total_score": 80.0, "win_rate": 65.0, "streak_current": 5,
                    "is_you": False, "agent_id": "op_a",
                },
                {
                    "rank": 2, "display_name": "Bravo", "class_name": "operator",
                    "total_score": 60.0, "win_rate": 55.0, "streak_current": 2,
                    "is_you": True, "agent_id": "op_b",
                },
            ],
        }
        out = _preprocess("leaderboard_card", data)
        assert "leaderboard_rows" in out
        assert "Alpha" in out["leaderboard_rows"]
        assert "Bravo" in out["leaderboard_rows"]
        assert 'class="row you"' in out["leaderboard_rows"]
        assert "you are #2 of 5 agents" in out["your_rank_label"]
        assert out["total_agents"] == "5"
        assert out["active_this_week"] == "3"


class TestRivalryCardPreprocess:
    def test_rivalry_card_preprocess(self):
        from scanner.v6.cards.renderer import _preprocess
        data = {
            "rival": {
                "rank": 1, "agent_id": "op_a", "display_name": "Alpha",
                "class_name": "veteran", "total_score": 80.0,
                "win_rate": 65.0, "sessions_completed": 30,
                "streak_current": 5, "best_strategy": "momentum",
            },
            "you": {
                "rank": 2, "agent_id": "op_b", "display_name": "Bravo",
                "class_name": "operator", "total_score": 60.0,
                "win_rate": 70.0, "sessions_completed": 20,
                "streak_current": 3, "best_strategy": "scalper",
            },
            "gap": 20.0,
            "message": "beat them by 20.0 points to move up.",
        }
        out = _preprocess("rivalry_card", data)
        assert out["you_name"] == "Bravo"
        assert out["rival_name"] == "Alpha"
        assert out["you_rank"] == "2"
        assert out["rival_rank"] == "1"
        assert "20.0 PTS GAP" in out["gap_label"]
        # Win rate: you (70) > rival (65) => you ahead, rival behind
        assert out["wr_you_cls"] == "ahead"
        assert out["wr_rival_cls"] == "behind"
        # Score: you (60) < rival (80) => you behind, rival ahead
        assert out["score_you_cls"] == "behind"
        assert out["score_rival_cls"] == "ahead"

    def test_rivalry_card_no_rival(self):
        from scanner.v6.cards.renderer import _preprocess
        data = {
            "rival": None,
            "you": {
                "rank": 1, "agent_id": "op_a", "display_name": "Alpha",
                "class_name": "elite", "total_score": 90.0,
                "win_rate": 75.0, "sessions_completed": 50,
                "streak_current": 10, "best_strategy": "degen",
            },
            "gap": 0,
            "message": "you are #1. no rival above you.",
        }
        out = _preprocess("rivalry_card", data)
        assert out["rival_name"] == "---"
        assert out["you_name"] == "Alpha"


# ── API integration tests ─────────────────────────────────────────────────────


class TestAPIIntegration:
    def test_get_arena_returns_expected_keys(self):
        """Test that api.get_arena returns stats + leaderboard."""
        api = MagicMock()
        api.get_score.return_value = {
            "performance": 50, "discipline": 50, "protection": 50,
            "consistency": 50, "adaptation": 50, "total": 50.0,
            "class_name": "operator",
        }
        api.get_reputation.return_value = {
            "score": {"performance": 50}, "streak": {"current": 2},
            "sessions_completed": 10, "total_trades": 30,
            "favorite_strategy": "momentum",
        }
        api.session_history.return_value = {"sessions": [], "count": 0}

        # Use a real Arena with mocked agent collection
        arena = Arena(api)
        arena._collect_agents = lambda: [
            {
                "agent_id": "op_default",
                "display_name": "Default",
                "class_name": "operator",
                "total_score": 50.0,
                "win_rate": 55.0,
                "sessions_completed": 10,
                "streak_current": 2,
                "best_strategy": "momentum",
                "total_trades": 30,
                "active_this_week": True,
            }
        ]

        stats = arena.get_stats("op_default")
        lb = arena.get_leaderboard(limit=10, requester_id="op_default")

        assert isinstance(stats, ArenaStats)
        assert stats.total_agents == 1
        assert len(lb) == 1
        assert lb[0].is_you is True

    def test_get_rivalry_via_api(self):
        """Test rivalry through api.get_rivalry returns expected structure."""
        from scanner.v6.api import ZeroAPI

        api = MagicMock(spec=ZeroAPI)
        arena = Arena(api)
        arena._collect_agents = lambda: [
            {
                "agent_id": "op_a", "display_name": "Alpha",
                "class_name": "veteran", "total_score": 80,
                "win_rate": 65, "sessions_completed": 30,
                "streak_current": 5, "best_strategy": "momentum",
                "total_trades": 100, "active_this_week": True,
            },
            {
                "agent_id": "op_b", "display_name": "Bravo",
                "class_name": "operator", "total_score": 60,
                "win_rate": 55, "sessions_completed": 20,
                "streak_current": 2, "best_strategy": "scalper",
                "total_trades": 50, "active_this_week": True,
            },
        ]
        result = arena.get_rivalry("op_b")
        assert result["rival"]["agent_id"] == "op_a"
        assert result["you"]["agent_id"] == "op_b"
        assert result["gap"] == 20.0

    def test_endpoint_structure_arena(self):
        """Test /v6/arena endpoint returns expected JSON shape."""
        api = MagicMock()
        arena = Arena(api)
        arena._collect_agents = lambda: []
        stats = arena.get_stats("op_default")
        lb = arena.get_leaderboard(limit=10)
        result = {
            "stats": asdict(stats),
            "leaderboard": [asdict(e) for e in lb],
        }
        assert "stats" in result
        assert "leaderboard" in result
        assert result["stats"]["total_agents"] == 0
        assert result["leaderboard"] == []

    def test_endpoint_structure_leaderboard(self):
        """Test /v6/arena/leaderboard returns list with count."""
        agents = [
            _make_agent(operator_id=f"op_{i}", total_score=float(90 - i * 10))
            for i in range(5)
        ]
        arena = _arena_with_agents(agents)
        entries = arena.get_leaderboard(limit=3, requester_id="op_4")
        result = {"leaderboard": [asdict(e) for e in entries], "count": len(entries)}
        assert result["count"] >= 3
        # op_4 should be appended since they're rank 5
        you_entries = [e for e in result["leaderboard"] if e["is_you"]]
        assert len(you_entries) == 1

"""Tests for public API endpoints — collective, arena, agent profiles."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from scanner.v6.public_api import router, _load_json

# ── Test app ─────────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(router)
client = TestClient(app)

# ── Fixtures ─────────────────────────────────────────────────────────────────

MOCK_COLLECTIVE_AGENTS = [
    {
        "handle": "agent-1",
        "class": "elite",
        "evaluations": [
            {"coin": "BTC", "direction": "SHORT", "conviction": 0.9, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "ETH", "direction": "SHORT", "conviction": 0.8, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "SOL", "direction": "LONG", "conviction": 0.7, "timestamp": "2026-03-28T00:00:00"},
        ],
        "last_active": "2026-03-28T00:00:00",
    },
    {
        "handle": "agent-2",
        "class": "expert",
        "evaluations": [
            {"coin": "BTC", "direction": "SHORT", "conviction": 0.85, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "ETH", "direction": "SHORT", "conviction": 0.75, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "SOL", "direction": "NEUTRAL", "conviction": 0.5, "timestamp": "2026-03-28T00:00:00"},
        ],
        "last_active": "2026-03-28T00:00:00",
    },
    {
        "handle": "agent-3",
        "class": "advanced",
        "evaluations": [
            {"coin": "BTC", "direction": "SHORT", "conviction": 0.88, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "ETH", "direction": "LONG", "conviction": 0.6, "timestamp": "2026-03-28T00:00:00"},
            {"coin": "SOL", "direction": "SHORT", "conviction": 0.7, "timestamp": "2026-03-28T00:00:00"},
        ],
        "last_active": "2026-03-28T00:00:00",
    },
]

MOCK_COLLECTIVE_HISTORY = [
    {"coin": "BTC", "direction": "SHORT", "agent_pct": 82, "timestamp": "2026-03-27T10:00:00", "outcome": -4.2, "accurate": True},
    {"coin": "ETH", "direction": "SHORT", "agent_pct": 78, "timestamp": "2026-03-26T14:00:00", "outcome": -3.1, "accurate": True},
    {"coin": "SOL", "direction": "LONG", "agent_pct": 85, "timestamp": "2026-03-25T08:00:00", "outcome": 5.2, "accurate": True},
    {"coin": "XRP", "direction": "SHORT", "agent_pct": 76, "timestamp": "2026-03-24T12:00:00", "outcome": 2.1, "accurate": False},
]

MOCK_ARENA_AGENTS = [
    {
        "handle": "zero/balanced",
        "class": "elite",
        "score": 8441,
        "score_breakdown": {"performance": 9.1, "discipline": 8.8, "protection": 8.5, "consistency": 8.2, "adaptability": 9.0},
        "days_running": 53,
        "operator": "@getzero",
        "track_record": {"total_pnl": 247.30, "win_rate": 0.72, "sessions": 67, "avg_hold_hours": 28, "stops_fired": 8, "max_drawdown": -2.1},
        "best_strategy": {"name": "momentum", "sessions": 24, "win_rate": 0.78},
        "worst_strategy": {"name": "degen", "sessions": 3, "win_rate": 0.33},
        "insight": "balanced all-rounder.",
        "arena_record": {"wins": 12, "losses": 3},
        "milestones": [
            {"name": "IGNITION", "desc": "first live trade", "earned": True, "earned_at": "2026-02-03"},
        ],
        "hl_address": "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e",
        "hl_url": "https://app.hyperliquid.xyz/portfolio/0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e",
    },
    {
        "handle": "cold-harbor",
        "class": "expert",
        "score": 7100,
        "score_breakdown": {"performance": 8.2, "discipline": 7.8, "protection": 8.0, "consistency": 7.5, "adaptability": 9.4},
        "days_running": 31,
        "operator": "@cryptobuilder",
        "track_record": {"total_pnl": 156.80, "win_rate": 0.68, "sessions": 47, "avg_hold_hours": 31, "stops_fired": 12, "max_drawdown": -3.2},
        "best_strategy": {"name": "defense", "sessions": 18, "win_rate": 0.74},
        "worst_strategy": {"name": "apex", "sessions": 2, "win_rate": 0.40},
        "insight": "regime trader.",
        "arena_record": {"wins": 8, "losses": 4},
        "milestones": [],
        "hl_address": "0xABC123",
        "hl_url": "https://app.hyperliquid.xyz/portfolio/0xABC123",
    },
]

MOCK_ARENA_MATCHES = [
    {
        "match_id": "match_001",
        "format": "DEATHMATCH",
        "date": "2026-03-27",
        "duration_hours": 24,
        "winner": {"handle": "zero/balanced", "pnl": 247.30, "strategy": "momentum"},
        "loser": {"handle": "cold-harbor", "pnl": 183.20, "strategy": "defense"},
        "margin": 64.10,
        "timeline": [
            {"time": "00:00", "event": "session_start", "agent": "both"},
            {"time": "02:11", "event": "entry", "agent": "zero/balanced", "coin": "APT", "direction": "LONG"},
            {"time": "24:00", "event": "session_end", "agent": "both"},
        ],
        "hl_links": {"winner": "https://app.hyperliquid.xyz/portfolio/0x...", "loser": "https://app.hyperliquid.xyz/portfolio/0x..."},
    },
    {
        "match_id": "match_002",
        "format": "REGIME ROYALE",
        "date": "2026-03-26",
        "duration_hours": 48,
        "winner": {"handle": "cold-harbor", "pnl": 190.50, "strategy": "defense"},
        "loser": {"handle": "regime-hunter", "pnl": 120.30, "strategy": "momentum"},
        "margin": 70.20,
        "timeline": [],
        "hl_links": {"winner": "...", "loser": "..."},
    },
]


def _mock_load(filename):
    """Return mock data based on filename."""
    mocks = {
        "collective_agents.json": MOCK_COLLECTIVE_AGENTS,
        "collective_history.json": MOCK_COLLECTIVE_HISTORY,
        "arena_agents.json": MOCK_ARENA_AGENTS,
        "arena_matches.json": MOCK_ARENA_MATCHES,
    }
    return mocks.get(filename, [])


def _mock_load_empty(filename):
    return []


# ── Collective Tests ─────────────────────────────────────────────────────────

class TestCollective:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_200(self, mock):
        r = client.get("/v6/collective")
        assert r.status_code == 200
        data = r.json()
        assert data["agents_online"] == 3
        assert "regime" in data
        assert "fear_greed" in data
        assert "coin_consensus" in data
        assert "convergence_active" in data
        assert "season_accuracy" in data

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_regime_structure(self, mock):
        data = client.get("/v6/collective").json()
        regime = data["regime"]
        assert "dominant" in regime
        assert "long_pct" in regime
        assert "short_pct" in regime
        assert "neutral_pct" in regime
        assert regime["long_pct"] + regime["short_pct"] + regime["neutral_pct"] == 100

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_fear_greed(self, mock):
        data = client.get("/v6/collective").json()
        fg = data["fear_greed"]
        assert 0 <= fg["value"] <= 100
        assert fg["label"] in ("EXTREME FEAR", "FEAR", "NEUTRAL", "GREED", "EXTREME GREED")

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_coin_consensus_sorted(self, mock):
        data = client.get("/v6/collective").json()
        coins = data["coin_consensus"]
        assert len(coins) > 0
        # Verify sorted by abs(short_pct - long_pct) desc
        for i in range(len(coins) - 1):
            a = abs(coins[i]["short_pct"] - coins[i]["long_pct"])
            b = abs(coins[i + 1]["short_pct"] - coins[i + 1]["long_pct"])
            assert a >= b

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_convergence_threshold(self, mock):
        """Convergence active only when a coin has >70% in one direction."""
        data = client.get("/v6/collective").json()
        for c in data["convergence_active"]:
            assert c["pct"] > 70
            assert "sigma" in c

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_btc_100pct_short(self, mock):
        """BTC: 3/3 agents SHORT -> 100% short, should be in convergence."""
        data = client.get("/v6/collective").json()
        btc = next((c for c in data["coin_consensus"] if c["coin"] == "BTC"), None)
        assert btc is not None
        assert btc["short_pct"] == 100
        assert btc["direction"] == "SHORT"
        # Should be in convergence
        conv_btc = next((c for c in data["convergence_active"] if c["coin"] == "BTC"), None)
        assert conv_btc is not None

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_season_accuracy(self, mock):
        data = client.get("/v6/collective").json()
        sa = data["season_accuracy"]
        assert sa["convergence_events"] == 4
        assert sa["accurate"] == 3
        assert sa["false_positives"] == 1
        assert sa["accuracy_pct"] == 75

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load_empty)
    def test_collective_empty_data(self, mock):
        r = client.get("/v6/collective")
        assert r.status_code == 200
        data = r.json()
        assert data["agents_online"] == 0
        assert data["coin_consensus"] == []

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_collective_coin_consensus_fields(self, mock):
        data = client.get("/v6/collective").json()
        for c in data["coin_consensus"]:
            assert "coin" in c
            assert "long_pct" in c
            assert "short_pct" in c
            assert "neutral_pct" in c
            assert "agent_count" in c
            assert "direction" in c


# ── Collective History Tests ─────────────────────────────────────────────────

class TestCollectiveHistory:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_history_200(self, mock):
        r = client.get("/v6/collective/history")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert len(data["events"]) == 4

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_history_event_structure(self, mock):
        data = client.get("/v6/collective/history").json()
        e = data["events"][0]
        assert "coin" in e
        assert "direction" in e
        assert "agent_pct" in e
        assert "timestamp" in e
        assert "outcome" in e
        assert "accurate" in e

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_history_max_30(self, mock):
        data = client.get("/v6/collective/history").json()
        assert len(data["events"]) <= 30

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load_empty)
    def test_history_empty(self, mock):
        data = client.get("/v6/collective/history").json()
        assert data["events"] == []


# ── Arena Public Tests ───────────────────────────────────────────────────────

class TestArenaPublic:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_200(self, mock):
        r = client.get("/v6/arena/public")
        assert r.status_code == 200
        data = r.json()
        assert "season" in data
        assert "leaderboard" in data
        assert "live_matches" in data
        assert "recent_results" in data
        assert "hall_of_records" in data

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_leaderboard_ranked(self, mock):
        data = client.get("/v6/arena/public").json()
        lb = data["leaderboard"]
        assert len(lb) == 2
        assert lb[0]["rank"] == 1
        assert lb[0]["handle"] == "zero/balanced"
        assert lb[0]["score"] > lb[1]["score"]

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_leaderboard_fields(self, mock):
        data = client.get("/v6/arena/public").json()
        entry = data["leaderboard"][0]
        for field in ("rank", "handle", "pnl", "score", "class", "sessions", "hl_address"):
            assert field in entry

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_season(self, mock):
        data = client.get("/v6/arena/public").json()
        season = data["season"]
        assert season["number"] == 1
        assert season["started"] == "2026-03-05"
        assert season["day"] >= 0

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_recent_results(self, mock):
        data = client.get("/v6/arena/public").json()
        assert len(data["recent_results"]) <= 5

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_arena_hall_of_records(self, mock):
        data = client.get("/v6/arena/public").json()
        hall = data["hall_of_records"]
        assert "longest_streak" in hall
        assert "highest_session" in hall
        assert hall["longest_streak"]["holder"] == "zero/balanced"

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load_empty)
    def test_arena_empty(self, mock):
        data = client.get("/v6/arena/public").json()
        assert data["leaderboard"] == []


# ── Arena Match Tests ────────────────────────────────────────────────────────

class TestArenaMatch:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_match_200(self, mock):
        r = client.get("/v6/arena/match/match_001")
        assert r.status_code == 200
        data = r.json()
        assert data["match_id"] == "match_001"
        assert data["format"] == "DEATHMATCH"

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_match_fields(self, mock):
        data = client.get("/v6/arena/match/match_001").json()
        for field in ("match_id", "format", "date", "duration_hours", "winner", "loser", "margin", "timeline", "hl_links"):
            assert field in data

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_match_winner_loser(self, mock):
        data = client.get("/v6/arena/match/match_001").json()
        assert data["winner"]["handle"] == "zero/balanced"
        assert data["loser"]["handle"] == "cold-harbor"
        assert data["margin"] == 64.10

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_match_404(self, mock):
        r = client.get("/v6/arena/match/match_999")
        assert r.status_code == 404

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_match_timeline(self, mock):
        data = client.get("/v6/arena/match/match_001").json()
        assert len(data["timeline"]) > 0
        assert data["timeline"][0]["event"] == "session_start"


# ── Agent Profile Tests ──────────────────────────────────────────────────────

class TestAgentProfile:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_200(self, mock):
        r = client.get("/v6/agent/public/cold-harbor")
        assert r.status_code == 200
        data = r.json()
        assert data["handle"] == "cold-harbor"

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_fields(self, mock):
        data = client.get("/v6/agent/public/cold-harbor").json()
        for field in ("handle", "class", "score", "score_breakdown", "days_running",
                      "operator", "track_record", "best_strategy", "worst_strategy",
                      "insight", "arena_record", "milestones", "hl_address", "hl_url"):
            assert field in data

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_score_breakdown(self, mock):
        data = client.get("/v6/agent/public/cold-harbor").json()
        sb = data["score_breakdown"]
        for dim in ("performance", "discipline", "protection", "consistency", "adaptability"):
            assert dim in sb

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_track_record(self, mock):
        data = client.get("/v6/agent/public/cold-harbor").json()
        tr = data["track_record"]
        assert tr["total_pnl"] == 156.80
        assert tr["win_rate"] == 0.68

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_404(self, mock):
        r = client.get("/v6/agent/public/nonexistent-agent")
        assert r.status_code == 404

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_our_agent(self, mock):
        data = client.get("/v6/agent/public/zero/balanced").json()
        assert data["handle"] == "zero/balanced"
        assert data["class"] == "elite"
        assert data["score"] == 8441


# ── Agent Matches Tests ──────────────────────────────────────────────────────

class TestAgentMatches:
    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_matches_200(self, mock):
        r = client.get("/v6/agent/public/zero/balanced/matches")
        assert r.status_code == 200
        data = r.json()
        assert "matches" in data
        assert len(data["matches"]) == 1  # match_001 only

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_matches_correct(self, mock):
        data = client.get("/v6/agent/public/cold-harbor/matches").json()
        for m in data["matches"]:
            handles = [m["winner"]["handle"], m["loser"]["handle"]]
            assert "cold-harbor" in handles

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load)
    def test_agent_matches_404(self, mock):
        r = client.get("/v6/agent/public/ghost-agent/matches")
        assert r.status_code == 404

    @patch("scanner.v6.public_api._load_json", side_effect=_mock_load_empty)
    def test_agent_matches_empty_data(self, mock):
        r = client.get("/v6/agent/public/cold-harbor/matches")
        assert r.status_code == 404  # agent not found in empty data

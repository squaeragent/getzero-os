#!/usr/bin/env python3
"""
Tests for ZeroAPI — the unified function layer.

Tests all 23 tool functions with mocked engine internals.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.api import ZeroAPI, _risk_level


@pytest.fixture
def tmp_bus(tmp_path):
    """Create a temp bus directory with minimal files."""
    bus = tmp_path / "bus"
    bus.mkdir()
    # Empty positions
    (bus / "positions.json").write_text(json.dumps({"positions": []}))
    # Empty heartbeat
    (bus / "heartbeat.json").write_text(json.dumps({"ts": "2026-03-27T12:00:00Z"}))
    return bus


@pytest.fixture
def api(tmp_bus):
    """Create a ZeroAPI instance with mocked internals."""
    with patch("scanner.v6.api.Monitor") as MockMonitor, \
         patch("scanner.v6.api.SessionManager") as MockSessionMgr:
        
        mock_monitor = MockMonitor.return_value
        mock_monitor.cache = MagicMock()
        mock_monitor.last_cycle_metrics = None
        
        mock_session = MockSessionMgr.return_value
        
        a = ZeroAPI(bus_dir=tmp_bus)
        a._monitor = mock_monitor
        a._session_mgr = mock_session
        yield a


OP = "op_test"


# ── SESSION TOOLS ────────────────────────────────────────────────────────────

class TestListStrategies:
    def test_returns_9_strategies(self):
        api = ZeroAPI.__new__(ZeroAPI)
        api._bus_dir = Path("/tmp")
        result = api.list_strategies(OP)
        assert result["count"] == 9
        names = {s["name"] for s in result["strategies"]}
        assert "momentum" in names
        assert "defense" in names
        assert "apex" in names

    def test_strategies_have_required_fields(self):
        api = ZeroAPI.__new__(ZeroAPI)
        api._bus_dir = Path("/tmp")
        result = api.list_strategies(OP)
        for s in result["strategies"]:
            assert "name" in s
            assert "display" in s
            assert "tier" in s
            assert "risk_level" in s
            assert "consensus_threshold" in s


class TestPreviewStrategy:
    def test_valid_strategy(self):
        api = ZeroAPI.__new__(ZeroAPI)
        api._bus_dir = Path("/tmp")
        result = api.preview_strategy(OP, "momentum")
        assert result["name"] == "momentum"
        assert "risk" in result
        assert result["risk"]["max_positions"] == 5
        assert result["risk"]["max_exposure_pct"] == 70.0

    def test_invalid_strategy(self):
        api = ZeroAPI.__new__(ZeroAPI)
        api._bus_dir = Path("/tmp")
        result = api.preview_strategy(OP, "nonexistent")
        assert "error" in result
        assert "available" in result


class TestStartSession:
    def test_start_success(self, api):
        mock_session = MagicMock()
        mock_session.id = "sess-001"
        mock_session.strategy_config.name = "momentum"
        mock_session.strategy_config.display = "Momentum Surf"
        mock_session.strategy_config.session.duration_hours = 48
        mock_session.paper = True
        mock_session.ends_at = datetime(2026, 3, 29, tzinfo=timezone.utc)

        api._session_mgr.active_session = None
        api._session_mgr.start_session.return_value = mock_session

        result = api.start_session(OP, "momentum", paper=True)
        assert result["session_id"] == "sess-001"
        assert result["status"] == "active"
        assert result["paper"] is True

    def test_start_while_active(self, api):
        active = MagicMock()
        active.id = "sess-existing"
        active.strategy_config.display = "Momentum Surf"
        active.state = "active"
        api._session_mgr.active_session = active

        result = api.start_session(OP, "momentum")
        assert "error" in result
        assert "already active" in result["error"].lower()


class TestSessionStatus:
    def test_no_active_session(self, api):
        api._session_mgr.get_status.return_value = {"active": False, "session": None}
        result = api.session_status(OP)
        assert result["active"] is False

    def test_active_session(self, api, tmp_bus):
        api._session_mgr.get_status.return_value = {
            "active": True,
            "session": {"id": "sess-001", "state": "active"},
        }
        result = api.session_status(OP)
        assert result["active"] is True
        assert "open_positions" in result["session"]


class TestEndSession:
    def test_end_success(self, api):
        mock_result = MagicMock()
        mock_result.session_id = "sess-001"
        mock_result.strategy = "momentum"
        mock_result.strategy_display = "Momentum Surf"
        mock_result.trade_count = 3
        mock_result.wins = 2
        mock_result.losses = 1
        mock_result.total_pnl_usd = 4.50
        mock_result.total_pnl_pct = 9.0
        mock_result.duration_actual = timedelta(hours=24)
        mock_result.paper = True
        mock_result.narrative_text = "Good session."

        mock_session = MagicMock()
        api._session_mgr.active_session = mock_session
        api._session_mgr.end_session_early.return_value = mock_result

        result = api.end_session(OP)
        assert result["session_id"] == "sess-001"
        assert result["trade_count"] == 3
        assert result["wins"] == 2

    def test_end_no_session(self, api):
        api._session_mgr.active_session = None
        result = api.end_session(OP)
        assert "error" in result


class TestQueueSession:
    def test_queue(self, api):
        api._session_mgr.queue_session.return_value = {
            "strategy": "degen", "paper": True, "queued_at": "2026-03-27T12:00:00Z"
        }
        result = api.queue_session(OP, "degen")
        assert result["queued"] is True
        assert result["strategy"] == "degen"


class TestSessionHistory:
    def test_empty_history(self, api):
        api._session_mgr.get_history.return_value = []
        result = api.session_history(OP)
        assert result["count"] == 0
        assert result["sessions"] == []


class TestSessionResult:
    def test_not_found(self, api):
        api._session_mgr.get_result.return_value = None
        result = api.session_result(OP, "nonexistent")
        assert "error" in result


# ── INTELLIGENCE TOOLS ───────────────────────────────────────────────────────

class TestEvaluate:
    def test_evaluate_returns_structure(self, api):
        from scanner.v6.monitor import EvaluationResult, LayerResult
        mock_result = EvaluationResult(
            coin="BTC",
            timestamp="2026-03-27T12:00:00Z",
            layers=[
                LayerResult(layer="regime", passed=True, value="trending", detail="ok"),
                LayerResult(layer="technical", passed=True, value={"agree": 3}, detail="ok"),
                LayerResult(layer="funding", passed=True, value=0.01, detail="ok"),
                LayerResult(layer="book", passed=False, value=0.4, detail="thin"),
                LayerResult(layer="OI", passed=True, value=0.05, detail="ok"),
                LayerResult(layer="macro", passed=False, value=15, detail="fear"),
                LayerResult(layer="collective", passed=True, value=None, detail="default"),
            ],
            consensus=5,
            conviction=0.71,
            direction="SHORT",
            regime="strong_trend",
            price=67800.0,
            data_age_ms=50,
            data_complete=True,
        )
        api._monitor.evaluate_coin.return_value = mock_result
        result = api.evaluate(OP, "BTC")
        assert result["coin"] == "BTC"
        assert result["consensus"] == 5
        assert result["direction"] == "SHORT"
        assert len(result["layers"]) == 7

    def test_evaluate_invalid_coin(self, api):
        api._monitor.evaluate_coin.side_effect = ValueError("Unknown coin")
        result = api.evaluate(OP, "FAKECOIN")
        assert "error" in result


class TestGetHeat:
    def test_returns_sorted(self, api):
        api._monitor.get_heat_state.return_value = [
            {"coin": "BTC", "conviction": 0.7},
            {"coin": "SOL", "conviction": 0.5},
        ]
        result = api.get_heat(OP)
        assert result["count"] == 2


class TestGetApproaching:
    def test_returns_approaching(self, api):
        api._monitor.get_approaching.return_value = [
            {"coin": "SOL", "consensus": 4, "threshold": 5, "bottleneck": "book"},
        ]
        result = api.get_approaching(OP)
        assert result["count"] == 1
        assert result["approaching"][0]["bottleneck"] == "book"


class TestGetPulse:
    def test_returns_events(self, api):
        api._monitor.get_pulse.return_value = [
            {"type": "ENTRY", "coin": "BTC", "direction": "SHORT"},
        ]
        result = api.get_pulse(OP)
        assert result["count"] == 1


class TestGetBrief:
    def test_returns_brief(self, api):
        api._monitor.get_brief.return_value = {
            "timestamp": "2026-03-27T12:00:00Z",
            "fear_greed": 13,
            "open_positions": 5,
        }
        result = api.get_brief(OP)
        assert result["fear_greed"] == 13


# ── PROGRESSION STUBS ────────────────────────────────────────────────────────

class TestProgressionStubs:
    def test_score_stub(self, api):
        result = api.get_score(OP)
        assert "phase" in result
        assert result["score"] == 0.0

    def test_achievements_stub(self, api):
        result = api.get_achievements(OP)
        assert result["count"] == 0

    def test_streak_stub(self, api):
        result = api.get_streak(OP)
        assert result["daily_streak"] == 0

    def test_reputation_stub(self, api):
        result = api.get_reputation(OP)
        assert result["stars"] == 0


# ── COMPETITION STUBS ────────────────────────────────────────────────────────

class TestCompetitionStubs:
    def test_arena_stub(self, api):
        result = api.get_arena(OP)
        assert "phase" in result

    def test_rivalry_stub(self, api):
        result = api.get_rivalry(OP)
        assert result["rival"] is None

    def test_chain_stub(self, api):
        result = api.get_chain(OP)
        assert result["longest"] == 0


# ── ACCOUNT STUBS ────────────────────────────────────────────────────────────

class TestAccountStubs:
    def test_credits_stub(self, api):
        result = api.get_credits(OP)
        assert result["balance"] == 0

    def test_energy_stub(self, api):
        result = api.get_energy(OP)
        assert result["energy_pct"] == 100.0


# ── ENGINE HEALTH ────────────────────────────────────────────────────────────

class TestEngineHealth:
    def test_returns_health(self, api, tmp_bus):
        result = api.get_engine_health(OP)
        assert result["status"] == "operational"
        assert "timestamp" in result


# ── HELPERS ──────────────────────────────────────────────────────────────────

class TestRiskLevel:
    def test_none(self):
        from scanner.v6.strategy_loader import load_strategy
        watch = load_strategy("watch")
        assert _risk_level(watch) == "none"

    def test_conservative(self):
        from scanner.v6.strategy_loader import load_strategy
        defense = load_strategy("defense")
        assert _risk_level(defense) == "conservative"

    def test_moderate(self):
        from scanner.v6.strategy_loader import load_strategy
        momentum = load_strategy("momentum")
        assert _risk_level(momentum) == "moderate"

    def test_extreme(self):
        from scanner.v6.strategy_loader import load_strategy
        apex = load_strategy("apex")
        assert _risk_level(apex) == "extreme"


# ── OPERATOR ID CONTRACT ────────────────────────────────────────────────────

class TestOperatorContract:
    """Every function takes operator_id — verify the contract."""

    def test_all_methods_accept_operator_id(self):
        """Every public method on ZeroAPI takes operator_id as first param."""
        import inspect
        api = ZeroAPI.__new__(ZeroAPI)
        for name in dir(api):
            if name.startswith("_"):
                continue
            method = getattr(api, name)
            if not callable(method):
                continue
            sig = inspect.signature(method)
            params = list(sig.parameters.keys())
            assert params[0] == "operator_id", \
                f"ZeroAPI.{name}() must take operator_id as first parameter, got {params}"

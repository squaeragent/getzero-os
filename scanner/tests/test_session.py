#!/usr/bin/env python3
"""
Tests for Session Lifecycle State Machine (session 10).

Covers: state transitions, result cards, rejection rates, near misses,
narrative builder, early end, failure, concurrent sessions, paper flag,
events, timeline.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.session import (
    Session,
    SessionManager,
    SessionResult,
    NearMiss,
    TimelineEvent,
    build_narrative,
    detect_near_misses,
    build_timeline_from_events,
    _compute_max_drawdown,
    _trade_won,
    _trade_pnl,
)
from scanner.v6.strategy_loader import load_strategy


# ════════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def bus_dir(tmp_path):
    """Isolated bus directory."""
    bus = tmp_path / "bus"
    bus.mkdir()
    return bus


@pytest.fixture
def mgr(bus_dir):
    """SessionManager with isolated bus."""
    return SessionManager(bus_dir=bus_dir)


@pytest.fixture
def active_session(mgr):
    """A started (active) session using momentum strategy."""
    return mgr.start_session("momentum", paper=True)


@pytest.fixture
def session_with_trades(active_session):
    """Session with some mock trades recorded."""
    trades = [
        {"coin": "SOL", "direction": "LONG", "entry_price": 148.20,
         "exit_price": 151.10, "size_usd": 100.0, "pnl_usd": 2.90,
         "pnl_pct": 1.96, "won": True, "exit_reason": "trailing_stop"},
        {"coin": "ETH", "direction": "LONG", "entry_price": 3200.0,
         "exit_price": 3150.0, "size_usd": 100.0, "pnl_usd": -1.56,
         "pnl_pct": -1.56, "won": False, "exit_reason": "stop_loss"},
    ]
    active_session.trades = trades
    active_session.eval_count = 5000
    active_session.reject_count = 4870
    return active_session


# ════════════════════════════════════════════════════════════════════════════════
# TEST: STATE TRANSITIONS
# ════════════════════════════════════════════════════════════════════════════════

class TestStartSession:
    def test_start_session_state_transition(self, mgr):
        """PENDING → ACTIVE on start."""
        session = mgr.start_session("momentum", paper=True)
        assert session.state == "active"
        assert session.strategy == "momentum"
        assert session.paper is True
        assert session.id  # non-empty UUID

    def test_start_session_sets_timer(self, active_session):
        """ends_at = started_at + duration_hours."""
        cfg = active_session.strategy_config
        expected_end = active_session.started_at + timedelta(hours=cfg.session.duration_hours)
        # Allow 2 second tolerance for timing
        diff = abs((active_session.ends_at - expected_end).total_seconds())
        assert diff < 2


class TestSessionTimer:
    def test_session_timer_not_expired(self, mgr, active_session):
        """Session just started — not expired."""
        assert mgr.check_session_time(active_session) is False
        assert active_session.state == "active"

    def test_session_timer_expires(self, mgr, active_session):
        """State → COMPLETING when time up."""
        # Force expire
        active_session.ends_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert mgr.check_session_time(active_session) is True
        assert active_session.state == "completing"


class TestCompleteSession:
    def test_complete_session(self, mgr, active_session):
        """All positions closed → COMPLETED with result."""
        result = mgr.complete_session(active_session)
        assert active_session.state == "completed"
        assert isinstance(result, SessionResult)
        assert result.session_id == active_session.id
        assert result.paper is True

    def test_result_card_fields(self, mgr, session_with_trades):
        """All required fields present in SessionResult."""
        result = mgr.complete_session(session_with_trades)
        rd = result.to_dict()
        required = [
            "session_id", "strategy", "strategy_display", "duration_actual_s",
            "paper", "trade_count", "wins", "losses", "best_trade", "worst_trade",
            "total_pnl_usd", "total_pnl_pct", "max_drawdown_pct",
            "eval_count", "reject_count", "rejection_rate_pct",
            "near_misses", "timeline", "narrative_text",
            "started_at", "completed_at", "coins_in_scope",
        ]
        for f in required:
            assert f in rd, f"Missing field: {f}"

    def test_result_trade_stats(self, mgr, session_with_trades):
        """Trade counts, wins, losses, PnL computed correctly."""
        result = mgr.complete_session(session_with_trades)
        assert result.trade_count == 2
        assert result.wins == 1
        assert result.losses == 1
        assert abs(result.total_pnl_usd - 1.34) < 0.01  # 2.90 - 1.56
        assert result.best_trade == 2.90
        assert result.worst_trade == -1.56


class TestRejectionRate:
    def test_rejection_rate(self, mgr, session_with_trades):
        """Calculated correctly: reject_count / eval_count * 100."""
        result = mgr.complete_session(session_with_trades)
        expected = 4870 / 5000 * 100  # 97.4%
        assert abs(result.rejection_rate_pct - expected) < 0.1

    def test_rejection_rate_zero_evals(self, mgr, active_session):
        """No evals → 0% rejection rate."""
        result = mgr.complete_session(active_session)
        assert result.rejection_rate_pct == 0.0


# ════════════════════════════════════════════════════════════════════════════════
# TEST: NEAR MISSES
# ════════════════════════════════════════════════════════════════════════════════

class TestNearMisses:
    def test_near_misses_detected(self, mgr, active_session, bus_dir):
        """Retrospective detection reads near_misses.jsonl."""
        # Write a near miss that happened during the session
        nm_data = {
            "coin": "AVAX",
            "actual_move_pct": 6.8,
            "would_pass_strategies": ["degen"],
            "failing_layers": ["funding"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        nm_file = bus_dir / "near_misses.jsonl"
        nm_file.write_text(json.dumps(nm_data) + "\n")

        detected = detect_near_misses(active_session, bus_dir)
        assert len(detected) >= 1
        assert detected[0].coin == "AVAX"
        assert detected[0].actual_move_pct == 6.8
        assert "degen" in detected[0].would_pass


# ════════════════════════════════════════════════════════════════════════════════
# TEST: NARRATIVE
# ════════════════════════════════════════════════════════════════════════════════

class TestNarrative:
    def test_narrative_built(self, mgr, session_with_trades):
        """Narrative contains key elements: evals, trades, near misses."""
        # Add a near miss
        session_with_trades.near_misses = [NearMiss(
            coin="AVAX", actual_move_pct=6.8, active_strategy="momentum",
            would_pass=["degen"], failing_layers=["funding"],
            estimated_gain_pct=6.8, timestamp=datetime.now(timezone.utc).isoformat(),
        )]
        # Add an entry timeline event
        session_with_trades.timeline = [TimelineEvent(
            hour=8, event_type="entry",
            detail="SOL LONG",
            data={"coin": "SOL", "price": 148.20, "direction": "long", "conviction": 0.85},
        )]

        narrative = build_narrative(session_with_trades)
        assert "5,000 evaluations" in narrative
        assert "4,870 rejected" in narrative
        assert "SOL" in narrative
        assert "AVAX" in narrative
        assert "degen" in narrative
        assert "2 trades" in narrative

    def test_narrative_zero_trades(self, active_session):
        """Narrative for 0-trade session."""
        narrative = build_narrative(active_session)
        assert "0 trades" in narrative or "Pure observation" in narrative


# ════════════════════════════════════════════════════════════════════════════════
# TEST: END EARLY
# ════════════════════════════════════════════════════════════════════════════════

class TestEndEarly:
    def test_end_early(self, mgr, active_session):
        """Marked ended_early=True, result still generated."""
        result = mgr.end_session_early(active_session)
        assert active_session.state == "completed"
        assert result.ended_early is True
        assert isinstance(result, SessionResult)


# ════════════════════════════════════════════════════════════════════════════════
# TEST: FAIL SESSION
# ════════════════════════════════════════════════════════════════════════════════

class TestFailSession:
    def test_fail_session(self, mgr, active_session, bus_dir):
        """State=FAILED, error recorded, event emitted."""
        mgr.fail_session(active_session, "exchange_down")
        assert active_session.state == "failed"
        assert active_session.error == "exchange_down"
        assert mgr.active_session is None

        # Check event emitted
        events_file = bus_dir / "events.jsonl"
        assert events_file.exists()
        events = [json.loads(l) for l in events_file.read_text().splitlines() if l.strip()]
        failed_events = [e for e in events if e.get("type") == "SESSION_FAILED"]
        assert len(failed_events) >= 1
        assert failed_events[0]["error"] == "exchange_down"


# ════════════════════════════════════════════════════════════════════════════════
# TEST: CONCURRENT SESSIONS
# ════════════════════════════════════════════════════════════════════════════════

class TestConcurrentSessions:
    def test_no_concurrent_sessions(self, mgr):
        """Can't start while one is ACTIVE."""
        mgr.start_session("momentum", paper=True)
        with pytest.raises(RuntimeError, match="already active"):
            mgr.start_session("degen", paper=True)


# ════════════════════════════════════════════════════════════════════════════════
# TEST: PAPER FLAG
# ════════════════════════════════════════════════════════════════════════════════

class TestPaperFlag:
    def test_paper_flag_propagates(self, mgr):
        """paper=True flows through session → result."""
        session = mgr.start_session("momentum", paper=True)
        assert session.paper is True
        result = mgr.complete_session(session)
        assert result.paper is True

    def test_paper_false(self, mgr):
        """paper=False also works."""
        session = mgr.start_session("momentum", paper=False)
        assert session.paper is False
        result = mgr.complete_session(session)
        assert result.paper is False


# ════════════════════════════════════════════════════════════════════════════════
# TEST: EVENTS
# ════════════════════════════════════════════════════════════════════════════════

class TestEvents:
    def test_session_started_event(self, active_session, bus_dir):
        """SESSION_STARTED emitted on start."""
        events_file = bus_dir / "events.jsonl"
        assert events_file.exists()
        events = [json.loads(l) for l in events_file.read_text().splitlines() if l.strip()]
        started = [e for e in events if e.get("type") == "SESSION_STARTED"]
        assert len(started) == 1
        assert started[0]["strategy"] == "momentum"
        assert started[0]["session_id"] == active_session.id

    def test_session_completed_event(self, mgr, active_session, bus_dir):
        """SESSION_COMPLETED emitted with full result."""
        mgr.complete_session(active_session)
        events_file = bus_dir / "events.jsonl"
        events = [json.loads(l) for l in events_file.read_text().splitlines() if l.strip()]
        completed = [e for e in events if e.get("type") == "SESSION_COMPLETED"]
        assert len(completed) == 1
        assert "trade_count" in completed[0]
        assert "narrative_text" in completed[0]


# ════════════════════════════════════════════════════════════════════════════════
# TEST: TIMELINE
# ════════════════════════════════════════════════════════════════════════════════

class TestTimeline:
    def test_timeline_events(self, mgr, active_session, bus_dir):
        """Timeline records entries/exits/near misses with hour markers."""
        # Write some events to the bus
        events_file = bus_dir / "events.jsonl"
        now_iso = datetime.now(timezone.utc).isoformat()
        events_data = [
            {"type": "TRADE_OPENED", "ts": now_iso, "coin": "SOL", "direction": "LONG"},
            {"type": "TRADE_CLOSED", "ts": now_iso, "coin": "SOL", "pnl_usd": 2.90},
            {"type": "NEAR_MISS", "ts": now_iso, "coin": "AVAX"},
        ]
        with open(events_file, "w") as f:
            for ev in events_data:
                f.write(json.dumps(ev) + "\n")

        timeline = build_timeline_from_events(active_session, bus_dir)
        types = [t.event_type for t in timeline]
        assert "entry" in types
        assert "exit" in types
        assert "near_miss" in types

        # All have hour >= 0
        for t in timeline:
            assert t.hour >= 0
            assert isinstance(t, TimelineEvent)


# ════════════════════════════════════════════════════════════════════════════════
# TEST: PERSISTENCE
# ════════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_session_persisted(self, active_session, bus_dir):
        """Session saved to bus/session.json."""
        session_file = bus_dir / "session.json"
        assert session_file.exists()
        data = json.loads(session_file.read_text())
        assert data["id"] == active_session.id
        assert data["state"] == "active"

    def test_history_appended(self, mgr, active_session, bus_dir):
        """Completed session appended to session_history.jsonl."""
        mgr.complete_session(active_session)
        history_file = bus_dir / "session_history.jsonl"
        assert history_file.exists()
        lines = [l for l in history_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["session_id"] == active_session.id
        assert entry["strategy"] == "momentum"


# ════════════════════════════════════════════════════════════════════════════════
# TEST: UTILITIES
# ════════════════════════════════════════════════════════════════════════════════

class TestUtilities:
    def test_max_drawdown(self):
        """Max drawdown computed from PnL sequence."""
        pnls = [10, 5, -8, 3, -12, 2]
        dd = _compute_max_drawdown(pnls)
        assert dd > 0

    def test_max_drawdown_empty(self):
        assert _compute_max_drawdown([]) == 0.0

    def test_trade_won_dict(self):
        assert _trade_won({"won": True}) is True
        assert _trade_won({"won": False}) is False
        assert _trade_won({"pnl_usd": 5}) is True
        assert _trade_won({"pnl_usd": -1}) is False

    def test_trade_pnl_dict(self):
        assert _trade_pnl({"pnl_usd": 2.5}) == 2.5

    def test_record_evaluation(self, mgr, active_session):
        """record_evaluation increments counters."""
        mgr.record_evaluation(active_session, passed=False)
        mgr.record_evaluation(active_session, passed=True)
        mgr.record_evaluation(active_session, passed=False)
        assert active_session.eval_count == 3
        assert active_session.reject_count == 2

    def test_near_miss_dataclass(self):
        nm = NearMiss(
            coin="AVAX", actual_move_pct=6.8, active_strategy="momentum",
            would_pass=["degen"], failing_layers=["funding"],
            estimated_gain_pct=6.8, timestamp="2026-01-01T00:00:00+00:00",
        )
        d = nm.to_dict()
        assert d["coin"] == "AVAX"
        assert d["actual_move_pct"] == 6.8

    def test_timeline_event_dataclass(self):
        te = TimelineEvent(hour=5, event_type="entry", detail="SOL LONG", data={"coin": "SOL"})
        d = te.to_dict()
        assert d["hour"] == 5
        assert d["event_type"] == "entry"


# ════════════════════════════════════════════════════════════════════════════════
# TEST: STRATEGY LOADING IN SESSION
# ════════════════════════════════════════════════════════════════════════════════

class TestStrategyLoading:
    def test_invalid_strategy_raises(self, mgr):
        """Starting with nonexistent strategy raises."""
        with pytest.raises(FileNotFoundError):
            mgr.start_session("nonexistent_strategy_xyz", paper=True)

    def test_strategy_config_attached(self, active_session):
        """Session has full StrategyConfig."""
        cfg = active_session.strategy_config
        assert cfg.name == "momentum"
        assert cfg.session.duration_hours > 0
        assert cfg.evaluation.consensus_threshold > 0

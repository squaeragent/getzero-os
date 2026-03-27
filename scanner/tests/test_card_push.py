"""Tests for scanner.v6.card_push — proactive card push logic."""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scanner.v6 import card_push


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state(tmp_path):
    """Redirect state + log files to tmp for isolation."""
    state_file = tmp_path / "card_push_state.json"
    log_file = tmp_path / "card_push_log.jsonl"
    with patch.object(card_push, "STATE_FILE", state_file), \
         patch.object(card_push, "LOG_FILE", log_file):
        yield state_file, log_file


@pytest.fixture
def mock_api():
    api = MagicMock(spec=["get_heat", "get_approaching", "get_brief"])
    api.get_heat.return_value = {
        "coins": [
            {"coin": "SOL", "consensus": 5, "conviction": 0.833, "direction": "SHORT", "price": 82.95, "regime": "strong_trend"},
            {"coin": "BTC", "consensus": 3, "conviction": 0.5, "direction": "LONG", "price": 66500, "regime": "ranging"},
        ],
        "count": 2,
    }
    api.get_approaching.return_value = {
        "approaching": [
            {"coin": "DOGE", "consensus": 4, "threshold": 5, "distance": 1, "direction": "SHORT",
             "passing_layers": ["regime", "funding", "OI"], "failing_layers": ["technical"],
             "bottleneck": "technical", "urgency": "high"},
        ],
        "count": 1,
    }
    api.get_brief.return_value = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fear_greed": 13,
        "open_positions": 2,
        "positions": [
            {"coin": "APT", "direction": "SHORT", "entry_price": 1.03, "size_usd": 20.0},
            {"coin": "LINK", "direction": "LONG", "entry_price": 14.5, "size_usd": 15.0},
        ],
    }
    return api


# ---------------------------------------------------------------------------
# State read/write
# ---------------------------------------------------------------------------

class TestState:
    def test_load_empty(self, tmp_state):
        """load_state returns defaults when no file exists."""
        state = card_push.load_state()
        assert state["last_run"] is None
        assert state["last_heat"] == {}
        assert state["pushes_today"] == 0

    def test_save_and_load(self, tmp_state):
        state_file, _ = tmp_state
        state = {"last_run": "2026-03-27T12:00:00+00:00", "last_heat": {"SOL": {"consensus": 3, "direction": "SHORT"}},
                 "last_approaching": ["SEI"], "last_positions": ["APT"], "pushes_today": 2, "last_push": "2026-03-27T11:00:00+00:00"}
        card_push.save_state(state)
        loaded = card_push.load_state()
        assert loaded == state

    def test_log_push(self, tmp_state):
        _, log_file = tmp_state
        card_push.log_push({"type": "test", "ts": "now"})
        card_push.log_push({"type": "test2", "ts": "now"})
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "test"


# ---------------------------------------------------------------------------
# Heat shift detection
# ---------------------------------------------------------------------------

class TestHeatShift:
    def test_no_shift_when_no_prior_state(self, mock_api):
        state = {"last_heat": {}}
        shifts, _ = card_push.detect_heat_shifts(mock_api, state)
        assert shifts == []
        assert "SOL" in state["last_heat"]

    def test_detects_shift_of_2(self, mock_api):
        state = {"last_heat": {"SOL": {"consensus": 3, "direction": "SHORT"}, "BTC": {"consensus": 3, "direction": "LONG"}}}
        shifts, _ = card_push.detect_heat_shifts(mock_api, state)
        assert len(shifts) == 1
        assert shifts[0]["coin"] == "SOL"
        assert shifts[0]["old_consensus"] == 3
        assert shifts[0]["new_consensus"] == 5

    def test_ignores_shift_of_1(self, mock_api):
        """SOL 4→5 = diff 1, BTC 2→3 = diff 1 — both below threshold."""
        state = {"last_heat": {"SOL": {"consensus": 4, "direction": "SHORT"}, "BTC": {"consensus": 2, "direction": "LONG"}}}
        shifts, _ = card_push.detect_heat_shifts(mock_api, state)
        assert len(shifts) == 0


class TestApproachingChanges:
    def test_new_approaching_detected(self, mock_api):
        state = {"last_approaching": ["SEI"]}
        new, _ = card_push.detect_approaching_changes(mock_api, state)
        assert len(new) == 1
        assert new[0]["coin"] == "DOGE"
        assert state["last_approaching"] == ["DOGE"]

    def test_no_new_approaching(self, mock_api):
        state = {"last_approaching": ["DOGE"]}
        new, _ = card_push.detect_approaching_changes(mock_api, state)
        assert new == []


class TestPositionChanges:
    def test_new_position_detected(self, mock_api):
        state = {"last_positions": ["APT"]}
        new_pos, closed, _ = card_push.detect_position_changes(mock_api, state)
        assert len(new_pos) == 1
        assert new_pos[0]["coin"] == "LINK"
        assert closed == []

    def test_closed_position_detected(self, mock_api):
        state = {"last_positions": ["APT", "LINK", "SOL"]}
        new_pos, closed, _ = card_push.detect_position_changes(mock_api, state)
        assert new_pos == []
        assert "SOL" in closed

    def test_both_new_and_closed(self, mock_api):
        state = {"last_positions": ["SOL", "AAVE"]}
        new_pos, closed, _ = card_push.detect_position_changes(mock_api, state)
        new_coins = {p["coin"] for p in new_pos}
        assert new_coins == {"APT", "LINK"}
        assert set(closed) == {"SOL", "AAVE"}


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_allows_when_under_limit(self):
        state = {"pushes_today": 3, "last_push": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()}
        assert card_push.can_push(state) is True

    def test_blocks_when_max_pushes(self):
        state = {"pushes_today": 8, "last_push": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()}
        assert card_push.can_push(state) is False

    def test_blocks_when_too_soon(self):
        state = {"pushes_today": 1, "last_push": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}
        assert card_push.can_push(state) is False

    def test_force_bypasses(self):
        state = {"pushes_today": 8, "last_push": datetime.now(timezone.utc).isoformat()}
        assert card_push.can_push(state, force=True) is True

    def test_morning_bypasses(self):
        state = {"pushes_today": 8, "last_push": datetime.now(timezone.utc).isoformat()}
        assert card_push.can_push(state, is_morning=True) is True

    def test_daily_reset(self):
        state = {"pushes_today": 7, "last_push": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
        state = card_push.reset_daily_counter(state)
        assert state["pushes_today"] == 0

    def test_no_reset_same_day(self):
        state = {"pushes_today": 3, "last_push": datetime.now(timezone.utc).isoformat()}
        state = card_push.reset_daily_counter(state)
        assert state["pushes_today"] == 3

    def test_record_push_increments(self):
        state = {"pushes_today": 2, "last_push": None}
        state = card_push.record_push(state)
        assert state["pushes_today"] == 3
        assert state["last_push"] is not None


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_state_write(self, tmp_state, mock_api):
        state_file, _ = tmp_state
        with patch.object(card_push, "ZeroAPI", return_value=mock_api), \
             patch.object(card_push, "CardRenderer"):
            card_push.run(dry_run=True)
        assert not state_file.exists()

    def test_dry_run_no_card_files(self, tmp_state, mock_api):
        with patch.object(card_push, "ZeroAPI", return_value=mock_api), \
             patch.object(card_push, "CardRenderer") as MockRenderer:
            renderer = MockRenderer.return_value
            card_push.run(dry_run=True)
            renderer.render_to_file.assert_not_called()


# ---------------------------------------------------------------------------
# Morning brief
# ---------------------------------------------------------------------------

class TestMorningBrief:
    def test_morning_always_pushes(self, tmp_state, mock_api, capsys):
        with patch.object(card_push, "ZeroAPI", return_value=mock_api), \
             patch.object(card_push, "CardRenderer") as MockRenderer:
            renderer = MockRenderer.return_value
            renderer.render_to_file.return_value = "/tmp/zero_push_brief.png"
            card_push.run(morning=True)
        captured = capsys.readouterr()
        assert "[PUSH] morning_brief" in captured.out

    def test_morning_generates_brief_and_gauge(self, tmp_state, mock_api):
        with patch.object(card_push, "ZeroAPI", return_value=mock_api), \
             patch.object(card_push, "CardRenderer") as MockRenderer:
            renderer = MockRenderer.return_value
            renderer.render_to_file.return_value = "/tmp/test.png"
            card_push.run(morning=True)
            calls = renderer.render_to_file.call_args_list
            templates = [c[0][0] for c in calls]
            assert "brief_card" in templates
            assert "gauge_card" in templates


# ---------------------------------------------------------------------------
# Integration: quiet output
# ---------------------------------------------------------------------------

class TestQuietOutput:
    def test_no_changes_prints_quiet(self, tmp_state, mock_api, capsys):
        """When state matches current data, output [QUIET]."""
        # Pre-populate state to match mock data
        state = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_heat": {
                "SOL": {"consensus": 5, "direction": "SHORT"},
                "BTC": {"consensus": 3, "direction": "LONG"},
            },
            "last_approaching": ["DOGE"],
            "last_positions": ["APT", "LINK"],
            "pushes_today": 0,
            "last_push": None,
        }
        card_push.save_state(state)

        with patch.object(card_push, "ZeroAPI", return_value=mock_api), \
             patch.object(card_push, "CardRenderer"):
            card_push.run()
        captured = capsys.readouterr()
        assert "[QUIET] no changes detected" in captured.out

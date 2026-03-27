"""Tests for scanner.v6.conviction_history — velocity tracking."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from scanner.v6 import conviction_history
from scanner.v6.conviction_history import ConvictionTracker, MAX_READINGS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tracker(tmp_path):
    """Create a tracker with isolated storage."""
    hist_file = tmp_path / "conviction_history.json"
    with patch.object(conviction_history, "HISTORY_FILE", hist_file):
        t = ConvictionTracker()
        yield t


@pytest.fixture
def tracker_file(tmp_path):
    """Return (tracker, hist_file) for persistence tests."""
    hist_file = tmp_path / "conviction_history.json"
    with patch.object(conviction_history, "HISTORY_FILE", hist_file):
        yield ConvictionTracker(), hist_file


# ---------------------------------------------------------------------------
# Record & retrieval
# ---------------------------------------------------------------------------

def test_record_and_retrieve(tracker):
    tracker.record("SOL", 3, "SHORT", 0.5)
    assert "SOL" in tracker.history
    assert len(tracker.history["SOL"]) == 1
    assert tracker.history["SOL"][0]["consensus"] == 3


def test_record_skips_zero_consensus(tracker):
    tracker.record("BTC", 0, "NONE", 0.0)
    assert "BTC" not in tracker.history


def test_multiple_records(tracker):
    tracker.record("SOL", 2, "SHORT", 0.3)
    tracker.record("SOL", 4, "SHORT", 0.6)
    assert len(tracker.history["SOL"]) == 2


# ---------------------------------------------------------------------------
# Velocity calculation
# ---------------------------------------------------------------------------

def test_velocity_empty_history(tracker):
    assert tracker.get_velocity("NOPE") == 0.0


def test_velocity_single_reading(tracker):
    tracker.record("SOL", 3, "SHORT", 0.5)
    assert tracker.get_velocity("SOL") == 0.0


def test_velocity_two_readings(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 2, "direction": "SHORT", "conviction": 0.3},
        {"timestamp": now.isoformat(), "consensus": 4, "direction": "SHORT", "conviction": 0.6},
    ]
    v = tracker.get_velocity("SOL")
    assert abs(v - 2.0) < 0.1  # 2 layers in 1 hour


def test_velocity_three_readings(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=2)).isoformat(), "consensus": 1, "direction": "LONG", "conviction": 0.1},
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 3, "direction": "LONG", "conviction": 0.4},
        {"timestamp": now.isoformat(), "consensus": 5, "direction": "LONG", "conviction": 0.7},
    ]
    v = tracker.get_velocity("SOL")
    assert abs(v - 2.0) < 0.1  # 4 layers in 2 hours


def test_velocity_same_consensus(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["ETH"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 3, "direction": "LONG", "conviction": 0.5},
        {"timestamp": now.isoformat(), "consensus": 3, "direction": "LONG", "conviction": 0.5},
    ]
    assert tracker.get_velocity("ETH") == 0.0


def test_velocity_decreasing(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["DOGE"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 5, "direction": "LONG", "conviction": 0.7},
        {"timestamp": now.isoformat(), "consensus": 2, "direction": "LONG", "conviction": 0.3},
    ]
    v = tracker.get_velocity("DOGE")
    assert v < 0
    assert abs(v - (-3.0)) < 0.1


def test_velocity_twelve_readings_uses_last_three(tracker):
    """With 12 readings, velocity uses last 3."""
    now = datetime.now(timezone.utc)
    readings = []
    for i in range(12):
        readings.append({
            "timestamp": (now - timedelta(hours=12 - i)).isoformat(),
            "consensus": 1,  # flat for first 9
            "direction": "SHORT",
            "conviction": 0.1,
        })
    # Last 3 readings ramp up
    readings[-3]["consensus"] = 2
    readings[-2]["consensus"] = 4
    readings[-1]["consensus"] = 6
    tracker.history["SOL"] = readings
    v = tracker.get_velocity("SOL")
    # 6-2 = 4 layers over ~2 hours
    assert v > 1.5


# ---------------------------------------------------------------------------
# MAX_READINGS cap
# ---------------------------------------------------------------------------

def test_max_readings_cap(tracker):
    for i in range(MAX_READINGS + 3):
        tracker.record("SOL", min(i + 1, 7), "SHORT", 0.5)
    assert len(tracker.history["SOL"]) == MAX_READINGS


# ---------------------------------------------------------------------------
# Velocity labels
# ---------------------------------------------------------------------------

def test_velocity_label_accelerating(tracker):
    assert tracker.get_velocity_label(2.0) == "ACCELERATING"
    assert tracker.get_velocity_label(5.0) == "ACCELERATING"


def test_velocity_label_building(tracker):
    assert tracker.get_velocity_label(0.5) == "BUILDING"
    assert tracker.get_velocity_label(1.9) == "BUILDING"


def test_velocity_label_steady(tracker):
    assert tracker.get_velocity_label(0.0) == "STEADY"
    assert tracker.get_velocity_label(0.4) == "STEADY"
    assert tracker.get_velocity_label(-0.4) == "STEADY"


def test_velocity_label_decelerating(tracker):
    assert tracker.get_velocity_label(-0.5) == "DECELERATING"
    assert tracker.get_velocity_label(-1.9) == "DECELERATING"


def test_velocity_label_retreating(tracker):
    assert tracker.get_velocity_label(-2.0) == "RETREATING"
    assert tracker.get_velocity_label(-5.0) == "RETREATING"


# ---------------------------------------------------------------------------
# Acceleration alerts
# ---------------------------------------------------------------------------

def test_acceleration_alerts_filters(tracker):
    now = datetime.now(timezone.utc)
    # SOL: fast velocity (2 layers/hour)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 2, "direction": "SHORT", "conviction": 0.3},
        {"timestamp": now.isoformat(), "consensus": 4, "direction": "SHORT", "conviction": 0.6},
    ]
    # BTC: slow velocity (0.5 layers/hour)
    tracker.history["BTC"] = [
        {"timestamp": (now - timedelta(hours=2)).isoformat(), "consensus": 3, "direction": "LONG", "conviction": 0.4},
        {"timestamp": now.isoformat(), "consensus": 4, "direction": "LONG", "conviction": 0.5},
    ]
    alerts = tracker.get_acceleration_alerts(threshold=1.5)
    coins = [a["coin"] for a in alerts]
    assert "SOL" in coins
    assert "BTC" not in coins


def test_acceleration_alerts_empty(tracker):
    assert tracker.get_acceleration_alerts() == []


# ---------------------------------------------------------------------------
# Time to threshold
# ---------------------------------------------------------------------------

def test_time_to_threshold_estimate(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 2, "direction": "SHORT", "conviction": 0.3},
        {"timestamp": now.isoformat(), "consensus": 4, "direction": "SHORT", "conviction": 0.6},
    ]
    est = tracker.estimate_time_to_threshold("SOL", target=5)
    assert est is not None
    assert "~" in est
    assert "min" in est  # 0.5 hours = ~30 min


def test_time_to_threshold_none_when_negative_velocity(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["DOGE"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 5, "direction": "LONG", "conviction": 0.7},
        {"timestamp": now.isoformat(), "consensus": 3, "direction": "LONG", "conviction": 0.4},
    ]
    assert tracker.estimate_time_to_threshold("DOGE") is None


def test_time_to_threshold_none_when_already_above(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 4, "direction": "SHORT", "conviction": 0.6},
        {"timestamp": now.isoformat(), "consensus": 6, "direction": "SHORT", "conviction": 0.9},
    ]
    assert tracker.estimate_time_to_threshold("SOL", target=5) is None


def test_time_to_threshold_none_empty(tracker):
    assert tracker.estimate_time_to_threshold("NOPE") is None


def test_time_to_threshold_hours_format(tracker):
    now = datetime.now(timezone.utc)
    # Slow velocity: 0.5 layers/hour, need 3 layers → 6 hours
    tracker.history["XRP"] = [
        {"timestamp": (now - timedelta(hours=2)).isoformat(), "consensus": 1, "direction": "LONG", "conviction": 0.1},
        {"timestamp": now.isoformat(), "consensus": 2, "direction": "LONG", "conviction": 0.3},
    ]
    est = tracker.estimate_time_to_threshold("XRP", target=5)
    assert est is not None
    assert "hour" in est


# ---------------------------------------------------------------------------
# Persistence (save/load roundtrip)
# ---------------------------------------------------------------------------

def test_persistence_roundtrip(tracker_file):
    tracker, hist_file = tracker_file
    tracker.record("SOL", 4, "SHORT", 0.6)
    tracker.record("BTC", 3, "LONG", 0.4)

    # Create a new tracker from same file
    with patch.object(conviction_history, "HISTORY_FILE", hist_file):
        tracker2 = ConvictionTracker()
    assert "SOL" in tracker2.history
    assert "BTC" in tracker2.history
    assert tracker2.history["SOL"][0]["consensus"] == 4


def test_persistence_file_created(tracker_file):
    tracker, hist_file = tracker_file
    tracker.record("ETH", 2, "LONG", 0.3)
    assert hist_file.exists()
    data = json.loads(hist_file.read_text())
    assert "ETH" in data


# ---------------------------------------------------------------------------
# get_coin_data
# ---------------------------------------------------------------------------

def test_get_coin_data(tracker):
    now = datetime.now(timezone.utc)
    tracker.history["SOL"] = [
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "consensus": 2, "direction": "SHORT", "conviction": 0.3},
        {"timestamp": now.isoformat(), "consensus": 4, "direction": "SHORT", "conviction": 0.6},
    ]
    data = tracker.get_coin_data("SOL")
    assert data["velocity"] > 0
    assert data["velocity_label"] in ("ACCELERATING", "BUILDING")
    assert data["peak_consensus"] == 4
    assert data["time_to_threshold"] is not None
    assert data["readings_count"] == 2


def test_get_coin_data_unknown(tracker):
    data = tracker.get_coin_data("NOPE")
    assert data["velocity"] == 0.0
    assert data["velocity_label"] == "STEADY"
    assert data["peak_consensus"] == 0

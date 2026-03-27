"""
Tests for Drive Modes — comfort / sport / track.

Covers:
  - Mode YAML parsing for all 9 strategies
  - ModeConfig defaults
  - Mode switching mid-session
  - approval_required flag per mode
  - push_on list per mode
  - mode_card rendering (preprocessing)
  - /v6/cards/mode endpoint
  - /v6/session/mode endpoint
  - Invalid mode name rejection
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.strategy_loader import (
    load_strategy,
    load_all_strategies,
    list_strategies,
    get_mode_config,
    ModeConfig,
    VALID_MODES,
    _DEFAULT_MODES,
)
from scanner.v6.session import Session, SessionManager
from scanner.v6.cards.renderer import _preprocess


# ════════════════════════════════════════════════════════════════════
# FIXTURES
# ════════════════════════════════════════════════════════════════════

EXPECTED_STRATEGIES = {
    "momentum", "defense", "watch", "scout",
    "funding", "degen", "sniper", "fade", "apex",
}


@pytest.fixture
def bus_dir(tmp_path):
    bus = tmp_path / "bus"
    bus.mkdir()
    return bus


@pytest.fixture
def mgr(bus_dir):
    return SessionManager(bus_dir=bus_dir)


@pytest.fixture
def active_session(mgr):
    return mgr.start_session("momentum", paper=True)


# ════════════════════════════════════════════════════════════════════
# YAML PARSING — ALL 9 STRATEGIES
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_modes_parsed_for_all_strategies(name):
    """Every strategy YAML has modes parsed into 3 ModeConfig objects."""
    cfg = load_strategy(name)
    assert hasattr(cfg, "modes")
    assert set(cfg.modes.keys()) == {"comfort", "sport", "track"}
    for mode_name, mc in cfg.modes.items():
        assert isinstance(mc, ModeConfig), f"{name}.{mode_name} is not ModeConfig"


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_comfort_mode_config(name):
    """Comfort mode: no heat pushes, no approaching, no approval."""
    mc = get_mode_config(name, "comfort")
    assert mc.approval_required is False
    assert mc.heat_push_interval_hours is None
    assert mc.approaching_push is False
    assert "entry" in mc.push_on
    assert "exit" in mc.push_on
    assert "brief" in mc.push_on
    assert "circuit_breaker" in mc.push_on


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_sport_mode_config(name):
    """Sport mode: heat pushes every 2h, approaching enabled, no approval."""
    mc = get_mode_config(name, "sport")
    assert mc.approval_required is False
    assert mc.heat_push_interval_hours == 2
    assert mc.approaching_push is True
    assert "approaching" in mc.push_on
    assert "heat_shift" in mc.push_on
    assert "regime_shift" in mc.push_on


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_track_mode_config(name):
    """Track mode: approval required, 5-min timeout, all push types."""
    mc = get_mode_config(name, "track")
    assert mc.approval_required is True
    assert mc.approval_timeout_seconds == 300
    assert mc.heat_push_interval_hours == 1
    assert mc.approaching_push is True
    assert "eval_candidate" in mc.push_on


# ════════════════════════════════════════════════════════════════════
# ModeConfig DEFAULTS
# ════════════════════════════════════════════════════════════════════

def test_default_modes_exist():
    """All 3 default modes are defined."""
    assert set(_DEFAULT_MODES.keys()) == {"comfort", "sport", "track"}


def test_mode_config_to_dict():
    """ModeConfig.to_dict() returns all fields."""
    mc = _DEFAULT_MODES["comfort"]
    d = mc.to_dict()
    assert "push_on" in d
    assert "approval_required" in d
    assert "approval_timeout_seconds" in d
    assert "heat_push_interval_hours" in d
    assert "approaching_push" in d


def test_valid_modes_constant():
    """VALID_MODES is a set of 3 strings."""
    assert VALID_MODES == {"comfort", "sport", "track"}


# ════════════════════════════════════════════════════════════════════
# MODE SWITCHING MID-SESSION
# ════════════════════════════════════════════════════════════════════

def test_session_default_mode(active_session):
    """New session starts in comfort mode."""
    assert active_session.mode == "comfort"


def test_set_mode_to_sport(mgr, active_session):
    """Switch to sport mode mid-session."""
    result = mgr.set_mode(active_session, "sport")
    assert result["mode"] == "sport"
    assert result["previous_mode"] == "comfort"
    assert active_session.mode == "sport"


def test_set_mode_to_track(mgr, active_session):
    """Switch to track mode mid-session."""
    result = mgr.set_mode(active_session, "track")
    assert result["mode"] == "track"
    assert result["config"]["approval_required"] is True


def test_set_mode_back_to_comfort(mgr, active_session):
    """Switch sport -> comfort."""
    mgr.set_mode(active_session, "sport")
    result = mgr.set_mode(active_session, "comfort")
    assert result["mode"] == "comfort"
    assert result["previous_mode"] == "sport"


def test_mode_persists_in_session_dict(mgr, active_session):
    """Mode appears in session.to_dict()."""
    mgr.set_mode(active_session, "track")
    d = active_session.to_dict()
    assert d["mode"] == "track"


def test_mode_emits_event(mgr, active_session):
    """Mode change emits MODE_CHANGED event."""
    mgr.set_mode(active_session, "sport")
    events = [e for e in active_session.events if e.get("type") == "MODE_CHANGED"]
    assert len(events) == 1
    assert events[0]["new_mode"] == "sport"


# ════════════════════════════════════════════════════════════════════
# APPROVAL FLAG PER MODE
# ════════════════════════════════════════════════════════════════════

def test_comfort_no_approval():
    mc = get_mode_config("momentum", "comfort")
    assert mc.approval_required is False


def test_sport_no_approval():
    mc = get_mode_config("momentum", "sport")
    assert mc.approval_required is False


def test_track_approval_required():
    mc = get_mode_config("momentum", "track")
    assert mc.approval_required is True


def test_track_approval_timeout():
    mc = get_mode_config("momentum", "track")
    assert mc.approval_timeout_seconds == 300


# ════════════════════════════════════════════════════════════════════
# PUSH_ON LIST PER MODE
# ════════════════════════════════════════════════════════════════════

def test_comfort_push_on_minimal():
    mc = get_mode_config("momentum", "comfort")
    assert set(mc.push_on) == {"entry", "exit", "brief", "circuit_breaker"}


def test_sport_push_on_expanded():
    mc = get_mode_config("momentum", "sport")
    expected = {"entry", "exit", "brief", "approaching", "heat_shift", "regime_shift", "circuit_breaker"}
    assert set(mc.push_on) == expected


def test_track_push_on_all():
    mc = get_mode_config("momentum", "track")
    expected = {"entry", "exit", "brief", "approaching", "heat_shift", "regime_shift", "eval_candidate", "circuit_breaker"}
    assert set(mc.push_on) == expected


# ════════════════════════════════════════════════════════════════════
# MODE CARD RENDERING (preprocessing)
# ════════════════════════════════════════════════════════════════════

def _mode_card_data(active="comfort"):
    cfg = load_strategy("momentum")
    modes_dict = {}
    for m in VALID_MODES:
        mc = cfg.get_mode_config(m)
        modes_dict[m] = mc.to_dict()
    return {"active_mode": active, "modes": modes_dict}


def test_mode_card_preprocess_comfort():
    data = _mode_card_data("comfort")
    out = _preprocess("mode_card", data)
    assert out["active_mode"] == "COMFORT"
    assert out["comfort_border"] == "#c8ff00"
    assert out["sport_border"] == "#333"
    assert out["track_border"] == "#333"


def test_mode_card_preprocess_sport():
    data = _mode_card_data("sport")
    out = _preprocess("mode_card", data)
    assert out["active_mode"] == "SPORT"
    assert out["sport_border"] == "#c8ff00"
    assert out["comfort_border"] == "#333"


def test_mode_card_preprocess_track():
    data = _mode_card_data("track")
    out = _preprocess("mode_card", data)
    assert out["active_mode"] == "TRACK"
    assert out["track_border"] == "#c8ff00"
    assert out["track_approval"] == "YES"
    assert out["track_approval_color"] == "#ffb000"


def test_mode_card_preprocess_pushes():
    data = _mode_card_data("comfort")
    out = _preprocess("mode_card", data)
    # Comfort should show checkmarks for entry/exit/brief/circuit_breaker
    assert "\u2713" in out["comfort_pushes"]
    assert "\u2717" in out["comfort_pushes"]  # some types not in comfort


def test_mode_card_preprocess_heat_interval():
    data = _mode_card_data("sport")
    out = _preprocess("mode_card", data)
    assert out["sport_heat_interval"] == "2.0h"
    assert out["comfort_heat_interval"] == "---"


# ════════════════════════════════════════════════════════════════════
# /v6/cards/mode ENDPOINT
# ════════════════════════════════════════════════════════════════════

def test_card_mode_endpoint_exists():
    """The /v6/cards/mode route is registered."""
    from scanner.v6.cards.card_api import router
    routes = [r.path for r in router.routes]
    assert "/mode" in routes or "/v6/cards/mode" in routes


# ════════════════════════════════════════════════════════════════════
# /v6/session/mode ENDPOINT (via ZeroAPI)
# ════════════════════════════════════════════════════════════════════

def test_api_set_mode_no_session():
    """set_mode returns error when no session is active."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI(bus_dir=Path("/tmp/zero_test_mode_bus"))
    result = api.set_mode("op_test", "sport")
    assert "error" in result


def test_api_set_mode_invalid():
    """set_mode returns error for invalid mode name."""
    from scanner.v6.api import ZeroAPI
    api = ZeroAPI()
    result = api.set_mode("op_test", "turbo")
    assert "error" in result
    assert "turbo" in result["error"]


# ════════════════════════════════════════════════════════════════════
# INVALID MODE REJECTION
# ════════════════════════════════════════════════════════════════════

def test_invalid_mode_raises_on_strategy_config():
    """get_mode_config with invalid mode raises ValueError."""
    cfg = load_strategy("momentum")
    with pytest.raises(ValueError, match="Invalid mode"):
        cfg.get_mode_config("turbo")


def test_invalid_mode_raises_on_public_function():
    """Public get_mode_config with invalid mode raises ValueError."""
    with pytest.raises(ValueError, match="Invalid mode"):
        get_mode_config("momentum", "eco")


def test_set_mode_invalid_on_session(mgr, active_session):
    """set_mode with invalid mode raises ValueError."""
    with pytest.raises(ValueError, match="Invalid mode"):
        mgr.set_mode(active_session, "nitro")


def test_set_mode_on_completed_session(mgr, active_session):
    """Cannot set mode on a completed session."""
    mgr.complete_session(active_session)
    with pytest.raises(RuntimeError, match="Cannot change mode"):
        mgr.set_mode(active_session, "sport")

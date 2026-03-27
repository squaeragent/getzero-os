"""
Controller tests — risk gate logic with strategy YAML overrides.

Tests:
  - All 9 risk checks in approve_entry()
  - Strategy overrides take priority over config.py fallbacks
  - Fallback to config.py constants when no strategy is active
  - inject_strategy_params() correctly enriches entry dicts
  - check_time_exits() fires at max_hold_hours
  - handle_entry_end_events() respects entry_end_action
  - Watch strategy blocks all entries
  - Pipeline flow: entry → risk check → approved dict → executor params

All tests are fully mocked — no file I/O, no mainnet calls.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.controller import (
    approve_entry,
    inject_strategy_params,
    check_time_exits,
    handle_entry_end_events,
    _StrategyParams,
)
from scanner.v6.strategy_loader import load_strategy, StrategyConfig


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

def make_entry(
    coin="BTC",
    direction="LONG",
    signal_name="test",
    consensus_layers=5,
    event_type="ENTRY",
) -> dict:
    return {
        "coin": coin,
        "direction": direction,
        "signal_name": signal_name,
        "consensus_layers": consensus_layers,
        "event_type": event_type,
    }


def make_position(
    coin="ETH",
    direction="LONG",
    size_usd=100.0,
    entry_time: datetime | None = None,
) -> dict:
    if entry_time is None:
        entry_time = datetime.now(timezone.utc) - timedelta(hours=1)
    return {
        "coin": coin,
        "direction": direction,
        "size_usd": size_usd,
        "entry_time": entry_time.isoformat(),
    }


def make_risk(daily_loss_usd=0.0, halted=False, peak_equity=100.0) -> dict:
    return {
        "daily_loss_usd":   daily_loss_usd,
        "halted":           halted,
        "peak_equity":      peak_equity,
        "daily_loss_since": datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat(),
    }


def params_from_strategy(name: str, equity: float = 1000.0) -> _StrategyParams:
    """Build strategy params. Default equity=$1000 so reserve checks don't dominate."""
    cfg = load_strategy(name)
    return _StrategyParams(cfg, equity)


def params_no_strategy(equity: float = 1000.0) -> _StrategyParams:
    return _StrategyParams(None, equity)


# ─── CHECK 1: max_positions ───────────────────────────────────────────────────

class TestMaxPositions:
    """Risk check 1: max_positions blocks new entries when at limit."""

    def test_blocks_at_limit(self):
        params = params_from_strategy("momentum")   # max_positions=5, equity=$1000
        positions = [make_position(f"COIN{i}", size_usd=10.0) for i in range(5)]
        ok, reason = approve_entry(make_entry(), positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "max_positions" in reason

    def test_allows_below_limit(self):
        params = params_from_strategy("momentum")   # max_positions=5, equity=$1000
        positions = [make_position(f"COIN{i}", size_usd=10.0) for i in range(4)]
        ok, _ = approve_entry(make_entry(), positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_strategy_limit_overrides_fallback(self):
        """Defense has max_positions=3 — stricter than fallback."""
        params = params_from_strategy("defense")    # max_positions=3, equity=$1000
        positions = [make_position(f"COIN{i}", size_usd=10.0) for i in range(3)]
        ok, reason = approve_entry(make_entry(), positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "max_positions" in reason
        assert "defense" in reason

    def test_fallback_uses_dynamic_limits(self):
        """Without strategy, fallback uses get_dynamic_limits()."""
        from scanner.v6.config import get_dynamic_limits
        equity = 1000.0
        limits = get_dynamic_limits(equity)
        params = params_no_strategy(equity)
        assert params.max_positions == limits["max_positions"]


# ─── CHECK 2: max_daily_loss_pct (circuit breaker) ────────────────────────────

class TestDailyLossCircuitBreaker:
    """Risk check 2: daily_loss_pct circuit breaker halts all entries."""

    def test_blocks_when_daily_loss_exceeded(self):
        params = params_from_strategy("momentum")   # max_daily_loss_pct=5%, equity=$1000
        # 5% of $1000 = $50; daily_loss_usd=$51 → should block
        risk = make_risk(daily_loss_usd=51.0, peak_equity=1000.0)
        ok, reason = approve_entry(make_entry(), [], risk, 1000.0, params)
        assert not ok
        assert "daily_loss" in reason

    def test_allows_below_daily_loss(self):
        params = params_from_strategy("momentum")   # max_daily_loss_pct=5%, equity=$1000
        risk = make_risk(daily_loss_usd=20.0, peak_equity=1000.0)  # $20 < $50 limit
        ok, _ = approve_entry(make_entry(), [], risk, 1000.0, params)
        assert ok

    def test_degen_higher_tolerance(self):
        """Degen allows 10% daily loss — more room than momentum's 5%."""
        equity = 1000.0
        cfg_m = load_strategy("momentum")
        cfg_d = load_strategy("degen")
        limit_m = cfg_m.daily_loss_limit_usd(equity)
        limit_d = cfg_d.daily_loss_limit_usd(equity)
        assert limit_d > limit_m

    def test_defense_lower_tolerance(self):
        """Defense limits to 3% — tightest of the standard strategies."""
        params = params_from_strategy("defense")    # max_daily_loss_pct=3%, equity=$1000
        # 3% of $1000 = $30; $31 should block
        risk = make_risk(daily_loss_usd=31.0, peak_equity=1000.0)
        ok, reason = approve_entry(make_entry(), [], risk, 1000.0, params)
        assert not ok
        assert "daily_loss" in reason


# ─── CHECK 3: reserve_pct ─────────────────────────────────────────────────────

class TestReservePct:
    """Risk check 3: cash reserve must be maintained."""

    def test_blocks_when_reserve_violated(self):
        """Momentum: 20% reserve on $1000 = $200 must be kept uninvested.
        If $900 invested → available = $1000 - $200 - $900 = -$100 → blocked.
        """
        params = params_from_strategy("momentum")   # reserve_pct=20%, size_pct=10%
        # $900 invested: available = $1000 - $200 - $900 = -$100 → can't fit $100 position
        positions = [make_position("ETH", size_usd=900.0)]
        ok, reason = approve_entry(make_entry(), positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "reserve_pct" in reason

    def test_allows_when_enough_available(self):
        """Enough room after reserve for a new position."""
        params = params_from_strategy("momentum")   # reserve=20%, size=10%
        # $200 invested: available = $1000 - $200 - $200 = $600 → fits $100 (10%) position
        positions = [make_position("ETH", size_usd=200.0)]
        ok, _ = approve_entry(make_entry(), positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_watch_100_reserve(self):
        """Watch strategy has 100% reserve — always blocked."""
        params = params_from_strategy("watch")
        ok, reason = approve_entry(make_entry(), [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        # Should fail on watch_mode check before reserve check
        assert "watch_mode" in reason

    def test_reserve_usd_calculation(self):
        """reserve_usd() returns correct dollar amount."""
        cfg = load_strategy("momentum")   # reserve_pct=20%
        assert cfg.reserve_usd(100.0) == pytest.approx(20.0)
        assert cfg.reserve_usd(200.0) == pytest.approx(40.0)


# ─── CHECK 4: max_hold_hours (time exit) ──────────────────────────────────────

class TestMaxHoldHours:
    """Risk check 4: positions force-exit after max_hold_hours."""

    def test_time_exit_fires_when_expired(self):
        cfg = load_strategy("momentum")   # max_hold_hours=48
        params = _StrategyParams(cfg, 100.0)
        # Position open 49 hours ago — should trigger exit
        old_time = datetime.now(timezone.utc) - timedelta(hours=49)
        pos = make_position("BTC", entry_time=old_time)
        exits = check_time_exits([pos], params)
        assert len(exits) == 1
        assert exits[0]["coin"] == "BTC"
        assert "max_hold_hours" in exits[0]["reason"]

    def test_no_exit_when_within_limit(self):
        cfg = load_strategy("momentum")   # max_hold_hours=48
        params = _StrategyParams(cfg, 100.0)
        # Position open 10 hours ago — still within limit
        recent_time = datetime.now(timezone.utc) - timedelta(hours=10)
        pos = make_position("BTC", entry_time=recent_time)
        exits = check_time_exits([pos], params)
        assert exits == []

    def test_multiple_positions_mixed(self):
        """Only expired positions trigger time exit."""
        cfg = load_strategy("momentum")   # max_hold_hours=48
        params = _StrategyParams(cfg, 100.0)
        old_time    = datetime.now(timezone.utc) - timedelta(hours=50)
        recent_time = datetime.now(timezone.utc) - timedelta(hours=10)
        positions = [
            make_position("BTC", entry_time=old_time),
            make_position("ETH", entry_time=recent_time),
        ]
        exits = check_time_exits(positions, params)
        assert len(exits) == 1
        assert exits[0]["coin"] == "BTC"

    def test_degen_24h_expires_faster(self):
        """Degen 24h limit catches positions faster than momentum 48h."""
        cfg_degen    = load_strategy("degen")
        cfg_momentum = load_strategy("momentum")
        params_degen    = _StrategyParams(cfg_degen, 100.0)
        params_momentum = _StrategyParams(cfg_momentum, 100.0)

        # Position open 25 hours — expires for degen, not for momentum
        hold_time = datetime.now(timezone.utc) - timedelta(hours=25)
        pos = make_position("BTC", entry_time=hold_time)

        exits_degen    = check_time_exits([pos], params_degen)
        exits_momentum = check_time_exits([pos], params_momentum)

        assert len(exits_degen) == 1,    "Degen 24h: should have expired"
        assert len(exits_momentum) == 0, "Momentum 48h: should still be within limit"

    def test_defense_7d_hold(self):
        """Defense allows 168h (7d) holds."""
        cfg    = load_strategy("defense")   # max_hold_hours=168
        params = _StrategyParams(cfg, 100.0)
        hold_time = datetime.now(timezone.utc) - timedelta(hours=100)
        pos = make_position("BTC", entry_time=hold_time)
        exits = check_time_exits([pos], params)
        assert exits == [], "Defense 7d: 100h should still be within limit"

    def test_no_entry_time_skipped(self):
        """Positions without entry_time are skipped safely."""
        cfg    = load_strategy("momentum")
        params = _StrategyParams(cfg, 100.0)
        pos = {"coin": "BTC", "direction": "LONG"}  # no entry_time
        exits = check_time_exits([pos], params)
        assert exits == []

    def test_strategy_name_in_reason(self):
        """Exit reason includes strategy name for traceability."""
        cfg    = load_strategy("degen")
        params = _StrategyParams(cfg, 100.0)
        old_time = datetime.now(timezone.utc) - timedelta(hours=30)
        pos  = make_position("BTC", entry_time=old_time)
        exits = check_time_exits([pos], params)
        assert "degen" in exits[0]["reason"]


# ─── CHECK 5: entry_end_action ────────────────────────────────────────────────

class TestEntryEndAction:
    """Risk check 5: ENTRY_END events trigger hold or close based on strategy."""

    def test_hold_action_returns_no_exits(self):
        """Momentum entry_end_action=hold → no exits on ENTRY_END."""
        cfg    = load_strategy("momentum")
        params = _StrategyParams(cfg, 100.0)
        signals = [{"coin": "BTC", "event_type": "ENTRY_END"}]
        positions = [make_position("BTC")]
        exits = handle_entry_end_events(signals, positions, params)
        assert exits == []

    def test_close_action_returns_exit(self):
        """Funding entry_end_action=close → exit signal on ENTRY_END."""
        cfg    = load_strategy("funding")
        params = _StrategyParams(cfg, 100.0)
        signals = [{"coin": "BTC", "event_type": "ENTRY_END"}]
        positions = [make_position("BTC")]
        exits = handle_entry_end_events(signals, positions, params)
        assert len(exits) == 1
        assert exits[0]["coin"] == "BTC"
        assert "entry_end_action=close" in exits[0]["reason"]

    def test_close_only_affects_open_positions(self):
        """ENTRY_END close only triggers for coins we actually hold."""
        cfg    = load_strategy("funding")
        params = _StrategyParams(cfg, 100.0)
        signals = [{"coin": "XRP", "event_type": "ENTRY_END"}]   # no XRP position
        positions = [make_position("BTC")]
        exits = handle_entry_end_events(signals, positions, params)
        assert exits == []

    def test_fallback_is_hold(self):
        """Without strategy, entry_end_action defaults to hold."""
        params = params_no_strategy()
        assert params.entry_end_action == "hold"
        signals = [{"coin": "BTC"}]
        positions = [make_position("BTC")]
        exits = handle_entry_end_events(signals, positions, params)
        assert exits == []


# ─── CHECK 6: consensus_threshold ────────────────────────────────────────────

class TestConsensusThreshold:
    """Risk check 6: reject entries below consensus threshold."""

    def test_blocks_below_threshold(self):
        params = params_from_strategy("momentum")   # threshold=5
        entry = make_entry(consensus_layers=4)       # below threshold
        ok, reason = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "consensus_threshold" in reason

    def test_allows_at_threshold(self):
        params = params_from_strategy("momentum")   # threshold=5
        entry = make_entry(consensus_layers=5)       # exactly at threshold
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_allows_above_threshold(self):
        params = params_from_strategy("momentum")   # threshold=5
        entry = make_entry(consensus_layers=7)       # above threshold
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_sniper_requires_7_of_7(self):
        """Sniper only passes with 7/7 consensus."""
        params = params_from_strategy("sniper")     # threshold=7
        entry = make_entry(consensus_layers=6)       # 6/7 — should fail sniper
        ok, reason = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "consensus_threshold" in reason

    def test_sniper_passes_7_of_7(self):
        """Sniper passes when all 7 layers agree."""
        params = params_from_strategy("sniper")     # threshold=7
        entry = make_entry(consensus_layers=7)
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_no_consensus_field_skips_check(self):
        """Entry without consensus_layers field skips the consensus check."""
        params = params_from_strategy("momentum")
        entry = make_entry()
        del entry["consensus_layers"]               # remove consensus field
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok                                   # passes — no consensus data to check

    def test_fallback_consensus_default(self):
        """Without strategy, consensus threshold defaults to 5."""
        params = params_no_strategy()
        assert params.consensus_threshold == 5


# ─── CHECK 7: min_regime ──────────────────────────────────────────────────────

class TestMinRegime:
    """Risk check 7: reject entries when current regime not in allowed list."""

    def test_blocks_disallowed_regime(self):
        params = params_from_strategy("momentum")   # min_regime=[trending, stable]
        entry = make_entry()
        with patch("scanner.v6.controller._get_current_regime", return_value="chaotic"):
            ok, reason = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "min_regime" in reason

    def test_allows_in_allowed_regime(self):
        params = params_from_strategy("momentum")   # min_regime=[trending, stable]
        entry = make_entry()
        with patch("scanner.v6.controller._get_current_regime", return_value="trending"):
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_unknown_regime_skips_check(self):
        """When regime is unknown (no data), don't block entries."""
        params = params_from_strategy("momentum")
        entry = make_entry()
        with patch("scanner.v6.controller._get_current_regime", return_value="unknown"):
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_fade_allows_reverting(self):
        """Fade strategy is designed for reverting regime."""
        params = params_from_strategy("fade")
        entry = make_entry()
        with patch("scanner.v6.controller._get_current_regime", return_value="reverting"):
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_degen_accepts_reverting(self):
        """Degen accepts trending/stable/reverting — broader regime acceptance."""
        params = params_from_strategy("degen")
        cfg = load_strategy("degen")
        assert "reverting" in cfg.evaluation.min_regime

    def test_no_strategy_no_regime_filter(self):
        """Without strategy, min_regime is empty — no regime filter applied."""
        params = params_no_strategy()
        assert params.min_regime == []


# ─── CHECKS 8 & 9: position_size_pct + stop_loss_pct injection ───────────────

class TestParamInjection:
    """Checks 8 & 9: strategy params injected into entry dict for executor."""

    def test_position_size_pct_injected(self):
        """strategy_size_pct is added to approved entry."""
        cfg = load_strategy("momentum")   # position_size_pct=10
        params = _StrategyParams(cfg, 100.0)
        entry = make_entry()
        enriched = inject_strategy_params(entry, params)
        assert "strategy_size_pct" in enriched
        assert enriched["strategy_size_pct"] == 10.0

    def test_stop_loss_pct_injected_as_decimal(self):
        """stop_loss_pct is converted to decimal for executor compatibility."""
        cfg = load_strategy("momentum")   # stop_loss_pct=3 (%)
        params = _StrategyParams(cfg, 100.0)
        entry = make_entry()
        enriched = inject_strategy_params(entry, params)
        assert "stop_loss_pct" in enriched
        assert enriched["stop_loss_pct"] == pytest.approx(0.03)  # 3% → 0.03

    def test_strategy_name_tagged(self):
        """Enriched entry includes strategy_name for telemetry."""
        cfg = load_strategy("sniper")
        params = _StrategyParams(cfg, 100.0)
        enriched = inject_strategy_params(make_entry(), params)
        assert enriched["strategy_name"] == "sniper"

    def test_original_entry_not_mutated(self):
        """inject_strategy_params does not mutate the original entry dict."""
        cfg = load_strategy("momentum")
        params = _StrategyParams(cfg, 100.0)
        entry = make_entry()
        original_keys = set(entry.keys())
        inject_strategy_params(entry, params)
        assert set(entry.keys()) == original_keys

    def test_degen_larger_size(self):
        """Degen injects larger size_pct than defense."""
        cfg_degen   = load_strategy("degen")    # position_size_pct=15
        cfg_defense = load_strategy("defense")  # position_size_pct=7
        p_degen   = _StrategyParams(cfg_degen, 100.0)
        p_defense = _StrategyParams(cfg_defense, 100.0)
        e_degen   = inject_strategy_params(make_entry(), p_degen)
        e_defense = inject_strategy_params(make_entry(), p_defense)
        assert e_degen["strategy_size_pct"] > e_defense["strategy_size_pct"]

    def test_apex_widest_stop(self):
        """Apex injects widest stop loss."""
        cfg_apex   = load_strategy("apex")    # stop_loss_pct=8
        cfg_defense = load_strategy("defense") # stop_loss_pct=2
        p_apex   = _StrategyParams(cfg_apex, 100.0)
        p_defense = _StrategyParams(cfg_defense, 100.0)
        e_apex   = inject_strategy_params(make_entry(), p_apex)
        e_defense = inject_strategy_params(make_entry(), p_defense)
        assert e_apex["stop_loss_pct"] > e_defense["stop_loss_pct"]

    def test_fallback_no_size_injection(self):
        """Without strategy, position_size_pct is None — executor uses conviction sizer."""
        params = params_no_strategy()
        assert params.position_size_pct is None
        entry = make_entry()
        enriched = inject_strategy_params(entry, params)
        assert "strategy_size_pct" not in enriched

    def test_watch_has_no_size(self):
        """Watch strategy: position_size_pct=0 → None → no size injection."""
        params = params_from_strategy("watch")
        assert params.position_size_pct is None


# ─── DIRECTION FILTER ─────────────────────────────────────────────────────────

class TestDirectionFilter:
    """Strategy direction whitelist blocks disallowed trade sides."""

    def test_allows_long(self):
        params = params_from_strategy("momentum")
        entry = make_entry(direction="LONG")
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_allows_short(self):
        params = params_from_strategy("momentum")
        entry = make_entry(direction="SHORT")
        ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_case_insensitive(self):
        """Direction check is case-insensitive."""
        params = params_from_strategy("momentum")
        for direction in ("long", "LONG", "Long", "short", "SHORT"):
            entry = make_entry(direction=direction)
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
            assert ok, f"Should allow direction: {direction!r}"


# ─── WATCH MODE ───────────────────────────────────────────────────────────────

class TestWatchMode:
    """Watch strategy blocks ALL entry approvals."""

    def test_watch_blocks_everything(self):
        params = params_from_strategy("watch")
        ok, reason = approve_entry(make_entry(), [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "watch_mode" in reason

    def test_watch_blocks_regardless_of_consensus(self):
        """Even 7/7 consensus doesn't pass watch mode."""
        params = params_from_strategy("watch")
        entry = make_entry(consensus_layers=7)
        ok, reason = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "watch_mode" in reason


# ─── CAPITAL FLOOR ────────────────────────────────────────────────────────────

class TestCapitalFloor:
    """Capital floor always blocks entries regardless of strategy."""

    def test_blocks_below_floor(self):
        params = params_from_strategy("degen", equity=5.0)   # most aggressive, still blocked
        equity = 5.0
        risk = make_risk(peak_equity=100.0)
        ok, reason = approve_entry(make_entry(), [], risk, equity, params)
        assert not ok
        assert "capital_floor" in reason

    def test_allows_above_floor(self):
        params = params_from_strategy("momentum")
        equity = 950.0
        risk = make_risk(peak_equity=1000.0)
        ok, _ = approve_entry(make_entry(), [], risk, equity, params)
        assert ok


# ─── PER-COIN LIMITS ──────────────────────────────────────────────────────────

class TestPerCoinLimits:
    """Duplicate coin and opposing position checks."""

    def test_blocks_duplicate_coin(self):
        # Use large equity so reserve check passes, leaving per-coin as the gate
        params = params_from_strategy("momentum")
        positions = [make_position("BTC", size_usd=10.0)]
        entry = make_entry(coin="BTC")
        ok, reason = approve_entry(entry, positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "max_per_coin" in reason

    def test_blocks_opposing_position(self):
        # Use large equity so reserve check passes; opposing position should fire
        params = params_from_strategy("momentum")
        positions = [{"coin": "BTC", "direction": "LONG", "size_usd": 10.0}]
        entry = make_entry(coin="BTC", direction="SHORT")
        ok, reason = approve_entry(entry, positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        # max_per_coin fires first (coin=BTC already held regardless of direction)
        assert "max_per_coin" in reason or "opposing" in reason

    def test_allows_different_coin(self):
        # Use large equity so reserve + position checks comfortably pass
        params = params_from_strategy("momentum")
        positions = [make_position("ETH", size_usd=10.0)]
        entry = make_entry(coin="BTC")
        ok, _ = approve_entry(entry, positions, make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok


# ─── _StrategyParams FALLBACK BEHAVIOR ────────────────────────────────────────

class TestStrategyParamsFallback:
    """_StrategyParams falls back to config.py when no strategy active."""

    def test_fallback_has_strategy_false(self):
        params = params_no_strategy()
        assert not params.has_strategy

    def test_strategy_has_strategy_true(self):
        params = params_from_strategy("momentum")
        assert params.has_strategy

    def test_fallback_name(self):
        params = params_no_strategy()
        assert params.name == "fallback"

    def test_strategy_name(self):
        params = params_from_strategy("momentum")
        assert params.name == "momentum"

    def test_fallback_directions_unrestricted(self):
        params = params_no_strategy()
        assert "LONG" in params.directions
        assert "SHORT" in params.directions

    def test_fallback_no_regime_filter(self):
        params = params_no_strategy()
        assert params.min_regime == []

    def test_invested_usd_calculation(self):
        params = params_from_strategy("momentum")
        positions = [
            make_position("BTC", size_usd=50.0),
            make_position("ETH", size_usd=30.0),
        ]
        assert params.invested_usd(positions) == pytest.approx(80.0)

    def test_available_usd_after_reserve(self):
        """Available = equity - reserve - invested."""
        cfg    = load_strategy("momentum")  # reserve_pct=20%
        params = _StrategyParams(cfg, 1000.0)
        positions = [make_position("BTC", size_usd=400.0)]
        # $1000 - $200 reserve - $400 invested = $400 available
        assert params.available_usd(positions) == pytest.approx(400.0)

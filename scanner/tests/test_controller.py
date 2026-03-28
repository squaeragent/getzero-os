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
  - Position / TradeResult / SessionState dataclasses (Session 8b)
  - Near-miss detection logging

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
    Position,
    TradeResult,
    SessionState,
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
        with patch("scanner.v6.risk_gate._get_current_regime", return_value="chaotic"):
            ok, reason = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert not ok
        assert "min_regime" in reason

    def test_allows_in_allowed_regime(self):
        params = params_from_strategy("momentum")   # min_regime=[trending, stable]
        entry = make_entry()
        with patch("scanner.v6.risk_gate._get_current_regime", return_value="trending"):
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_unknown_regime_skips_check(self):
        """When regime is unknown (no data), don't block entries."""
        params = params_from_strategy("momentum")
        entry = make_entry()
        with patch("scanner.v6.risk_gate._get_current_regime", return_value="unknown"):
            ok, _ = approve_entry(entry, [], make_risk(peak_equity=1000.0), 1000.0, params)
        assert ok

    def test_fade_allows_reverting(self):
        """Fade strategy is designed for reverting regime."""
        params = params_from_strategy("fade")
        entry = make_entry()
        with patch("scanner.v6.risk_gate._get_current_regime", return_value="reverting"):
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


# ─── DATACLASSES (Session 8b) ─────────────────────────────────────────────────

class TestPositionDataclass:
    """Position dataclass — typed position record replaces raw dicts."""

    def _make_pos(self, **kwargs) -> Position:
        defaults = dict(
            id="BTC_LONG_123",
            coin="BTC",
            direction="LONG",
            strategy="momentum",
            session_id="sess_001",
            entry_price=50000.0,
            size_usd=500.0,
            size_coins=0.01,
            stop_loss_pct=0.03,
            stop_loss_price=48500.0,
            entry_time="2026-03-01T00:00:00+00:00",
            signal_name="test_signal",
            sharpe=2.0,
            hl_order_id="0xabc",
            sl_order_id="0xdef",
        )
        defaults.update(kwargs)
        return Position(**defaults)

    def test_create_position(self):
        pos = self._make_pos()
        assert pos.coin == "BTC"
        assert pos.direction == "LONG"
        assert pos.peak_pnl_pct == 0.0
        assert pos.trailing_activated is False

    def test_default_fields(self):
        pos = self._make_pos()
        assert pos.peak_pnl_pct == 0.0
        assert not pos.trailing_activated
        assert pos.win_rate == 0.0

    def test_to_dict(self):
        pos = self._make_pos()
        d   = pos.to_dict()
        assert isinstance(d, dict)
        assert d["coin"] == "BTC"
        assert d["strategy"] == "momentum"

    def test_from_dict_roundtrip(self):
        pos = self._make_pos()
        d   = pos.to_dict()
        pos2 = Position.from_dict(d)
        assert pos2.coin == pos.coin
        assert pos2.entry_price == pos.entry_price
        assert pos2.trailing_activated == pos.trailing_activated

    def test_from_dict_ignores_unknown_keys(self):
        pos = self._make_pos()
        d   = pos.to_dict()
        d["some_legacy_field"] = "junk"
        pos2 = Position.from_dict(d)
        assert pos2.coin == pos.coin

    def test_direction_values(self):
        long_pos  = self._make_pos(direction="LONG")
        short_pos = self._make_pos(direction="SHORT")
        assert long_pos.direction == "LONG"
        assert short_pos.direction == "SHORT"

    def test_trailing_activated_toggle(self):
        pos = self._make_pos(trailing_activated=True)
        assert pos.trailing_activated is True

    def test_peak_pnl_pct_update(self):
        pos = self._make_pos()
        assert pos.peak_pnl_pct == 0.0
        pos.peak_pnl_pct = 0.05
        assert pos.peak_pnl_pct == pytest.approx(0.05)


class TestTradeResultDataclass:
    """TradeResult dataclass — typed trade log record."""

    def _make_trade(self, **kwargs) -> TradeResult:
        defaults = dict(
            position_id="BTC_LONG_123",
            coin="BTC",
            direction="LONG",
            strategy="momentum",
            session_id="sess_001",
            entry_price=50000.0,
            exit_price=52000.0,
            size_usd=500.0,
            size_coins=0.01,
            entry_time="2026-03-01T00:00:00+00:00",
            exit_time="2026-03-01T06:00:00+00:00",
            exit_reason="max_hold_hours",
            pnl_usd=19.0,
            pnl_pct=0.04,
            pnl_usd_gross=20.0,
            fees_usd=1.0,
            slippage_pct=0.01,
            actual_notional=520.0,
            won=True,
            sharpe=2.0,
            win_rate=0.6,
        )
        defaults.update(kwargs)
        return TradeResult(**defaults)

    def test_create(self):
        tr = self._make_trade()
        assert tr.won is True
        assert tr.pnl_usd == pytest.approx(19.0)

    def test_losing_trade(self):
        tr = self._make_trade(pnl_usd=-10.0, won=False)
        assert not tr.won
        assert tr.pnl_usd < 0

    def test_to_dict(self):
        tr = self._make_trade()
        d  = tr.to_dict()
        assert d["coin"] == "BTC"
        assert d["won"] is True
        assert "zero_fee" in d


class TestSessionStateDataclass:
    """SessionState — lifecycle state machine."""

    def _make_session(self, **kwargs) -> SessionState:
        defaults = dict(
            session_id="sess_001",
            strategy="momentum",
            status="active",
            started_at="2026-03-01T00:00:00+00:00",
            expires_at="2026-03-03T00:00:00+00:00",
            equity_start=1000.0,
        )
        defaults.update(kwargs)
        return SessionState(**defaults)

    def test_initial_state(self):
        ss = self._make_session()
        assert ss.status == "active"
        assert ss.total_pnl == 0.0
        assert ss.trade_count == 0

    def test_result_card_no_trades(self):
        ss   = self._make_session(equity_end=1000.0)
        card = ss.result_card()
        assert card["roi_pct"] == 0.0
        assert card["trade_count"] == 0
        assert card["win_rate"] == 0

    def test_result_card_profitable(self):
        ss = self._make_session(
            equity_end=1100.0, total_pnl=100.0,
            trade_count=5, wins=4, losses=1,
            status="completed",
        )
        card = ss.result_card()
        assert card["roi_pct"] == pytest.approx(10.0)
        assert card["win_rate"] == pytest.approx(80.0)
        assert card["total_pnl"] == pytest.approx(100.0)

    def test_expired_state(self):
        ss = self._make_session(status="expired")
        assert ss.status == "expired"

    def test_near_misses_tracked(self):
        ss = self._make_session(near_misses=3)
        card = ss.result_card()
        assert card["near_misses"] == 3

    def test_all_states(self):
        for state in ["pending", "active", "completing", "completed", "expired"]:
            ss = self._make_session(status=state)
            assert ss.status == state


# ─── SESSION 8c: ROLLS ROYCE TESTS ───────────────────────────────────────────

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


# Re-import Controller and new symbols
from scanner.v6.controller import (
    Controller,
    log_decision,
    DECISION_LOG_FILE,
    EVENTS_LOG_FILE,
    CONTROLLER_STATE_FILE,
    append_jsonl,
    save_json_atomic,
    load_json,
    now_iso,
    _make_shutdown_handler,
)


# ─── DECISION LOG ────────────────────────────────────────────────────────────

class TestDecisionLog:
    """Decision log writes correct format."""

    def test_writes_correct_format(self, tmp_path):
        decision_file = tmp_path / "decisions.jsonl"
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", decision_file):
            log_decision(
                coin="BTC",
                strategy="momentum",
                layers_passed=5,
                verdict="approved",
                price=65000.0,
                reason="ok",
                session_id="abc123",
            )
        lines = decision_file.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["coin"] == "BTC"
        assert record["strategy"] == "momentum"
        assert record["layers_passed"] == 5
        assert record["verdict"] == "approved"
        assert record["price"] == 65000.0
        assert record["reason"] == "ok"
        assert record["session_id"] == "abc123"
        assert "ts" in record

    def test_rejected_verdict_written(self, tmp_path):
        decision_file = tmp_path / "decisions.jsonl"
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", decision_file):
            log_decision(
                coin="ETH", strategy="defense", layers_passed=2,
                verdict="rejected", price=3000.0, reason="max_positions",
            )
        record = json.loads(decision_file.read_text().strip())
        assert record["verdict"] == "rejected"

    def test_approve_entry_writes_decision_log(self, tmp_path):
        """approve_entry with a controller should write to decision log."""
        decision_file = tmp_path / "decisions.jsonl"
        ctrl = Controller()
        params = params_from_strategy("momentum")
        entry = make_entry(consensus_layers=5)
        risk  = make_risk(peak_equity=1000.0)

        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", decision_file), \
             patch("scanner.v6.controller.BUS_DIR", tmp_path), \
             patch("scanner.v6.controller.load_json", return_value={}):
            ok, reason = approve_entry(entry, [], risk, 1000.0, params, ctrl)
        assert decision_file.exists()
        record = json.loads(decision_file.read_text().strip())
        assert record["verdict"] in ("approved", "rejected", "near_miss")


# ─── HARD CAPS ───────────────────────────────────────────────────────────────

class TestHardCaps:
    """Hard caps enforce absolute safety limits."""

    def _make_ctrl(self) -> Controller:
        return Controller()

    def test_rejects_oversized_position(self):
        """Position size > 25% of equity → reject."""
        ctrl  = self._make_ctrl()
        entry = make_entry()
        entry["strategy_size_pct"] = 30.0   # 30% > HARD_MAX_POSITION_PCT (25%)
        ok, reason = ctrl.check_hard_caps(entry, [], equity=1000.0)
        assert not ok
        assert "hard_cap:position_size" in reason

    def test_allows_position_within_cap(self):
        ctrl  = self._make_ctrl()
        entry = make_entry()
        entry["strategy_size_pct"] = 20.0   # 20% <= 25%
        ok, reason = ctrl.check_hard_caps(entry, [], equity=1000.0)
        assert ok

    def test_rejects_when_exposure_limit_exceeded(self):
        """Total exposure >= 80% of equity → reject."""
        ctrl = self._make_ctrl()
        # 3 positions × $300 = $900 of $1000 = 90% → over limit
        positions = [make_position(f"COIN{i}", size_usd=300.0) for i in range(3)]
        entry = make_entry()
        # Set strategy_size_pct within hard cap so only exposure fires
        entry["strategy_size_pct"] = 10.0
        ok, reason = ctrl.check_hard_caps(entry, positions, equity=1000.0)
        assert not ok
        assert "hard_cap:exposure" in reason

    def test_allows_exposure_under_cap(self):
        ctrl = self._make_ctrl()
        # $500 / $1000 = 50% → under 80% limit
        positions = [make_position("ETH", size_usd=500.0)]
        entry = make_entry()
        entry["strategy_size_pct"] = 10.0   # within size cap
        ok, _  = ctrl.check_hard_caps(entry, positions, equity=1000.0)
        assert ok

    def test_rejects_when_order_rate_exceeded(self):
        """More than 10 orders per minute → reject."""
        ctrl = self._make_ctrl()
        # Fill up the per-minute bucket
        now_ts = time.time()
        ctrl._orders_this_minute = [now_ts] * ctrl.HARD_MAX_ORDERS_PER_MIN
        entry = make_entry()
        ok, reason = ctrl.check_hard_caps(entry, [], equity=1000.0)
        assert not ok
        assert "hard_cap:orders_per_min" in reason

    def test_rejects_when_session_order_cap_hit(self):
        """100 orders in session → reject."""
        ctrl = self._make_ctrl()
        ctrl._orders_this_session = ctrl.HARD_MAX_ORDERS_PER_SESSION
        entry = make_entry()
        ok, reason = ctrl.check_hard_caps(entry, [], equity=1000.0)
        assert not ok
        assert "hard_cap:orders_per_session" in reason

    def test_approve_entry_applies_hard_caps(self):
        """approve_entry with controller should reject on hard cap breach."""
        ctrl  = self._make_ctrl()
        ctrl._orders_this_session = ctrl.HARD_MAX_ORDERS_PER_SESSION
        params = params_from_strategy("momentum")
        entry  = make_entry(consensus_layers=5)
        risk   = make_risk(peak_equity=1000.0)
        ok, reason = approve_entry(entry, [], risk, 1000.0, params, ctrl)
        assert not ok
        assert "hard_cap" in reason


# ─── REJECTION COUNTER ────────────────────────────────────────────────────────

class TestRejectionCounter:
    """Rejection counter increments correctly."""

    def test_eval_count_increments(self):
        ctrl   = Controller()
        params = params_from_strategy("momentum")
        entry  = make_entry(consensus_layers=5)
        risk   = make_risk(peak_equity=1000.0)
        assert ctrl.eval_count == 0
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", Path("/tmp/test_decisions.jsonl")), \
             patch("scanner.v6.controller.load_json", return_value={}):
            approve_entry(entry, [], risk, 1000.0, params, ctrl)
        assert ctrl.eval_count == 1

    def test_reject_count_increments_on_rejection(self):
        ctrl   = Controller()
        params = params_from_strategy("momentum")
        # 5 positions fills max_positions=5
        positions = [make_position(f"C{i}", size_usd=10.0) for i in range(5)]
        entry  = make_entry(consensus_layers=5)
        risk   = make_risk(peak_equity=1000.0)
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", Path("/tmp/test_decisions.jsonl")), \
             patch("scanner.v6.controller.load_json", return_value={}):
            ok, _ = approve_entry(entry, positions, risk, 1000.0, params, ctrl)
        assert not ok
        assert ctrl.reject_count == 1

    def test_reject_count_not_incremented_on_approval(self):
        ctrl   = Controller()
        params = params_from_strategy("momentum")
        entry  = make_entry(consensus_layers=5)
        entry["strategy_size_pct"] = 10.0   # within hard cap (10% < 25%)
        risk   = make_risk(peak_equity=1000.0)
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", Path("/tmp/test_decisions.jsonl")), \
             patch("scanner.v6.controller.load_json", return_value={}):
            ok, _ = approve_entry(entry, [], risk, 1000.0, params, ctrl)
        assert ok
        assert ctrl.reject_count == 0

    def test_narrative_built_from_timeline(self):
        ctrl = Controller()
        ctrl.add_timeline_event("Session started", "momentum")
        ctrl.add_timeline_event("Entered BTC LONG", "strategy=momentum")
        ctrl.eval_count   = 47
        ctrl.reject_count = 45
        narrative = ctrl.build_narrative()
        assert "Session started" in narrative
        assert "47 evaluations" in narrative
        assert "45 rejected" in narrative
        assert "selectivity" in narrative


# ─── EVENT BUS ───────────────────────────────────────────────────────────────

class TestEventBus:
    """Event bus emits correct event types."""

    def test_emit_stores_in_process(self, tmp_path):
        ctrl = Controller()
        events_file = tmp_path / "events.jsonl"
        with patch("scanner.v6.controller.EVENTS_LOG_FILE", events_file):
            ctrl.emit("SESSION_STARTED", {"mode": "DRY"})
        assert len(ctrl.events) == 1
        assert ctrl.events[0]["type"] == "SESSION_STARTED"
        assert ctrl.events[0]["mode"] == "DRY"
        assert "ts" in ctrl.events[0]

    def test_emit_writes_to_file(self, tmp_path):
        ctrl = Controller()
        events_file = tmp_path / "events.jsonl"
        with patch("scanner.v6.controller.EVENTS_LOG_FILE", events_file):
            ctrl.emit("TRADE_ENTERED", {"coin": "BTC", "direction": "LONG"})
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "TRADE_ENTERED"
        assert record["coin"] == "BTC"

    def test_emit_multiple_event_types(self, tmp_path):
        ctrl = Controller()
        events_file = tmp_path / "events.jsonl"
        event_types = [
            "SESSION_STARTED", "TRADE_ENTERED", "TRADE_EXITED",
            "NEAR_MISS", "SESSION_COMPLETED", "RISK_BREACH", "HEARTBEAT",
        ]
        with patch("scanner.v6.controller.EVENTS_LOG_FILE", events_file):
            for et in event_types:
                ctrl.emit(et, {"test": True})
        assert len(ctrl.events) == len(event_types)
        written_types = [
            json.loads(l)["type"]
            for l in events_file.read_text().strip().split("\n")
        ]
        assert written_types == event_types


# ─── GRACEFUL SHUTDOWN ────────────────────────────────────────────────────────

class TestGracefulShutdown:
    """Graceful shutdown writes state file; recovery loads it."""

    def test_write_state_creates_file(self, tmp_path):
        ctrl = Controller()
        ctrl._orders_this_session = 5
        ctrl.eval_count   = 10
        ctrl.reject_count = 8

        state_file    = tmp_path / "controller_state.json"
        positions_file = tmp_path / "positions.json"
        # Write dummy positions
        save_json_atomic(positions_file, {"positions": [{"coin": "BTC", "size_usd": 500.0}]})

        with patch("scanner.v6.controller.CONTROLLER_STATE_FILE", state_file), \
             patch("scanner.v6.controller.POSITIONS_FILE", positions_file), \
             patch("scanner.v6.controller.load_json_locked",
                   return_value={"positions": [{"coin": "BTC"}]}):
            ctrl.write_state()

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["orders_this_session"] == 5
        assert state["eval_count"] == 10
        assert state["reject_count"] == 8
        assert "ts" in state

    def test_write_state_includes_positions(self, tmp_path):
        ctrl = Controller()
        state_file = tmp_path / "controller_state.json"
        mock_positions = [{"coin": "ETH", "direction": "SHORT", "size_usd": 200.0}]

        with patch("scanner.v6.controller.CONTROLLER_STATE_FILE", state_file), \
             patch("scanner.v6.controller.load_json_locked",
                   return_value={"positions": mock_positions}):
            ctrl.write_state()

        state = json.loads(state_file.read_text())
        assert len(state["positions"]) == 1
        assert state["positions"][0]["coin"] == "ETH"

    def test_write_state_includes_session(self, tmp_path):
        ctrl = Controller()
        state_file = tmp_path / "controller_state.json"
        session = SessionState(
            session_id="sess_recovery",
            strategy="momentum",
            status="active",
            started_at=now_iso(),
            expires_at=now_iso(),
            equity_start=1000.0,
            total_pnl=50.0,
        )
        with patch("scanner.v6.controller.CONTROLLER_STATE_FILE", state_file), \
             patch("scanner.v6.controller.load_json_locked",
                   return_value={"positions": []}):
            ctrl.write_state(session=session)

        state = json.loads(state_file.read_text())
        assert "session" in state
        assert state["session"]["session_id"] == "sess_recovery"
        assert state["session"]["total_pnl"] == 50.0

    def test_shutdown_handler_writes_state(self, tmp_path):
        """Signal handler writes state file before exit."""
        import signal as _signal
        ctrl = Controller()
        ctrl.eval_count = 42
        state_file  = tmp_path / "controller_state.json"
        events_file = tmp_path / "events.jsonl"

        with patch("scanner.v6.controller.CONTROLLER_STATE_FILE", state_file), \
             patch("scanner.v6.controller.EVENTS_LOG_FILE", events_file), \
             patch("scanner.v6.controller.load_json_locked",
                   return_value={"positions": []}), \
             patch("sys.exit") as mock_exit:
            handler = _make_shutdown_handler(ctrl)
            handler(_signal.SIGTERM, None)

        assert state_file.exists()
        mock_exit.assert_called_once_with(0)


# ─── STOP VERIFICATION TESTS ─────────────────────────────────────────────────

from scanner.v6.controller import open_trade, rotate_logs_start, rotate_logs_end


class TestStopVerification:
    """Stop verification after placement: confirms stop oid is in open orders."""

    def _make_mock_client(self, stop_oid="12345", open_orders_returns=None):
        """Build a mock HLClient that succeeds at placing a trade + stop."""
        client = MagicMock()
        client.get_price.return_value = 100.0
        client.get_predicted_funding.return_value = 0.0
        client.get_l2_book.return_value = {
            "bids": [(99.9, 10.0)], "asks": [(100.1, 10.0)],
            "bid_depth_usd": 999.0, "ask_depth_usd": 999.0,
        }
        client.get_fee_rates.return_value = {"taker": 0.00045, "maker": 0.00015}
        client.round_price.side_effect = lambda x: round(x, 2)
        client.market_buy.return_value = {
            "status": "ok",
            "response": {"data": {"statuses": [{"filled": {"avgPx": "100.0", "totalSz": "1.0", "oid": 99}}]}},
        }
        client.market_sell.return_value = {
            "status": "ok",
            "response": {"data": {"statuses": [{"filled": {"avgPx": "100.0", "totalSz": "1.0", "oid": 99}}]}},
        }
        client.place_stop_loss.return_value = {
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": int(stop_oid)}}]}},
        }
        if open_orders_returns is not None:
            client.get_open_orders.side_effect = open_orders_returns
        else:
            client.get_open_orders.return_value = [{"oid": int(stop_oid), "coin": "BTC"}]
        client._sign_and_send.return_value = {"status": "ok"}
        return client

    @patch("scanner.v6.controller.save_json_locked")
    @patch("scanner.v6.controller.load_json_locked", return_value={"positions": []})
    @patch("scanner.v6.controller.send_alert")
    @patch("scanner.v6.controller.time")
    @patch("scanner.v6.controller.COIN_TO_ASSET", {"BTC": 0})
    @patch("scanner.v6.controller.COIN_SZ_DECIMALS", {"BTC": 5})
    def test_stop_verified_after_placement(self, mock_time, mock_alert, mock_load, mock_save):
        """After placing a stop and getting oid, verify it appears in open orders."""
        mock_time.time.return_value = 1000000
        mock_time.sleep = MagicMock()

        client = self._make_mock_client(stop_oid="12345")
        trade = {
            "coin": "BTC", "direction": "LONG", "signal_name": "test",
            "sharpe": 2.0, "win_rate": 55.0, "composite_score": 5.0,
            "max_hold_hours": 12, "stop_loss_pct": 0.05,
        }

        result = open_trade(client, trade, dry=False)
        assert result is True
        # Verify get_open_orders was called (for stop verification)
        assert client.get_open_orders.called

    @patch("scanner.v6.controller.save_json_locked")
    @patch("scanner.v6.controller.load_json_locked", return_value={"positions": []})
    @patch("scanner.v6.controller.send_alert")
    @patch("scanner.v6.controller.time")
    @patch("scanner.v6.controller.COIN_TO_ASSET", {"BTC": 0})
    @patch("scanner.v6.controller.COIN_SZ_DECIMALS", {"BTC": 5})
    def test_stop_verification_fails_closes_position(self, mock_time, mock_alert, mock_load, mock_save):
        """If stop oid is NOT in open orders after 2 attempts, position is closed."""
        mock_time.time.return_value = 1000000
        mock_time.sleep = MagicMock()

        # get_open_orders returns empty list (stop not found) for both attempts
        client = self._make_mock_client(
            stop_oid="12345",
            open_orders_returns=[[], []],
        )
        trade = {
            "coin": "BTC", "direction": "LONG", "signal_name": "test",
            "sharpe": 2.0, "win_rate": 55.0, "composite_score": 5.0,
            "max_hold_hours": 12, "stop_loss_pct": 0.05,
        }

        result = open_trade(client, trade, dry=False)
        assert result is False
        # Should have called market_sell to close (LONG position → sell to close)
        assert client.market_sell.called


# ─── LOG ROTATION TESTS ──────────────────────────────────────────────────────

class TestLogRotation:
    """Log rotation creates per-session files and archives on end."""

    def test_rotate_logs_start_archives_existing(self, tmp_path):
        """rotate_logs_start renames existing log files."""
        decisions = tmp_path / "decisions.jsonl"
        decisions.write_text('{"test": true}\n')

        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", decisions), \
             patch("scanner.v6.controller.EVENTS_LOG_FILE", tmp_path / "events.jsonl"), \
             patch("scanner.v6.trade_logger.NEAR_MISS_LOG_FILE", tmp_path / "near_misses.jsonl"), \
             patch("scanner.v6.controller._ROTATABLE_LOGS", [decisions]):
            rotate_logs_start("test_session_123")

        # Original file should have been renamed
        assert not decisions.exists()
        archived = tmp_path / "decisions_pre_test_session_123.jsonl"
        assert archived.exists()

    def test_rotate_logs_end_archives(self, tmp_path):
        """rotate_logs_end renames active logs with session suffix."""
        decisions = tmp_path / "decisions.jsonl"
        decisions.write_text('{"test": true}\n')

        with patch("scanner.v6.controller._ROTATABLE_LOGS", [decisions]):
            rotate_logs_end("session_42")

        assert not decisions.exists()
        archived = tmp_path / "decisions_session_42.jsonl"
        assert archived.exists()

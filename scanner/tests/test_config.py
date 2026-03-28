"""
Config tests — verify all trading parameters are sane.
No mainnet calls. Pure unit tests on config values.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.config import (
    get_dynamic_limits, get_leverage, get_stop_pct, get_slippage,
    get_trailing_trigger, validate_config,
    CAPITAL, CAPITAL_FLOOR_PCT, CAPITAL_FLOOR, DAILY_LOSS_LIMIT_PCT,
    MAX_POSITION_PCT, MIN_POSITION_PCT, FEE_RATE,
    MAX_PER_COIN, COIN_LEVERAGE, DEFAULT_LEVERAGE,
    COIN_STOP_PCT, STOP_LOSS_PCT, COIN_SLIPPAGE, DEFAULT_SLIPPAGE,
    MIN_HOLD_MINUTES, CYCLE_SECONDS, RECONCILE_INTERVAL,
    HEARTBEAT_INTERVAL, FAILED_ENTRY_COOLDOWN, ALERT_COOLDOWN,
    HARD_MAX_POSITION_PCT, HARD_MAX_EXPOSURE_PCT,
    HARD_MAX_ORDERS_PER_MIN, HARD_MAX_ORDERS_PER_SESSION,
    ALL_COINS, ACTIVE_COINS_COUNT, STRATEGY_VERSION, API_VERSION,
    BUS_DIR, DATA_DIR, V6_DIR,
)


class TestConfigPaths:
    """All configured paths exist or are reasonable."""

    def test_v6_dir_exists(self):
        assert V6_DIR.exists()

    def test_bus_dir_is_inside_v6(self):
        assert str(BUS_DIR).startswith(str(V6_DIR))

    def test_data_dir_is_inside_v6(self):
        assert str(DATA_DIR).startswith(str(V6_DIR))


class TestConfigConstants:
    """Validate all trading constants are within safe ranges."""

    def test_capital_floor_pct_reasonable(self):
        """Floor between 20-60% of peak."""
        assert 0.20 <= CAPITAL_FLOOR_PCT <= 0.60

    def test_daily_loss_limit_reasonable(self):
        """Daily loss limit between 5-25% of equity."""
        assert 0.05 <= DAILY_LOSS_LIMIT_PCT <= 0.25

    def test_max_position_pct_reasonable(self):
        """Max position between 15-60% of equity."""
        assert 0.15 <= MAX_POSITION_PCT <= 0.60

    def test_min_position_pct_reasonable(self):
        """Min position between 3-15% of equity."""
        assert 0.03 <= MIN_POSITION_PCT <= 0.15

    def test_fee_rate_reasonable(self):
        """Fee rate between 0.01-0.1%."""
        assert 0.0001 <= FEE_RATE <= 0.001

    def test_max_per_coin_is_one(self):
        """Only one position per coin (critical risk control)."""
        assert MAX_PER_COIN == 1


class TestDynamicLimits:
    """Dynamic limit scaling with equity."""

    def test_scales_max_positions(self):
        """More equity = more positions allowed."""
        low = get_dynamic_limits(100)
        high = get_dynamic_limits(5000)
        assert high["max_positions"] >= low["max_positions"]

    def test_max_position_scales_linearly(self):
        """Max position USD scales with equity."""
        limits = get_dynamic_limits(1000)
        assert limits["max_position_usd"] == pytest.approx(1000 * MAX_POSITION_PCT)

    def test_min_position_has_floor(self):
        """Min position is at least $10."""
        limits = get_dynamic_limits(50)
        assert limits["min_position_usd"] >= 10

    def test_daily_loss_scales(self):
        """Daily loss limit scales with equity."""
        limits = get_dynamic_limits(1000)
        assert limits["daily_loss_limit"] == pytest.approx(1000 * DAILY_LOSS_LIMIT_PCT)


class TestLeverage:
    """Leverage tiers are sane."""

    def test_no_leverage_above_10x(self):
        for coin, lev in COIN_LEVERAGE.items():
            assert lev <= 10, f"{coin} has {lev}x leverage"

    def test_no_leverage_below_1x(self):
        for coin, lev in COIN_LEVERAGE.items():
            assert lev >= 1, f"{coin} has {lev}x leverage"

    def test_default_leverage_sane(self):
        assert 1 <= DEFAULT_LEVERAGE <= 10

    def test_unknown_coin_uses_default(self):
        assert get_leverage("DEFINITELY_NOT_A_REAL_COIN") == DEFAULT_LEVERAGE

    def test_btc_eth_highest(self):
        """BTC and ETH get highest leverage."""
        btc = get_leverage("BTC")
        eth = get_leverage("ETH")
        for coin in ["TRUMP", "FARTCOIN", "PUMP"]:
            assert btc >= get_leverage(coin), f"BTC should have >= leverage than {coin}"
            assert eth >= get_leverage(coin), f"ETH should have >= leverage than {coin}"


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:
    """validate_config() catches misconfigurations."""

    def test_current_config_valid(self):
        """Current config passes all validation checks."""
        errors = validate_config()
        assert errors == [], f"Config errors: {errors}"


# ═══════════════════════════════════════════════════════════════════════════════
# STOP LOSS / SLIPPAGE
# ═══════════════════════════════════════════════════════════════════════════════

class TestStopLoss:
    """Per-coin stop loss configuration."""

    def test_all_stops_positive(self):
        for coin, pct in COIN_STOP_PCT.items():
            assert 0 < pct < 1, f"{coin} stop {pct} out of range"

    def test_default_stop_reasonable(self):
        assert 0.01 <= STOP_LOSS_PCT <= 0.15

    def test_get_stop_uses_coin_value(self):
        """Known coin returns its configured stop."""
        assert get_stop_pct("BTC") == COIN_STOP_PCT["BTC"]

    def test_get_stop_unknown_uses_default(self):
        assert get_stop_pct("UNKNOWN_XYZ") == STOP_LOSS_PCT

    def test_signal_stop_override(self):
        """Signal stop overrides coin stop if tighter."""
        coin_stop = COIN_STOP_PCT.get("BTC", STOP_LOSS_PCT)
        tighter = coin_stop * 0.5
        assert get_stop_pct("BTC", signal_stop=tighter) == tighter

    def test_signal_stop_ignored_if_wider(self):
        """Signal stop is ignored if wider than coin stop."""
        coin_stop = COIN_STOP_PCT.get("BTC", STOP_LOSS_PCT)
        wider = coin_stop * 2.0
        assert get_stop_pct("BTC", signal_stop=wider) == coin_stop

    def test_btc_tighter_than_memes(self):
        """BTC has tighter stops than meme coins."""
        btc_stop = COIN_STOP_PCT.get("BTC", STOP_LOSS_PCT)
        for meme in ["FARTCOIN", "TRUMP", "PUMP"]:
            if meme in COIN_STOP_PCT:
                assert btc_stop <= COIN_STOP_PCT[meme], f"BTC should have <= stop than {meme}"


class TestSlippage:
    """Per-coin slippage configuration."""

    def test_all_slippage_positive(self):
        for coin, slip in COIN_SLIPPAGE.items():
            assert 0 < slip < 0.1, f"{coin} slippage {slip} out of range"

    def test_default_slippage_reasonable(self):
        assert 0.001 <= DEFAULT_SLIPPAGE <= 0.05

    def test_get_slippage_known_coin(self):
        assert get_slippage("BTC") == COIN_SLIPPAGE["BTC"]

    def test_get_slippage_unknown_coin(self):
        assert get_slippage("UNKNOWN_XYZ") == DEFAULT_SLIPPAGE

    def test_btc_tightest_slippage(self):
        """BTC (deep book) has tightest slippage."""
        btc_slip = COIN_SLIPPAGE.get("BTC", DEFAULT_SLIPPAGE)
        for coin, slip in COIN_SLIPPAGE.items():
            assert btc_slip <= slip, f"BTC should have <= slippage than {coin}"


class TestTrailingStop:
    """Trailing stop trigger calculation."""

    def test_trailing_trigger_is_half_stop(self):
        for coin in ["BTC", "ETH", "SOL"]:
            trigger = get_trailing_trigger(coin)
            stop = get_stop_pct(coin)
            assert trigger == pytest.approx(stop * 0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# TIMING CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimingConstants:
    """Controller and system timing."""

    def test_cycle_seconds_positive(self):
        assert CYCLE_SECONDS >= 1

    def test_reconcile_interval_longer_than_cycle(self):
        assert RECONCILE_INTERVAL > CYCLE_SECONDS

    def test_heartbeat_interval_reasonable(self):
        assert 10 <= HEARTBEAT_INTERVAL <= 600

    def test_cooldowns_positive(self):
        assert FAILED_ENTRY_COOLDOWN > 0
        assert ALERT_COOLDOWN > 0

    def test_min_hold_positive(self):
        assert MIN_HOLD_MINUTES > 0


# ═══════════════════════════════════════════════════════════════════════════════
# HARD SAFETY CAPS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHardCaps:
    """Safety caps that can never be exceeded."""

    def test_max_position_pct_cap(self):
        assert 1 <= HARD_MAX_POSITION_PCT <= 100

    def test_max_exposure_pct_cap(self):
        assert 1 <= HARD_MAX_EXPOSURE_PCT <= 100

    def test_max_orders_per_min_cap(self):
        assert HARD_MAX_ORDERS_PER_MIN >= 1

    def test_max_orders_per_session_cap(self):
        assert HARD_MAX_ORDERS_PER_SESSION >= 1

    def test_exposure_exceeds_position(self):
        """Exposure cap >= position cap (logical consistency)."""
        assert HARD_MAX_EXPOSURE_PCT >= HARD_MAX_POSITION_PCT


# ═══════════════════════════════════════════════════════════════════════════════
# COIN UNIVERSE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoinUniverse:
    """Coin universe configuration."""

    def test_coins_not_empty(self):
        assert len(ALL_COINS) > 0

    def test_no_duplicate_coins(self):
        assert len(ALL_COINS) == len(set(ALL_COINS))

    def test_btc_eth_included(self):
        assert "BTC" in ALL_COINS
        assert "ETH" in ALL_COINS

    def test_active_coins_within_universe(self):
        assert ACTIVE_COINS_COUNT <= len(ALL_COINS)

    def test_coins_alphabetically_sorted(self):
        """Coin list should be sorted for readability."""
        assert ALL_COINS == sorted(ALL_COINS)


# ═══════════════════════════════════════════════════════════════════════════════
# API VERSION
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPIVersion:
    """API version metadata."""

    def test_version_is_semver(self):
        """API version follows semver format."""
        import re
        assert re.match(r"^\d+\.\d+\.\d+$", API_VERSION)

    def test_strategy_version_matches(self):
        """Strategy version is 6 (V6 engine)."""
        assert STRATEGY_VERSION == 6

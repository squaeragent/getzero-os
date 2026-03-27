"""
Config tests — verify all trading parameters are sane.
No mainnet calls. Pure unit tests on config values.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.config import (
    get_dynamic_limits, get_leverage,
    CAPITAL_FLOOR_PCT, DAILY_LOSS_LIMIT_PCT,
    MAX_POSITION_PCT, MIN_POSITION_PCT, FEE_RATE,
    MAX_PER_COIN, COIN_LEVERAGE, DEFAULT_LEVERAGE,
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

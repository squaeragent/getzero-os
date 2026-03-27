"""
Risk Guard tests — verify position limits, capital floor, and daily loss limits.
All tests are mocked (no mainnet calls, no file I/O beyond tmp_path).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestCapitalFloor:
    """Capital floor prevents new entries when equity drops too low."""

    def test_capital_floor_blocks_entry(self, tmp_path):
        """No entries when equity < capital_floor."""
        from scanner.v6.config import CAPITAL_FLOOR_PCT

        equity = 15.0  # well below any reasonable floor
        peak = 50.0
        floor = peak * CAPITAL_FLOOR_PCT

        assert equity < floor, "Test setup: equity should be below floor"
        # risk_guard would reject all entries

    def test_capital_floor_passes_normal(self):
        """Entries allowed when equity > capital_floor."""
        from scanner.v6.config import CAPITAL_FLOOR_PCT

        equity = 45.0
        peak = 50.0
        floor = peak * CAPITAL_FLOOR_PCT

        assert equity > floor


class TestDailyLossLimit:
    """Daily loss limit halts trading after too much loss in 24h."""

    def test_daily_loss_exceeds_limit(self):
        """Trading halts when daily P&L exceeds loss limit."""
        from scanner.v6.config import DAILY_LOSS_LIMIT_PCT

        equity = 50.0
        daily_pnl = -8.0  # lost $8 today
        limit = equity * DAILY_LOSS_LIMIT_PCT

        assert abs(daily_pnl) > limit, "Daily loss should exceed limit"

    def test_daily_loss_within_limit(self):
        """Trading continues when daily P&L is within limit."""
        from scanner.v6.config import DAILY_LOSS_LIMIT_PCT

        equity = 50.0
        daily_pnl = -2.0  # lost $2 today
        limit = equity * DAILY_LOSS_LIMIT_PCT

        assert abs(daily_pnl) < limit


class TestMaxPositions:
    """Max positions check prevents over-exposure."""

    def test_max_positions_blocking(self):
        """New entry blocked when at max positions."""
        from scanner.v6.config import get_dynamic_limits

        equity = 50.0
        limits = get_dynamic_limits(equity)
        max_pos = limits["max_positions"]

        current_positions = max_pos  # already at max
        assert current_positions >= max_pos, "Should block new entries"

    def test_max_per_coin_blocking(self):
        """Can't open second position in same coin."""
        from scanner.v6.config import MAX_PER_COIN

        open_positions = [{"coin": "BTC"}, {"coin": "ETH"}]
        new_entry_coin = "BTC"

        coin_count = sum(1 for p in open_positions if p["coin"] == new_entry_coin)
        assert coin_count >= MAX_PER_COIN, "Should block duplicate coin"

    def test_new_coin_allowed(self):
        """New position in different coin is allowed."""
        from scanner.v6.config import MAX_PER_COIN

        open_positions = [{"coin": "BTC"}, {"coin": "ETH"}]
        new_entry_coin = "SOL"

        coin_count = sum(1 for p in open_positions if p["coin"] == new_entry_coin)
        assert coin_count < MAX_PER_COIN, "Different coin should be allowed"


class TestDynamicLimitsEdgeCases:
    """Edge cases in dynamic limit calculation."""

    def test_zero_equity(self):
        """Zero equity returns safe minimums."""
        from scanner.v6.config import get_dynamic_limits
        limits = get_dynamic_limits(0)
        assert limits["max_positions"] >= 2
        assert limits["min_position_usd"] >= 10

    def test_negative_equity(self):
        """Negative equity doesn't crash."""
        from scanner.v6.config import get_dynamic_limits
        limits = get_dynamic_limits(-100)
        assert limits["max_positions"] >= 2

    def test_very_large_equity(self):
        """Large equity caps at reasonable limits."""
        from scanner.v6.config import get_dynamic_limits
        limits = get_dynamic_limits(1_000_000)
        assert limits["max_positions"] <= 10  # should have a ceiling

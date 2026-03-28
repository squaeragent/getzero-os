"""Tests for scanner/v6/mcp_server.py — validation, tier gating, tool registration."""

import re

import pytest
from unittest.mock import patch, MagicMock

from scanner.v6.mcp_server import (
    _validate_coin,
    _validate_strategy,
    _validate_session_id,
    _validate_limit,
    _gate,
    _COIN_RE,
    _STRATEGY_RE,
    _SESSION_ID_RE,
    _MODE_VALID,
    mcp,
)


# ═══════════════════════════════════════════════════════════════════════════════
# COIN VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateCoin:
    """Coin name input validation."""

    def test_valid_coins(self):
        """Standard coin symbols pass validation."""
        for coin in ["BTC", "ETH", "SOL", "DOGE", "XRP", "LINK"]:
            assert _validate_coin(coin) is None, f"{coin} should be valid"

    def test_alphanumeric_allowed(self):
        """Alphanumeric coins like 1000PEPE pass."""
        assert _validate_coin("1000PEPE") is None

    def test_empty_string_rejected(self):
        """Empty string is invalid."""
        result = _validate_coin("")
        assert result is not None
        assert "error" in result

    def test_none_rejected(self):
        """None is invalid."""
        result = _validate_coin(None)
        assert result is not None

    def test_lowercase_rejected(self):
        """Lowercase coins rejected (must be uppercase)."""
        result = _validate_coin("btc")
        assert result is not None
        assert "error" in result

    def test_special_chars_rejected(self):
        """Special characters rejected."""
        for bad in ["BTC!", "ETH/USD", "SOL-PERP", "BTC@1", "DROP TABLE"]:
            result = _validate_coin(bad)
            assert result is not None, f"{bad} should be rejected"

    def test_too_long_rejected(self):
        """Coin names > 20 chars rejected."""
        result = _validate_coin("A" * 21)
        assert result is not None

    def test_max_length_allowed(self):
        """20-char coin name allowed."""
        assert _validate_coin("A" * 20) is None


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateStrategy:
    """Strategy name input validation."""

    def test_valid_strategies(self):
        """Standard strategy names pass."""
        for name in ["momentum", "mean-reversion", "conservative_v2", "scalp"]:
            assert _validate_strategy(name) is None, f"{name} should be valid"

    def test_empty_rejected(self):
        result = _validate_strategy("")
        assert result is not None

    def test_uppercase_rejected(self):
        """Strategy names must be lowercase."""
        result = _validate_strategy("Momentum")
        assert result is not None

    def test_special_chars_rejected(self):
        for bad in ["momentum!", "mean reversion", "strat@2", "'; DROP TABLE"]:
            result = _validate_strategy(bad)
            assert result is not None, f"{bad} should be rejected"

    def test_too_long_rejected(self):
        result = _validate_strategy("a" * 51)
        assert result is not None

    def test_max_length_allowed(self):
        assert _validate_strategy("a" * 50) is None


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION ID VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateSessionId:
    """Session ID input validation."""

    def test_valid_ids(self):
        for sid in ["sess_123", "abc-def", "session1", "A_b-C_3"]:
            assert _validate_session_id(sid) is None, f"{sid} should be valid"

    def test_empty_rejected(self):
        result = _validate_session_id("")
        assert result is not None

    def test_special_chars_rejected(self):
        for bad in ["sess 123", "id!@#", "../etc/passwd", "id;echo"]:
            result = _validate_session_id(bad)
            assert result is not None, f"{bad} should be rejected"

    def test_too_long_rejected(self):
        result = _validate_session_id("a" * 65)
        assert result is not None

    def test_max_length_allowed(self):
        assert _validate_session_id("a" * 64) is None


# ═══════════════════════════════════════════════════════════════════════════════
# LIMIT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateLimit:
    """Pagination limit validation."""

    def test_valid_limits(self):
        for n in [1, 10, 50, 100]:
            assert _validate_limit(n) is None, f"{n} should be valid"

    def test_zero_rejected(self):
        result = _validate_limit(0)
        assert result is not None

    def test_negative_rejected(self):
        result = _validate_limit(-1)
        assert result is not None

    def test_over_max_rejected(self):
        result = _validate_limit(101)
        assert result is not None

    def test_custom_max(self):
        assert _validate_limit(50, max_val=50) is None
        result = _validate_limit(51, max_val=50)
        assert result is not None

    def test_non_int_rejected(self):
        result = _validate_limit("10")
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# TIER GATING
# ═══════════════════════════════════════════════════════════════════════════════

class TestGate:
    """MCP tool tier gating via _gate()."""

    def test_public_tool_allowed(self):
        """Public tools (evaluate) pass gating."""
        assert _gate("zero_evaluate") is None

    def test_free_tool_allowed(self):
        """Free tools pass gating (default plan is scale)."""
        assert _gate("zero_get_heat") is None

    def test_all_tools_allowed_for_scale(self):
        """Default plan is 'scale' — all tools should pass."""
        # _gate uses _get_plan() which returns "scale"
        assert _gate("zero_session_result") is None
        assert _gate("zero_get_pulse") is None


# ═══════════════════════════════════════════════════════════════════════════════
# DRIVE MODE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

class TestModeConfig:
    """Drive mode validation."""

    def test_valid_modes(self):
        assert _MODE_VALID == {"comfort", "sport", "track"}

    def test_mode_count(self):
        assert len(_MODE_VALID) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# REGEX PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegexPatterns:
    """Verify regex patterns match expected inputs."""

    def test_coin_regex_uppercase_only(self):
        assert _COIN_RE.match("BTC")
        assert not _COIN_RE.match("btc")
        assert not _COIN_RE.match("")

    def test_strategy_regex_lowercase_dash_underscore(self):
        assert _STRATEGY_RE.match("mean-reversion")
        assert _STRATEGY_RE.match("scalp_v2")
        assert not _STRATEGY_RE.match("Mean")
        assert not _STRATEGY_RE.match("")

    def test_session_id_regex_mixed_case(self):
        assert _SESSION_ID_RE.match("sess_123")
        assert _SESSION_ID_RE.match("A-b_C")
        assert not _SESSION_ID_RE.match("")
        assert not _SESSION_ID_RE.match("id with spaces")

"""Tests for scanner/v6/auth.py — token management, rate limiting, tier gating."""

import json
import time

import pytest
from unittest.mock import patch

from scanner.v6.auth import (
    generate_token,
    register_token,
    resolve_token,
    check_rate_limit,
    check_tool_tier,
    _rate_counters,
    RATE_LIMITS,
    TIER_ORDER,
    TOOL_TIERS,
    PUBLIC_PREFIXES,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenGeneration:
    """Token format and uniqueness."""

    def test_token_has_prefix(self):
        """Tokens start with 'zr_' prefix."""
        token = generate_token()
        assert token.startswith("zr_")

    def test_token_length(self):
        """Token is zr_ + 32 hex chars = 35 chars."""
        token = generate_token()
        assert len(token) == 35  # "zr_" + 32 hex chars

    def test_tokens_unique(self):
        """Two generated tokens are different."""
        t1 = generate_token()
        t2 = generate_token()
        assert t1 != t2

    def test_token_hex_suffix(self):
        """Token suffix is valid hex."""
        token = generate_token()
        hex_part = token[3:]
        int(hex_part, 16)  # raises ValueError if not hex


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN REGISTRATION & RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestTokenRegistration:
    """Token storage and lookup."""

    def test_register_and_resolve(self, tmp_path):
        """register_token creates a token that resolve_token finds."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            token = register_token("op_1", "free")
            result = resolve_token(token)

        assert result is not None
        assert result["operator_id"] == "op_1"
        assert result["plan"] == "free"

    def test_register_idempotent(self, tmp_path):
        """Registering same operator twice returns same token."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            token1 = register_token("op_1", "free")
            token2 = register_token("op_1", "free")

        assert token1 == token2

    def test_different_operators_different_tokens(self, tmp_path):
        """Different operators get different tokens."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            t1 = register_token("op_1", "free")
            t2 = register_token("op_2", "free")

        assert t1 != t2

    def test_resolve_invalid_token(self, tmp_path):
        """Unknown token returns None."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            result = resolve_token("zr_nonexistent")

        assert result is None

    def test_register_stores_plan(self, tmp_path):
        """Token stores the correct plan."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            token = register_token("op_pro", "pro")
            result = resolve_token(token)

        assert result["plan"] == "pro"

    def test_register_stores_timestamp(self, tmp_path):
        """Token record includes created_at timestamp."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            token = register_token("op_1", "free")
            result = resolve_token(token)

        assert "created_at" in result

    def test_tokens_persisted(self, tmp_path):
        """Tokens survive file reload."""
        tokens_file = tmp_path / "tokens.json"
        with patch("scanner.v6.auth.TOKENS_FILE", tokens_file):
            token = register_token("op_1", "free")

        # Read raw file
        data = json.loads(tokens_file.read_text())
        assert token in data
        assert data[token]["operator_id"] == "op_1"


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    """Sliding window rate limiter."""

    def setup_method(self):
        """Clear rate counters between tests."""
        _rate_counters.clear()

    def test_first_request_allowed(self):
        """First request is always allowed."""
        allowed, headers = check_rate_limit("op_test", "free")
        assert allowed is True

    def test_headers_present(self):
        """Response includes rate limit headers."""
        _, headers = check_rate_limit("op_test", "free")
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers

    def test_free_limit(self):
        """Free plan: 30 requests per hour."""
        _, headers = check_rate_limit("op_free", "free")
        assert headers["X-RateLimit-Limit"] == "30"

    def test_pro_limit(self):
        """Pro plan: 120 requests per hour."""
        _, headers = check_rate_limit("op_pro", "pro")
        assert headers["X-RateLimit-Limit"] == "120"

    def test_scale_limit(self):
        """Scale plan: 1200 requests per hour."""
        _, headers = check_rate_limit("op_scale", "scale")
        assert headers["X-RateLimit-Limit"] == "1200"

    def test_limit_exceeded(self):
        """Exceeding rate limit returns False."""
        op = "op_limited"
        for _ in range(30):
            allowed, _ = check_rate_limit(op, "free")
            assert allowed is True
        # 31st request should be blocked
        allowed, headers = check_rate_limit(op, "free")
        assert allowed is False
        assert headers["X-RateLimit-Remaining"] == "0"

    def test_remaining_decrements(self):
        """Remaining count decreases with each request."""
        op = "op_count"
        _, h1 = check_rate_limit(op, "free")
        # Remaining is calculated before current request is appended
        assert int(h1["X-RateLimit-Remaining"]) == 30
        _, h2 = check_rate_limit(op, "free")
        assert int(h2["X-RateLimit-Remaining"]) == 29

    def test_window_expiry(self):
        """Old requests expire after 1 hour."""
        op = "op_expire"
        # Inject old timestamps
        _rate_counters[op] = [time.time() - 7200] * 30  # 2 hours ago
        allowed, headers = check_rate_limit(op, "free")
        assert allowed is True
        assert int(headers["X-RateLimit-Remaining"]) == 30  # old ones cleaned

    def test_separate_operators(self):
        """Rate limits are per-operator."""
        for _ in range(30):
            check_rate_limit("op_a", "free")
        # op_a exhausted, but op_b is fresh
        allowed, _ = check_rate_limit("op_b", "free")
        assert allowed is True

    def test_unknown_plan_defaults_to_30(self):
        """Unknown plan uses default limit of 30."""
        _, headers = check_rate_limit("op_unknown", "imaginary_plan")
        assert headers["X-RateLimit-Limit"] == "30"


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL TIER GATING
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolTierGating:
    """MCP tool access control."""

    def test_public_tool_no_auth(self):
        """Public tools return None (allowed) for any plan."""
        assert check_tool_tier("zero_evaluate", "free") is None
        assert check_tool_tier("zero_evaluate", "pro") is None

    def test_free_tool_allowed_for_free(self):
        """Free tools allowed for free plan."""
        assert check_tool_tier("zero_get_heat", "free") is None

    def test_pro_tool_blocked_for_free(self):
        """Pro tools blocked for free plan."""
        result = check_tool_tier("zero_get_pulse", "free")
        assert result is not None
        assert result["error"] == "forbidden"
        assert "pro" in result["reason"]

    def test_pro_tool_allowed_for_pro(self):
        """Pro tools allowed for pro plan."""
        assert check_tool_tier("zero_get_pulse", "pro") is None

    def test_scale_tool_blocked_for_pro(self):
        """Scale tools blocked for pro plan."""
        result = check_tool_tier("zero_session_result", "pro")
        assert result is not None
        assert result["error"] == "forbidden"

    def test_scale_tool_allowed_for_scale(self):
        """Scale tools allowed for scale plan."""
        assert check_tool_tier("zero_session_result", "scale") is None

    def test_higher_plan_can_access_lower(self):
        """Scale plan can access free and pro tools."""
        assert check_tool_tier("zero_get_heat", "scale") is None
        assert check_tool_tier("zero_get_pulse", "scale") is None

    def test_unknown_tool_defaults_to_free(self):
        """Unknown tools require free tier by default."""
        assert check_tool_tier("zero_unknown_tool", "free") is None

    def test_all_tools_have_valid_tiers(self):
        """Every tool in TOOL_TIERS has a valid tier."""
        for tool, tier in TOOL_TIERS.items():
            assert tier in TIER_ORDER, f"{tool} has invalid tier {tier}"

    def test_tier_order_consistent(self):
        """Tier order: public < free < pro < scale."""
        assert TIER_ORDER["public"] < TIER_ORDER["free"]
        assert TIER_ORDER["free"] < TIER_ORDER["pro"]
        assert TIER_ORDER["pro"] < TIER_ORDER["scale"]


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicRoutes:
    """Public route prefix configuration."""

    def test_health_is_public(self):
        assert "/health" in PUBLIC_PREFIXES

    def test_mcp_is_public(self):
        assert "/mcp" in PUBLIC_PREFIXES

    def test_evaluate_is_public(self):
        assert any(p.startswith("/v6/evaluate") for p in PUBLIC_PREFIXES)

    def test_strategies_is_public(self):
        assert "/v6/strategies" in PUBLIC_PREFIXES

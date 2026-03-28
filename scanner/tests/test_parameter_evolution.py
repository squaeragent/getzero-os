"""Tests for scanner/agents/parameter_evolution.py — pure analysis functions."""

import pytest

from scanner.agents.parameter_evolution import (
    compute_stats_for_group,
    find_matching_trades,
    analyze_by_regime,
    analyze_by_direction,
    analyze_by_pattern,
)


# ��══════════════════════════════════════════════════════════════════════════════
# COMPUTE_STATS_FOR_GROUP
# ══���════���════════════════════════════════════���══════════════════════════════════

class TestComputeStats:
    """Trade group statistics."""

    def test_empty_trades(self):
        wr, count, pnl = compute_stats_for_group([])
        assert wr == 0.0
        assert count == 0
        assert pnl == 0.0

    def test_all_winners(self):
        trades = [{"pnl_dollars": 10}, {"pnl_dollars": 20}, {"pnl_dollars": 5}]
        wr, count, pnl = compute_stats_for_group(trades)
        assert wr == 100.0
        assert count == 3
        assert pnl == 35.0

    def test_all_losers(self):
        trades = [{"pnl_dollars": -10}, {"pnl_dollars": -20}]
        wr, count, pnl = compute_stats_for_group(trades)
        assert wr == 0.0
        assert count == 2
        assert pnl == -30.0

    def test_mixed(self):
        trades = [
            {"pnl_dollars": 10},
            {"pnl_dollars": -5},
            {"pnl_dollars": 20},
            {"pnl_dollars": -3},
        ]
        wr, count, pnl = compute_stats_for_group(trades)
        assert wr == 50.0
        assert count == 4
        assert pnl == 22.0

    def test_uses_pnl_usd_fallback(self):
        """Falls back to pnl_usd if pnl_dollars missing."""
        trades = [{"pnl_usd": 10}, {"pnl_usd": -5}]
        wr, count, pnl = compute_stats_for_group(trades)
        assert wr == 50.0
        assert pnl == 5.0

    def test_zero_pnl_is_not_win(self):
        """Zero PnL is not counted as a win."""
        trades = [{"pnl_dollars": 0}]
        wr, count, pnl = compute_stats_for_group(trades)
        assert wr == 0.0
        assert count == 1


# ════════════════════════════════════════════════════════════════��══════════════
# FIND_MATCHING_TRADES
# ══════════════���═════════════════════════════════════��══════════════════════════

class TestFindMatchingTrades:
    """Condition-based trade filtering."""

    def test_match_regime(self):
        trades = [
            {"regime": "trending", "pnl_dollars": 10},
            {"regime": "volatile", "pnl_dollars": -5},
            {"regime": "trending", "pnl_dollars": 20},
        ]
        matched = find_matching_trades(trades, "regime == 'trending'")
        assert len(matched) == 2

    def test_match_direction(self):
        trades = [
            {"direction": "LONG", "pnl_dollars": 10},
            {"direction": "SHORT", "pnl_dollars": -5},
        ]
        matched = find_matching_trades(trades, "direction == 'long'")
        assert len(matched) == 1
        assert matched[0]["direction"] == "LONG"

    def test_match_pattern(self):
        trades = [
            {"signal": "momentum_breakout", "pnl_dollars": 10},
            {"signal": "mean_reversion", "pnl_dollars": -5},
        ]
        matched = find_matching_trades(trades, "pattern == 'momentum_breakout'")
        assert len(matched) == 1

    def test_no_matches(self):
        trades = [{"regime": "trending", "pnl_dollars": 10}]
        matched = find_matching_trades(trades, "regime == 'nonexistent'")
        assert len(matched) == 0

    def test_empty_trades(self):
        matched = find_matching_trades([], "regime == 'trending'")
        assert len(matched) == 0

    def test_metadata_regime_fallback(self):
        """Matches regime from metadata if top-level missing."""
        trades = [{"metadata": {"regime": "volatile"}, "pnl_dollars": 5}]
        matched = find_matching_trades(trades, "regime == 'volatile'")
        assert len(matched) == 1

    def test_uses_side_fallback(self):
        """Falls back to 'side' field if 'direction' missing."""
        trades = [{"side": "SHORT", "pnl_dollars": -5}]
        matched = find_matching_trades(trades, "direction == 'short'")
        assert len(matched) == 1


# ═════════════════════════════════════════════════════════���═════════════════════
# ANALYZE_BY_REGIME
# ═════════════════��═════════════════════════════════════════════════════════════

class TestAnalyzeByRegime:
    """Regime-based rule proposal generation."""

    def test_high_wr_proposes_boost(self):
        """Regime with >60% WR proposes boost."""
        trades = [{"regime": "trending", "pnl_dollars": 10}] * 4
        proposals = analyze_by_regime(trades)
        boosts = [p for p in proposals if p["action"] == "boost_confidence"]
        assert len(boosts) >= 1
        assert "trending" in boosts[0]["condition"]

    def test_low_wr_proposes_reduce(self):
        """Regime with <30% WR proposes reduce."""
        trades = [
            {"regime": "choppy", "pnl_dollars": -10},
            {"regime": "choppy", "pnl_dollars": -5},
            {"regime": "choppy", "pnl_dollars": -3},
            {"regime": "choppy", "pnl_dollars": 1},  # 1/4 = 25%
        ]
        proposals = analyze_by_regime(trades)
        reduces = [p for p in proposals if p["action"] == "reduce_confidence"]
        assert len(reduces) >= 1

    def test_skips_unknown_regime(self):
        """Unknown regime is skipped."""
        trades = [{"regime": "unknown", "pnl_dollars": 10}] * 5
        proposals = analyze_by_regime(trades)
        assert len(proposals) == 0

    def test_skips_small_groups(self):
        """Groups with < 3 trades are skipped."""
        trades = [
            {"regime": "trending", "pnl_dollars": 10},
            {"regime": "trending", "pnl_dollars": 20},
        ]
        proposals = analyze_by_regime(trades)
        assert len(proposals) == 0

    def test_empty_trades(self):
        proposals = analyze_by_regime([])
        assert proposals == []


# ═════════���════════════════���════════════════════════════════════���═══════════════
# ANALYZE_BY_DIRECTION
# ═══��══════════════════════��═════════════════════════════���══════════════════════

class TestAnalyzeByDirection:
    """Direction-based rule proposal generation."""

    def test_poor_long_proposes_reduce(self):
        """LONG WR < 35% proposes reduce_confidence."""
        trades = [
            {"direction": "LONG", "pnl_dollars": -10},
            {"direction": "LONG", "pnl_dollars": -5},
            {"direction": "LONG", "pnl_dollars": 1},  # 1/3 = 33.3%
        ]
        proposals = analyze_by_direction(trades)
        reduces = [p for p in proposals if "LONG" in p["condition"]]
        assert len(reduces) >= 1

    def test_strong_short_proposes_boost(self):
        """SHORT WR > 65% proposes boost_confidence."""
        trades = [
            {"direction": "SHORT", "pnl_dollars": 10},
            {"direction": "SHORT", "pnl_dollars": 20},
            {"direction": "SHORT", "pnl_dollars": 15},  # 3/3 = 100%
        ]
        proposals = analyze_by_direction(trades)
        boosts = [p for p in proposals if "SHORT" in p["condition"]]
        assert len(boosts) >= 1


# ═══════════════════════════════��══════════════════════════════════���════════════
# ANALYZE_BY_PATTERN
# ═══��═══════════════════════════════════════════════════════════════════════════

class TestAnalyzeByPattern:
    """Pattern-based rule proposal generation."""

    def test_high_wr_pattern_proposes_boost(self):
        """Pattern with >70% WR proposes boost."""
        trades = [{"signal": "momentum_break", "pnl_dollars": 10}] * 4
        proposals = analyze_by_pattern(trades)
        boosts = [p for p in proposals if p["action"] == "boost_confidence"]
        assert len(boosts) >= 1

    def test_low_wr_pattern_proposes_reduce(self):
        """Pattern with <25% WR proposes reduce."""
        trades = [
            {"signal": "bad_signal", "pnl_dollars": -10},
            {"signal": "bad_signal", "pnl_dollars": -5},
            {"signal": "bad_signal", "pnl_dollars": -3},
            {"signal": "bad_signal", "pnl_dollars": 1},  # 1/4 = 25% -> boundary
        ]
        # 25% is not < 25%, need 0/4 or similar
        trades_worse = [{"signal": "terrible", "pnl_dollars": -10}] * 4
        proposals = analyze_by_pattern(trades_worse)
        reduces = [p for p in proposals if p["action"] == "reduce_confidence"]
        assert len(reduces) >= 1

    def test_skips_unknown_pattern(self):
        trades = [{"signal": "unknown", "pnl_dollars": 10}] * 5
        proposals = analyze_by_pattern(trades)
        # "unknown" is skipped
        assert all("unknown" not in p.get("condition", "") for p in proposals)

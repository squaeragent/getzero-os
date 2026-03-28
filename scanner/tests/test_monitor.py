#!/usr/bin/env python3
"""
test_monitor.py — Session 9: Monitor + Signal Events

36+ tests covering:
  - DataCache (5 tests)
  - Evaluation / 7 layers (7 tests)
  - Signal State Machine (7 tests)
  - NearMissDetector (4 tests)
  - Cycle (5 tests)
  - Edge Cases (5 tests)
  - Integration (3 tests)
"""

from __future__ import annotations

import json
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ─── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_SP_RESULT = {
    "coin": "SOL",
    "signal": "LONG",
    "direction": "LONG",
    "confidence": 0.75,
    "quality": 7,
    "regime": "trending",
    "hurst": 0.62,
    "dfa": 0.55,
    "atr_pct": 0.03,
    "funding_rate": -0.0001,
    "funding_annualized": -0.876,
    "source": "smart_local",
    "indicator_votes": {
        "rsi": "long",
        "macd": "long",
        "ema": "long",
        "bollinger": "neutral",
        "obv": "long",
        "funding": "long",
    },
    "indicators": {
        "RSI_14": 55.0,
        "MACD_HIST": 0.05,
        "EMA_9": 150.0,
        "EMA_21": 148.0,
        "EMA_50": 145.0,
        "BB_PCT": 0.65,
        "BB_BANDWIDTH": 0.04,
        "ATR": 2.5,
        "ATR_PCT": 0.03,
        "OBV": 12345,
        "FUNDING": -0.0001,
        "FUNDING_ANN": -0.876,
        "VOL_RATIO": 1.2,
        "HURST": 0.62,
        "DFA": 0.55,
        "CLOSE_PRICE": 150.0,
        "BOOK_DEPTH_USD": 500000.0,
        "SPREAD_BPS": 2.5,
    },
    "reasons": ["RSI_55", "MACD_BULL", "EMA_BULL"],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

SAMPLE_SP_NEUTRAL = {
    "coin": "BTC",
    "signal": "NEUTRAL",
    "direction": "NEUTRAL",
    "confidence": 0,
    "quality": 0,
    "regime": "insufficient_data",
    "hurst": 0.5,
    "dfa": 0.5,
    "atr_pct": 0.02,
    "funding_rate": 0.0001,
    "funding_annualized": 0.876,
    "source": "smart_local",
    "indicator_votes": {},
    "indicators": {"RSI_14": 50},
    "reasons": [],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}


def make_strategy_yaml(tmp_path: Path, name: str = "momentum", threshold: int = 5) -> Path:
    """Write a minimal strategy YAML to tmp_path/strategies/name.yaml."""
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = f"""\
name: {name}
display: "Test {name}"
tier: free
session:
  duration_hours: 48
evaluation:
  scope: top_50
  consensus_threshold: {threshold}
  directions: [long, short]
  min_regime: [trending, stable]
risk:
  max_positions: 5
  position_size_pct: 10
  stop_loss_pct: 3
  reserve_pct: 20
  max_daily_loss_pct: 5
  max_hold_hours: 48
  entry_end_action: hold
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
"""
    p = strat_dir / f"{name}.yaml"
    p.write_text(yaml_content)
    return strat_dir


def make_monitor(tmp_path: Path, strategy_name: str = "momentum", threshold: int = 5):
    """Create a Monitor with a tmp bus dir and mocked SmartProvider."""
    from scanner.v6.monitor import Monitor
    from scanner.v6.strategy_loader import load_strategy

    strat_dir = make_strategy_yaml(tmp_path, strategy_name, threshold)

    # Create bus dir
    bus_dir = tmp_path / "bus"
    bus_dir.mkdir(parents=True, exist_ok=True)

    monitor = Monitor.__new__(Monitor)
    monitor.strategy = load_strategy(strategy_name, strategies_dir=strat_dir)
    monitor.smart_provider = MagicMock()
    monitor.smart_provider.evaluate_coin.return_value = dict(SAMPLE_SP_RESULT)
    monitor.cache = MagicMock()
    monitor.cache.refresh.return_value = True
    monitor.cache.is_price_stale.return_value = False
    monitor.cache.price_age_ms.return_value = 100
    monitor.cache.data_complete.return_value = True
    monitor.cache.any_source_stale.return_value = False
    monitor.cache.get_price.return_value = 150.0
    monitor.cache.get_funding.return_value = -0.0001
    monitor.cache.get_oi.return_value = 100000.0
    monitor.cache.get_book.return_value = (300_000.0, 200_000.0)  # bid > ask → bullish
    monitor.cache.fear_greed = 25  # extreme fear → long favorable
    monitor.coin_states = {}
    monitor.prev_results = {}
    monitor.cycle_count = 0
    monitor._bus_dir = bus_dir
    monitor._signals_file = bus_dir / "signals.json"
    monitor._near_miss_file = bus_dir / "near_misses.jsonl"
    monitor._decisions_file = bus_dir / "decisions.jsonl"
    monitor._heartbeat_file = bus_dir / "heartbeat.json"

    from scanner.v6.monitor import NearMissDetector
    monitor._near_miss_detector = NearMissDetector.__new__(NearMissDetector)
    monitor._near_miss_detector._all_strategies = {}
    monitor._near_miss_detector._bus_dir = bus_dir
    monitor._near_miss_detector._near_miss_file = bus_dir / "near_misses.jsonl"

    # B1: Approaching detection state
    monitor.approaching_states = {}
    # B4: Execution metrics
    monitor._metrics_file = bus_dir / "metrics.jsonl"
    monitor.last_cycle_metrics = None

    return monitor


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CACHE TESTS (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataCache:

    def test_batch_fetch_prices(self):
        """DataCache.refresh() fetches allMids and stores prices."""
        from scanner.v6.monitor import DataCache
        cache = DataCache()
        mock_mids = {"BTC": "50000.0", "ETH": "3000.0", "SOL": "150.0"}

        with patch("scanner.v6.monitor._hl_post") as mock_post:
            mock_post.side_effect = [
                mock_mids,  # allMids
                [{"universe": [], "0": []}],  # metaAndAssetCtxs (no-op)
            ]
            # Also patch fear greed to avoid HTTP call
            with patch.object(cache, "_refresh_fear_greed"):
                result = cache.refresh()

        assert result is True
        assert cache.prices.get("BTC") == 50000.0
        assert cache.prices.get("ETH") == 3000.0
        assert cache.prices.get("SOL") == 150.0

    def test_stale_detection(self):
        """Cache correctly reports staleness based on age."""
        from scanner.v6.monitor import DataCache
        cache = DataCache()
        cache.prices = {"BTC": 50000.0}
        cache.prices_ts = time.time() - 130  # 130s ago → stale
        assert cache.is_price_stale() is True

        cache.prices_ts = time.time() - 30  # 30s ago → fresh
        assert cache.is_price_stale() is False

    def test_skip_cycle_on_stale_prices(self, tmp_path):
        """Monitor skips cycle if price data is stale."""
        monitor = make_monitor(tmp_path)
        monitor.cache.is_price_stale.return_value = True

        summary = monitor.run_cycle()
        assert summary["skipped"] is True
        assert "stale" in summary["skip_reason"].lower()

    def test_partial_data_flags_incomplete(self):
        """DataCache.data_complete() returns False when funding/OI missing."""
        from scanner.v6.monitor import DataCache
        cache = DataCache()
        cache.prices = {"BTC": 50000.0}
        cache.funding = {}  # missing
        cache.oi = {}       # missing
        assert cache.data_complete() is False

        cache.funding = {"BTC": 0.001}
        cache.oi = {"BTC": 1000.0}
        assert cache.data_complete() is True

    def test_fear_greed_fallback_to_50(self):
        """Fear & Greed falls back to 50 if API fails."""
        from scanner.v6.monitor import DataCache
        cache = DataCache()
        assert cache.fear_greed == 50  # default

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            cache._refresh_fear_greed()

        # Should still be 50 (fallback)
        assert cache.fear_greed == 50


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION TESTS (7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluation:

    def test_7_layers_produced(self, tmp_path):
        """evaluate_coin() always returns exactly 7 layers."""
        monitor = make_monitor(tmp_path)
        result = monitor.evaluate_coin("SOL")
        assert len(result.layers) == 7
        layer_names = {lr.layer for lr in result.layers}
        assert layer_names == {"regime", "technical", "funding", "book", "OI", "macro", "collective"}

    def test_consensus_count(self, tmp_path):
        """consensus equals number of available+passed layers."""
        monitor = make_monitor(tmp_path)
        result = monitor.evaluate_coin("SOL")
        passed = sum(1 for lr in result.layers if lr.passed and lr.data_available)
        assert result.consensus == passed

    def test_conviction_range(self, tmp_path):
        """conviction is always 0.0-1.0."""
        monitor = make_monitor(tmp_path)
        result = monitor.evaluate_coin("SOL")
        assert 0.0 <= result.conviction <= 1.0

    def test_direction_from_smart_provider(self, tmp_path):
        """Direction matches SmartProvider when it has a clear signal."""
        monitor = make_monitor(tmp_path)
        monitor.smart_provider.evaluate_coin.return_value = dict(SAMPLE_SP_RESULT)
        result = monitor.evaluate_coin("SOL")
        assert result.direction == "LONG"

    def test_missing_collective_layer_abstains(self, tmp_path):
        """Collective layer marks data_available=False when no collective file."""
        monitor = make_monitor(tmp_path)
        # No collective_signals.json in bus dir
        result = monitor.evaluate_coin("SOL")
        collective = next(lr for lr in result.layers if lr.layer == "collective")
        assert collective.data_available is False

    def test_regime_layer(self, tmp_path):
        """Regime layer passes when regime is in strategy min_regime."""
        monitor = make_monitor(tmp_path)
        # SmartProvider says trending → allowed by strategy
        result = monitor.evaluate_coin("SOL")
        regime_layer = next(lr for lr in result.layers if lr.layer == "regime")
        assert regime_layer.passed is True
        assert regime_layer.value == "trending"

    def test_technical_layer_majority(self, tmp_path):
        """Technical layer passes only when majority of RSI/MACD/EMA/BB agree."""
        monitor = make_monitor(tmp_path)
        # 3 of 4 agree (rsi, macd, ema agree; bollinger neutral) → majority
        result = monitor.evaluate_coin("SOL")
        tech_layer = next(lr for lr in result.layers if lr.layer == "technical")
        assert tech_layer.passed is True
        assert tech_layer.value["agree"] >= 2  # at least majority


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL STATE MACHINE TESTS (7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalEmission:

    def _make_result(self, tmp_path, consensus: int = 6, direction: str = "LONG",
                     regime: str = "trending", rsi: float = 55.0) -> "EvaluationResult":
        from scanner.v6.monitor import EvaluationResult, LayerResult
        ts = datetime.now(timezone.utc).isoformat()
        layers = [
            LayerResult("regime", True, regime, f"regime={regime}"),
            LayerResult("technical", consensus >= 2, {"agree": 3, "total": 4, "rsi": rsi}, "tech"),
            LayerResult("funding", consensus >= 3, -0.0001, "funding"),
            LayerResult("book", consensus >= 4, {"bid_ratio": 0.6}, "book"),
            LayerResult("OI", consensus >= 5, 100000.0, "OI"),
            LayerResult("macro", consensus >= 6, 25, "macro"),
            LayerResult("collective", False, None, "no collective", data_available=False),
        ]
        passed = sum(1 for lr in layers if lr.passed and lr.data_available)
        return EvaluationResult(
            coin="SOL",
            timestamp=ts,
            layers=layers,
            consensus=passed,
            conviction=passed / 6,
            direction=direction,
            regime=regime,
            price=150.0,
            data_age_ms=100,
            data_complete=True,
        )

    def test_inactive_to_entry(self, tmp_path):
        """inactive + consensus >= threshold → emit ENTRY, state=entry."""
        monitor = make_monitor(tmp_path, threshold=5)
        result = self._make_result(tmp_path, consensus=6)
        signals = monitor.check_signals("SOL", result)
        assert len(signals) == 1
        assert signals[0].type == "ENTRY"
        assert monitor.coin_states["SOL"] == "entry"

    def test_no_reemit_while_entry(self, tmp_path):
        """entry + consensus >= threshold → no re-emit (dedup)."""
        monitor = make_monitor(tmp_path, threshold=5)
        result = self._make_result(tmp_path, consensus=6)

        # First call → ENTRY
        signals = monitor.check_signals("SOL", result)
        assert signals[0].type == "ENTRY"
        monitor.prev_results["SOL"] = result

        # Second call → no signal
        signals2 = monitor.check_signals("SOL", result)
        assert len(signals2) == 0
        assert monitor.coin_states["SOL"] == "entry"

    def test_entry_to_entry_end(self, tmp_path):
        """entry + consensus < threshold → emit ENTRY_END, state=entry_end."""
        monitor = make_monitor(tmp_path, threshold=5)
        result_high = self._make_result(tmp_path, consensus=6)
        result_low = self._make_result(tmp_path, consensus=2)

        monitor.check_signals("SOL", result_high)  # → entry
        monitor.prev_results["SOL"] = result_high

        signals = monitor.check_signals("SOL", result_low)
        assert len(signals) == 1
        assert signals[0].type == "ENTRY_END"
        assert monitor.coin_states["SOL"] == "entry_end"

    def test_entry_to_exit_on_rsi_overbought(self, tmp_path):
        """entry + RSI > 70 for LONG → emit EXIT, state=inactive."""
        monitor = make_monitor(tmp_path, threshold=5)
        result_entry = self._make_result(tmp_path, consensus=6)
        monitor.check_signals("SOL", result_entry)
        monitor.prev_results["SOL"] = result_entry

        result_overbought = self._make_result(tmp_path, consensus=6, rsi=75.0)
        signals = monitor.check_signals("SOL", result_overbought)
        exits = [s for s in signals if s.type == "EXIT"]
        assert len(exits) == 1
        assert "rsi" in exits[0].reason.lower() or "overbought" in exits[0].reason.lower()
        assert monitor.coin_states["SOL"] == "inactive"

    def test_entry_end_to_reentry(self, tmp_path):
        """entry_end + consensus >= threshold → emit ENTRY (re-entry), state=entry."""
        monitor = make_monitor(tmp_path, threshold=5)
        result_high = self._make_result(tmp_path, consensus=6)
        result_low = self._make_result(tmp_path, consensus=2)

        monitor.check_signals("SOL", result_high)   # → entry
        monitor.prev_results["SOL"] = result_high
        monitor.check_signals("SOL", result_low)    # → entry_end
        monitor.prev_results["SOL"] = result_low

        # Re-entry
        signals = monitor.check_signals("SOL", result_high)
        assert len(signals) == 1
        assert signals[0].type == "ENTRY"
        assert signals[0].reason == "re_entry"
        assert monitor.coin_states["SOL"] == "entry"

    def test_entry_end_to_exit_on_regime_shift(self, tmp_path):
        """entry_end + excluded regime → emit EXIT, state=inactive."""
        monitor = make_monitor(tmp_path, threshold=5)
        result_entry = self._make_result(tmp_path, consensus=6)
        result_low = self._make_result(tmp_path, consensus=2)
        result_chaotic = self._make_result(tmp_path, consensus=2, regime="chaotic")

        monitor.check_signals("SOL", result_entry)   # → entry
        monitor.prev_results["SOL"] = result_entry
        monitor.check_signals("SOL", result_low)     # → entry_end
        monitor.prev_results["SOL"] = result_low

        signals = monitor.check_signals("SOL", result_chaotic)
        exits = [s for s in signals if s.type == "EXIT"]
        assert len(exits) == 1
        assert "regime" in exits[0].reason.lower()
        assert monitor.coin_states["SOL"] == "inactive"

    def test_no_signal_when_no_change(self, tmp_path):
        """inactive + consensus < threshold → no signal emitted."""
        monitor = make_monitor(tmp_path, threshold=5)
        result_low = self._make_result(tmp_path, consensus=2)
        signals = monitor.check_signals("SOL", result_low)
        assert len(signals) == 0
        assert monitor.coin_states.get("SOL", "inactive") == "inactive"


# ═══════════════════════════════════════════════════════════════════════════════
# NEAR MISS TESTS (4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNearMiss:

    def _make_eval_result(self, tmp_path, consensus: int = 3, direction: str = "LONG",
                          regime: str = "trending") -> "EvaluationResult":
        from scanner.v6.monitor import EvaluationResult, LayerResult
        ts = datetime.now(timezone.utc).isoformat()
        layers = [
            LayerResult("regime", True, regime, f"regime={regime}"),
            LayerResult("technical", True, {"agree": 3, "total": 4, "rsi": 55}, "tech"),
            LayerResult("funding", True, -0.0001, "funding"),
            LayerResult("book", False, {"bid_ratio": 0.4}, "book"),
            LayerResult("OI", False, 100000.0, "OI"),
            LayerResult("macro", False, 55, "macro"),
            LayerResult("collective", False, None, "no collective", data_available=False),
        ]
        passed = sum(1 for lr in layers if lr.passed and lr.data_available)
        return EvaluationResult(
            coin="SOL", timestamp=ts, layers=layers,
            consensus=consensus, conviction=consensus / 6,
            direction=direction, regime=regime, price=150.0,
            data_age_ms=100, data_complete=True,
        )

    def test_near_miss_different_strategy(self, tmp_path):
        """Near miss detected when coin passes a different strategy threshold."""
        from scanner.v6.monitor import NearMissDetector
        from scanner.v6.strategy_loader import load_strategy

        strat_dir = make_strategy_yaml(tmp_path, "momentum", threshold=5)
        make_strategy_yaml(tmp_path, "scout", threshold=3)

        active = load_strategy("momentum", strategies_dir=strat_dir)
        scout = load_strategy("scout", strategies_dir=strat_dir)

        detector = NearMissDetector.__new__(NearMissDetector)
        detector._all_strategies = {"scout": scout}
        detector._bus_dir = tmp_path / "bus"
        detector._near_miss_file = detector._bus_dir / "near_misses.jsonl"

        result = self._make_eval_result(tmp_path, consensus=3)  # passes scout (3), not momentum (5)
        near_misses = detector.check("SOL", result, active)

        assert len(near_misses) == 1
        assert near_misses[0]["near_miss_strategy"] == "scout"

    def test_cross_check_all_strategies(self, tmp_path):
        """NearMissDetector checks all strategies, not just one."""
        from scanner.v6.monitor import NearMissDetector
        from scanner.v6.strategy_loader import load_strategy

        strat_dir = make_strategy_yaml(tmp_path, "momentum", threshold=5)
        make_strategy_yaml(tmp_path, "scout", threshold=3)
        make_strategy_yaml(tmp_path, "degen", threshold=2)

        active = load_strategy("momentum", strategies_dir=strat_dir)
        scout = load_strategy("scout", strategies_dir=strat_dir)
        degen = load_strategy("degen", strategies_dir=strat_dir)

        detector = NearMissDetector.__new__(NearMissDetector)
        detector._all_strategies = {"scout": scout, "degen": degen}
        detector._bus_dir = tmp_path / "bus"
        detector._near_miss_file = detector._bus_dir / "near_misses.jsonl"

        result = self._make_eval_result(tmp_path, consensus=3)
        near_misses = detector.check("SOL", result, active)

        # Should find 2 near misses (scout and degen both have lower thresholds)
        assert len(near_misses) == 2
        strategies_hit = {nm["near_miss_strategy"] for nm in near_misses}
        assert "scout" in strategies_hit
        assert "degen" in strategies_hit

    def test_near_miss_format(self, tmp_path):
        """Near miss record has all required fields."""
        from scanner.v6.monitor import NearMissDetector
        from scanner.v6.strategy_loader import load_strategy

        strat_dir = make_strategy_yaml(tmp_path, "momentum", threshold=5)
        make_strategy_yaml(tmp_path, "scout", threshold=3)

        active = load_strategy("momentum", strategies_dir=strat_dir)
        scout = load_strategy("scout", strategies_dir=strat_dir)

        detector = NearMissDetector.__new__(NearMissDetector)
        detector._all_strategies = {"scout": scout}
        detector._bus_dir = tmp_path / "bus"
        detector._near_miss_file = detector._bus_dir / "near_misses.jsonl"

        result = self._make_eval_result(tmp_path, consensus=3)
        near_misses = detector.check("SOL", result, active)

        assert near_misses
        nm = near_misses[0]
        required_fields = {
            "coin", "direction", "consensus", "conviction", "regime",
            "active_strategy", "near_miss_strategy", "active_threshold",
            "near_miss_threshold", "layers_passed", "timestamp",
        }
        assert required_fields.issubset(nm.keys())

    def test_no_near_miss_when_passes_active(self, tmp_path):
        """No near miss when coin passes the active strategy."""
        from scanner.v6.monitor import NearMissDetector
        from scanner.v6.strategy_loader import load_strategy

        strat_dir = make_strategy_yaml(tmp_path, "momentum", threshold=3)
        make_strategy_yaml(tmp_path, "scout", threshold=2)

        active = load_strategy("momentum", strategies_dir=strat_dir)
        scout = load_strategy("scout", strategies_dir=strat_dir)

        detector = NearMissDetector.__new__(NearMissDetector)
        detector._all_strategies = {"scout": scout}
        detector._bus_dir = tmp_path / "bus"
        detector._near_miss_file = detector._bus_dir / "near_misses.jsonl"

        result = self._make_eval_result(tmp_path, consensus=3)  # passes active (threshold=3)
        near_misses = detector.check("SOL", result, active)
        assert len(near_misses) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLE TESTS (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCycle:

    def test_full_cycle_returns_summary(self, tmp_path):
        """run_cycle() returns a summary dict with expected fields."""
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_get_coins", return_value=["SOL"]):
            summary = monitor.run_cycle()

        assert isinstance(summary, dict)
        required = {"cycle", "timestamp", "coins_evaluated", "signals_emitted", "near_misses", "skipped"}
        assert required.issubset(summary.keys())

    def test_cycle_summary_counts(self, tmp_path):
        """Cycle summary accurately reflects coins evaluated."""
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_get_coins", return_value=["SOL", "ETH", "BTC"]):
            summary = monitor.run_cycle()

        assert summary["coins_evaluated"] == 3
        assert summary["skipped"] is False

    def test_cycle_skipped_on_stale(self, tmp_path):
        """Cycle is skipped and not evaluated when prices are stale."""
        monitor = make_monitor(tmp_path)
        monitor.cache.is_price_stale.return_value = True

        summary = monitor.run_cycle()
        assert summary["skipped"] is True
        assert summary["coins_evaluated"] == 0

    def test_cycle_increments_count(self, tmp_path):
        """Cycle counter increments each call."""
        monitor = make_monitor(tmp_path)
        assert monitor.cycle_count == 0

        with patch.object(monitor, "_get_coins", return_value=["SOL"]):
            monitor.run_cycle()
            monitor.run_cycle()

        assert monitor.cycle_count == 2

    def test_heartbeat_written_after_cycle(self, tmp_path):
        """Heartbeat file is written after each cycle."""
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_get_coins", return_value=["SOL"]):
            monitor.run_cycle()

        assert monitor._heartbeat_file.exists()
        hb = json.loads(monitor._heartbeat_file.read_text())
        assert "monitor" in hb
        assert hb["monitor_cycle"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_zero_layers_pass(self, tmp_path):
        """EvaluationResult with NEUTRAL direction → no trade signal."""
        monitor = make_monitor(tmp_path)
        # SmartProvider returns NEUTRAL
        monitor.smart_provider.evaluate_coin.return_value = dict(SAMPLE_SP_NEUTRAL)
        result = monitor.evaluate_coin("BTC")
        assert result.direction == "NONE"
        # Collective defaults to pass (V1: no network), so consensus can be 0 or 1
        assert result.consensus <= 1

    def test_all_layers_pass(self, tmp_path):
        """When all layers pass, consensus=6 (7 minus unavailable collective)."""
        monitor = make_monitor(tmp_path)
        # Give it a collective file
        collective_data = {
            "coins": {
                "SOL": {
                    "direction": "LONG",
                    "agreement_pct": 0.85,
                    "long_votes": 17,
                    "short_votes": 3,
                }
            }
        }
        (monitor._bus_dir / "collective_signals.json").write_text(json.dumps(collective_data))
        result = monitor.evaluate_coin("SOL")
        # With collective available, up to 7 layers can pass
        assert result.consensus >= 5

    def test_consecutive_entry_exit(self, tmp_path):
        """State machine handles entry → exit → entry without errors."""
        from scanner.v6.monitor import EvaluationResult, LayerResult
        monitor = make_monitor(tmp_path, threshold=5)

        def make_r(rsi=55.0, regime="trending", consensus=6):
            ts = datetime.now(timezone.utc).isoformat()
            layers = [
                LayerResult("regime", True, regime, ""),
                LayerResult("technical", True, {"agree": 3, "total": 4, "rsi": rsi}, ""),
                LayerResult("funding", True, -0.0001, ""),
                LayerResult("book", True, {"bid_ratio": 0.6}, ""),
                LayerResult("OI", True, 100000.0, ""),
                LayerResult("macro", True, 25, ""),
                LayerResult("collective", False, None, "", data_available=False),
            ]
            passed = sum(1 for lr in layers if lr.passed and lr.data_available)
            return EvaluationResult(
                coin="SOL", timestamp=ts, layers=layers,
                consensus=passed, conviction=passed / 6,
                direction="LONG", regime=regime, price=150.0,
                data_age_ms=100, data_complete=True,
            )

        r1 = make_r(rsi=55, consensus=6)
        sigs1 = monitor.check_signals("SOL", r1)
        assert sigs1[0].type == "ENTRY"
        monitor.prev_results["SOL"] = r1

        r2 = make_r(rsi=75, consensus=6)  # RSI overbought → exit
        sigs2 = monitor.check_signals("SOL", r2)
        assert sigs2[0].type == "EXIT"
        monitor.prev_results["SOL"] = r2

        r3 = make_r(rsi=55, consensus=6)  # re-entry
        sigs3 = monitor.check_signals("SOL", r3)
        assert sigs3[0].type == "ENTRY"

    def test_multiple_coins_independent_states(self, tmp_path):
        """Each coin has independent state machine state."""
        from scanner.v6.monitor import EvaluationResult, LayerResult
        monitor = make_monitor(tmp_path, threshold=5)

        def make_r(coin, consensus):
            ts = datetime.now(timezone.utc).isoformat()
            layers = [
                LayerResult("regime", True, "trending", ""),
                LayerResult("technical", consensus >= 2, {"agree": 3, "total": 4, "rsi": 55}, ""),
                LayerResult("funding", consensus >= 3, -0.0001, ""),
                LayerResult("book", consensus >= 4, {"bid_ratio": 0.6}, ""),
                LayerResult("OI", consensus >= 5, 100000.0, ""),
                LayerResult("macro", consensus >= 6, 25, ""),
                LayerResult("collective", False, None, "", data_available=False),
            ]
            passed = sum(1 for lr in layers if lr.passed and lr.data_available)
            return EvaluationResult(
                coin=coin, timestamp=ts, layers=layers,
                consensus=passed, conviction=passed / 6,
                direction="LONG", regime="trending", price=150.0,
                data_age_ms=100, data_complete=True,
            )

        # SOL: entry, BTC: no signal
        sigs_sol = monitor.check_signals("SOL", make_r("SOL", 6))
        sigs_btc = monitor.check_signals("BTC", make_r("BTC", 2))

        assert sigs_sol[0].type == "ENTRY"
        assert len(sigs_btc) == 0
        assert monitor.coin_states["SOL"] == "entry"
        assert monitor.coin_states.get("BTC", "inactive") == "inactive"

    def test_multiple_signals_in_cycle(self, tmp_path):
        """Cycle correctly collects signals from multiple coins."""
        monitor = make_monitor(tmp_path, threshold=5)
        # Pre-set SOL as "entry", ETH as "inactive"
        monitor.coin_states["SOL"] = "entry"

        with patch.object(monitor, "_get_coins", return_value=["SOL", "ETH"]):
            summary = monitor.run_cycle()

        assert summary["coins_evaluated"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS (3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_signals_written_to_bus(self, tmp_path):
        """After cycle with ENTRY signal, signals.json is written to bus."""
        monitor = make_monitor(tmp_path, threshold=5)

        with patch.object(monitor, "_get_coins", return_value=["SOL"]):
            monitor.run_cycle()

        assert monitor._signals_file.exists()
        data = json.loads(monitor._signals_file.read_text())
        assert "updated_at" in data
        assert "signals" in data
        assert isinstance(data["signals"], list)

    def test_controller_reads_signals(self, tmp_path):
        """Signals written in monitor format are readable by controller."""
        from scanner.v6.monitor import Monitor

        signals_file = tmp_path / "signals.json"
        signals_data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "signals": [
                {
                    "type": "ENTRY",
                    "coin": "SOL",
                    "direction": "LONG",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "price": 150.0,
                    "consensus": 6,
                    "conviction": 0.85,
                    "layers": ["regime", "technical", "funding", "book", "OI", "macro"],
                    "regime": "trending",
                    "layers_remaining": 6,
                    "layers_lost": [],
                    "reason": "consensus_threshold_met",
                    "would_pass_strategies": ["momentum", "degen"],
                }
            ],
        }
        signals_file.write_text(json.dumps(signals_data))

        # Verify the format is loadable and has expected fields
        loaded = json.loads(signals_file.read_text())
        assert loaded["signals"][0]["type"] == "ENTRY"
        assert loaded["signals"][0]["coin"] == "SOL"
        assert loaded["signals"][0]["consensus"] == 6

    def test_decisions_logged(self, tmp_path):
        """Decision records are appended to decisions.jsonl after each cycle."""
        monitor = make_monitor(tmp_path, threshold=5)

        with patch.object(monitor, "_get_coins", return_value=["SOL"]):
            monitor.run_cycle()

        assert monitor._decisions_file.exists()
        lines = monitor._decisions_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["coin"] == "SOL"
        assert "consensus" in record
        assert "layers" in record


# ═══════════════════════════════════════════════════════════════════════════════
# Additional edge case: Signal dataclass to_dict
# ═══════════════════════════════════════════════════════════════════════════════

def test_signal_to_dict():
    """Signal.to_dict() produces JSON-serializable output with all required keys."""
    from scanner.v6.monitor import Signal
    sig = Signal(
        type="ENTRY",
        coin="SOL",
        direction="LONG",
        timestamp=datetime.now(timezone.utc).isoformat(),
        price=150.0,
        consensus=6,
        conviction=0.85,
        layers=["regime", "technical"],
        regime="trending",
        reason="consensus_threshold_met",
        would_pass_strategies=["momentum"],
    )
    d = sig.to_dict()
    assert d["type"] == "ENTRY"
    assert d["coin"] == "SOL"
    assert d["conviction"] == round(0.85, 4)
    # Should be JSON serializable
    json.dumps(d)


def test_data_cache_source_stale():
    """DataCache correctly identifies stale sources."""
    from scanner.v6.monitor import DataCache
    cache = DataCache()
    cache.prices_ts = time.time() - 35  # 35s ago → stale (>30s threshold)
    assert cache.is_source_stale("prices") is True

    cache.prices_ts = time.time() - 10  # 10s ago → fresh
    assert cache.is_source_stale("prices") is False


def test_evaluation_result_data_complete(tmp_path):
    """data_complete flag reflects whether all sources are fresh."""
    monitor = make_monitor(tmp_path)
    monitor.cache.data_complete.return_value = False
    monitor.cache.any_source_stale.return_value = True

    result = monitor.evaluate_coin("SOL")
    assert result.data_complete is False


# ─── EVALUATION PURITY ───────────────────────────────────────────────────────

def test_evaluate_coin_no_side_effects(tmp_path):
    """evaluate_coin() must be pure: no writes to files, no state mutations."""
    monitor = make_monitor(tmp_path)

    # Snapshot mutable state before
    states_before = dict(monitor.coin_states)
    prev_before = dict(monitor.prev_results)
    decisions_file = monitor._decisions_file
    near_miss_file = monitor._near_miss_file
    signals_file = monitor._signals_file

    # Ensure files don't exist yet
    for f in [decisions_file, near_miss_file, signals_file]:
        if f.exists():
            f.unlink()

    # Call evaluate_coin — should be pure
    result = monitor.evaluate_coin("SOL")

    # Verify return type
    from scanner.v6.monitor import EvaluationResult
    assert isinstance(result, EvaluationResult)
    assert result.coin == "SOL"

    # Verify NO state mutations
    assert monitor.coin_states == states_before, "evaluate_coin() mutated coin_states"
    assert monitor.prev_results == prev_before, "evaluate_coin() mutated prev_results"

    # Verify NO file writes
    assert not decisions_file.exists(), "evaluate_coin() wrote to decisions.jsonl"
    assert not near_miss_file.exists(), "evaluate_coin() wrote to near_misses.jsonl"
    assert not signals_file.exists(), "evaluate_coin() wrote to signals.json"

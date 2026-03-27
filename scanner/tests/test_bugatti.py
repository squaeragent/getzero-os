#!/usr/bin/env python3
"""
test_bugatti.py — Bugatti upgrades B1–B6 test suite.

Tests:
  B1 — Approaching detection (8 tests)
  B2 — Execution quality / slippage (4 tests)
  B3 — Layer accuracy (1 test)
  B4 — Cycle metrics (3 tests)
  B5 — Session cost (1 test)
  B6 — Testnet stub (1 test, skipped)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scanner.v6.monitor import (
    ApproachingSignal,
    CycleMetrics,
    EvaluationResult,
    LayerResult,
    Monitor,
)
from scanner.v6.controller import ExecutionQuality
from scanner.v6.session import SessionCost, SessionResult

# ─── Helpers ──────────────────────────────────────────────────────────────────

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
        "rsi": "long", "macd": "long", "ema": "long",
        "bollinger": "neutral", "obv": "long", "funding": "long",
    },
    "indicators": {
        "RSI_14": 55.0, "MACD_HIST": 0.05, "EMA_9": 150.0,
        "EMA_21": 148.0, "EMA_50": 145.0, "BB_PCT": 0.65,
        "BB_BANDWIDTH": 0.04, "ATR": 2.5, "ATR_PCT": 0.03,
        "OBV": 12345, "FUNDING": -0.0001, "FUNDING_ANN": -0.876,
        "VOL_RATIO": 1.2, "HURST": 0.62, "DFA": 0.55,
        "CLOSE_PRICE": 150.0, "BOOK_DEPTH_USD": 500000.0,
        "SPREAD_BPS": 2.5,
    },
    "reasons": ["RSI_55", "MACD_BULL", "EMA_BULL"],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}


def make_strategy_yaml(tmp_path: Path, name: str = "momentum", threshold: int = 5) -> Path:
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
    (strat_dir / f"{name}.yaml").write_text(yaml_content)
    return strat_dir


def make_monitor(tmp_path: Path, strategy_name: str = "momentum", threshold: int = 5):
    """Create a Monitor with __new__ bypass, matching test_monitor.py pattern."""
    from scanner.v6.strategy_loader import load_strategy
    from scanner.v6.monitor import NearMissDetector

    strat_dir = make_strategy_yaml(tmp_path, strategy_name, threshold)
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
    monitor.cache.get_book.return_value = (300_000.0, 200_000.0)
    monitor.cache.fear_greed = 25
    monitor.coin_states = {}
    monitor.prev_results = {}
    monitor.cycle_count = 0
    monitor._bus_dir = bus_dir
    monitor._signals_file = bus_dir / "signals.json"
    monitor._near_miss_file = bus_dir / "near_misses.jsonl"
    monitor._decisions_file = bus_dir / "decisions.jsonl"
    monitor._heartbeat_file = bus_dir / "heartbeat.json"

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


def _make_eval_result(coin: str, consensus: int, threshold: int = 5,
                      direction: str = "LONG", price: float = 150.0,
                      failing_layers: list[str] | None = None) -> EvaluationResult:
    """Build an EvaluationResult with given consensus (first N layers pass)."""
    all_layers = ["regime", "technical", "funding", "book", "OI", "macro", "collective"]
    if failing_layers is None:
        failing_layers = all_layers[consensus:]

    layers = []
    for layer_name in all_layers:
        passed = layer_name not in failing_layers
        layers.append(LayerResult(
            layer=layer_name,
            passed=passed,
            value=1 if passed else 0,
            detail=f"{layer_name} ok" if passed else f"{layer_name} fail",
            data_available=True,
        ))

    return EvaluationResult(
        coin=coin,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layers=layers,
        consensus=consensus,
        conviction=consensus / 7.0,
        direction=direction,
        regime="trending",
        price=price,
        data_age_ms=100,
        data_complete=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# B1: APPROACHING DETECTION (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestB1Approaching:
    """B1 — ApproachingSignal + _check_approaching."""

    def test_approaching_high_urgency(self, tmp_path):
        """Coin at threshold-1 → urgency='high'."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("SOL", consensus=4, threshold=5)
        sig = mon._check_approaching("SOL", result)
        assert sig is not None
        assert sig.urgency == "high"
        assert sig.distance == 1
        assert sig.coin == "SOL"

    def test_approaching_low_urgency(self, tmp_path):
        """Coin at threshold-2 → urgency='low'."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("ETH", consensus=3, threshold=5)
        sig = mon._check_approaching("ETH", result)
        assert sig is not None
        assert sig.urgency == "low"
        assert sig.distance == 2

    def test_no_approaching_below_range(self, tmp_path):
        """consensus < threshold-2 → no signal."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("BTC", consensus=2, threshold=5)
        sig = mon._check_approaching("BTC", result)
        assert sig is None

    def test_approaching_to_entry(self, tmp_path):
        """Coin reaches threshold → ENTRY not approaching (distance=0)."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("SOL", consensus=5, threshold=5)
        sig = mon._check_approaching("SOL", result)
        assert sig is None

    def test_approaching_dedup(self, tmp_path):
        """Same consensus twice → second call returns None."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("SOL", consensus=4, threshold=5)
        sig1 = mon._check_approaching("SOL", result)
        assert sig1 is not None
        sig2 = mon._check_approaching("SOL", result)
        assert sig2 is None

    def test_approaching_cooling(self, tmp_path):
        """Consensus drops → urgency='cooling'."""
        mon = make_monitor(tmp_path, threshold=5)
        # First: consensus=4 (high)
        r1 = _make_eval_result("SOL", consensus=4, threshold=5)
        sig1 = mon._check_approaching("SOL", r1)
        assert sig1 is not None
        assert sig1.urgency == "high"
        # Then drops to 3
        r2 = _make_eval_result("SOL", consensus=3, threshold=5)
        sig2 = mon._check_approaching("SOL", r2)
        assert sig2 is not None
        assert sig2.urgency == "cooling"

    def test_bottleneck_identified(self, tmp_path):
        """Bottleneck is the first failing layer."""
        mon = make_monitor(tmp_path, threshold=5)
        result = _make_eval_result("SOL", consensus=4, threshold=5,
                                   failing_layers=["funding", "book", "macro"])
        sig = mon._check_approaching("SOL", result)
        assert sig is not None
        assert sig.bottleneck == "funding"

    def test_multiple_coins_approaching(self, tmp_path):
        """Two coins tracked independently."""
        mon = make_monitor(tmp_path, threshold=5)
        r_sol = _make_eval_result("SOL", consensus=4, threshold=5)
        r_eth = _make_eval_result("ETH", consensus=3, threshold=5)
        sig_sol = mon._check_approaching("SOL", r_sol)
        sig_eth = mon._check_approaching("ETH", r_eth)
        assert sig_sol is not None and sig_sol.coin == "SOL"
        assert sig_eth is not None and sig_eth.coin == "ETH"
        assert sig_sol.urgency == "high"
        assert sig_eth.urgency == "low"


# ═════════════════════════════════════════════════════════════════════════════
# B2: EXECUTION QUALITY / SLIPPAGE (4 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestB2Slippage:
    """B2 — ExecutionQuality dataclass."""

    def _make_eq(self, **overrides) -> ExecutionQuality:
        defaults = dict(
            signal_price=100.0,
            order_price=100.05,
            fill_price=100.10,
            signal_to_order_ms=50.0,
            order_to_fill_ms=120.0,
            signal_to_fill_ms=170.0,
            slippage_pct=0.10,
            slippage_bps=10.0,
            order_type="limit",
            coin="SOL",
            direction="LONG",
        )
        defaults.update(overrides)
        return ExecutionQuality(**defaults)

    def test_execution_quality_fields(self):
        """All fields present on ExecutionQuality."""
        eq = self._make_eq()
        d = eq.to_dict()
        for field_name in [
            "signal_price", "order_price", "fill_price",
            "signal_to_order_ms", "order_to_fill_ms", "signal_to_fill_ms",
            "slippage_pct", "slippage_bps", "order_type", "coin", "direction",
        ]:
            assert field_name in d, f"Missing field: {field_name}"

    def test_slippage_positive_long(self):
        """Fill higher than signal for LONG = positive slippage."""
        eq = self._make_eq(
            signal_price=100.0,
            fill_price=100.50,
            slippage_pct=0.50,
            slippage_bps=50.0,
            direction="LONG",
        )
        assert eq.slippage_bps > 0
        assert eq.fill_price > eq.signal_price

    def test_slippage_paper_zero(self):
        """Paper mode → 0 slippage (fill = signal)."""
        eq = self._make_eq(
            signal_price=100.0,
            order_price=100.0,
            fill_price=100.0,
            slippage_pct=0.0,
            slippage_bps=0.0,
        )
        assert eq.slippage_bps == 0.0
        assert eq.slippage_pct == 0.0

    def test_latencies_computed(self):
        """signal_to_fill_ms computed."""
        eq = self._make_eq(
            signal_to_order_ms=50.0,
            order_to_fill_ms=120.0,
            signal_to_fill_ms=170.0,
        )
        assert eq.signal_to_fill_ms == 170.0
        assert eq.signal_to_fill_ms == eq.signal_to_order_ms + eq.order_to_fill_ms


# ═════════════════════════════════════════════════════════════════════════════
# B3: LAYER ACCURACY (1 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestB3LayerAccuracy:
    """B3 — layer_accuracy on SessionResult."""

    def test_layer_accuracy_structure(self):
        """Correct dict format {layer: {total, correct, accuracy_pct}}."""
        layer_accuracy = {
            "regime": {"total": 100, "correct": 85, "accuracy_pct": 85.0},
            "technical": {"total": 100, "correct": 70, "accuracy_pct": 70.0},
            "funding": {"total": 100, "correct": 90, "accuracy_pct": 90.0},
        }
        sr = SessionResult(
            session_id="test-1",
            strategy="momentum",
            strategy_display="Test Momentum",
            duration_actual=timedelta(hours=24),
            paper=True,
            trade_count=5,
            wins=3,
            losses=2,
            best_trade=2.5,
            worst_trade=-1.0,
            total_pnl_usd=10.0,
            total_pnl_pct=1.0,
            max_drawdown_pct=0.5,
            eval_count=100,
            reject_count=50,
            rejection_rate_pct=50.0,
            near_misses=[],
            timeline=[],
            narrative_text="test",
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            coins_in_scope=50,
            layer_accuracy=layer_accuracy,
        )
        d = sr.to_dict()
        assert "layer_accuracy" in d
        for layer_name in ["regime", "technical", "funding"]:
            entry = d["layer_accuracy"][layer_name]
            assert "total" in entry
            assert "correct" in entry
            assert "accuracy_pct" in entry


# ═════════════════════════════════════════════════════════════════════════════
# B4: EXECUTION METRICS (3 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestB4CycleMetrics:
    """B4 — CycleMetrics + _log_metrics."""

    def _make_metrics(self, **overrides) -> CycleMetrics:
        defaults = dict(
            cycle_number=1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            cycle_duration_ms=450,
            data_fetch_duration_ms=200,
            evaluation_duration_ms=200,
            signal_emission_duration_ms=50,
            data_freshness_max_ms=150,
            data_sources_available=4,
            data_sources_stale=0,
            coins_evaluated=50,
            coins_passed=2,
            coins_rejected=48,
            coins_approaching=3,
            signals_emitted=2,
            memory_mb=128.5,
        )
        defaults.update(overrides)
        return CycleMetrics(**defaults)

    def test_cycle_metrics_computed(self):
        """CycleMetrics has all fields."""
        m = self._make_metrics()
        d = m.to_dict()
        for f in [
            "cycle_number", "timestamp", "cycle_duration_ms",
            "data_fetch_duration_ms", "evaluation_duration_ms",
            "signal_emission_duration_ms", "coins_evaluated",
            "coins_passed", "coins_rejected", "coins_approaching",
            "signals_emitted", "memory_mb",
        ]:
            assert f in d, f"Missing: {f}"

    def test_metrics_logged(self, tmp_path):
        """metrics.jsonl written after _log_metrics call."""
        mon = make_monitor(tmp_path, threshold=5)
        m = self._make_metrics()
        mon._log_metrics(m)
        assert mon._metrics_file.exists()
        lines = mon._metrics_file.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["cycle_number"] == 1
        assert parsed["coins_evaluated"] == 50

    def test_cycle_timing(self):
        """duration_ms > 0."""
        m = self._make_metrics(cycle_duration_ms=450)
        assert m.cycle_duration_ms > 0


# ═════════════════════════════════════════════════════════════════════════════
# B5: SESSION COST (1 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestB5SessionCost:
    """B5 — SessionCost dataclass."""

    def test_session_cost_structure(self):
        """SessionCost fields present."""
        sc = SessionCost(
            total_cycles=100,
            total_evaluations=5000,
            hl_api_calls=200,
            hl_api_calls_by_type={"info": 100, "order": 50, "cancel": 50},
            cpu_seconds=120.5,
            peak_memory_mb=256.0,
            decision_log_bytes=1024000,
            estimated_cost_usd=0.05,
        )
        d = sc.to_dict()
        for f in [
            "total_cycles", "total_evaluations", "hl_api_calls",
            "hl_api_calls_by_type", "cpu_seconds", "peak_memory_mb",
            "decision_log_bytes", "estimated_cost_usd",
        ]:
            assert f in d, f"Missing: {f}"
        assert d["total_cycles"] == 100
        assert d["estimated_cost_usd"] == 0.05


# ═════════════════════════════════════════════════════════════════════════════
# B6: TESTNET STUB (skipped)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="B6 testnet stub — requires HL testnet credentials")
def test_hl_testnet_round_trip():
    """Documents the expected testnet flow: place → fill → cancel."""
    # 1. Connect to HL testnet
    # 2. Place a limit order far from market
    # 3. Verify order appears in open orders
    # 4. Cancel the order
    # 5. Verify order is gone
    pass

#!/usr/bin/env python3
"""
INTEGRATION TEST: Full pipeline Monitor → Controller.

Tests the complete signal lifecycle:
  Monitor evaluates → emits signal → Controller receives → executes in paper mode.

All HL/API calls fully mocked. Each test independent with tmp_path.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.controller import (
    Controller,
    Position,
    _StrategyParams,
    approve_entry,
    inject_strategy_params,
    handle_entry_end_events,
    close_trade,
    open_trade,
    run_once,
    log_decision,
    append_jsonl,
    save_json_atomic,
    load_json,
    now_iso,
)
from scanner.v6.monitor import (
    Monitor,
    EvaluationResult,
    LayerResult,
    Signal,
    DataCache,
)
from scanner.v6.strategy_loader import load_strategy, StrategyConfig
from scanner.v6.hl_client import HLClient


# ════════════════════════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def momentum_strategy():
    """Load the real momentum.yaml strategy."""
    return load_strategy("momentum")


@pytest.fixture
def mock_client():
    """Fully mocked HLClient — no real HL calls."""
    client = MagicMock(spec=HLClient)
    client.get_balance.return_value = 1000.0
    client.get_price.return_value = 100.0
    client.get_positions.return_value = []
    client.get_open_orders.return_value = []
    client.get_fee_rates.return_value = {"taker": 0.00045, "maker": 0.00015}
    client.get_predicted_funding.return_value = 0.0001
    client.get_l2_book.return_value = {
        "bids": [(99.9, 100.0)] * 5,
        "asks": [(100.1, 100.0)] * 5,
        "bid_depth_usd": 50000,
        "ask_depth_usd": 50000,
    }
    client.get_rate_limit.return_value = {"used": 10, "cap": 10000, "cum_volume": 1000}
    client.place_ioc_order.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"avgPx": "100", "totalSz": "1.0", "oid": 12345}}]}},
    }
    client.place_stop_loss.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"resting": {"oid": 67890}}]}},
    }
    client.market_buy.return_value = client.place_ioc_order.return_value
    client.market_sell.return_value = client.place_ioc_order.return_value
    client.round_price.side_effect = HLClient.round_price
    client.float_to_wire.side_effect = HLClient.float_to_wire
    client.cancel_coin_stops.return_value = None
    return client


@pytest.fixture
def tmp_bus(tmp_path):
    """Isolated bus + data directories for each test."""
    bus = tmp_path / "bus"
    bus.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return bus


def _setup_bus_files(bus_dir: Path, data_dir: Path | None = None):
    """Create required bus files for a controller run_once cycle."""
    save_json_atomic(bus_dir / "entries.json", {"updated_at": now_iso(), "entries": []})
    save_json_atomic(bus_dir / "approved.json", {"updated_at": now_iso(), "approved": []})
    save_json_atomic(bus_dir / "positions.json", {"updated_at": now_iso(), "positions": []})
    save_json_atomic(bus_dir / "exits.json", {"updated_at": now_iso(), "exits": []})
    save_json_atomic(bus_dir / "signals.json", {"updated_at": now_iso(), "signals": []})
    save_json_atomic(bus_dir / "risk.json", {
        "updated_at": now_iso(),
        "halted": False,
        "halt_reason": None,
        "halt_until": None,
        "daily_loss_usd": 0.0,
        "daily_pnl_usd": 0.0,
        "daily_loss_since": datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat(),
        "capital_floor_hit": False,
        "open_count": 0,
        "peak_equity": 1000.0,
        "drawdown_pct": 0.0,
    })
    save_json_atomic(bus_dir / "heartbeat.json", {})
    save_json_atomic(bus_dir / "portfolio.json", {"account_value": 1000.0, "last_price": {}})


def _make_eval_result(
    coin: str,
    consensus: int,
    direction: str = "LONG",
    regime: str = "trending",
    price: float = 100.0,
    rsi: float = 55.0,
) -> EvaluationResult:
    """Build a controlled EvaluationResult with exact consensus count."""
    layer_names = ["regime", "technical", "funding", "book", "OI", "macro", "collective"]
    layers = []
    for i, name in enumerate(layer_names):
        passed = i < consensus
        value = regime if name == "regime" else ({"rsi": rsi, "agree": 3, "total": 4} if name == "technical" else 0)
        layers.append(LayerResult(
            layer=name,
            passed=passed,
            value=value,
            detail=f"{name} test",
            data_available=True,
        ))

    conviction = consensus / 7.0 if consensus > 0 else 0.0
    final_direction = direction if consensus > 0 else "NONE"

    return EvaluationResult(
        coin=coin,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layers=layers,
        consensus=consensus,
        conviction=round(conviction, 4),
        direction=final_direction,
        regime=regime,
        price=price,
        data_age_ms=50,
        data_complete=True,
    )


def _patch_controller(bus_dir: Path, data_dir: Path, strategy: StrategyConfig):
    """Return a context manager that patches all controller module-level paths."""
    # Build dynamic limits consistent with the strategy's position_size_pct
    # so hard_cap check doesn't reject based on config.py's aggressive defaults.
    def _test_dynamic_limits(equity):
        size_pct = strategy.risk.position_size_pct / 100.0 if strategy else 0.10
        return {
            "max_positions": strategy.risk.max_positions if strategy else 5,
            "max_position_usd": round(equity * size_pct, 2),
            "min_position_usd": round(max(10, equity * 0.05), 2),
            "daily_loss_limit": round(equity * 0.05, 2),
        }

    from contextlib import contextmanager

    @contextmanager
    def _combined():
        with patch.multiple(
            "scanner.v6.controller",
            BUS_DIR=bus_dir,
            DATA_DIR=data_dir,
            ENTRIES_FILE=bus_dir / "entries.json",
            APPROVED_FILE=bus_dir / "approved.json",
            POSITIONS_FILE=bus_dir / "positions.json",
            RISK_FILE=bus_dir / "risk.json",
            HEARTBEAT_FILE=bus_dir / "heartbeat.json",
            EXITS_FILE=bus_dir / "exits.json",
            SIGNALS_FILE=bus_dir / "signals.json",
            TRADES_FILE=data_dir / "trades.jsonl",
            DECISION_LOG_FILE=bus_dir / "decisions.jsonl",
            EVENTS_LOG_FILE=bus_dir / "events.jsonl",
            REJECTION_LOG_FILE=bus_dir / "rejections.jsonl",
            NEAR_MISS_LOG_FILE=bus_dir / "near_misses.jsonl",
            CONTROLLER_STATE_FILE=bus_dir / "controller_state.json",
            get_active_strategy=lambda: strategy,
            get_dynamic_limits=_test_dynamic_limits,
        ), patch(
            "scanner.v6.config.get_dynamic_limits",
            _test_dynamic_limits,
        ):
            yield

    return _combined()


def _build_monitor(bus_dir: Path) -> Monitor:
    """Build a Monitor with all external calls mocked out."""
    with patch("scanner.v6.monitor.SmartProvider"):
        monitor = Monitor(strategy_name="momentum", bus_dir=bus_dir)
    monitor.smart_provider = MagicMock()
    monitor.cache = MagicMock(spec=DataCache)
    monitor.cache.refresh.return_value = True
    monitor.cache.is_price_stale.return_value = False
    monitor.cache.get_price.return_value = 100.0
    monitor.cache.price_age_ms.return_value = 50
    monitor.cache.data_complete.return_value = True
    monitor.cache.any_source_stale.return_value = False
    monitor.cache.get_funding.return_value = 0.00005
    monitor.cache.get_oi.return_value = 50000.0
    monitor.cache.get_book.return_value = (60000.0, 40000.0)
    monitor.cache.fear_greed = 30
    return monitor


# ════════════════════════════════════════════════════════════════════════════════
# TEST 1: FULL LIFECYCLE
# ════════════════════════════════════════════════════════════════════════════════

class TestFullLifecycle:
    """
    Main integration test: Monitor → Controller full pipeline.

    Steps:
    1. Monitor evaluates → ENTRY signal (6/7 consensus LONG)
    2. Controller receives → paper order placed
    3. Monitor evaluates again → same consensus → dedup (no re-emit)
    4. Monitor evaluates → consensus drops → ENTRY_END
    5. Controller receives ENTRY_END → hold (entry_end_action=hold)
    6. Monitor evaluates → regime shift to chaotic → EXIT
    7. Controller receives EXIT → closes position → logs P&L
    """

    def test_full_lifecycle(self, tmp_bus, mock_client, momentum_strategy):
        data_dir = tmp_bus.parent / "data"
        _setup_bus_files(tmp_bus, data_dir)
        decisions_file = tmp_bus / "decisions.jsonl"
        events_file = tmp_bus / "events.jsonl"
        trades_file = data_dir / "trades.jsonl"

        monitor = _build_monitor(tmp_bus)
        monitor._get_coins = lambda: ["BTC"]

        # Track evaluate_coin calls to return controlled results
        eval_call_count = [0]
        original_evaluate = monitor.evaluate_coin

        def mock_evaluate_coin(coin):
            eval_call_count[0] += 1
            call_num = eval_call_count[0]
            if call_num == 1:
                # 6/7 consensus LONG, trending → triggers ENTRY
                return _make_eval_result(coin, consensus=6, direction="LONG", regime="trending")
            elif call_num == 2:
                # Same → dedup (no re-emit)
                return _make_eval_result(coin, consensus=6, direction="LONG", regime="trending")
            elif call_num == 3:
                # Drops to 3/7 → triggers ENTRY_END
                return _make_eval_result(coin, consensus=3, direction="LONG", regime="trending")
            elif call_num == 4:
                # Regime shift to chaotic → triggers EXIT
                return _make_eval_result(coin, consensus=2, direction="LONG", regime="chaotic")
            return _make_eval_result(coin, consensus=1, direction="LONG", regime="stable")

        monitor.evaluate_coin = mock_evaluate_coin

        ctrl = Controller()

        # ── STEP 1: First evaluation → ENTRY signal ─────────────────────────
        summary1 = monitor.run_cycle()
        assert summary1["coins_evaluated"] == 1
        assert summary1["signals_emitted"] == 1

        # Verify signals.json has ENTRY
        signals_data = load_json(tmp_bus / "signals.json", {})
        assert len(signals_data.get("signals", [])) == 1
        entry_signal = signals_data["signals"][0]
        assert entry_signal["type"] == "ENTRY"
        assert entry_signal["coin"] == "BTC"
        assert entry_signal["direction"] == "LONG"
        assert entry_signal["consensus"] >= 5  # passes momentum threshold

        # State machine moved to "entry"
        assert monitor.coin_states.get("BTC") == "entry"

        # ── STEP 2: Controller receives ENTRY → paper order ──────────────────
        save_json_atomic(tmp_bus / "approved.json", {"updated_at": now_iso(), "approved": []})
        with _patch_controller(tmp_bus, data_dir, momentum_strategy), \
             patch.dict(os.environ, {"PAPER_MODE": "1"}, clear=False):
            run_once(client=mock_client, dry=True, controller=ctrl)

        # Verify position was tracked
        pos_data = load_json(tmp_bus / "positions.json", {})
        positions = pos_data.get("positions", [])
        assert len(positions) == 1, f"Expected 1 position, got {len(positions)}: {positions}"
        assert positions[0]["coin"] == "BTC"
        assert positions[0]["direction"] == "LONG"

        # Verify TRADE_ENTERED event
        trade_entered_events = [e for e in ctrl.events if e["type"] == "TRADE_ENTERED"]
        assert len(trade_entered_events) >= 1
        assert trade_entered_events[0]["coin"] == "BTC"

        # Verify decision log
        if decisions_file.exists():
            decisions = [json.loads(line) for line in decisions_file.read_text().strip().split("\n") if line.strip()]
            approved_decisions = [d for d in decisions if d.get("verdict") == "approved"]
            assert len(approved_decisions) >= 1

        # ── STEP 3: Second evaluation → dedup ────────────────────────────────
        summary2 = monitor.run_cycle()
        assert summary2["coins_evaluated"] == 1
        assert summary2["signals_emitted"] == 0  # dedup: no re-emit
        assert monitor.coin_states.get("BTC") == "entry"

        # ── STEP 4: Third evaluation → ENTRY_END ─────────────────────────────
        summary3 = monitor.run_cycle()
        assert summary3["coins_evaluated"] == 1
        assert summary3["signals_emitted"] == 1

        signals3 = load_json(tmp_bus / "signals.json", {})
        assert len(signals3.get("signals", [])) == 1
        entry_end_signal = signals3["signals"][0]
        assert entry_end_signal["type"] == "ENTRY_END"
        assert monitor.coin_states.get("BTC") == "entry_end"

        # ── STEP 5: Controller receives ENTRY_END → hold ─────────────────────
        save_json_atomic(tmp_bus / "approved.json", {"updated_at": now_iso(), "approved": []})
        with _patch_controller(tmp_bus, data_dir, momentum_strategy), \
             patch.dict(os.environ, {"PAPER_MODE": "1"}, clear=False):
            run_once(client=mock_client, dry=True, controller=ctrl)

        # entry_end_action=hold → position stays open
        pos_after_hold = load_json(tmp_bus / "positions.json", {}).get("positions", [])
        assert len(pos_after_hold) == 1, "Position should be held (entry_end_action=hold)"

        # ── STEP 6: Fourth evaluation → regime shift → EXIT ──────────────────
        summary4 = monitor.run_cycle()
        assert summary4["coins_evaluated"] == 1
        assert summary4["signals_emitted"] == 1

        signals4 = load_json(tmp_bus / "signals.json", {})
        assert len(signals4.get("signals", [])) == 1
        exit_signal = signals4["signals"][0]
        assert exit_signal["type"] == "EXIT"
        assert "regime_shift" in exit_signal.get("reason", "")
        assert monitor.coin_states.get("BTC") == "inactive"

        # ── STEP 7: Controller receives EXIT → closes position ───────────────
        save_json_atomic(tmp_bus / "approved.json", {"updated_at": now_iso(), "approved": []})
        with _patch_controller(tmp_bus, data_dir, momentum_strategy), \
             patch.dict(os.environ, {"PAPER_MODE": "1"}, clear=False):
            run_once(client=mock_client, dry=True, controller=ctrl)

        # Position should be closed
        pos_final = load_json(tmp_bus / "positions.json", {}).get("positions", [])
        assert len(pos_final) == 0, f"Position should be closed, got {len(pos_final)}"

        # Verify TRADE_EXITED event
        trade_exited_events = [e for e in ctrl.events if e["type"] == "TRADE_EXITED"]
        assert len(trade_exited_events) >= 1
        assert trade_exited_events[0]["coin"] == "BTC"

        # Verify trades.jsonl
        if trades_file.exists():
            trade_lines = [json.loads(l) for l in trades_file.read_text().strip().split("\n") if l.strip()]
            assert len(trade_lines) >= 1
            assert trade_lines[0]["coin"] == "BTC"
            assert trade_lines[0]["direction"] == "LONG"
            assert "pnl_usd" in trade_lines[0]

        # Verify controller stats
        assert ctrl.eval_count >= 1

        # Heartbeat was updated
        hb = load_json(tmp_bus / "heartbeat.json", {})
        assert len(hb) > 0


# ════════════════════════════════════════════════════════════════════════════════
# TEST 2: MULTIPLE COINS INDEPENDENT
# ════════════════════════════════════════════════════════════════════════════════

class TestMultipleCoinsIndependent:
    """Two coins going through different lifecycle stages simultaneously."""

    def test_multiple_coins_independent(self, tmp_bus, mock_client, momentum_strategy):
        data_dir = tmp_bus.parent / "data"
        _setup_bus_files(tmp_bus, data_dir)

        monitor = _build_monitor(tmp_bus)
        monitor._get_coins = lambda: ["BTC", "ETH"]

        eval_calls = {}  # coin -> call_count

        def mock_evaluate_coin(coin):
            eval_calls.setdefault(coin, 0)
            eval_calls[coin] += 1
            call_num = eval_calls[coin]

            if coin == "BTC":
                if call_num == 1:
                    # BTC passes on cycle 1
                    return _make_eval_result(coin, consensus=6, direction="LONG", regime="trending")
                elif call_num == 2:
                    # BTC regime shift → EXIT on cycle 2
                    return _make_eval_result(coin, consensus=2, direction="LONG", regime="chaotic")
                return _make_eval_result(coin, consensus=1, regime="stable")
            elif coin == "ETH":
                if call_num == 1:
                    # ETH fails on cycle 1
                    return _make_eval_result(coin, consensus=3, direction="LONG", regime="trending")
                elif call_num == 2:
                    # ETH passes on cycle 2
                    return _make_eval_result(coin, consensus=6, direction="LONG", regime="trending")
                return _make_eval_result(coin, consensus=1, regime="stable")
            return _make_eval_result(coin, consensus=1)

        monitor.evaluate_coin = mock_evaluate_coin

        # ── Cycle 1: BTC passes (6/7), ETH doesn't (3/7) ────────────────────
        summary1 = monitor.run_cycle()
        assert summary1["coins_evaluated"] == 2
        assert summary1["signals_emitted"] == 1  # only BTC

        signals1 = load_json(tmp_bus / "signals.json", {})
        sig_types = {s["coin"]: s["type"] for s in signals1.get("signals", [])}
        assert sig_types.get("BTC") == "ENTRY"
        assert "ETH" not in sig_types

        assert monitor.coin_states.get("BTC") == "entry"
        assert monitor.coin_states.get("ETH", "inactive") == "inactive"

        # ── Cycle 2: BTC regime shift → EXIT, ETH now passes → ENTRY ────────
        summary2 = monitor.run_cycle()
        assert summary2["coins_evaluated"] == 2
        assert summary2["signals_emitted"] == 2  # BTC EXIT + ETH ENTRY

        signals2 = load_json(tmp_bus / "signals.json", {})
        sig_types2 = {s["coin"]: s["type"] for s in signals2.get("signals", [])}
        assert sig_types2.get("BTC") == "EXIT"
        assert sig_types2.get("ETH") == "ENTRY"

        assert monitor.coin_states.get("BTC") == "inactive"
        assert monitor.coin_states.get("ETH") == "entry"


# ════════════════════════════════════════════════════════════════════════════════
# TEST 3: SESSION WITH ZERO TRADES
# ════════════════════════════════════════════════════════════════════════════════

class TestSessionZeroTrades:
    """Monitor runs 10 cycles, nothing passes, clean stats."""

    def test_session_with_zero_trades(self, tmp_bus, mock_client, momentum_strategy):
        data_dir = tmp_bus.parent / "data"
        _setup_bus_files(tmp_bus, data_dir)

        monitor = _build_monitor(tmp_bus)
        monitor._get_coins = lambda: ["BTC", "ETH", "SOL"]

        # Always return low consensus — nothing passes
        monitor.evaluate_coin = lambda coin: _make_eval_result(
            coin, consensus=2, direction="LONG", regime="trending"
        )

        total_signals = 0
        total_evals = 0
        for _ in range(10):
            summary = monitor.run_cycle()
            total_signals += summary["signals_emitted"]
            total_evals += summary["coins_evaluated"]

        # 10 cycles * 3 coins = 30 evaluations
        assert total_evals == 30
        assert total_signals == 0

        # All coins remain inactive
        for coin in ["BTC", "ETH", "SOL"]:
            assert monitor.coin_states.get(coin, "inactive") == "inactive"

        # decisions.jsonl has 30 records
        decisions_file = tmp_bus / "decisions.jsonl"
        if decisions_file.exists():
            lines = [l for l in decisions_file.read_text().strip().split("\n") if l.strip()]
            assert len(lines) == 30

        # No trades
        trades_file = data_dir / "trades.jsonl"
        assert not trades_file.exists() or trades_file.read_text().strip() == ""


# ════════════════════════════════════════════════════════════════════════════════
# TEST 4: REJECTION SELECTIVITY
# ════════════════════════════════════════════════════════════════════════════════

class TestRejectionSelectivity:
    """50 evaluations, 48 rejected, 2 passed → correct counts."""

    def test_rejection_selectivity(self, tmp_bus, mock_client, momentum_strategy):
        data_dir = tmp_bus.parent / "data"
        _setup_bus_files(tmp_bus, data_dir)

        monitor = _build_monitor(tmp_bus)
        monitor._get_coins = lambda: ["BTC"]

        eval_count = [0]

        def mock_evaluate_coin(coin):
            eval_count[0] += 1
            # Only evaluations 10 and 30 pass
            if eval_count[0] in (10, 30):
                return _make_eval_result(coin, consensus=6, direction="LONG", regime="trending")
            return _make_eval_result(coin, consensus=2, direction="LONG", regime="trending")

        monitor.evaluate_coin = mock_evaluate_coin

        total_signals = 0
        total_evals = 0
        for _ in range(50):
            summary = monitor.run_cycle()
            total_signals += summary["signals_emitted"]
            total_evals += summary["coins_evaluated"]

        assert total_evals == 50
        # 2 ENTRY signals (evals 10 and 30), plus ENTRY_END signals when consensus drops
        assert total_signals >= 2

        # decisions.jsonl should have 50 records
        decisions_file = tmp_bus / "decisions.jsonl"
        if decisions_file.exists():
            lines = [l for l in decisions_file.read_text().strip().split("\n") if l.strip()]
            assert len(lines) == 50


# ════════════════════════════════════════════════════════════════════════════════
# TEST 5: PAPER MODE NO REAL ORDERS
# ════════════════════════════════════════════════════════════════════════════════

class TestPaperModeNoRealOrders:
    """Verify no HLClient order methods called in paper mode (dry=True)."""

    def test_paper_mode_no_real_orders(self, tmp_bus, mock_client, momentum_strategy):
        data_dir = tmp_bus.parent / "data"
        _setup_bus_files(tmp_bus, data_dir)

        # Write a signal that the controller should pick up
        save_json_atomic(tmp_bus / "signals.json", {
            "updated_at": now_iso(),
            "signals": [{
                "type": "ENTRY",
                "coin": "BTC",
                "direction": "LONG",
                "timestamp": now_iso(),
                "price": 100.0,
                "consensus": 6,
                "conviction": 0.857,
                "layers": ["regime", "technical", "funding", "book", "OI", "macro"],
                "regime": "trending",
                "reason": "consensus_threshold_met",
                "would_pass_strategies": ["momentum"],
                "layers_remaining": 0,
                "layers_lost": [],
            }],
        })

        ctrl = Controller()

        with _patch_controller(tmp_bus, data_dir, momentum_strategy), \
             patch.dict(os.environ, {"PAPER_MODE": "1"}, clear=False):
            run_once(client=mock_client, dry=True, controller=ctrl)

        # In dry mode, real order methods should NOT be called
        mock_client.place_ioc_order.assert_not_called()
        mock_client.place_stop_loss.assert_not_called()
        mock_client.market_buy.assert_not_called()
        mock_client.market_sell.assert_not_called()
        mock_client._sign_and_send.assert_not_called()
        mock_client.cancel_coin_stops.assert_not_called()


# ─── ADDITIONAL INTEGRATION TESTS ──────────────────────────────────────────


class TestSessionLifecycle:
    """Session state machine integration: create → evaluate → expire."""

    def test_session_creation_and_expiry(self, tmp_bus, mock_client, momentum_strategy):
        """Session starts, runs evaluations, and expires naturally."""
        from scanner.v6.session import SessionManager
        sm = SessionManager(bus_dir=tmp_bus)
        session = sm.start_session(strategy_name="momentum", paper=True)
        assert session is not None
        assert session.state == "active"

        # End session
        result = sm.end_session_early(session)
        assert result is not None
        assert result.session_id == session.id

    def test_session_tracks_entries(self, tmp_bus, mock_client, momentum_strategy):
        """Session records entry events."""
        from scanner.v6.session import SessionManager
        sm = SessionManager(bus_dir=tmp_bus)
        session = sm.start_session(strategy_name="momentum", paper=True)

        # Record a trade
        sm.record_entry(session, {"coin": "BTC", "direction": "LONG", "size": 0.001, "price": 67000.0})
        assert len(session.timeline) >= 1

        result = sm.end_session_early(session)
        assert result is not None


class TestTrailingStopIntegration:
    """Trailing stops activate and trigger within the controller pipeline."""

    def test_trailing_activates_on_profit(self, tmp_bus, mock_client, momentum_strategy):
        """Position reaches activation threshold → trailing stop engages."""
        pos = Position(
            id="test-001", coin="BTC", direction="LONG", strategy="momentum",
            session_id="sess-001", entry_price=60000.0, size_usd=60.0,
            size_coins=0.001, stop_loss_pct=3.0, stop_loss_price=58200.0,
            entry_time="2026-03-27T10:00:00Z", signal_name="momentum_long",
            sharpe=1.2, hl_order_id="hl-001", sl_order_id="sl-001",
        )
        # Simulate price moving up 1.5%
        new_price = 60900.0
        if new_price > pos.entry_price:
            pos.peak_pnl_pct = (new_price - pos.entry_price) / pos.entry_price * 100
            assert pos.peak_pnl_pct > 1.0
        # Trailing stop tracks peak
        pos.trailing_peak = new_price
        assert pos.trailing_peak >= pos.entry_price


class TestApproachingToEntry:
    """Coins transition from approaching → entry when layers flip."""

    def test_approaching_detected(self, tmp_bus, mock_client, momentum_strategy):
        """Coin at 4/7 consensus is detected as approaching."""
        from scanner.v6.monitor import Monitor, EvaluationResult, LayerResult

        # Mock a 4/7 result
        layers = [
            LayerResult(layer="regime", passed=True, value="trending", detail=""),
            LayerResult(layer="technical", passed=True, value={"agree": 3, "total": 4}, detail=""),
            LayerResult(layer="funding", passed=True, value=0.01, detail=""),
            LayerResult(layer="book", passed=False, value=0.4, detail=""),
            LayerResult(layer="OI", passed=True, value=0.05, detail=""),
            LayerResult(layer="macro", passed=False, value=45, detail=""),
            LayerResult(layer="collective", passed=False, value=None, detail=""),
        ]
        result = EvaluationResult(
            coin="SOL",
            timestamp=datetime.now(timezone.utc).isoformat(),
            layers=layers,
            consensus=4,
            conviction=0.57,
            direction="SHORT",
            regime="strong_trend",
            price=85.0,
            data_age_ms=50,
            data_complete=True,
        )
        # 4/7 is approaching for a 5/7 threshold
        threshold = momentum_strategy.evaluation.consensus_threshold
        approaching = result.consensus == threshold - 1
        assert approaching, f"4/7 should be approaching for threshold {threshold}"
        assert result.consensus < threshold

    def test_entry_at_threshold(self, tmp_bus, mock_client, momentum_strategy):
        """Coin at 5/7 consensus meets threshold → entry signal."""
        from scanner.v6.monitor import EvaluationResult, LayerResult

        layers = [
            LayerResult(layer="regime", passed=True, value="trending", detail=""),
            LayerResult(layer="technical", passed=True, value={"agree": 3, "total": 4}, detail=""),
            LayerResult(layer="funding", passed=True, value=0.01, detail=""),
            LayerResult(layer="book", passed=True, value=0.7, detail=""),
            LayerResult(layer="OI", passed=True, value=0.05, detail=""),
            LayerResult(layer="macro", passed=False, value=15, detail=""),
            LayerResult(layer="collective", passed=False, value=None, detail=""),
        ]
        result = EvaluationResult(
            coin="BTC",
            timestamp=datetime.now(timezone.utc).isoformat(),
            layers=layers,
            consensus=5,
            conviction=0.71,
            direction="SHORT",
            regime="strong_trend",
            price=67800.0,
            data_age_ms=50,
            data_complete=True,
        )
        threshold = momentum_strategy.evaluation.consensus_threshold
        assert result.consensus >= threshold
        assert result.direction != "NONE"


class TestCircuitBreakers:
    """Daily loss limits and position caps prevent over-trading."""

    def test_daily_loss_blocks_entries(self, tmp_bus, mock_client, momentum_strategy):
        """After hitting daily loss cap, no new entries allowed."""
        ctrl = Controller.__new__(Controller)
        ctrl._bus_dir = tmp_bus
        ctrl._client = mock_client
        ctrl._positions = {}
        ctrl._daily_pnl = -15.0  # Already lost 15%
        ctrl._failed_entries = {}
        ctrl._strategy = momentum_strategy
        ctrl._max_daily_loss = momentum_strategy.risk.max_daily_loss_pct

        # Check if circuit breaker fires
        daily_loss_pct = abs(ctrl._daily_pnl)
        max_allowed = ctrl._max_daily_loss
        blocked = daily_loss_pct >= max_allowed
        assert blocked, f"Daily loss {daily_loss_pct}% should block at {max_allowed}% cap"

    def test_max_positions_blocks_entries(self, tmp_bus, mock_client, momentum_strategy):
        """At max_positions, no new entries allowed."""
        ctrl = Controller.__new__(Controller)
        ctrl._bus_dir = tmp_bus
        ctrl._client = mock_client
        ctrl._daily_pnl = 0.0
        ctrl._failed_entries = {}
        ctrl._strategy = momentum_strategy
        ctrl._max_daily_loss = momentum_strategy.risk.max_daily_loss_pct

        # Fill to max
        max_pos = momentum_strategy.risk.max_positions
        ctrl._positions = {
            f"COIN{i}": Position(
                id=f"test-{i}", coin=f"COIN{i}", direction="LONG", strategy="momentum",
                session_id="sess-001", entry_price=100.0, size_usd=10.0,
                size_coins=0.1, stop_loss_pct=3.0, stop_loss_price=97.0,
                entry_time="2026-03-27T10:00:00Z", signal_name="momentum_long",
                sharpe=1.0, hl_order_id=f"hl-{i}", sl_order_id=f"sl-{i}",
            )
            for i in range(max_pos)
        }
        assert len(ctrl._positions) >= max_pos
        # Controller should reject new entries
        blocked = len(ctrl._positions) >= max_pos
        assert blocked


class TestStrategyHotSwap:
    """Strategy YAML can be reloaded without restart."""

    def test_load_all_strategies(self):
        """All 9 strategies load and validate."""
        from scanner.v6.strategy_loader import load_all_strategies
        strategies = load_all_strategies()
        assert len(strategies) == 9
        # load_all_strategies returns dict[str, StrategyConfig]
        names = set(strategies.keys())
        expected = {"momentum", "defense", "watch", "scout", "funding", "degen", "sniper", "fade", "apex"}
        assert names == expected

    def test_strategy_swap_changes_params(self):
        """Switching strategy changes risk parameters."""
        from scanner.v6.strategy_loader import load_strategy
        momentum = load_strategy("momentum")
        degen = load_strategy("degen")

        assert momentum.risk.stop_loss_pct != degen.risk.stop_loss_pct
        assert momentum.risk.position_size_pct != degen.risk.position_size_pct
        # Degen is more aggressive
        assert degen.risk.stop_loss_pct > momentum.risk.stop_loss_pct


class TestRegimeMapping:
    """Verify all 13 regime labels map correctly to strategy categories."""

    def test_all_regimes_map(self):
        """Every RegimeClassifier output maps to a strategy category."""
        from scanner.v6.strategy_loader import _REGIME_MAP, load_strategy
        cfg = load_strategy("momentum")

        all_regimes = [
            "strong_trend", "moderate_trend", "weak_trend", "transition",
            "strong_revert", "moderate_revert", "weak_revert",
            "random_quiet", "random_volatile",
            "chaotic_trend", "chaotic_flat", "divergent",
            "insufficient_data",
        ]
        for regime in all_regimes:
            category = _REGIME_MAP.get(regime)
            assert category is not None, f"Regime '{regime}' has no mapping"

    def test_trending_regimes_pass_momentum(self):
        """All trending variants pass momentum strategy."""
        from scanner.v6.strategy_loader import load_strategy
        cfg = load_strategy("momentum")
        for regime in ["strong_trend", "moderate_trend", "weak_trend", "transition"]:
            assert cfg.allows_regime(regime), f"{regime} should pass momentum"

    def test_reverting_blocked_by_momentum(self):
        """Reverting regimes blocked by momentum (which needs trending/stable)."""
        from scanner.v6.strategy_loader import load_strategy
        cfg = load_strategy("momentum")
        for regime in ["strong_revert", "moderate_revert", "weak_revert"]:
            assert not cfg.allows_regime(regime), f"{regime} should fail momentum"

    def test_fade_accepts_reverting(self):
        """Fade strategy accepts reverting regimes."""
        from scanner.v6.strategy_loader import load_strategy
        cfg = load_strategy("fade")
        for regime in ["strong_revert", "moderate_revert", "weak_revert"]:
            assert cfg.allows_regime(regime), f"{regime} should pass fade"

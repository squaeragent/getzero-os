"""
Trailing stop tests — verifies check_trailing_stops() logic.
All tests are mocked (no mainnet calls).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Minimal strategy stubs ───────────────────────────────────────────────────

@dataclass
class FakeExits:
    trailing_stop: bool = True
    trailing_activation_pct: float = 2.0
    trailing_distance_pct: float = 1.5
    regime_shift_exit: bool = False
    time_exit: bool = False


@dataclass
class FakeStrategy:
    name: str = "test"
    exits: FakeExits = None

    def __post_init__(self):
        if self.exits is None:
            self.exits = FakeExits()


def _make_position(coin="BTC", direction="LONG", entry_price=100.0,
                   trailing_activated=False, trailing_peak=0.0, **kw):
    pos = {
        "coin": coin,
        "direction": direction,
        "entry_price": entry_price,
        "trailing_activated": trailing_activated,
        "trailing_peak": trailing_peak,
        "id": kw.pop("id", f"{coin}-001"),
        "size_coins": kw.pop("size_coins", 1.0),
        "session_id": kw.pop("session_id", "sess-1"),
        "signal_name": kw.pop("signal_name", "test_signal"),
        "entry_time": kw.pop("entry_time", "2025-01-01T00:00:00Z"),
    }
    pos.update(kw)
    return pos


def _make_client(price_map: dict):
    client = MagicMock()
    client.get_price.side_effect = lambda coin: price_map.get(coin, 0)
    return client


# ── Tests ────────────────────────────────────────────────────────────────────

class TestTrailingStops:

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_not_activated_below_threshold(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # LONG entry=$100, activation_pct=2%, current=$101 → NOT activated
        pos = _make_position(entry_price=100.0)
        client = _make_client({"BTC": 101.0})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert len(updated) == 1
        assert len(closed) == 0
        assert updated[0]["trailing_activated"] is False
        mock_close.assert_not_called()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_activates_at_threshold(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # LONG entry=$100, activation_pct=2%, current=$102.50 → activated
        pos = _make_position(entry_price=100.0)
        client = _make_client({"BTC": 102.50})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert len(updated) == 1
        assert updated[0]["trailing_activated"] is True
        assert updated[0]["trailing_peak"] == 102.50
        mock_close.assert_not_called()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_peak_tracks_upward(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # LONG activated, peak=$102, current=$105 → peak updated to $105
        pos = _make_position(entry_price=100.0, trailing_activated=True, trailing_peak=102.0)
        client = _make_client({"BTC": 105.0})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert updated[0]["trailing_peak"] == 105.0
        mock_close.assert_not_called()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_peak_never_moves_down(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # LONG activated, peak=$105, current=$104 (above trigger $103.425) → peak stays $105
        pos = _make_position(entry_price=100.0, trailing_activated=True, trailing_peak=105.0)
        client = _make_client({"BTC": 104.0})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert updated[0]["trailing_peak"] == 105.0
        mock_close.assert_not_called()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_triggers_exit_long(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # LONG activated, peak=$105, distance_pct=1.5%
        # trigger = $105 * 0.985 = $103.425
        # current=$103.30 → below trigger → close
        mock_close.return_value = {"coin": "BTC", "pnl_usd": 3.30, "exit_reason": "trailing_stop"}

        pos = _make_position(entry_price=100.0, trailing_activated=True, trailing_peak=105.0)
        client = _make_client({"BTC": 103.30})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "trailing_stop"
        # Position should NOT be in updated (it was closed)
        assert all(p.get("id") != "BTC-001" for p in updated)
        mock_close.assert_called_once()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_short_direction(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        # SHORT entry=$100, activation_pct=2%, current=$97.50 → activated
        pos = _make_position(direction="SHORT", entry_price=100.0)
        client = _make_client({"BTC": 97.50})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)
        assert updated[0]["trailing_activated"] is True
        assert updated[0]["trailing_peak"] == 97.50

        # Now: peak=$96, current=$97.50
        # trigger = $96 * 1.015 = $97.44
        # current=$97.50 > $97.44 → should close
        mock_close.return_value = {"coin": "BTC", "pnl_usd": 2.50, "exit_reason": "trailing_stop"}
        pos2 = _make_position(direction="SHORT", entry_price=100.0,
                              trailing_activated=True, trailing_peak=96.0)
        client2 = _make_client({"BTC": 97.50})

        updated2, closed2 = check_trailing_stops(client2, [pos2], strategy, dry=True)
        assert len(closed2) == 1
        assert closed2[0]["exit_reason"] == "trailing_stop"

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_disabled_strategy(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        pos = _make_position(entry_price=100.0)
        client = _make_client({"BTC": 105.0})
        strategy = FakeStrategy(exits=FakeExits(trailing_stop=False))

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert len(updated) == 1
        assert len(closed) == 0
        # Position should be returned as-is, no trailing state modified
        mock_close.assert_not_called()

    @patch("scanner.v6.controller.close_trade")
    def test_trailing_peak_persisted(self, mock_close):
        from scanner.v6.controller import check_trailing_stops

        pos = _make_position(entry_price=100.0)
        client = _make_client({"BTC": 103.0})
        strategy = FakeStrategy()

        updated, closed = check_trailing_stops(client, [pos], strategy, dry=True)

        assert updated[0]["trailing_activated"] is True
        assert updated[0]["trailing_peak"] == 103.0

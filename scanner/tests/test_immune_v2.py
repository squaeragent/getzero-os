"""
Immune v2 tests — verifies independent position protection system.
All tests are mocked (no mainnet calls).
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.immune_v2 import ImmuneSystem, CONTROLLER_STALE_THRESHOLD


# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_positions(bus_dir: Path, positions: list[dict]):
    (bus_dir / "positions.json").write_text(
        json.dumps({"positions": positions})
    )


def _write_heartbeat(bus_dir: Path, controller_ts: str | None = None,
                     immune_ts: str | None = None):
    hb = {}
    if controller_ts:
        hb["controller"] = controller_ts
    if immune_ts:
        hb["immune"] = immune_ts
    (bus_dir / "heartbeat.json").write_text(json.dumps(hb))


def _make_client(orders=None, positions=None, prices=None):
    client = MagicMock()
    client.get_open_orders.return_value = orders or []
    client.get_positions.return_value = positions or []
    client.get_price.side_effect = lambda coin: (prices or {}).get(coin, 100.0)
    client.place_stop_loss.return_value = True
    client.cancel_coin_stops.return_value = True
    return client


def _make_position(coin="BTC", direction="LONG", entry_price=100.0,
                   size_coins=1.0, stop_loss_pct=0.03):
    return {
        "coin": coin,
        "direction": direction,
        "entry_price": entry_price,
        "size_coins": size_coins,
        "stop_loss_pct": stop_loss_pct,
    }


# ── Tests ────────────────────────────────────────────────────────────────────

class TestImmuneStopVerification:

    def test_stop_verified(self, tmp_path):
        """All positions have matching stops in open_orders → no repair."""
        bus = tmp_path / "bus"
        bus.mkdir()

        positions = [_make_position("BTC"), _make_position("ETH")]
        _write_positions(bus, positions)

        client = _make_client(orders=[
            {"coin": "BTC", "type": "stop"},
            {"coin": "ETH", "type": "stop"},
        ])

        immune = ImmuneSystem(bus_dir=bus, hl_client=client)
        missing = immune.check_all_stops()

        assert missing == []
        client.place_stop_loss.assert_not_called()

    def test_missing_stop_repaired(self, tmp_path):
        """Position with no matching stop → place_stop_loss called."""
        bus = tmp_path / "bus"
        bus.mkdir()

        positions = [_make_position("BTC"), _make_position("ETH")]
        _write_positions(bus, positions)

        # Only BTC has a stop — ETH missing
        client = _make_client(orders=[{"coin": "BTC", "type": "stop"}])

        immune = ImmuneSystem(bus_dir=bus, hl_client=client)
        missing = immune.check_all_stops()

        assert len(missing) == 1
        assert missing[0]["coin"] == "ETH"

        # Repair
        immune.repair_stop(missing[0])
        client.place_stop_loss.assert_called_once()
        call_kwargs = client.place_stop_loss.call_args
        assert call_kwargs[1]["coin"] == "ETH"


class TestImmuneHeartbeat:

    def test_heartbeat_written(self, tmp_path):
        """write_heartbeat() → heartbeat.json has 'immune' timestamp."""
        bus = tmp_path / "bus"
        bus.mkdir()

        immune = ImmuneSystem(bus_dir=bus)
        immune.write_heartbeat()

        hb = json.loads((bus / "heartbeat.json").read_text())
        assert "immune" in hb
        # Verify it's a valid ISO timestamp
        ts = datetime.fromisoformat(hb["immune"].replace("Z", "+00:00"))
        assert ts.year >= 2025


class TestDeadManSwitch:

    def test_dead_man_stale_controller(self, tmp_path):
        """Controller heartbeat > 5 min old → tighten stops."""
        bus = tmp_path / "bus"
        bus.mkdir()

        positions = [_make_position("BTC", size_coins=2.0)]
        _write_positions(bus, positions)

        # Controller heartbeat 10 minutes stale
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        _write_heartbeat(bus, controller_ts=stale)

        client = _make_client(prices={"BTC": 50000.0})
        immune = ImmuneSystem(bus_dir=bus, hl_client=client)

        triggered = immune.dead_man_check()

        assert triggered is True
        # Should tighten: place_stop_loss with 1% from current
        client.place_stop_loss.assert_called_once()
        call_kw = client.place_stop_loss.call_args[1]
        assert call_kw["coin"] == "BTC"
        # LONG: tight_stop = 50000 * 0.99 = 49500
        assert abs(call_kw["trigger"] - 49500.0) < 1.0

    def test_dead_man_fresh_controller(self, tmp_path):
        """Controller heartbeat < 5 min → no action."""
        bus = tmp_path / "bus"
        bus.mkdir()

        fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _write_heartbeat(bus, controller_ts=fresh)

        client = _make_client()
        immune = ImmuneSystem(bus_dir=bus, hl_client=client)

        triggered = immune.dead_man_check()

        assert triggered is False
        client.place_stop_loss.assert_not_called()


class TestImmunePositionLoading:

    def test_immune_reads_positions(self, tmp_path):
        """Correctly reads positions.json from bus dir."""
        bus = tmp_path / "bus"
        bus.mkdir()

        positions = [
            _make_position("BTC"),
            _make_position("ETH", direction="SHORT", entry_price=3000.0),
        ]
        _write_positions(bus, positions)

        immune = ImmuneSystem(bus_dir=bus)
        loaded = immune.load_positions()

        assert len(loaded) == 2
        assert loaded[0]["coin"] == "BTC"
        assert loaded[1]["coin"] == "ETH"
        assert loaded[1]["direction"] == "SHORT"


class TestImmuneRunCycle:

    def test_run_cycle_completes(self, tmp_path):
        """run_cycle() completes without error with mocked client."""
        bus = tmp_path / "bus"
        bus.mkdir()

        # No positions, fresh heartbeat
        _write_positions(bus, [])
        fresh = datetime.now(timezone.utc).isoformat()
        _write_heartbeat(bus, controller_ts=fresh)

        client = _make_client()
        immune = ImmuneSystem(bus_dir=bus, hl_client=client)

        # Should not raise
        immune.run_cycle()

        # Heartbeat should be written
        hb = json.loads((bus / "heartbeat.json").read_text())
        assert "immune" in hb


class TestTightenedStops:

    def test_tightened_stop_at_1_percent(self, tmp_path):
        """When tightening: new stop = current * 0.99 LONG, *1.01 SHORT."""
        bus = tmp_path / "bus"
        bus.mkdir()

        positions = [
            _make_position("BTC", direction="LONG", size_coins=1.0),
            _make_position("ETH", direction="SHORT", size_coins=2.0),
        ]
        _write_positions(bus, positions)

        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        _write_heartbeat(bus, controller_ts=stale)

        client = _make_client(prices={"BTC": 50000.0, "ETH": 3000.0})
        immune = ImmuneSystem(bus_dir=bus, hl_client=client)

        triggered = immune.dead_man_check()
        assert triggered is True

        # Check both calls
        assert client.place_stop_loss.call_count == 2
        calls = client.place_stop_loss.call_args_list

        # Find calls by coin
        btc_call = next(c for c in calls if c[1]["coin"] == "BTC")
        eth_call = next(c for c in calls if c[1]["coin"] == "ETH")

        # LONG: 50000 * 0.99 = 49500
        assert abs(btc_call[1]["trigger"] - 49500.0) < 1.0
        # SHORT: 3000 * 1.01 = 3030
        assert abs(eth_call[1]["trigger"] - 3030.0) < 1.0

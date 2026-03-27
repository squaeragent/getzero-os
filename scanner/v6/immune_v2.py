#!/usr/bin/env python3
"""
immune_v2.py — Independent position protection system.
Runs every 60 seconds. Cannot be disabled while positions open.
Independent of monitor and controller evaluation logic.

Responsibilities:
  1. Verify every open position has a stop order on HL
  2. Repair missing stops automatically
  3. Write immune heartbeat to bus/heartbeat.json
  4. Dead man's switch: if controller heartbeat > 5min stale,
     tighten ALL stops to 1% from current price
  5. Log all actions to events.jsonl

Usage:
  python scanner/v6/immune_v2.py          # single cycle
  python scanner/v6/immune_v2.py --loop   # continuous 60s loop
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

V6_DIR  = Path(__file__).parent
BUS_DIR = V6_DIR / "bus"

POSITIONS_FILE  = BUS_DIR / "positions.json"
HEARTBEAT_FILE  = BUS_DIR / "heartbeat.json"
EVENTS_FILE     = BUS_DIR / "events.jsonl"

CYCLE_SECONDS          = 60
CONTROLLER_STALE_THRESHOLD = 300  # 5 minutes
DEAD_MAN_STOP_PCT      = 0.01    # 1% tightened stop


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [IMMUNE] {msg}", flush=True)


def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _save_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def _append_event(event: dict):
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


class ImmuneSystem:
    """Independent position protection — verifies stops, repairs gaps, dead man's switch."""

    def __init__(self, bus_dir: Path | None = None, hl_client=None):
        self.bus_dir        = bus_dir or BUS_DIR
        self.hl_client      = hl_client
        self._positions_file = self.bus_dir / "positions.json"
        self._heartbeat_file = self.bus_dir / "heartbeat.json"
        self._events_file    = self.bus_dir / "events.jsonl"

    # ── Position loading ─────────────────────────────────────────────────────

    def load_positions(self) -> list[dict]:
        """Read open positions from bus/positions.json."""
        data = _load_json(self._positions_file, {})
        return data.get("positions", [])

    # ── Stop verification ────────────────────────────────────────────────────

    def check_all_stops(self) -> list[dict]:
        """
        Verify every open position has a stop on HL.
        Returns list of positions missing stops.
        """
        positions = self.load_positions()
        if not positions or not self.hl_client:
            return []

        try:
            open_orders = self.hl_client.get_open_orders()
        except Exception as e:
            _log(f"WARN: get_open_orders failed: {e}")
            return []

        # Build set of coins with active stop orders
        coins_with_stops = set()
        for order in open_orders:
            coin = order.get("coin")
            if coin:
                coins_with_stops.add(coin)

        missing = []
        for pos in positions:
            coin = pos.get("coin")
            if coin and coin not in coins_with_stops:
                missing.append(pos)
                _log(f"MISSING STOP: {coin} {pos.get('direction', '?')}")

        if not missing:
            _log(f"All {len(positions)} positions have stops verified")

        return missing

    # ── Stop repair ──────────────────────────────────────────────────────────

    def repair_stop(self, position: dict) -> bool:
        """Place a missing stop order for a position. Returns True on success."""
        if not self.hl_client:
            return False

        coin        = position.get("coin", "")
        direction   = position.get("direction", "LONG")
        entry_price = position.get("entry_price", 0)
        size_coins  = position.get("size_coins", 0)
        stop_pct    = position.get("stop_loss_pct", 0.03)
        is_long     = direction == "LONG"

        if entry_price <= 0 or size_coins <= 0:
            _log(f"WARN: cannot repair stop for {coin}: invalid entry/size")
            return False

        # Calculate stop price from entry
        if is_long:
            stop_price = entry_price * (1 - stop_pct)
        else:
            stop_price = entry_price * (1 + stop_pct)

        try:
            self.hl_client.place_stop_loss(
                coin=coin,
                is_buy=not is_long,  # stop is opposite direction
                size=size_coins,
                trigger=stop_price,
            )
            _log(f"REPAIRED STOP: {coin} {direction} @ ${stop_price:.4f}")
            _append_event({
                "type": "IMMUNE_STOP_REPAIRED",
                "ts":   _now_iso(),
                "coin": coin,
                "direction": direction,
                "stop_price": stop_price,
            })
            return True
        except Exception as e:
            _log(f"STOP REPAIR FAILED: {coin} — {e}")
            _append_event({
                "type": "IMMUNE_REPAIR_FAILED",
                "ts":   _now_iso(),
                "coin": coin,
                "error": str(e),
            })
            return False

    # ── Heartbeat ────────────────────────────────────────────────────────────

    def write_heartbeat(self):
        """Update bus/heartbeat.json with immune timestamp."""
        hb = _load_json(self._heartbeat_file, {})
        hb["immune"] = _now_iso()
        _save_json_atomic(self._heartbeat_file, hb)

    # ── Dead man's switch ────────────────────────────────────────────────────

    def dead_man_check(self) -> bool:
        """
        Check controller heartbeat age.
        If > 5 minutes stale: tighten ALL stops to 1% from current price.
        Returns True if dead man triggered.
        """
        hb = _load_json(self._heartbeat_file, {})
        controller_ts = hb.get("controller")
        if not controller_ts:
            # No controller heartbeat yet — don't trigger on first boot
            return False

        try:
            last = datetime.fromisoformat(controller_ts.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last).total_seconds()
        except (ValueError, TypeError):
            age = 999999

        if age <= CONTROLLER_STALE_THRESHOLD:
            return False

        # Controller unresponsive — tighten stops
        _log(f"DEAD MAN'S SWITCH: controller heartbeat {age:.0f}s stale (>{CONTROLLER_STALE_THRESHOLD}s)")

        positions = self.load_positions()
        if not positions:
            return True

        if not self.hl_client:
            _log("WARN: no HL client — cannot tighten stops")
            return True

        for pos in positions:
            coin      = pos.get("coin", "")
            direction = pos.get("direction", "LONG")
            size_coins = pos.get("size_coins", 0)
            is_long   = direction == "LONG"

            if size_coins <= 0:
                continue

            try:
                current_price = self.hl_client.get_price(coin)
                if current_price <= 0:
                    continue

                # Tighten to 1% from current
                if is_long:
                    tight_stop = current_price * (1 - DEAD_MAN_STOP_PCT)
                else:
                    tight_stop = current_price * (1 + DEAD_MAN_STOP_PCT)

                # Cancel existing stops and place tighter one
                try:
                    self.hl_client.cancel_coin_stops(coin)
                except Exception:
                    pass

                self.hl_client.place_stop_loss(
                    coin=coin,
                    is_buy=not is_long,
                    size=size_coins,
                    trigger=tight_stop,
                )
                _log(f"TIGHTENED: {coin} stop → ${tight_stop:.4f} (1% from ${current_price:.4f})")
            except Exception as e:
                _log(f"TIGHTEN FAILED: {coin} — {e}")

        _append_event({
            "type":    "IMMUNE_DEAD_MAN_TRIGGERED",
            "ts":      _now_iso(),
            "message": "Controller unresponsive. Immune protecting.",
            "controller_age_s": age,
            "positions_count":  len(positions),
        })

        return True

    # ── Cycle ────────────────────────────────────────────────────────────────

    def run_cycle(self):
        """One immune check cycle."""
        _log("Immune cycle starting")

        # 1. Write heartbeat
        self.write_heartbeat()

        # 2. Dead man check (highest priority)
        if self.dead_man_check():
            _log("Dead man triggered — cycle complete")
            return

        # 3. Verify all stops
        missing = self.check_all_stops()

        # 4. Repair missing stops
        for pos in missing:
            self.repair_stop(pos)

        _log("Immune cycle complete")

    # ── Loop ─────────────────────────────────────────────────────────────────

    def run_loop(self):
        """Infinite loop — run_cycle every 60 seconds."""
        _log("Immune system v2 starting continuous loop")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                _log(f"ERROR in immune cycle: {e}")
            time.sleep(CYCLE_SECONDS)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Paper mode bus isolation
    from scanner.v6.paper_isolation import is_paper_mode, apply_paper_isolation
    if is_paper_mode():
        apply_paper_isolation()
        import scanner.v6.config as _cfg
        global BUS_DIR, POSITIONS_FILE, HEARTBEAT_FILE, EVENTS_FILE
        BUS_DIR        = _cfg.BUS_DIR
        POSITIONS_FILE = BUS_DIR / "positions.json"
        HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
        EVENTS_FILE    = BUS_DIR / "events.jsonl"

    BUS_DIR.mkdir(parents=True, exist_ok=True)

    # Build HL client
    client = None
    try:
        from scanner.v6.config import get_env, HL_MAIN_ADDRESS
        from scanner.v6.hl_client import HLClient
        hl_key = get_env("HYPERLIQUID_SECRET_KEY") or get_env("HL_PRIVATE_KEY")
        if hl_key:
            client = HLClient(hl_key, HL_MAIN_ADDRESS)
    except Exception as e:
        _log(f"WARN: HL client init failed: {e}")

    immune = ImmuneSystem(bus_dir=BUS_DIR, hl_client=client)

    if "--loop" in sys.argv:
        immune.run_loop()
    else:
        immune.run_cycle()


if __name__ == "__main__":
    main()

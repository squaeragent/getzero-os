"""
Position management — dataclasses, persistence, reconciliation.

Depends on: ctrl_util, trade_logger, bus_io, config, hl_client, exceptions.
"""

from __future__ import annotations

import json
import os
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

import scanner.v6.config as _cfg
from scanner.v6.ctrl_util import log, now_iso, load_json, save_json_atomic
from scanner.v6.trade_logger import send_alert
from scanner.v6.bus_io import load_json_locked, save_json_locked
from scanner.exceptions import APIError, StopLossError


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """Typed position record — replaces raw dict tracking."""
    id:                 str
    coin:               str
    direction:          str          # LONG or SHORT
    strategy:           str          # YAML strategy name
    session_id:         str
    entry_price:        float
    size_usd:           float
    size_coins:         float
    stop_loss_pct:      float
    stop_loss_price:    float
    entry_time:         str          # ISO format
    signal_name:        str
    sharpe:             float
    hl_order_id:        str
    sl_order_id:        str
    peak_pnl_pct:       float = 0.0
    trailing_activated: bool  = False
    trailing_peak:      float = 0.0
    # Extra metadata (optional)
    win_rate:           float = 0.0
    composite_score:    float = 0.0
    expression:         str   = ""
    exit_expression:    str   = ""
    max_hold_hours:     int   = 12
    dry:                bool  = False
    strategy_version:   int   = _cfg.STRATEGY_VERSION
    # B2: Slippage tracking
    signal_price:       float = 0.0
    signal_timestamp:   str   = ""
    execution_quality:  dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


@dataclass
class ExecutionQuality:
    """B2: Tracks execution quality — slippage and latency."""
    signal_price: float
    order_price: float
    fill_price: float
    signal_to_order_ms: float
    order_to_fill_ms: float
    signal_to_fill_ms: float
    slippage_pct: float
    slippage_bps: float
    order_type: str
    coin: str
    direction: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class TradeResult:
    """Typed trade result for trades.jsonl logging."""
    position_id:        str
    coin:               str
    direction:          str
    strategy:           str
    session_id:         str
    entry_price:        float
    exit_price:         float
    size_usd:           float
    size_coins:         float
    entry_time:         str
    exit_time:          str
    exit_reason:        str
    pnl_usd:            float
    pnl_pct:            float
    pnl_usd_gross:      float
    fees_usd:           float
    slippage_pct:       float
    actual_notional:    float
    won:                bool
    sharpe:             float
    win_rate:           float
    zero_fee:           float = 0.0
    pnl_usd_net:        float = 0.0
    execution_quality:  dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ══════════════════════════════════════════════════════════════════════════════
# POSITION PERSISTENCE (with empty-write guard)
# ══════════════════════════════════════════════════════════════════════════════

def _safe_save_positions(client, new_positions: list[dict],
                         source: str = "") -> None:
    """Save positions only if it won't lose track of live HL positions.

    If new state has 0 positions, verify with HL before writing empty.
    Paper mode / no client: write directly.
    """
    positions_file = _cfg.POSITIONS_FILE
    if client is None or os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes"):
        save_json_locked(positions_file, {"updated_at": now_iso(), "positions": new_positions})
        return

    if len(new_positions) == 0:
        try:
            hl_positions = client.get_positions()
            hl_active = [p for p in hl_positions
                         if float(p.get("position", {}).get("szi", 0)) != 0]
            if hl_active:
                log(f"🚨 DESYNC BLOCKED: {source} tried to write 0 positions but HL has {len(hl_active)}!")
                send_alert(
                    f"🚨 DESYNC BLOCKED\n"
                    f"{source} tried to write 0 positions but HL has {len(hl_active)} open.\n"
                    f"Auto-reconciling instead of writing empty."
                )
                _reconcile_positions(client)
                return
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
            log(f"WARN: HL check failed during empty-write guard ({source}): {e}")
            return  # When in doubt, don't overwrite with empty

    save_json_locked(positions_file, {"updated_at": now_iso(), "positions": new_positions})


# ══════════════════════════════════════════════════════════════════════════════
# HL RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════════

def _reconcile_positions(client) -> None:
    """Sync local positions.json with what HL actually has open.

    Prevents ghost positions (local thinks open, HL closed) and
    orphan positions (HL has open, local doesn't know).
    Also places emergency stops for naked positions.
    """
    positions_file = _cfg.POSITIONS_FILE
    try:
        hl_positions = client.get_positions()
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError) as e:
        log(f"WARN: reconciliation skipped — HL query failed: {e}")
        return

    hl_map: dict[str, dict] = {}
    for p in hl_positions:
        pos = p.get("position", {})
        sz  = float(pos.get("szi", 0))
        if sz == 0:
            continue
        coin = pos["coin"]
        hl_map[coin] = {
            "coin":           coin,
            "direction":      "LONG" if sz > 0 else "SHORT",
            "size_coins":     abs(sz),
            "entry_price":    float(pos.get("entryPx", 0)),
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
        }

    local_data      = load_json_locked(positions_file, {})
    local_positions = local_data.get("positions", [])
    local_map       = {p["coin"]: p for p in local_positions}

    changes = []
    for coin in list(local_map.keys()):
        if coin not in hl_map:
            changes.append(f"GHOST removed: {coin} {local_map[coin].get('direction')} (closed on HL)")
    for coin, hl_pos in hl_map.items():
        if coin not in local_map:
            changes.append(f"ORPHAN adopted: {coin} {hl_pos['direction']} @ ${hl_pos['entry_price']:.2f}")
    for coin in set(local_map.keys()) & set(hl_map.keys()):
        if local_map[coin].get("direction") != hl_map[coin]["direction"]:
            changes.append(f"DIRECTION FIX: {coin} local={local_map[coin]['direction']} hl={hl_map[coin]['direction']}")

    if changes:
        log(f"  RECONCILIATION: {len(changes)} fixes")
        for c in changes:
            log(f"    {c}")

    new_positions = []
    for coin, hl_pos in hl_map.items():
        local = local_map.get(coin, {})
        new_positions.append({
            "coin":              coin,
            "direction":         hl_pos["direction"],
            "entry_price":       hl_pos["entry_price"],
            "size_coins":        hl_pos["size_coins"],
            "size_usd":          hl_pos["entry_price"] * hl_pos["size_coins"],
            "entry_time":        local.get("entry_time", now_iso()),
            "signal_name":       local.get("signal_name", "reconciled_from_hl"),
            "stop_loss_pct":     local.get("stop_loss_pct", 0.05),
            "strategy":          local.get("strategy", "unknown"),
            "session_id":        local.get("session_id", ""),
            "strategy_version":  local.get("strategy_version", _cfg.STRATEGY_VERSION),
            "sharpe":            local.get("sharpe", 0),
            "win_rate":          local.get("win_rate", 0),
            "id":                local.get("id", f"{coin}_{hl_pos['direction']}_reconciled"),
            "hl_order_id":       local.get("hl_order_id", ""),
            "sl_order_id":       local.get("sl_order_id", ""),
            "peak_pnl_pct":      local.get("peak_pnl_pct", 0.0),
            "trailing_activated": local.get("trailing_activated", False),
            "trailing_peak":      local.get("trailing_peak", 0.0),
        })

    save_json_locked(positions_file, {"updated_at": now_iso(), "positions": new_positions})

    if not changes:
        log(f"  Positions synced: {len(new_positions)} match HL")

    # Stop order verification: every position needs a stop on HL
    if new_positions:
        try:
            open_orders     = client.get_open_orders()
            coins_with_stops = {}
            for order in open_orders:
                order_coin  = order.get("coin")
                order_price = float(order.get("limitPx", 0))
                if order_coin and order_price > 0:
                    coins_with_stops[order_coin] = order_price

            for pos in new_positions:
                coin = pos["coin"]
                if coin in coins_with_stops:
                    pos["stop_loss_price"] = coins_with_stops[coin]
                else:
                    direction  = pos["direction"]
                    entry      = pos.get("entry_price", 0)
                    stop_pct   = pos.get("stop_loss_pct", 0.05)
                    is_long    = direction == "LONG"
                    log(f"  🚨 NAKED POSITION: {coin} {direction} — no stop on HL!")
                    send_alert(
                        f"🚨 NAKED POSITION: {coin} {direction} @ ${entry:.2f}\n"
                        f"No stop loss on HL! Placing emergency stop."
                    )
                    try:
                        stop_price = client.round_price(
                            entry * (1 - stop_pct) if is_long else entry * (1 + stop_pct)
                        )
                        size = pos.get("size_coins", 0)
                        if size > 0:
                            sl = client.place_stop_loss(coin, not is_long, size, stop_price)
                            log(f"  Emergency stop placed: {json.dumps(sl)}")
                        else:
                            log(f"  Cannot place emergency stop for {coin} — size=0")
                            send_alert(f"🚨 Cannot place stop for {coin} — size unknown. CLOSE MANUALLY.")
                    except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError, StopLossError, APIError) as e:
                        log(f"  Emergency stop FAILED: {e}")
                        send_alert(f"🚨🚨 EMERGENCY STOP FAILED for {coin}: {e}\nCLOSE MANUALLY NOW.")

            save_json_locked(positions_file, {"updated_at": now_iso(), "positions": new_positions})
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError, ValueError) as e:
            log(f"  WARN: stop verification failed: {e}")

#!/usr/bin/env python3
"""
V6 Controller — THE SINGLE ENGINE.

Session 8b: executor.py + risk_guard.py + session_manager.py absorbed here.
This is now the only file needed to run the trading loop.

Architecture:
    EVALUATOR → MONITOR → [CONTROLLER] → HL Exchange

The controller:
  1. Loads strategy config from YAML (via strategy_loader.py)
  2. Runs all 9 risk checks (IF — controller decides)
  3. Executes approved entries on Hyperliquid (HOW — controller does)
  4. Manages open positions (trailing stops, time exits, entry_end)
  5. Handles session lifecycle (pending → active → completing → completed)
  6. Logs trades, sends Telegram alerts, updates portfolio.json

Risk checks (all 9 from spec):
  1. max_positions         → reject ENTRY if at limit
  2. max_daily_loss_pct    → circuit breaker, stop all entries
  3. reserve_pct           → ensure equity × reserve_pct stays uninvested
  4. max_hold_hours        → force EXIT when hold time exceeded
  5. entry_end_action      → hold or close when signal disappears
  6. consensus_threshold   → reject if consensus layers < threshold
  7. min_regime            → reject if current regime not allowed
  8. position_size_pct     → used in compute_size_usd()
  9. stop_loss_pct         → used in stop placement

Fallback: when no strategy YAML is active → config.py constants are used.

Bus files:
  Reads:  bus/entries.json         (ENTRY signals from monitor/evaluator)
          bus/exits.json           (EXIT signals from monitor)
          bus/positions.json       (open positions)
          bus/risk.json            (risk state — daily loss, halts)
          bus/active_strategy.json (which YAML strategy is running)
          bus/approved.json        (legacy: entries approved by gate)
  Writes: bus/positions.json       (updated after open/close)
          bus/approved.json        (cleared each cycle)
          bus/exits.json           (adds time-exit signals)
          bus/risk.json            (updated risk state)
          bus/portfolio.json       (equity snapshot)
          bus/heartbeat.json       (cycle heartbeat)
          bus/rejections.jsonl     (rejection log)
          bus/near_misses.jsonl    (signals that almost passed)
          data/trades.jsonl        (trade log)

Usage:
  python scanner/v6/controller.py           # single run (gate only)
  python scanner/v6/controller.py --loop    # continuous 5s cycle (full engine)
  python scanner/v6/controller.py --dry     # paper/dry-run (no real orders)
  python scanner/v6/controller.py --paper   # same as --dry
  python scanner/v6/controller.py --status  # print strategy + risk state
"""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.bus_io import load_json_locked, save_json_locked
from scanner.v6.config import (
    BUS_DIR, DATA_DIR,
    ENTRIES_FILE, APPROVED_FILE, POSITIONS_FILE, RISK_FILE,
    HEARTBEAT_FILE, EXITS_FILE, TRADES_FILE,
    CAPITAL, CAPITAL_FLOOR_PCT, MAX_PER_COIN,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN_ENV,
    FEE_RATE, STRATEGY_VERSION,
    get_env, get_stop_pct, get_dynamic_limits, get_slippage, get_leverage,
    HL_MAIN_ADDRESS,
)
from scanner.v6.strategy_loader import StrategyConfig, get_active_strategy
from scanner.v6.hl_client import HLClient, load_hl_meta, COIN_TO_ASSET, COIN_SZ_DECIMALS

CYCLE_SECONDS = 5

REJECTION_LOG_FILE  = BUS_DIR / "rejections.jsonl"
NEAR_MISS_LOG_FILE  = BUS_DIR / "near_misses.jsonl"
DECISION_LOG_FILE   = BUS_DIR / "decisions.jsonl"
EVENTS_LOG_FILE     = BUS_DIR / "events.jsonl"
CONTROLLER_STATE_FILE = BUS_DIR / "controller_state.json"
SIGNALS_FILE        = BUS_DIR / "signals.json"   # Session 9: monitor signals

# Failed entry cooldown — don't retry same coin+direction for 15 min after failure
_failed_entries: dict[str, float] = {}
_FAILED_ENTRY_COOLDOWN = 900  # 15 minutes

# Telegram alert dedup
_alert_history: dict[str, float] = {}
_ALERT_COOLDOWN = 300  # 5 min between identical alerts


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
    # Extra metadata (optional)
    win_rate:           float = 0.0
    composite_score:    float = 0.0
    expression:         str   = ""
    exit_expression:    str   = ""
    max_hold_hours:     int   = 12
    dry:                bool  = False
    strategy_version:   int   = STRATEGY_VERSION

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


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

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SessionState:
    """Session lifecycle state machine: pending → active → completing → completed."""
    session_id:    str
    strategy:      str
    status:        str   # pending | active | completing | completed | expired
    started_at:    str
    expires_at:    str
    equity_start:  float
    equity_end:    float = 0.0
    total_pnl:     float = 0.0
    trade_count:   int   = 0
    wins:          int   = 0
    losses:        int   = 0
    near_misses:   int   = 0
    trades:        list  = field(default_factory=list)
    completed_at:  str   = ""
    reason:        str   = ""
    narrative:     str   = ""

    def result_card(self) -> dict:
        roi_pct = (
            (self.equity_end - self.equity_start) / self.equity_start * 100
            if self.equity_start > 0 else 0
        )
        return {
            "session_id":  self.session_id,
            "strategy":    self.strategy,
            "status":      self.status,
            "started_at":  self.started_at,
            "completed_at": self.completed_at,
            "reason":      self.reason,
            "equity_start": round(self.equity_start, 2),
            "equity_end":   round(self.equity_end, 2),
            "roi_pct":      round(roi_pct, 2),
            "total_pnl":    round(self.total_pnl, 2),
            "trade_count":  self.trade_count,
            "wins":         self.wins,
            "losses":       self.losses,
            "win_rate":     round(self.wins / self.trade_count * 100, 1) if self.trade_count else 0,
            "near_misses":  self.near_misses,
            "narrative":    self.narrative,
        }


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [CTRL] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def send_alert(message: str) -> None:
    """Send Telegram message. Never raises. Suppressed in paper mode. Rate-limited."""
    if os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes"):
        log(f"[PAPER] Alert suppressed: {message[:80]}")
        return
    alert_key = message[:60]
    now = time.time()
    if alert_key in _alert_history and (now - _alert_history[alert_key]) < _ALERT_COOLDOWN:
        return
    _alert_history[alert_key] = now
    try:
        token = get_env(TELEGRAM_BOT_TOKEN_ENV)
        if not token:
            return
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     message,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"WARN: Telegram failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FILE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def update_heartbeat() -> None:
    hb = load_json(HEARTBEAT_FILE, {})
    hb["controller"] = now_iso()
    save_json_atomic(HEARTBEAT_FILE, hb)


# ══════════════════════════════════════════════════════════════════════════════
# POSITION PERSISTENCE (with empty-write guard)
# ══════════════════════════════════════════════════════════════════════════════

def _safe_save_positions(client: HLClient | None, new_positions: list[dict],
                          source: str = "") -> None:
    """Save positions only if it won't lose track of live HL positions.

    If new state has 0 positions, verify with HL before writing empty.
    Paper mode / no client: write directly.
    """
    if client is None or os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes"):
        save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": new_positions})
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
        except Exception as e:
            log(f"WARN: HL check failed during empty-write guard ({source}): {e}")
            return  # When in doubt, don't overwrite with empty

    save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": new_positions})


# ══════════════════════════════════════════════════════════════════════════════
# HL RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════════

def _reconcile_positions(client: HLClient) -> None:
    """Sync local positions.json with what HL actually has open.

    Prevents ghost positions (local thinks open, HL closed) and
    orphan positions (HL has open, local doesn't know).
    Also places emergency stops for naked positions.
    """
    try:
        hl_positions = client.get_positions()
    except Exception as e:
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

    local_data      = load_json_locked(POSITIONS_FILE, {})
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
            "strategy_version":  local.get("strategy_version", STRATEGY_VERSION),
            "sharpe":            local.get("sharpe", 0),
            "win_rate":          local.get("win_rate", 0),
            "id":                local.get("id", f"{coin}_{hl_pos['direction']}_reconciled"),
            "hl_order_id":       local.get("hl_order_id", ""),
            "sl_order_id":       local.get("sl_order_id", ""),
            "peak_pnl_pct":      local.get("peak_pnl_pct", 0.0),
            "trailing_activated": local.get("trailing_activated", False),
        })

    save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": new_positions})

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
                    except Exception as e:
                        log(f"  Emergency stop FAILED: {e}")
                        send_alert(f"🚨🚨 EMERGENCY STOP FAILED for {coin}: {e}\nCLOSE MANUALLY NOW.")

            save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": new_positions})
        except Exception as e:
            log(f"  WARN: stop verification failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# RISK STATE
# ══════════════════════════════════════════════════════════════════════════════

def _today_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def load_risk() -> dict:
    default = {
        "updated_at":        now_iso(),
        "halted":            False,
        "halt_reason":       None,
        "halt_until":        None,
        "daily_loss_usd":    0.0,
        "daily_pnl_usd":     0.0,
        "daily_loss_since":  _today_start(),
        "capital_floor_hit": False,
        "open_count":        0,
        "peak_equity":       CAPITAL,
        "drawdown_pct":      0.0,
    }
    risk = load_json(RISK_FILE, default)
    if risk.get("daily_loss_since", "")[:10] != _today_start()[:10]:
        log("Daily counters reset (new UTC day)")
        risk["daily_loss_usd"]  = 0.0
        risk["daily_pnl_usd"]   = 0.0
        risk["daily_loss_since"] = _today_start()
        risk["halted"]           = False
        risk["halt_reason"]      = None
        risk["halt_until"]       = None
    return risk


def save_risk(risk: dict) -> None:
    risk["updated_at"] = now_iso()
    save_json_locked(RISK_FILE, risk)


def get_equity() -> float:
    portfolio_file = BUS_DIR / "portfolio.json"
    if portfolio_file.exists():
        try:
            p = json.loads(portfolio_file.read_text())
            equity = p.get("account_value") or p.get("equity_usd")
            if equity:
                return float(equity)
        except Exception:
            pass
    return CAPITAL


def check_halt(risk: dict) -> tuple[bool, str]:
    if not risk.get("halted"):
        return False, ""
    halt_until = risk.get("halt_until")
    if halt_until:
        try:
            until_dt = datetime.fromisoformat(halt_until.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < until_dt:
                remaining = (until_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                return True, f"{risk.get('halt_reason', 'unknown')} (resumes in {remaining:.1f}h)"
            else:
                log("Halt expired — resuming trading")
                risk["halted"]      = False
                risk["halt_reason"] = None
                risk["halt_until"]  = None
                return False, ""
        except Exception:
            pass
    return True, risk.get("halt_reason", "unknown")


def _get_current_regime() -> str:
    for candidate in [
        BUS_DIR / "market_regimes.json",
        BUS_DIR.parent.parent / "bus" / "regimes.json",
    ]:
        if candidate.exists():
            try:
                data = load_json(candidate, {})
                return data.get("regime", data.get("current", "unknown"))
            except Exception:
                pass
    return "unknown"


def log_rejection(coin: str, direction: str, reason: str,
                  gate: str = "controller") -> None:
    try:
        entry = {
            "ts": now_iso(), "coin": coin, "dir": direction,
            "reason": reason, "gate": gate,
        }
        with open(REJECTION_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def log_near_miss(entry: dict, reason: str, params: "_StrategyParams") -> None:
    """Log signals that almost passed risk gates — useful for tuning."""
    try:
        record = {
            "ts":           now_iso(),
            "coin":         entry.get("coin"),
            "direction":    entry.get("direction"),
            "signal_name":  entry.get("signal_name"),
            "consensus":    entry.get("consensus_layers"),
            "failed_gate":  reason,
            "strategy":     params.name,
            "near_miss":    True,
        }
        with open(NEAR_MISS_LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def log_decision(
    coin: str,
    strategy: str,
    layers_passed: int,
    verdict: str,
    price: float,
    reason: str,
    session_id: str = "",
) -> None:
    """Decision log — every evaluation verdict (approved, rejected, near_miss)."""
    try:
        record = {
            "ts":           now_iso(),
            "coin":         coin,
            "strategy":     strategy,
            "layers_passed": layers_passed,
            "verdict":      verdict,     # approved | rejected | near_miss
            "price":        price,
            "reason":       reason,
            "session_id":   session_id,
        }
        append_jsonl(DECISION_LOG_FILE, record)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY PARAMS (unified accessor — YAML or config.py fallback)
# ══════════════════════════════════════════════════════════════════════════════

class _StrategyParams:
    """Unified accessor for risk params — strategy YAML when active, config.py fallback."""

    def __init__(self, strategy: StrategyConfig | None, equity: float):
        self._strategy = strategy
        self._equity   = equity
        self._dyn      = get_dynamic_limits(equity)

    @property
    def has_strategy(self) -> bool:
        return self._strategy is not None

    @property
    def name(self) -> str:
        return self._strategy.name if self._strategy else "fallback"

    @property
    def max_positions(self) -> int:
        if self._strategy:
            return self._strategy.risk.max_positions
        return self._dyn["max_positions"]

    @property
    def max_daily_loss_usd(self) -> float:
        if self._strategy:
            return self._strategy.daily_loss_limit_usd(self._equity)
        return self._dyn["daily_loss_limit"]

    @property
    def reserve_usd(self) -> float:
        if self._strategy:
            return self._strategy.reserve_usd(self._equity)
        return 0.0

    @property
    def consensus_threshold(self) -> int:
        if self._strategy:
            return self._strategy.evaluation.consensus_threshold
        return 5

    @property
    def min_regime(self) -> list[str]:
        if self._strategy:
            return self._strategy.evaluation.min_regime
        return []

    @property
    def directions(self) -> list[str]:
        if self._strategy:
            return [d.upper() for d in self._strategy.evaluation.directions]
        return ["LONG", "SHORT"]

    @property
    def max_hold_hours(self) -> int:
        if self._strategy:
            return self._strategy.risk.max_hold_hours
        return 168

    @property
    def entry_end_action(self) -> str:
        if self._strategy:
            return self._strategy.risk.entry_end_action
        return "hold"

    @property
    def position_size_pct(self) -> float | None:
        if self._strategy and self._strategy.risk.position_size_pct > 0:
            return self._strategy.risk.position_size_pct
        return None

    @property
    def stop_loss_pct(self) -> float | None:
        if self._strategy and self._strategy.risk.stop_loss_pct > 0:
            return self._strategy.risk.stop_loss_pct
        return None

    @property
    def is_watch_only(self) -> bool:
        if self._strategy:
            return self._strategy.is_watch_only()
        return False

    def invested_usd(self, positions: list) -> float:
        return sum(float(p.get("size_usd", 0)) for p in positions)

    def available_usd(self, positions: list) -> float:
        return max(0.0, self._equity - self.reserve_usd - self.invested_usd(positions))


# ══════════════════════════════════════════════════════════════════════════════
# CONTROLLER (stateful — hard caps, event bus, rejection counter, heartbeat)
# ══════════════════════════════════════════════════════════════════════════════

class Controller:
    """
    Stateful controller shell for systems that need in-process state.

    Embeds:
      - Hard caps (unconfigurable safety limits)
      - Event bus (in-process + events.jsonl)
      - Rejection counter + session timeline + narrative builder
      - Dead man's switch heartbeat writer
      - Position reconciliation timer
      - Graceful shutdown state writer

    The controller instance is created in main() and passed around as needed.
    All approve_entry calls that need hard-cap enforcement should use
    controller.check_hard_caps() BEFORE calling approve_entry().
    """

    def __init__(self) -> None:
        # ── Hard caps — CANNOT be overridden by YAML or session params ─────────
        self.HARD_MAX_POSITION_PCT    = 25    # max 25% of equity per position
        self.HARD_MAX_EXPOSURE_PCT    = 80    # max 80% of equity in open positions
        self.HARD_MAX_ORDERS_PER_MIN  = 10    # rate limit per minute
        self.HARD_MAX_ORDERS_PER_SESSION = 100
        self._orders_this_session: int       = 0
        self._orders_this_minute:  list[float] = []   # timestamps

        # ── Event bus ──────────────────────────────────────────────────────────
        self.events: list[dict] = []

        # ── Rejection counter + timeline ────────────────────────────────────────
        self.eval_count:       int        = 0
        self.reject_count:     int        = 0
        self.session_timeline: list[dict] = []
        self._session_start:   float      = time.time()

        # ── Periodic timers ─────────────────────────────────────────────────────
        self._last_reconcile_time:  float = 0.0
        self._last_heartbeat_write: float = 0.0
        self.RECONCILE_INTERVAL   = 300   # 5 minutes
        self.HEARTBEAT_INTERVAL   = 60    # 1 minute

    # ── EVENT BUS ─────────────────────────────────────────────────────────────

    def emit(self, event_type: str, data: dict) -> None:
        """Emit an event: store in-process + append to events.jsonl."""
        event = {"type": event_type, "ts": now_iso(), **data}
        self.events.append(event)
        try:
            append_jsonl(EVENTS_LOG_FILE, event)
        except Exception:
            pass

    # ── TIMELINE HELPER ───────────────────────────────────────────────────────

    def _session_hour(self) -> int:
        return int((time.time() - self._session_start) / 3600)

    def add_timeline_event(self, event: str, detail: str = "") -> None:
        """Record a significant event with the hour number since session start."""
        self.session_timeline.append({
            "hour":   self._session_hour(),
            "event":  event,
            "detail": detail,
            "ts":     now_iso(),
        })

    def build_narrative(self) -> str:
        """Build human-readable session narrative from timeline."""
        parts = []
        for item in self.session_timeline:
            h = item["hour"]
            e = item["event"]
            d = item.get("detail", "")
            parts.append(f"Hour {h}: {e}" + (f" ({d})" if d else "") + ".")
        selectivity = (
            f"{self.reject_count / self.eval_count * 100:.1f}%"
            if self.eval_count > 0 else "n/a"
        )
        parts.append(
            f"{self.eval_count} evaluations, {self.reject_count} rejected "
            f"({selectivity} selectivity)."
        )
        return " ".join(parts)

    # ── HARD CAPS ─────────────────────────────────────────────────────────────

    def check_hard_caps(
        self,
        entry: dict,
        positions: list,
        equity: float,
    ) -> tuple[bool, str]:
        """
        Pre-flight hard-cap checks. Run BEFORE strategy YAML checks.
        Returns (passed, reason).
        """
        coin      = entry.get("coin", "")
        direction = entry.get("direction", "LONG")

        # ── Order rate: per minute ────────────────────────────────────────────
        now_ts = time.time()
        self._orders_this_minute = [
            t for t in self._orders_this_minute if now_ts - t < 60
        ]
        if len(self._orders_this_minute) >= self.HARD_MAX_ORDERS_PER_MIN:
            return False, (
                f"hard_cap:orders_per_min: {len(self._orders_this_minute)} >= "
                f"{self.HARD_MAX_ORDERS_PER_MIN}"
            )

        # ── Order rate: per session ───────────────────────────────────────────
        if self._orders_this_session >= self.HARD_MAX_ORDERS_PER_SESSION:
            return False, (
                f"hard_cap:orders_per_session: {self._orders_this_session} >= "
                f"{self.HARD_MAX_ORDERS_PER_SESSION}"
            )

        if equity <= 0:
            return True, "ok"   # can't compute pct with 0 equity, let other gates decide

        # ── Position size cap ─────────────────────────────────────────────────
        from scanner.v6.config import get_dynamic_limits
        limits = get_dynamic_limits(equity)
        # Estimate what size this entry would get
        size_pct_estimate = entry.get("strategy_size_pct", None)
        if size_pct_estimate is None:
            # Use max_position_usd as the ceiling estimate
            max_pos_usd = limits.get("max_position_usd", equity)
            size_pct_estimate = max_pos_usd / equity * 100
        if size_pct_estimate > self.HARD_MAX_POSITION_PCT:
            return False, (
                f"hard_cap:position_size: {size_pct_estimate:.1f}% > "
                f"{self.HARD_MAX_POSITION_PCT}% of equity"
            )

        # ── Total exposure cap ────────────────────────────────────────────────
        open_positions = [p for p in positions if not p.get("_pending")]
        total_invested = sum(float(p.get("size_usd", 0)) for p in open_positions)
        exposure_pct   = total_invested / equity * 100
        if exposure_pct >= self.HARD_MAX_EXPOSURE_PCT:
            return False, (
                f"hard_cap:exposure: {exposure_pct:.1f}% >= "
                f"{self.HARD_MAX_EXPOSURE_PCT}% of equity"
            )

        return True, "ok"

    def record_order(self) -> None:
        """Call when an order is actually sent to HL."""
        now_ts = time.time()
        self._orders_this_minute.append(now_ts)
        self._orders_this_session += 1

    # ── DEAD MAN'S SWITCH ─────────────────────────────────────────────────────

    def maybe_write_heartbeat(self) -> None:
        """Write controller heartbeat if >60s since last write."""
        now_ts = time.time()
        if now_ts - self._last_heartbeat_write >= self.HEARTBEAT_INTERVAL:
            try:
                hb = load_json(HEARTBEAT_FILE, {})
                hb["controller"] = now_iso()
                save_json_atomic(HEARTBEAT_FILE, hb)
                self._last_heartbeat_write = now_ts
                self.emit("HEARTBEAT", {"heartbeat_ts": hb["controller"]})
            except Exception as e:
                log(f"WARN: heartbeat write failed: {e}")

    # ── PERIODIC RECONCILIATION ───────────────────────────────────────────────

    def maybe_reconcile(self, client) -> None:
        """Run position reconciliation if >5min since last run."""
        now_ts = time.time()
        if client and (now_ts - self._last_reconcile_time >= self.RECONCILE_INTERVAL):
            log("  [RECONCILE] Periodic position reconciliation (5min timer)")
            try:
                _reconcile_positions(client)
                self._last_reconcile_time = now_ts
            except Exception as e:
                log(f"WARN: periodic reconciliation failed: {e}")

    # ── GRACEFUL SHUTDOWN ─────────────────────────────────────────────────────

    def write_state(self, session: "SessionState | None" = None) -> None:
        """Write controller state to bus/controller_state.json for recovery."""
        try:
            positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
            state = {
                "ts":                   now_iso(),
                "positions":            positions,
                "orders_this_session":  self._orders_this_session,
                "eval_count":           self.eval_count,
                "reject_count":         self.reject_count,
                "session_timeline":     self.session_timeline,
            }
            if session:
                state["session"] = {
                    "session_id":  session.session_id,
                    "strategy":    session.strategy,
                    "status":      session.status,
                    "started_at":  session.started_at,
                    "expires_at":  session.expires_at,
                    "equity_start": session.equity_start,
                    "equity_end":  session.equity_end,
                    "total_pnl":   session.total_pnl,
                    "trade_count": session.trade_count,
                    "wins":        session.wins,
                    "losses":      session.losses,
                    "near_misses": session.near_misses,
                    "completed_at": session.completed_at,
                    "reason":      session.reason,
                }
            save_json_atomic(CONTROLLER_STATE_FILE, state)
            log(f"  State written to {CONTROLLER_STATE_FILE}")
        except Exception as e:
            log(f"WARN: could not write controller state: {e}")


# Singleton controller instance (created in main, accessible module-wide for signal handlers)
_controller_instance: Controller | None = None


def _make_shutdown_handler(ctrl: Controller):
    """Create a signal handler that writes state and exits gracefully."""
    def _handler(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        log(f"  Graceful shutdown received ({sig_name})")
        ctrl.write_state()
        ctrl.emit("SESSION_COMPLETED", {"reason": f"graceful_shutdown:{sig_name}"})
        log("  Graceful shutdown complete.")
        sys.exit(0)
    return _handler


# ══════════════════════════════════════════════════════════════════════════════
# RISK CHECKS (all 9 — the gate)
# ══════════════════════════════════════════════════════════════════════════════

def approve_entry(
    entry: dict,
    positions: list,
    risk: dict,
    equity: float,
    params: _StrategyParams,
    controller: "Controller | None" = None,
) -> tuple[bool, str]:
    """Run all 9 risk checks. Returns (approved, reason)."""
    if controller is not None:
        controller.eval_count += 1
    ok, reason = _approve_entry_inner(entry, positions, risk, equity, params, controller)
    if controller is not None:
        if not ok:
            controller.reject_count += 1
        # Log to decision log
        price = 0.0
        try:
            portfolio = load_json(BUS_DIR / "portfolio.json", {})
            price = float(portfolio.get("last_price", {}).get(entry.get("coin", ""), 0))
        except Exception:
            pass
        layers_passed = entry.get("consensus_layers", 0) or 0
        session_id = entry.get("session_id", "")
        near = not ok and "consensus_threshold" in reason and layers_passed >= params.consensus_threshold - 2
        verdict = "approved" if ok else ("near_miss" if near else "rejected")
        log_decision(
            coin=entry.get("coin", ""),
            strategy=params.name,
            layers_passed=int(layers_passed),
            verdict=verdict,
            price=price,
            reason=reason,
            session_id=session_id,
        )
    return ok, reason


def _approve_entry_inner(
    entry: dict,
    positions: list,
    risk: dict,
    equity: float,
    params: _StrategyParams,
    controller: "Controller | None" = None,
) -> tuple[bool, str]:
    """Inner logic for approve_entry — all 9 risk checks."""
    coin      = entry.get("coin", "")
    direction = entry.get("direction", "LONG")

    # ── HARD CAPS (pre-flight, unconfigurable) ─────────────────────────────────
    if controller is not None:
        caps_ok, caps_reason = controller.check_hard_caps(entry, positions, equity)
        if not caps_ok:
            return False, caps_reason

    # ── Watch-only mode ──────────────────────────────────────────────────────
    if params.is_watch_only:
        return False, "watch_mode: max_positions=0, observation only"

    # ── CHECK 1: max_positions ───────────────────────────────────────────────
    open_positions = [p for p in positions if not p.get("_pending")]
    if len(open_positions) >= params.max_positions:
        return False, (
            f"max_positions: {len(open_positions)} >= {params.max_positions} "
            f"[strategy={params.name}]"
        )

    # ── CHECK 2: max_daily_loss_pct (circuit breaker) ────────────────────────
    daily_loss = float(risk.get("daily_loss_usd", 0.0))
    if params.has_strategy:
        daily_loss_pct = params._strategy.risk.max_daily_loss_pct
        limit = equity * daily_loss_pct / 100.0
    else:
        limit = get_dynamic_limits(equity)["daily_loss_limit"]
        daily_loss_pct = None
    if daily_loss >= limit:
        return False, (
            f"daily_loss_circuit_breaker: ${daily_loss:.2f} >= ${limit:.2f} "
            f"({daily_loss_pct}% of ${equity:.0f}) [strategy={params.name}]"
        )

    # ── CHECK 3: reserve_pct ──────────────────────────────────────────────────
    if params.has_strategy:
        reserve_pct  = params._strategy.risk.reserve_pct
        reserve      = equity * reserve_pct / 100.0
        invested     = sum(float(p.get("size_usd", 0)) for p in open_positions)
        available    = max(0.0, equity - reserve - invested)
        position_size = (
            equity * (params.position_size_pct / 100.0)
            if params.position_size_pct
            else get_dynamic_limits(equity)["min_position_usd"]
        )
        if reserve > 0 and available < position_size:
            return False, (
                f"reserve_pct: available=${available:.2f} < "
                f"min_position=${position_size:.2f} "
                f"(reserve={reserve_pct}% of ${equity:.0f}) [strategy={params.name}]"
            )

    # ── CHECK 4 & 5: time exits / entry_end handled separately (cycle checks)

    # ── CHECK 6: consensus_threshold ─────────────────────────────────────────
    consensus = entry.get("consensus_layers", entry.get("consensus", None))
    if consensus is not None:
        try:
            consensus_int = int(consensus)
        except (TypeError, ValueError):
            consensus_int = 0
        threshold = params.consensus_threshold
        if consensus_int < threshold:
            return False, (
                f"consensus_threshold: {consensus_int} < {threshold}/7 "
                f"[strategy={params.name}]"
            )

    # ── CHECK 7: min_regime ──────────────────────────────────────────────────
    allowed_regimes = params.min_regime
    if allowed_regimes:
        current_regime = _get_current_regime()
        if current_regime != "unknown" and current_regime.lower() not in allowed_regimes:
            return False, (
                f"min_regime: current regime '{current_regime}' not in "
                f"{allowed_regimes} [strategy={params.name}]"
            )

    # ── Checks 8 & 9 are applied via inject_strategy_params → executor ───────

    # ── Direction filter ──────────────────────────────────────────────────────
    dir_upper = direction.upper()
    if dir_upper not in params.directions:
        return False, f"direction_filter: {direction} not in {params.directions} [strategy={params.name}]"

    # ── Capital floor ─────────────────────────────────────────────────────────
    peak      = risk.get("peak_equity", CAPITAL)
    dyn_floor = max(CAPITAL * CAPITAL_FLOOR_PCT, peak * CAPITAL_FLOOR_PCT)
    if equity < dyn_floor:
        return False, f"capital_floor: equity=${equity:.0f} < ${dyn_floor:.0f}"

    # ── Per-coin duplicate ────────────────────────────────────────────────────
    coin_count = sum(1 for p in open_positions if p.get("coin") == coin)
    if coin_count >= MAX_PER_COIN:
        return False, f"max_per_coin: already {coin_count} position(s) on {coin}"

    for p in open_positions:
        if p.get("coin") == coin and p.get("direction") != direction:
            return False, f"opposing_position: already {p['direction']} on {coin}"

    return True, "ok"


def inject_strategy_params(entry: dict, params: _StrategyParams) -> dict:
    """Inject strategy-derived params (checks 8 & 9) into the entry dict."""
    enriched = dict(entry)
    if params.position_size_pct is not None:
        enriched["strategy_size_pct"] = params.position_size_pct
    if params.stop_loss_pct is not None:
        enriched["stop_loss_pct"] = params.stop_loss_pct / 100.0
    enriched["strategy_name"] = params.name
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# TIME EXIT + ENTRY_END (checks 4 & 5)
# ══════════════════════════════════════════════════════════════════════════════

def check_time_exits(positions: list, params: _StrategyParams) -> list[dict]:
    """CHECK 4: max_hold_hours. Returns exit signals for expired positions."""
    exits     = []
    max_hours = params.max_hold_hours
    now       = datetime.now(timezone.utc)
    for pos in positions:
        entry_time_str = pos.get("entry_time")
        if not entry_time_str:
            continue
        try:
            entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
        except Exception:
            continue
        hold_hours = (now - entry_time).total_seconds() / 3600.0
        if hold_hours >= max_hours:
            log(
                f"  TIME EXIT: {pos['coin']} {pos.get('direction', '?')} "
                f"held {hold_hours:.1f}h >= max {max_hours}h [strategy={params.name}]"
            )
            exits.append({
                "coin":   pos["coin"],
                "reason": f"max_hold_hours: {hold_hours:.1f}h >= {max_hours}h [strategy={params.name}]",
            })
    return exits


def handle_entry_end_events(
    entry_end_signals: list,
    positions: list,
    params: _StrategyParams,
) -> list[dict]:
    """CHECK 5: entry_end_action. Returns exit signals or empty list."""
    if params.entry_end_action == "hold":
        return []
    exits    = []
    pos_coins = {p["coin"] for p in positions}
    for sig in entry_end_signals:
        coin = sig.get("coin", "")
        if coin in pos_coins:
            log(f"  ENTRY_END → CLOSE: {coin} (entry_end_action=close, strategy={params.name})")
            exits.append({
                "coin":   coin,
                "reason": f"entry_end_action=close [strategy={params.name}]",
            })
    return exits


# ══════════════════════════════════════════════════════════════════════════════
# POSITION SIZING
# ══════════════════════════════════════════════════════════════════════════════

def compute_size_usd(trade: dict) -> float:
    """Compute position size. Strategy YAML → conviction-based fallback."""
    try:
        equity = float(load_json(BUS_DIR / "portfolio.json", {}).get("account_value", 0))
    except Exception:
        equity = 0

    if not equity:
        log("WARN: No equity in portfolio.json — skipping trade")
        return 0

    limits  = get_dynamic_limits(equity)
    min_pos = limits["min_position_usd"]
    max_pos = limits["max_position_usd"]

    # Strategy YAML size_pct takes priority
    strategy_size_pct = trade.get("strategy_size_pct")
    if strategy_size_pct is not None:
        size_usd = equity * strategy_size_pct / 100.0
        log(f"  Size (strategy {strategy_size_pct}%): ${size_usd:.0f}")
    else:
        # Flat sizing with quality tilt (conviction sizer removed as dead code)
        BASE_PCT = 0.15
        quality  = trade.get("quality", 5)
        quality_pct = max(0.10, min(0.20, BASE_PCT + (quality - 5) * 0.01))
        size_usd = equity * quality_pct
        log(f"  Size (flat {quality_pct:.0%}): ${size_usd:.0f}")

    return round(max(min_pos, min(max_pos, size_usd)), 2)


# ══════════════════════════════════════════════════════════════════════════════
# OPEN TRADE
# ══════════════════════════════════════════════════════════════════════════════

def open_trade(client: HLClient, trade: dict, dry: bool,
               strategy_name: str = "", session_id: str = "") -> bool:
    """Open a position from an approved entry. Returns True on success."""
    coin      = trade["coin"]
    direction = trade["direction"]
    is_buy    = direction == "LONG"

    # ── Failed entry cooldown ─────────────────────────────────────────────────
    cooldown_key = f"{coin}_{direction}"
    if cooldown_key in _failed_entries:
        elapsed = time.time() - _failed_entries[cooldown_key]
        if elapsed < _FAILED_ENTRY_COOLDOWN:
            remaining = int(_FAILED_ENTRY_COOLDOWN - elapsed)
            log(f"  SKIP {coin} {direction}: failed entry cooldown ({remaining}s remaining)")
            return False
        else:
            del _failed_entries[cooldown_key]

    size_usd    = compute_size_usd(trade)
    signal_stop = trade.get("stop_loss_pct", 0)
    stop_pct    = get_stop_pct(coin, signal_stop)

    if dry:
        price      = client.get_price(coin)
        size_coins = round(size_usd / price, COIN_SZ_DECIMALS.get(coin, 2)) if price > 0 else 0
        log(f"  [DRY] Would open {direction} {coin}: ${size_usd:.0f} @ ~${price:,.4f}")
        fill_px   = price
        filled_sz = size_coins
        hl_oid    = "dry"
        sl_oid    = ""
    else:
        price = client.get_price(coin)
        if price <= 0:
            log(f"  ERROR: no price for {coin}")
            log_rejection(coin, direction, "no_price")
            return False

        # ── Set leverage ──────────────────────────────────────────────────────
        target_lev = get_leverage(coin)
        try:
            asset = COIN_TO_ASSET.get(coin)
            if asset is not None:
                lev_action = {
                    "type":     "updateLeverage",
                    "asset":    asset,
                    "isCross":  True,
                    "leverage": target_lev,
                }
                lev_result = client._sign_and_send(lev_action)
                log(f"  Leverage set: {coin} → {target_lev}x cross")
        except Exception as e:
            log(f"  ❌ Leverage set FAILED for {coin}: {e}. Aborting trade.")
            log_rejection(coin, direction, "leverage_failed", {"error": str(e)})
            return False

        # ── Pre-trade checks ──────────────────────────────────────────────────

        # 1. Funding rate check
        funding_rate     = client.get_predicted_funding(coin)
        funding_cost_pct = abs(funding_rate) * trade.get("max_hold_hours", 12)
        funding_hurts    = (is_buy and funding_rate > 0) or (not is_buy and funding_rate < 0)
        if funding_hurts and funding_cost_pct > 0.005:
            log(f"  ⚠️ FUNDING: {coin} rate={funding_rate:.6f}/hr, cost={funding_cost_pct:.2%}")
            if funding_cost_pct > 0.02:
                log(f"  SKIP: funding cost {funding_cost_pct:.2%} exceeds 2%")
                log_rejection(coin, direction, "funding_cost",
                              {"rate": funding_rate, "cost_pct": funding_cost_pct})
                return False

        # 2. L2 book depth check
        book           = client.get_l2_book(coin, depth=5)
        relevant_depth = book["ask_depth_usd"] if is_buy else book["bid_depth_usd"]
        log(f"  L2 {coin}: bid=${book.get('bid_depth_usd',0):.0f} ask=${book.get('ask_depth_usd',0):.0f}")
        if relevant_depth <= 0:
            api_err = book.get("api_error")
            reason  = "book_api_error" if api_err else "book_depth_zero"
            log(f"  SKIP: L2 book depth 0 or failed for {coin} — {api_err or 'empty book'}")
            log_rejection(coin, direction, reason, {"error": api_err})
            return False
        if size_usd > relevant_depth * 0.50:
            log(f"  SKIP: order > 50% of visible liquidity (${relevant_depth:.0f})")
            log_rejection(coin, direction, "liquidity_too_thin",
                          {"size_usd": size_usd, "depth_usd": relevant_depth})
            return False

        # 3. Alpha vs cost filter
        fee_rates         = client.get_fee_rates()
        taker_fee         = fee_rates["taker"]
        expected_cost_pct = taker_fee * 2
        if funding_hurts:
            expected_cost_pct += funding_cost_pct
        signal_sharpe     = trade.get("sharpe", 1.5)
        expected_alpha_pct = max(0, (signal_sharpe - 1.0) * 0.003)
        if expected_alpha_pct < expected_cost_pct and signal_sharpe < 3.0:
            log(f"  SKIP: alpha {expected_alpha_pct:.3%} < cost {expected_cost_pct:.3%}")
            log_rejection(coin, direction, "alpha_vs_cost",
                          {"alpha_pct": expected_alpha_pct, "cost_pct": expected_cost_pct})
            return False

        # ── Size and execute ──────────────────────────────────────────────────

        raw_coins  = size_usd / price
        decimals   = COIN_SZ_DECIMALS.get(coin, 2)
        size_coins = math.floor(raw_coins * 10**decimals) / 10**decimals
        if size_coins * price < 10.0:
            size_coins = math.ceil(raw_coins * 10**decimals) / 10**decimals
        if size_coins <= 0:
            log(f"  ERROR: size_coins=0 for {coin}")
            log_rejection(coin, direction, "size_zero", {"size_usd": size_usd, "price": price})
            return False

        slippage = get_slippage(coin)

        # GTC vs IOC routing
        signal_time    = trade.get("signal_time") or trade.get("fired_at", "")
        signal_age_min = 0
        if signal_time:
            try:
                st = datetime.fromisoformat(signal_time.replace("Z", "+00:00"))
                signal_age_min = (datetime.now(timezone.utc) - st).total_seconds() / 60
            except Exception:
                pass

        if signal_age_min > 30:
            log(f"  SKIP {coin}: signal too stale ({signal_age_min:.0f}m old)")
            log_rejection(coin, direction, "stale_signal", {"age_min": signal_age_min})
            return False

        use_gtc = signal_age_min > 10
        if use_gtc and book.get("bids") and book.get("asks"):
            best_bid = book["bids"][0][0] if book["bids"] else price * 0.999
            best_ask = book["asks"][0][0] if book["asks"] else price * 1.001
            mid      = (best_bid + best_ask) / 2
            log(f"  Opening {direction} {coin}: {size_coins} coins GTC @ ${mid:,.4f} (age {signal_age_min:.0f}m)")
            result = client.place_gtc_order(coin, is_buy, size_coins, mid)
            log(f"  GTC result: {json.dumps(result)}")
            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                if statuses and "resting" in statuses[0]:
                    oid = statuses[0]["resting"]["oid"]
                    log(f"  GTC resting (oid={oid}), waiting 60s...")
                    time.sleep(60)
                    open_orders = client._info_post({"type": "openOrders", "user": client.main_address})
                    still_open  = any(o.get("oid") == oid for o in open_orders)
                    if still_open:
                        asset   = COIN_TO_ASSET.get(coin)
                        client._sign_and_send({"type": "cancel", "cancels": [{"a": asset, "o": oid}]})
                        log(f"  GTC unfilled → IOC fallback")
                        result = (client.market_buy(coin, size_coins, slippage=slippage)
                                  if is_buy else client.market_sell(coin, size_coins, slippage=slippage))
                    else:
                        log(f"  GTC filled within 60s")
        else:
            log(f"  Opening {direction} {coin}: {size_coins} coins IOC @ ${price:,.4f} "
                f"[funding={funding_rate:+.6f}/hr, depth=${relevant_depth:.0f}]")
            result = (client.market_buy(coin, size_coins, slippage=slippage)
                      if is_buy else client.market_sell(coin, size_coins, slippage=slippage))
            log(f"  Order result: {json.dumps(result)}")

        if result.get("status") == "err":
            log(f"  ERROR: order failed: {result.get('response')}")
            return False

        fills     = result.get("response", {}).get("data", {}).get("statuses", [{}])
        filled    = fills[0].get("filled", {}) if fills else {}
        fill_px   = float(filled.get("avgPx", 0))
        filled_sz = float(filled.get("totalSz", 0))
        hl_oid    = str(filled.get("oid", ""))

        if fill_px <= 0 or filled_sz <= 0:
            log(f"  🚨 NO FILL on entry for {coin}: fill_px={fill_px}, filled_sz={filled_sz}")
            send_alert(f"🚨 Entry order for {coin} {direction} got NO FILL.")
            _failed_entries[f"{coin}_{direction}"] = time.time()
            try:
                open_orders = client.get_open_orders()
                for oo in open_orders:
                    if oo.get("coin") == coin:
                        client.cancel_order(coin, oo["oid"])
            except Exception as e:
                log(f"  Warning: failed to cancel orphaned orders: {e}")
            return False

        if abs(filled_sz - size_coins) > 0.0001:
            log(f"  ⚠️ PARTIAL FILL: requested={size_coins}, filled={filled_sz}")
            size_coins = filled_sz

        price    = fill_px
        size_usd = price * filled_sz

        # ── Stop loss placement ────────────────────────────────────────────────
        stop_price = client.round_price(
            price * (1 - stop_pct) if is_buy else price * (1 + stop_pct)
        )
        sl_result = client.place_stop_loss(coin, not is_buy, size_coins, stop_price)
        sl_status = sl_result.get("status", "unknown")
        sl_fills  = sl_result.get("response", {}).get("data", {}).get("statuses", [{}])
        sl_oid    = ""
        if sl_fills:
            resting = sl_fills[0].get("resting", {})
            sl_oid  = str(resting.get("oid", ""))
        log(f"  Stop @ ${stop_price:,.4f} (oid={sl_oid}): {json.dumps(sl_result)}")

        if sl_status != "ok" or not sl_oid:
            log(f"  🚨 STOP LOSS FAILED: status={sl_status}")
            send_alert(f"🚨 STOP LOSS FAILED for {coin} {direction} @ ${stop_price:.2f}\nNAKED position!")
            try:
                time.sleep(1)
                sl_retry = client.place_stop_loss(coin, not is_buy, size_coins, stop_price)
                sl_retry_status = sl_retry.get("status", "unknown")
                sl_retry_fills  = sl_retry.get("response", {}).get("data", {}).get("statuses", [{}])
                if sl_retry_fills:
                    sl_oid = str(sl_retry_fills[0].get("resting", {}).get("oid", ""))
                if sl_retry_status == "ok" and sl_oid:
                    log(f"  Stop retry succeeded: oid={sl_oid}")
                else:
                    log(f"  Stop retry also failed: {sl_retry}")
                    send_alert(f"🚨🚨 STOP RETRY FAILED for {coin}. NAKED POSITION. CLOSE MANUALLY.")
                    if not sl_oid:
                        log(f"  ❌ CRITICAL: Closing {coin} to prevent naked exposure.")
                        send_alert(f"❌ Stop loss failed for {coin} {direction}. Closing immediately.")
                        close_res = (client.market_sell(coin, filled_sz)
                                     if is_buy else client.market_buy(coin, filled_sz))
                        log(f"  Emergency close: {close_res}")
                        return False
            except Exception as e:
                log(f"  Stop retry error: {e}")
                send_alert(f"❌ Stop loss failed for {coin} {direction}. Closing immediately.")
                close_res = (client.market_sell(coin, filled_sz)
                             if is_buy else client.market_buy(coin, filled_sz))
                log(f"  Emergency close: {close_res}")
                return False

    # ── Build position record ─────────────────────────────────────────────────
    pos_id     = f"{coin}_{direction}_{int(time.time())}"
    entry_time = now_iso()
    pos = {
        "id":              pos_id,
        "coin":            coin,
        "direction":       direction,
        "strategy":        strategy_name or trade.get("strategy_name", "unknown"),
        "session_id":      session_id or trade.get("session_id", ""),
        "signal_name":     trade.get("signal_name", ""),
        "expression":      trade.get("expression", ""),
        "exit_expression": trade.get("exit_expression", ""),
        "max_hold_hours":  trade.get("max_hold_hours", 12),
        "entry_price":     fill_px if not dry else price,
        "size_usd":        size_usd,
        "size_coins":      size_coins if not dry else round(size_usd / price, COIN_SZ_DECIMALS.get(coin, 2)) if price > 0 else 0,
        "stop_loss_pct":   stop_pct,
        "stop_loss_price": client.round_price(
            (fill_px if not dry else price) * (1 - stop_pct) if is_buy
            else (fill_px if not dry else price) * (1 + stop_pct)
        ),
        "entry_time":      entry_time,
        "sharpe":          trade.get("sharpe", 0),
        "win_rate":        trade.get("win_rate", 0),
        "composite_score": trade.get("composite_score", 0),
        "hl_order_id":     hl_oid if not dry else "dry",
        "sl_order_id":     sl_oid if not dry else "",
        "peak_pnl_pct":    0.0,
        "trailing_activated": False,
        "dry":             dry,
        "strategy_version": STRATEGY_VERSION,
    }

    pdata     = load_json_locked(POSITIONS_FILE, {})
    positions = pdata.get("positions", [])
    positions.append(pos)
    save_json_locked(POSITIONS_FILE, {"updated_at": now_iso(), "positions": positions})

    emoji = "🟢" if is_buy else "🔴"
    send_alert(
        f"{emoji} <b>V6 OPEN {direction}</b> {coin}\n"
        f"Signal: {trade.get('signal_name', '')}\n"
        f"Price: ${pos['entry_price']:,.4f}  Size: ${size_usd:.0f}\n"
        f"Stop: {stop_pct*100:.0f}%  Sharpe: {trade.get('sharpe', 0):.2f}"
        + ("  [DRY]" if dry else "")
    )
    log(f"  Opened {direction} {coin} @ ${pos['entry_price']:,.4f} (stop={stop_pct*100:.0f}%)")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# CLOSE TRADE
# ══════════════════════════════════════════════════════════════════════════════

def close_trade(client: HLClient, pos: dict, exit_reason: str, dry: bool) -> dict | None:
    """Close a position. Records P&L and sends alert. Returns trade_record or None."""
    coin        = pos["coin"]
    direction   = pos["direction"]
    is_long     = direction == "LONG"
    size_coins  = pos.get("size_coins", 0)
    entry_price = pos.get("entry_price", 0)
    entry_time  = pos.get("entry_time", "")

    if dry:
        exit_price = client.get_price(coin)
        log(f"  [DRY] Would close {direction} {coin} @ ~${exit_price:,.4f}")
    else:
        client.cancel_coin_stops(coin)
        result = (client.market_sell(coin, size_coins, reduce_only=True)
                  if is_long else client.market_buy(coin, size_coins, reduce_only=True))
        log(f"  Close result: {json.dumps(result)}")

        fills    = result.get("response", {}).get("data", {}).get("statuses", [{}])
        filled   = fills[0].get("filled", {}) if fills else {}
        fill_px  = float(filled.get("avgPx", 0))
        filled_sz = float(filled.get("totalSz", 0))

        if fill_px <= 0:
            log(f"  🚨 NO FILL PRICE for {coin} close — cannot record trade")
            send_alert(f"🚨 NO FILL PRICE for {coin} close. Position may still be open. CHECK HL.")
            return None

        exit_price = fill_px

        if filled_sz > 0 and abs(filled_sz - abs(size_coins)) > 0.0001:
            remaining = abs(size_coins) - filled_sz
            log(f"  PARTIAL FILL: filled {filled_sz}, remaining {remaining:.6f}")
            send_alert(f"⚠️ PARTIAL FILL on {coin} close: filled {filled_sz}/{abs(size_coins)}")
            try:
                retry = (client.market_sell(coin, remaining, reduce_only=True)
                         if is_long else client.market_buy(coin, remaining, reduce_only=True))
                retry_fills   = retry.get("response", {}).get("data", {}).get("statuses", [{}])
                retry_filled  = retry_fills[0].get("filled", {}) if retry_fills else {}
                if float(retry_filled.get("totalSz", 0)) > 0:
                    log(f"  Retry filled: {retry_filled.get('totalSz')} @ ${retry_filled.get('avgPx')}")
                else:
                    send_alert(f"🚨 FAILED to close remaining {remaining} {coin} — CHECK HL MANUALLY")
            except Exception as e:
                log(f"  RETRY ERROR: {e}")
                send_alert(f"🚨 RETRY ERROR closing {coin}: {e}")
        elif filled_sz == 0 and not dry:
            log(f"  CLOSE FAILED — no fill. Position still open on HL")
            send_alert(f"🚨 CLOSE FAILED for {coin} — no fill, position still open")
            return None

    # ── P&L computation ───────────────────────────────────────────────────────
    actual_entry_notional = entry_price * abs(size_coins) if entry_price and size_coins else pos.get("size_usd", 0)
    actual_exit_notional  = exit_price  * abs(size_coins) if exit_price  and size_coins else actual_entry_notional

    try:
        fee_rates = client.get_fee_rates()
        fee_rate  = fee_rates["taker"]
    except Exception:
        fee_rate = FEE_RATE

    exit_fee   = round(actual_exit_notional * fee_rate, 4)
    total_fees = exit_fee  # exit-only (entry fee already paid at open)

    if entry_price and exit_price and entry_price > 0 and size_coins:
        price_diff    = exit_price - entry_price
        pnl_usd_gross = round(price_diff * abs(size_coins), 4) if is_long else round(-price_diff * abs(size_coins), 4)
        raw_pct       = (exit_price - entry_price) / entry_price
        pnl_pct       = raw_pct if is_long else -raw_pct
        pnl_usd       = round(pnl_usd_gross - total_fees, 4)
    else:
        pnl_pct = pnl_usd_gross = pnl_usd = 0

    mid_at_close = 0
    try:
        mid_at_close = client.get_price(coin)
    except Exception:
        pass
    slippage_pct = (
        round(abs(exit_price - mid_at_close) / mid_at_close * 100, 4)
        if mid_at_close > 0 and exit_price > 0 else 0
    )

    exit_time = now_iso()

    # ── Performance fee ────────────────────────────────────────────────────────
    zero_fee = 0.0
    fee_info = None
    try:
        from performance_fee import calculate_and_collect_fee
        equity   = float(load_json(RISK_FILE, {}).get("peak_equity", 100))
        fee_info = calculate_and_collect_fee(
            trade_pnl=pnl_usd,
            trade_id=pos.get("id", f"{coin}_{direction}"),
            equity=equity,
            dry=dry,
        )
        zero_fee = fee_info.get("zero_fee", 0)
        if zero_fee > 0:
            pnl_usd = round(pnl_usd - zero_fee, 4)
    except Exception:
        pass

    trade_record = {
        **pos,
        "exit_price":      exit_price,
        "exit_time":       exit_time,
        "exit_reason":     exit_reason,
        "pnl_pct":         round(pnl_pct, 6),
        "pnl_usd":         pnl_usd,
        "pnl_usd_gross":   pnl_usd_gross,
        "fees_usd":        total_fees,
        "slippage_pct":    slippage_pct,
        "actual_notional": round(actual_exit_notional, 2),
        "won":             pnl_usd > 0,
        "sharpe":          pos.get("sharpe") if pos.get("sharpe") is not None else 0,
        "win_rate":        pos.get("win_rate") if pos.get("win_rate") is not None else 0,
        "zero_fee":        zero_fee,
        "pnl_usd_net":     pnl_usd,
    }

    append_jsonl(TRADES_FILE, trade_record)

    if not dry:
        risk = load_json(RISK_FILE, {})
        risk["daily_pnl_usd"] = round(risk.get("daily_pnl_usd", 0) + pnl_usd, 4)
        if pnl_usd < 0:
            risk["daily_loss_usd"] = round(risk.get("daily_loss_usd", 0) + abs(pnl_usd), 4)
        save_json_locked(RISK_FILE, {**risk, "updated_at": now_iso()})

    won   = pnl_usd > 0
    emoji = "✅" if won else "❌"
    fee_line = ""
    if zero_fee > 0:
        fee_line = f"\nzero fee (10%): -${zero_fee:.2f}  net: ${pnl_usd:+.2f}"
    elif fee_info and fee_info.get("fee_status") == "below_hwm":
        fee_line = "\nzero fee: $0.00 (below high-water mark)"
    elif pnl_usd <= 0:
        fee_line = "\nzero fee: $0.00"

    send_alert(
        f"{emoji} <b>V6 CLOSE {direction}</b> {coin}\n"
        f"Signal: {pos.get('signal_name', '')}\n"
        f"Entry: ${entry_price:,.4f}  Exit: ${exit_price:,.4f}\n"
        f"P&L: ${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%)"
        f"{fee_line}\n"
        f"Reason: {exit_reason}"
        + ("  [DRY]" if dry else "")
    )
    log(f"  Closed {direction} {coin}: P&L=${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%) — {exit_reason}")
    return trade_record


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CYCLE
# ══════════════════════════════════════════════════════════════════════════════

def run_once(client: HLClient | None = None, dry: bool = False,
             controller: "Controller | None" = None) -> None:
    """
    One controller cycle:
      1. Check circuit breaker
      2. Load strategy + risk state
      3. Enforce time exits (max_hold_hours)
      4. Handle ENTRY_END events (entry_end_action)
      5. Run all 9 risk checks on pending entries
      6. Inject strategy params into approved entries
      7. Execute approved entries on HL (if client available)
      8. Process exits on HL
      9. Update portfolio.json + heartbeat
    """
    # ── Circuit breaker ───────────────────────────────────────────────────────
    cb_path = BUS_DIR / "circuit_breaker.json"
    if cb_path.exists():
        cb = load_json(cb_path, {})
        if cb.get("paused") or cb.get("halted"):
            log("⛔ CIRCUIT BREAKER ACTIVE — controller halted")
            return

    # ── Load strategy ─────────────────────────────────────────────────────────
    strategy = get_active_strategy()
    equity   = get_equity()
    params   = _StrategyParams(strategy, equity)

    if strategy:
        log(f"[STRATEGY] {strategy.display} ({strategy.tier}) | "
            f"max_pos={strategy.risk.max_positions} | "
            f"size={strategy.risk.position_size_pct}% | "
            f"stop={strategy.risk.stop_loss_pct}% | "
            f"consensus={strategy.evaluation.consensus_threshold}/7")
    else:
        log("[STRATEGY] none active — using config.py fallback limits")

    # ── Load bus state ─────────────────────────────────────────────────────────
    risk      = load_risk()
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    entries   = load_json(ENTRIES_FILE,  {}).get("entries",  [])
    approved  = load_json(APPROVED_FILE, {}).get("approved", [])
    exits     = load_json(EXITS_FILE,    {}).get("exits",    [])

    # Session 9: merge monitor signals from bus/signals.json
    # ENTRY signals → treat like entries; EXIT signals → treat like exits
    # ENTRY_END signals → check entry_end_action
    _monitor_signals = load_json(SIGNALS_FILE, {}).get("signals", [])
    if _monitor_signals:
        for _sig in _monitor_signals:
            _stype = _sig.get("type", "")
            if _stype == "ENTRY":
                # Convert monitor Signal → entry format (compatible with approve_entry)
                _entry = {
                    "coin":            _sig.get("coin"),
                    "direction":       _sig.get("direction"),
                    "signal_name":     f"MONITOR_{_sig.get('direction')}_{_sig.get('coin')}_{_sig.get('regime', '')}",
                    "expression":      f"MONITOR_CONSENSUS={_sig.get('consensus', 0)}",
                    "exit_expression": "",
                    "max_hold_hours":  48,
                    "sharpe":          round(_sig.get("conviction", 0.5) * 7, 2),
                    "win_rate":        round(45 + _sig.get("conviction", 0.5) * 50, 1),
                    "composite_score": round(_sig.get("conviction", 0.5) * 7, 2),
                    "stop_loss_pct":   get_stop_pct(_sig.get("coin", "")),
                    "priority":        1,
                    "fired_at":        _sig.get("timestamp"),
                    "source":          "monitor",
                    "regime":          _sig.get("regime", ""),
                    "consensus":       _sig.get("consensus", 0),
                    "conviction":      _sig.get("conviction", 0),
                    "event_type":      "ENTRY",
                }
                entries.append(_entry)
            elif _stype == "EXIT":
                _exit = {
                    "coin":      _sig.get("coin"),
                    "direction": _sig.get("direction"),
                    "reason":    _sig.get("reason", "monitor_exit"),
                    "timestamp": _sig.get("timestamp"),
                    "source":    "monitor",
                }
                exits.append(_exit)
            elif _stype == "ENTRY_END":
                _ee = {
                    "coin":       _sig.get("coin"),
                    "direction":  _sig.get("direction"),
                    "timestamp":  _sig.get("timestamp"),
                    "event_type": "ENTRY_END",
                    "source":     "monitor",
                    "consensus":  _sig.get("consensus", 0),
                    "conviction": _sig.get("conviction", 0),
                    "layers_remaining": _sig.get("layers_remaining", 0),
                }
                entries.append(_ee)

    entry_signals     = [e for e in entries if e.get("event_type", "ENTRY") == "ENTRY"]
    entry_end_signals = [e for e in entries if e.get("event_type") == "ENTRY_END"]
    if not entry_signals and entries:
        entry_signals     = [e for e in entries if e.get("event_type", "ENTRY") not in ("ENTRY_END",)]
        entry_end_signals = []

    risk["open_count"] = len(positions)

    # ── Track peak equity ─────────────────────────────────────────────────────
    peak = risk.get("peak_equity", CAPITAL)
    if equity > peak:
        risk["peak_equity"] = equity

    # ── Drawdown monitoring ───────────────────────────────────────────────────
    if peak > 0:
        drawdown_pct = (peak - equity) / peak * 100
        risk["drawdown_pct"] = round(drawdown_pct, 2)
        last_alert_dd = risk.get("last_drawdown_alert_pct", 0)
        for threshold in [5, 10, 15, 20]:
            if drawdown_pct >= threshold and last_alert_dd < threshold:
                log(f"  ⚠️ DRAWDOWN ALERT: {drawdown_pct:.1f}% from peak ${peak:.0f}")
                send_alert(
                    f"⚠️ DRAWDOWN {drawdown_pct:.1f}%\n"
                    f"Peak: ${peak:.0f} → Current: ${equity:.0f}"
                )
                risk["last_drawdown_alert_pct"] = threshold

    # ── Global halt check ─────────────────────────────────────────────────────
    halted, halt_reason = check_halt(risk)
    if halted:
        log(f"HALTED: {halt_reason}")
        save_risk(risk)
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": []})
        update_heartbeat()
        return

    # ── CHECK 4: Time exits ───────────────────────────────────────────────────
    time_exit_signals = check_time_exits(positions, params) if positions else []

    # ── CHECK 5: ENTRY_END handler ────────────────────────────────────────────
    entry_end_exits = handle_entry_end_events(entry_end_signals, positions, params)

    # ── Merge exits ───────────────────────────────────────────────────────────
    all_new_exits = time_exit_signals + entry_end_exits
    if all_new_exits:
        existing_exits = load_json(EXITS_FILE, {}).get("exits", [])
        merged_coins   = {e["coin"] for e in existing_exits}
        for ex in all_new_exits:
            if ex["coin"] not in merged_coins:
                existing_exits.append(ex)
                merged_coins.add(ex["coin"])
        save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing_exits})
        exits = existing_exits  # use merged exits this cycle
        log(f"Added {len(all_new_exits)} exit signal(s) to bus")

    # ── Process exits on HL (if client available) ─────────────────────────────
    closed_ids: set[str] = set()
    if client and exits:
        pos_by_coin = {p["coin"]: p for p in positions}
        for ex in exits:
            coin = ex.get("coin", "")
            pos  = pos_by_coin.get(coin)
            if not pos:
                continue
            result = close_trade(client, pos, ex.get("reason", "exit_signal"), dry)
            if result is not None:
                closed_ids.add(pos.get("id", coin))
                if controller is not None:
                    controller.emit("TRADE_EXITED", {
                        "coin":        coin,
                        "direction":   pos.get("direction"),
                        "exit_reason": ex.get("reason", "exit_signal"),
                        "pnl_usd":     result.get("pnl_usd", 0),
                        "session_id":  pos.get("session_id", ""),
                    })
                    pnl = result.get("pnl_usd", 0)
                    controller.add_timeline_event(
                        f"Exited {coin} {pos.get('direction')}",
                        f"pnl=${pnl:+.2f}",
                    )
        save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": []})

    if closed_ids:
        remaining = [p for p in positions if p.get("id") not in closed_ids
                     and p.get("coin") not in closed_ids]
        positions = remaining
        _safe_save_positions(client, positions, source="run_once/close")

    # ── Gate: run all 9 checks on ENTRY signals ───────────────────────────────
    new_approved = []
    rejected     = []
    working_positions = list(positions)

    for entry in entry_signals:
        ok, reason = approve_entry(entry, working_positions, risk, equity, params, controller)
        if ok:
            enriched = inject_strategy_params(entry, params)
            new_approved.append(enriched)
            log(f"  APPROVED: {entry.get('coin')} {entry.get('direction')} "
                f"[{entry.get('signal_name', '?')}] strategy={params.name}")
            working_positions.append({
                "coin":      entry["coin"],
                "direction": entry["direction"],
                "_pending":  True,
            })
        else:
            rejected.append((entry.get("coin"), entry.get("signal_name"),
                              entry.get("direction", "?"), reason, entry))

    if rejected:
        for coin, sig, direction, reason, entry in rejected:
            log(f"  REJECTED: {coin} [{sig}] — {reason}")
            log_rejection(coin, direction, reason)
            # Near-miss detection: consensus was close but below threshold
            consensus = entry.get("consensus_layers")
            if consensus is not None and "consensus_threshold" in reason:
                threshold = params.consensus_threshold
                if int(consensus) >= threshold - 2:
                    log_near_miss(entry, reason, params)
                    log(f"  NEAR MISS: {coin} consensus={consensus}/{threshold} (within 2 of passing)")
                    if controller is not None:
                        controller.emit("NEAR_MISS", {
                            "coin": coin,
                            "consensus": consensus,
                            "threshold": threshold,
                            "strategy": params.name,
                        })
                        controller.add_timeline_event(
                            "Near miss",
                            f"{coin} consensus={consensus}/{threshold}",
                        )

    # ── Combine with any pre-approved entries from bus ────────────────────────
    # (legacy: approved.json may have entries from previous gate pass)
    all_approved = new_approved + [e for e in approved
                                   if e.get("coin") not in {a["coin"] for a in new_approved}]

    # ── Daily loss circuit breaker ────────────────────────────────────────────
    if risk.get("daily_loss_usd", 0.0) >= params.max_daily_loss_usd:
        log(f"DAILY LOSS CIRCUIT BREAKER: ${risk['daily_loss_usd']:.2f} >= "
            f"${params.max_daily_loss_usd:.2f} — halting 24h")
        risk["halted"]      = True
        risk["halt_reason"] = f"daily_loss_circuit_breaker [strategy={params.name}]"
        risk["halt_until"]  = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        all_approved = []
        if controller is not None:
            controller.emit("RISK_BREACH", {
                "breach_type": "daily_loss_circuit_breaker",
                "daily_loss": risk["daily_loss_usd"],
                "limit": params.max_daily_loss_usd,
                "strategy": params.name,
            })
            controller.add_timeline_event("Risk breach", "daily_loss_circuit_breaker")

    # ── Capital floor halt ────────────────────────────────────────────────────
    dynamic_floor = peak * CAPITAL_FLOOR_PCT
    if equity < dynamic_floor:
        log(f"CAPITAL FLOOR HIT: ${equity:.0f} < ${dynamic_floor:.0f} — halting")
        risk["halted"]            = True
        risk["halt_reason"]       = f"capital_floor: ${equity:.0f} < ${dynamic_floor:.0f}"
        risk["halt_until"]        = None
        risk["capital_floor_hit"] = True
        all_approved = []
        if controller is not None:
            controller.emit("RISK_BREACH", {
                "breach_type": "capital_floor",
                "equity": equity,
                "floor": dynamic_floor,
            })
            controller.add_timeline_event("Risk breach", f"capital_floor ${equity:.0f}")

    # ── Execute approved entries on HL ────────────────────────────────────────
    if client and all_approved:
        open_coins = {p["coin"] for p in positions}
        strat_name = strategy.name if strategy else "fallback"
        for trade in all_approved:
            if trade["coin"] in open_coins:
                log(f"  SKIP: already have position on {trade['coin']}")
                log_rejection(trade["coin"], trade.get("direction", "?"), "already_open")
                continue
            success = open_trade(
                client, trade, dry,
                strategy_name=strat_name,
                session_id=trade.get("session_id", ""),
            )
            if success:
                open_coins.add(trade["coin"])
                if controller is not None:
                    controller.record_order()
                    controller.emit("TRADE_ENTERED", {
                        "coin":      trade["coin"],
                        "direction": trade.get("direction"),
                        "strategy":  strat_name,
                        "session_id": trade.get("session_id", ""),
                    })
                    controller.add_timeline_event(
                        f"Entered {trade['coin']} {trade.get('direction')}",
                        f"strategy={strat_name}",
                    )

    # ── Write bus outputs ─────────────────────────────────────────────────────
    save_json_atomic(ENTRIES_FILE,  {"updated_at": now_iso(), "entries": []})
    save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": all_approved})
    save_risk(risk)
    update_heartbeat()
    if controller is not None:
        controller.maybe_write_heartbeat()


# ══════════════════════════════════════════════════════════════════════════════
# STATUS COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def print_status() -> None:
    strategy  = get_active_strategy()
    equity    = get_equity()
    risk      = load_risk()
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    params    = _StrategyParams(strategy, equity)

    print("\n══ CONTROLLER STATUS ══════════════════════════════════")
    if strategy:
        print(f"  Strategy:     {strategy.display} ({strategy.name}) [{strategy.tier}]")
        print(f"  Session:      {strategy.session.duration_hours}h")
        print(f"  Scope:        {strategy.evaluation.scope}")
        print(f"  Consensus:    {strategy.evaluation.consensus_threshold}/7")
        print(f"  Directions:   {strategy.evaluation.directions}")
        print(f"  Min regime:   {strategy.evaluation.min_regime}")
        print(f"  Max pos:      {strategy.risk.max_positions}")
        print(f"  Size:         {strategy.risk.position_size_pct}% of equity")
        print(f"  Stop:         {strategy.risk.stop_loss_pct}%")
        print(f"  Reserve:      {strategy.risk.reserve_pct}% (${params.reserve_usd:.2f})")
        print(f"  Max hold:     {strategy.risk.max_hold_hours}h")
        print(f"  Entry-end:    {strategy.risk.entry_end_action}")
        print(f"  Max daily loss: {strategy.risk.max_daily_loss_pct}% (${params.max_daily_loss_usd:.2f})")
    else:
        print("  Strategy:     NONE (config.py fallback)")
        print(f"  Max pos:      {params.max_positions}")
        print(f"  Max daily:    ${params.max_daily_loss_usd:.2f}")

    print(f"\n  Equity:       ${equity:.2f}")
    print(f"  Peak equity:  ${risk.get('peak_equity', CAPITAL):.2f}")
    print(f"  Drawdown:     {risk.get('drawdown_pct', 0.0):.1f}%")
    print(f"  Daily loss:   ${risk.get('daily_loss_usd', 0.0):.2f}")
    print(f"  Daily PnL:    ${risk.get('daily_pnl_usd', 0.0):.2f}")
    print(f"  Halted:       {risk.get('halted', False)} ({risk.get('halt_reason', 'n/a')})")
    print(f"  Open pos:     {len(positions)}")
    print("═══════════════════════════════════════════════════════\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _controller_instance

    loop   = "--loop"   in sys.argv
    dry    = "--dry"    in sys.argv or "--paper" in sys.argv
    status = "--status" in sys.argv

    if status:
        print_status()
        return

    # Paper mode bus isolation
    from scanner.v6.paper_isolation import is_paper_mode, apply_paper_isolation
    if is_paper_mode() or dry:
        apply_paper_isolation()
        import scanner.v6.config as _cfg
        global BUS_DIR, ENTRIES_FILE, APPROVED_FILE, POSITIONS_FILE, RISK_FILE, \
               HEARTBEAT_FILE, EXITS_FILE, TRADES_FILE, SIGNALS_FILE
        BUS_DIR        = _cfg.BUS_DIR
        ENTRIES_FILE   = _cfg.ENTRIES_FILE
        APPROVED_FILE  = _cfg.APPROVED_FILE
        POSITIONS_FILE = _cfg.POSITIONS_FILE
        RISK_FILE      = _cfg.RISK_FILE
        HEARTBEAT_FILE = _cfg.HEARTBEAT_FILE
        EXITS_FILE     = _cfg.EXITS_FILE
        TRADES_FILE    = _cfg.TRADES_FILE
        SIGNALS_FILE   = _cfg.BUS_DIR / "signals.json"
        log("=== PAPER MODE — controller using isolated bus ===")

    BUS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Create controller instance ────────────────────────────────────────────
    ctrl = Controller()
    _controller_instance = ctrl

    # ── Register signal handlers for graceful shutdown ────────────────────────
    handler = _make_shutdown_handler(ctrl)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT,  handler)

    # ── State recovery ─────────────────────────────────────────────────────────
    if CONTROLLER_STATE_FILE.exists():
        log(f"  RECOVERY: found {CONTROLLER_STATE_FILE} — loading previous state")
        try:
            saved_state = load_json(CONTROLLER_STATE_FILE, {})
            # Restore counters
            ctrl._orders_this_session = saved_state.get("orders_this_session", 0)
            ctrl.eval_count           = saved_state.get("eval_count", 0)
            ctrl.reject_count         = saved_state.get("reject_count", 0)
            ctrl.session_timeline     = saved_state.get("session_timeline", [])
            # Restore positions to positions.json if they were saved
            saved_positions = saved_state.get("positions", [])
            if saved_positions:
                log(f"  RECOVERY: restoring {len(saved_positions)} saved positions")
                save_json_locked(POSITIONS_FILE, {
                    "updated_at": now_iso(),
                    "positions":  saved_positions,
                })
            log(f"  RECOVERY: orders_this_session={ctrl._orders_this_session}, "
                f"eval_count={ctrl.eval_count}")
            # Delete state file after successful load
            CONTROLLER_STATE_FILE.unlink()
            log(f"  RECOVERY: state file deleted — recovery complete")
        except Exception as e:
            log(f"WARN: state recovery failed: {e}")

    # Load HL metadata (needed for coin sizing even in paper mode)
    load_hl_meta()

    mode_label = "DRY" if dry else "LIVE"
    log(f"=== V6 Controller [{mode_label}] starting ===")
    ctrl.emit("SESSION_STARTED", {"mode": mode_label, "loop": loop})
    ctrl.add_timeline_event("Session started", f"mode={mode_label}")

    strategy = get_active_strategy()
    if strategy:
        log(f"Active strategy: {strategy.display} ({strategy.tier})")
    else:
        log("No active strategy — config.py fallback limits active")

    # Build HL client
    client = None
    if not dry:
        from scanner.v6.paper_isolation import is_paper_mode as _is_paper
        if _is_paper():
            try:
                from scanner.v6.paper_executor import PaperExecutor
                client = PaperExecutor()
                log("=== PAPER MODE — virtual positions, real prices ===")
            except ImportError:
                log("WARN: PaperExecutor not available")
        else:
            hl_key = get_env("HYPERLIQUID_SECRET_KEY") or get_env("HL_PRIVATE_KEY")
            if not hl_key:
                log("FATAL: HL_PRIVATE_KEY not set — running gate-only mode")
            else:
                try:
                    client = HLClient(hl_key, HL_MAIN_ADDRESS)
                    equity = client.get_balance()
                    log(f"Account equity: ${equity:,.2f}")
                    save_json_atomic(BUS_DIR / "portfolio.json", {
                        "updated_at":       now_iso(),
                        "account_value":    equity,
                        "strategy_version": STRATEGY_VERSION,
                    })
                    try:
                        fees = client.get_fee_rates()
                        log(f"Fee rates: taker={fees['taker']*100:.3f}% maker={fees['maker']*100:.3f}%")
                    except Exception as e:
                        log(f"WARN: fee rate query failed: {e}")
                    try:
                        rl = client.get_rate_limit()
                        log(f"Rate limit: {rl['used']}/{rl['cap']} used")
                    except Exception as e:
                        log(f"WARN: rate limit query failed: {e}")
                    # Startup reconciliation
                    local_pos = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
                    startup_flag = BUS_DIR / ".controller_started"
                    if not local_pos and not startup_flag.exists():
                        log("⚠️  STARTUP: positions.json empty — reconciling from HL")
                        send_alert("⚠️ V6 Controller startup: positions.json was empty. Reconciling from HL.")
                        startup_flag.write_text(now_iso())
                    _reconcile_positions(client)
                    ctrl._last_reconcile_time = time.time()
                except Exception as exc:
                    log(f"WARN: HL client init failed: {exc} — gate-only mode")

    elif dry:
        # Dry mode: we still need a client for price queries
        hl_key = get_env("HYPERLIQUID_SECRET_KEY") or get_env("HL_PRIVATE_KEY")
        if hl_key:
            try:
                client = HLClient(hl_key, HL_MAIN_ADDRESS)
                log("Dry mode client ready (price queries only)")
            except Exception as exc:
                log(f"WARN: dry client init failed: {exc}")

    run_once(client, dry, controller=ctrl)

    if loop:
        last_meta_refresh    = time.time()
        META_REFRESH_INTERVAL = 600  # 10 min

        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                # Refresh HL metadata periodically
                if time.time() - last_meta_refresh >= META_REFRESH_INTERVAL:
                    load_hl_meta()
                    last_meta_refresh = time.time()

                # Update balance every cycle
                if client and not dry:
                    try:
                        equity = client.get_balance()
                        save_json_atomic(BUS_DIR / "portfolio.json", {
                            "updated_at":       now_iso(),
                            "account_value":    equity,
                            "strategy_version": STRATEGY_VERSION,
                        })
                    except Exception as e:
                        log(f"🚨 Balance fetch FAILED: {e} — skipping cycle")
                        send_alert(f"🚨 HL API DOWN: get_balance failed: {e}")
                        continue

                # Periodic reconciliation (timer-based, not every cycle)
                ctrl.maybe_reconcile(client if not dry else None)

                run_once(client, dry, controller=ctrl)
            except Exception as exc:
                log(f"ERROR in cycle: {exc}")


if __name__ == "__main__":
    main()

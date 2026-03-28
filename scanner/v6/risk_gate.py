"""
Risk gate — all 9 risk checks, strategy params, halt logic.

Depends on: ctrl_util, trade_logger, bus_io, config, strategy_loader.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scanner.v6.config as _cfg
from scanner.v6.ctrl_util import log, now_iso, load_json
from scanner.v6.trade_logger import log_decision
from scanner.v6.bus_io import save_json_locked
from scanner.v6.strategy_loader import StrategyConfig, get_active_strategy


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
        "peak_equity":       _cfg.CAPITAL,
        "drawdown_pct":      0.0,
    }
    risk = load_json(_cfg.RISK_FILE, default)
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
    save_json_locked(_cfg.RISK_FILE, risk)


def get_equity() -> float:
    portfolio_file = _cfg.BUS_DIR / "portfolio.json"
    if portfolio_file.exists():
        try:
            p = json.loads(portfolio_file.read_text())
            equity = p.get("account_value") or p.get("equity_usd")
            if equity:
                return float(equity)
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            log(f"WARN: get_equity failed reading portfolio.json: {e}")
    return _cfg.CAPITAL


def check_halt(risk: dict) -> tuple[bool, str]:
    # Stop failure circuit breaker — blocks new entries until resolved
    if risk.get("stop_failure_halt"):
        return True, f"stop_failure_halt: {risk.get('stop_failure_coin', '?')} stop could not be placed"
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
        except (ValueError, TypeError, KeyError) as e:
            log(f"WARN: halt_until parsing failed: {e}")
    return True, risk.get("halt_reason", "unknown")


def _get_current_regime() -> str:
    for candidate in [
        _cfg.BUS_DIR / "market_regimes.json",
        _cfg.BUS_DIR.parent.parent / "bus" / "regimes.json",
    ]:
        if candidate.exists():
            try:
                data = load_json(candidate, {})
                return data.get("regime", data.get("current", "unknown"))
            except (json.JSONDecodeError, OSError) as e:
                log(f"WARN: failed to read regime from {candidate}: {e}")
    return "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY PARAMS (unified accessor — YAML or config.py fallback)
# ══════════════════════════════════════════════════════════════════════════════

class _StrategyParams:
    """Unified accessor for risk params — strategy YAML when active, config.py fallback."""

    def __init__(self, strategy: StrategyConfig | None, equity: float):
        self._strategy = strategy
        self._equity   = equity
        self._dyn      = _cfg.get_dynamic_limits(equity)

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
# RISK CHECKS (all 9 — the gate)
# ══════════════════════════════════════════════════════════════════════════════

def approve_entry(
    entry: dict,
    positions: list,
    risk: dict,
    equity: float,
    params: _StrategyParams,
    controller=None,
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
            portfolio = load_json(_cfg.BUS_DIR / "portfolio.json", {})
            price = float(portfolio.get("last_price", {}).get(entry.get("coin", ""), 0))
        except (ValueError, TypeError, KeyError) as e:
            log(f"WARN: failed to read price for decision log: {e}")
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
    controller=None,
) -> tuple[bool, str]:
    """Inner logic for approve_entry — all 9 risk checks."""
    coin      = entry.get("coin", "")
    direction = entry.get("direction", "LONG")

    # ── INPUT VALIDATION ────────────────────────────────────────────────────────
    if not coin or not isinstance(coin, str):
        return False, f"invalid_coin: empty or non-string ({coin!r})"
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return False, f"invalid_direction: {direction!r} (must be LONG or SHORT)"
    if equity <= 0:
        return False, f"invalid_equity: {equity} (must be positive)"

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
        limit = _cfg.get_dynamic_limits(equity)["daily_loss_limit"]
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
            else _cfg.get_dynamic_limits(equity)["min_position_usd"]
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
    peak      = risk.get("peak_equity", _cfg.CAPITAL)
    dyn_floor = max(_cfg.CAPITAL * _cfg.CAPITAL_FLOOR_PCT, peak * _cfg.CAPITAL_FLOOR_PCT)
    if equity < dyn_floor:
        return False, f"capital_floor: equity=${equity:.0f} < ${dyn_floor:.0f}"

    # ── Per-coin duplicate ────────────────────────────────────────────────────
    coin_count = sum(1 for p in open_positions if p.get("coin") == coin)
    if coin_count >= _cfg.MAX_PER_COIN:
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
        except (ValueError, TypeError) as e:
            log(f"WARN: failed to parse entry_time for {pos.get('coin', '?')}: {e}")
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

#!/usr/bin/env python3
"""
V6 Controller — THE GATE between Monitor signals and Executor hands.

Architecture:
    EVALUATOR → MONITOR → [CONTROLLER] → EXECUTOR → IMMUNE

The controller is the single coordination layer that:
  1. Loads the active strategy config from YAML
  2. Runs all risk checks (IF — controller decides)
  3. Injects strategy params into approved entries (HOW — executor decides)
  4. Delegates execution to executor.open_trade / executor.close_trade
  5. Manages max_hold_hours time-exits each cycle

Risk checks (in order, all 9 from spec):
  1. max_positions         → reject ENTRY if at limit
  2. max_daily_loss_pct    → circuit breaker, stop all entries
  3. reserve_pct           → ensure equity × reserve_pct stays uninvested
  4. max_hold_hours        → force EXIT when hold time exceeded (checked each cycle)
  5. entry_end_action      → hold or close when signal disappears (ENTRY_END events)
  6. consensus_threshold   → reject if entry's consensus layers < threshold
  7. min_regime            → reject if current regime not in allowed list
  8. position_size_pct     → passed to executor for sizing
  9. stop_loss_pct         → passed to executor for stop placement

Fallback: when no strategy YAML is active → config.py constants used.

Bus files:
  Reads:  bus/entries.json    (ENTRY signals from monitor/evaluator)
          bus/exits.json      (EXIT signals from monitor)
          bus/positions.json  (open positions)
          bus/risk.json       (risk state — daily loss, halts)
          bus/active_strategy.json  (which strategy is running)
  Writes: bus/approved.json   (risk-cleared entries for executor)
          bus/exits.json      (adds time-exit signals)
          bus/risk.json       (updated risk state)

Usage:
  python scanner/v6/controller.py           # single run
  python scanner/v6/controller.py --loop    # continuous 5s cycle (replaces risk_guard + executor)
  python scanner/v6/controller.py --dry     # paper/dry-run mode
  python scanner/v6/controller.py --status  # print current strategy + risk state
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.bus_io import load_json_locked, save_json_locked
from scanner.v6.config import (
    BUS_DIR, ENTRIES_FILE, APPROVED_FILE, POSITIONS_FILE, RISK_FILE,
    HEARTBEAT_FILE, EXITS_FILE, CAPITAL, CAPITAL_FLOOR_PCT, MAX_PER_COIN,
    get_dynamic_limits,
)
from scanner.v6.strategy_loader import StrategyConfig, get_active_strategy

# ── Optional executor import (hands-off if executor unavailable) ─────────────
try:
    from scanner.v6.executor import (
        HLClient, open_trade, close_trade, load_hl_meta, _reconcile_positions,
        compute_size_usd, get_env,
    )
    _EXECUTOR_AVAILABLE = True
except Exception as _exec_import_err:
    _EXECUTOR_AVAILABLE = False
    _exec_import_err_msg = str(_exec_import_err)

# ── Supabase telemetry — never blocks trading ────────────────────────────────
try:
    from supabase_bridge import bridge as _sb
except Exception:
    _sb = None

CYCLE_SECONDS = 5
REJECTION_LOG_FILE = BUS_DIR / "rejections.jsonl"


# ─── LOGGING ──────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [CTRL] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_rejection(coin: str, direction: str, reason: str, gate: str = "controller") -> None:
    try:
        entry = {
            "ts": now_iso(), "coin": coin, "dir": direction,
            "reason": reason, "gate": gate,
        }
        with open(REJECTION_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ─── FILE HELPERS ─────────────────────────────────────────────────────────────

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


def update_heartbeat() -> None:
    hb = load_json(HEARTBEAT_FILE, {})
    hb["controller"] = now_iso()
    save_json_atomic(HEARTBEAT_FILE, hb)


# ─── RISK STATE ───────────────────────────────────────────────────────────────

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
        "daily_loss_since":  _today_start(),
        "capital_floor_hit": False,
        "open_count":        0,
        "peak_equity":       CAPITAL,
    }
    risk = load_json(RISK_FILE, default)
    # Reset daily counters at new UTC day
    if risk.get("daily_loss_since", "")[:10] != _today_start()[:10]:
        log("Daily counters reset (new UTC day)")
        risk["daily_loss_usd"]   = 0.0
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
                risk["halted"]     = False
                risk["halt_reason"] = None
                risk["halt_until"]  = None
                return False, ""
        except Exception:
            pass
    return True, risk.get("halt_reason", "unknown")


def _get_current_regime() -> str:
    """Read current market regime from bus."""
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


# ─── STRATEGY PARAMS WITH FALLBACK ───────────────────────────────────────────

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

    # ── risk check parameters ─────────────────────────────────────────────

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
        return 0.0  # no reserve enforced without strategy

    @property
    def consensus_threshold(self) -> int:
        if self._strategy:
            return self._strategy.evaluation.consensus_threshold
        return 5   # sensible default

    @property
    def min_regime(self) -> list[str]:
        if self._strategy:
            return self._strategy.evaluation.min_regime
        return []  # no regime filter without strategy

    @property
    def directions(self) -> list[str]:
        if self._strategy:
            return [d.upper() for d in self._strategy.evaluation.directions]
        return ["LONG", "SHORT"]  # no direction filter without strategy

    @property
    def max_hold_hours(self) -> int:
        if self._strategy:
            return self._strategy.risk.max_hold_hours
        from scanner.v6.config import MIN_HOLD_MINUTES
        return 168  # 7d fallback — executor handles MIN_HOLD_MINUTES

    @property
    def entry_end_action(self) -> str:
        if self._strategy:
            return self._strategy.risk.entry_end_action
        return "hold"

    # ── execution parameters (passed through to executor) ────────────────

    @property
    def position_size_pct(self) -> float | None:
        """None = let executor compute via conviction sizer."""
        if self._strategy and self._strategy.risk.position_size_pct > 0:
            return self._strategy.risk.position_size_pct
        return None

    @property
    def stop_loss_pct(self) -> float | None:
        """None = let executor use per-coin vol-based stops."""
        if self._strategy and self._strategy.risk.stop_loss_pct > 0:
            return self._strategy.risk.stop_loss_pct
        return None

    @property
    def is_watch_only(self) -> bool:
        if self._strategy:
            return self._strategy.is_watch_only()
        return False

    # ── invested capital accounting ───────────────────────────────────────

    def invested_usd(self, positions: list) -> float:
        """Total USD currently invested (sum of position size_usd)."""
        return sum(float(p.get("size_usd", 0)) for p in positions)

    def available_usd(self, positions: list) -> float:
        """Equity available for new positions after reserve and existing positions."""
        reserved   = self.reserve_usd
        invested   = self.invested_usd(positions)
        return max(0.0, self._equity - reserved - invested)


# ─── APPROVE ENTRY (the gate — all 9 checks) ─────────────────────────────────

def approve_entry(
    entry: dict,
    positions: list,
    risk: dict,
    equity: float,
    params: _StrategyParams,
) -> tuple[bool, str]:
    """
    Run all 9 risk checks against the active strategy YAML (or config.py fallback).

    Returns (approved: bool, reason: str)
    """
    coin      = entry.get("coin", "")
    direction = entry.get("direction", "LONG")

    # ── Watch-only mode ──────────────────────────────────────────────────────
    if params.is_watch_only:
        return False, "watch_mode: max_positions=0, observation only"

    # ── CHECK 1: max_positions ───────────────────────────────────────────────
    # Count non-pending, non-watch positions
    open_positions = [p for p in positions if not p.get("_pending")]
    if len(open_positions) >= params.max_positions:
        return False, (
            f"max_positions: {len(open_positions)} >= {params.max_positions} "
            f"[strategy={params.name}]"
        )

    # ── CHECK 2: max_daily_loss_pct (circuit breaker) ────────────────────────
    # Use passed `equity` (not params._equity) so callers are always consistent.
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
            f"({daily_loss_pct}% of ${equity:.0f}) "
            f"[strategy={params.name}]"
        )

    # ── CHECK 3: reserve_pct (cash reserve) ──────────────────────────────────
    # All dollar amounts derived from passed `equity` for consistency.
    if params.has_strategy:
        reserve_pct   = params._strategy.risk.reserve_pct
        reserve       = equity * reserve_pct / 100.0
        invested      = sum(float(p.get("size_usd", 0)) for p in open_positions)
        available     = max(0.0, equity - reserve - invested)
        position_size = (
            equity * (params.position_size_pct / 100.0)
            if params.position_size_pct
            else get_dynamic_limits(equity)["min_position_usd"]
        )
        if reserve > 0 and available < position_size:
            return False, (
                f"reserve_pct: available=${available:.2f} < "
                f"min_position=${position_size:.2f} "
                f"(reserve={reserve_pct}% of ${equity:.0f}) "
                f"[strategy={params.name}]"
            )

    # ── CHECK 4: max_hold_hours ───────────────────────────────────────────────
    # (enforced in check_time_exits — not an entry gate; included here for completeness)
    # Entry doesn't have a hold time yet — time exits are a separate cycle check.

    # ── CHECK 5: entry_end_action ────────────────────────────────────────────
    # Applied when processing ENTRY_END events; regular ENTRY is always ok here.

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

    # ── Checks 8 & 9 are passed through to executor (not rejection gates) ────
    # position_size_pct → injected into entry dict below
    # stop_loss_pct     → injected into entry dict below

    # ── Direction filter (bonus check from session_manager pattern) ──────────
    dir_upper = direction.upper()
    if dir_upper not in params.directions:
        return False, f"direction_filter: {direction} not in {params.directions} [strategy={params.name}]"

    # ── Capital floor (always enforced regardless of strategy) ───────────────
    peak        = risk.get("peak_equity", CAPITAL)
    dyn_floor   = max(CAPITAL * CAPITAL_FLOOR_PCT, peak * CAPITAL_FLOOR_PCT)
    if equity < dyn_floor:
        return False, f"capital_floor: equity=${equity:.0f} < ${dyn_floor:.0f}"

    # ── Per-coin duplicate prevention ────────────────────────────────────────
    coin_count = sum(1 for p in open_positions if p.get("coin") == coin)
    if coin_count >= MAX_PER_COIN:
        return False, f"max_per_coin: already {coin_count} position(s) on {coin}"

    for p in open_positions:
        if p.get("coin") == coin and p.get("direction") != direction:
            return False, f"opposing_position: already {p['direction']} on {coin}"

    return True, "ok"


def inject_strategy_params(entry: dict, params: _StrategyParams) -> dict:
    """
    Inject strategy-derived params into the entry dict before passing to executor.

    Checks 8 and 9: position_size_pct and stop_loss_pct become executor hints.
    Executor reads these from the entry dict and uses them for sizing/stop placement.
    """
    enriched = dict(entry)
    # Check 8: position_size_pct — overrides conviction sizer
    if params.position_size_pct is not None:
        enriched["strategy_size_pct"] = params.position_size_pct
    # Check 9: stop_loss_pct — overrides per-coin vol-based stop
    if params.stop_loss_pct is not None:
        enriched["stop_loss_pct"] = params.stop_loss_pct / 100.0  # executor expects decimal
    # Tag with strategy name for telemetry
    enriched["strategy_name"] = params.name
    return enriched


# ─── TIME EXIT CHECK (max_hold_hours) ────────────────────────────────────────

def check_time_exits(positions: list, params: _StrategyParams) -> list[dict]:
    """
    CHECK 4: max_hold_hours.
    Return list of exit signals for positions that have exceeded max hold time.
    """
    exits = []
    max_hours = params.max_hold_hours
    now = datetime.now(timezone.utc)

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


# ─── ENTRY_END HANDLER (entry_end_action) ────────────────────────────────────

def handle_entry_end_events(
    entry_end_signals: list,
    positions: list,
    params: _StrategyParams,
) -> list[dict]:
    """
    CHECK 5: entry_end_action.
    When the entry signal disappears (ENTRY_END), either hold or force close.

    Returns list of exit signals (empty if action=hold).
    """
    if params.entry_end_action == "hold":
        return []   # hold — keep position, signal may return

    exits = []
    pos_coins = {p["coin"] for p in positions}
    for sig in entry_end_signals:
        coin = sig.get("coin", "")
        if coin in pos_coins:
            log(
                f"  ENTRY_END → CLOSE: {coin} "
                f"(entry_end_action=close, strategy={params.name})"
            )
            exits.append({
                "coin":   coin,
                "reason": f"entry_end_action=close [strategy={params.name}]",
            })
    return exits


# ─── MAIN CYCLE ───────────────────────────────────────────────────────────────

def run_once(client=None, dry: bool = False) -> None:
    """
    One controller cycle:
      1. Load strategy + risk state
      2. Enforce time exits (max_hold_hours)
      3. Handle ENTRY_END events (entry_end_action)
      4. Run all 9 risk checks on pending entries
      5. Inject strategy params into approved entries
      6. Write approved entries + merged exits to bus
      7. (If running in integrated mode) invoke executor directly
    """
    # ── Load active strategy ──────────────────────────────────────────────────
    strategy = get_active_strategy()
    equity   = get_equity()
    params   = _StrategyParams(strategy, equity)

    if strategy:
        log(f"[STRATEGY] {strategy.display} ({strategy.tier}) | "
            f"max_pos={strategy.risk.max_positions} | "
            f"size={strategy.risk.position_size_pct}% | "
            f"stop={strategy.risk.stop_loss_pct}% | "
            f"reserve={strategy.risk.reserve_pct}% | "
            f"regime={strategy.evaluation.min_regime}")
    else:
        log("[STRATEGY] none active — using config.py fallback limits")

    # ── Load bus state ────────────────────────────────────────────────────────
    risk      = load_risk()
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    entries   = load_json(ENTRIES_FILE, {}).get("entries", [])

    # Split entries into ENTRY and ENTRY_END events
    entry_signals     = [e for e in entries if e.get("event_type", "ENTRY") == "ENTRY"]
    entry_end_signals = [e for e in entries if e.get("event_type") == "ENTRY_END"]
    if not entry_signals and entries:
        # Backwards compat: if no event_type set, treat all as ENTRY
        entry_signals = entries
        entry_end_signals = []

    risk["open_count"] = len(positions)

    # ── Track peak equity ─────────────────────────────────────────────────────
    peak = risk.get("peak_equity", CAPITAL)
    if equity > peak:
        risk["peak_equity"] = equity

    # ── Check global halt ─────────────────────────────────────────────────────
    halted, halt_reason = check_halt(risk)
    if halted:
        log(f"HALTED: {halt_reason}")
        save_risk(risk)
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": []})
        update_heartbeat()
        return

    # ── CHECK 4: Time exits (max_hold_hours) ──────────────────────────────────
    time_exit_signals = []
    if positions:
        time_exit_signals = check_time_exits(positions, params)

    # ── CHECK 5: ENTRY_END handler (entry_end_action) ─────────────────────────
    entry_end_exits = handle_entry_end_events(entry_end_signals, positions, params)

    # ── Merge all exits into bus/exits.json ───────────────────────────────────
    all_new_exits = time_exit_signals + entry_end_exits
    if all_new_exits:
        existing_exits = load_json(EXITS_FILE, {}).get("exits", [])
        # Deduplicate by coin
        merged_coins = {e["coin"] for e in existing_exits}
        for ex in all_new_exits:
            if ex["coin"] not in merged_coins:
                existing_exits.append(ex)
                merged_coins.add(ex["coin"])
        save_json_atomic(EXITS_FILE, {"updated_at": now_iso(), "exits": existing_exits})
        log(f"Added {len(all_new_exits)} exit signal(s) to bus")

    # ── Process ENTRY signals through risk gate ───────────────────────────────
    approved = []
    rejected = []

    # Snapshot positions list so we can add pending approvals to avoid
    # double-approving the same coin within one cycle
    working_positions = list(positions)

    for entry in entry_signals:
        ok, reason = approve_entry(entry, working_positions, risk, equity, params)
        if ok:
            enriched = inject_strategy_params(entry, params)
            approved.append(enriched)
            log(f"  APPROVED: {entry.get('coin')} {entry.get('direction')} "
                f"[{entry.get('signal_name', '?')}] strategy={params.name}")
            # Mark as pending so next entry in same cycle doesn't double-book
            working_positions.append({
                "coin":      entry["coin"],
                "direction": entry["direction"],
                "_pending":  True,
            })
        else:
            rejected.append((entry.get("coin"), entry.get("signal_name"), entry.get("direction", "?"), reason))

    if rejected:
        for coin, sig, direction, reason in rejected:
            log(f"  REJECTED: {coin} [{sig}] — {reason}")
            log_rejection(coin, direction, reason)

    # ── Enforce daily loss circuit breaker ────────────────────────────────────
    dyn_daily_loss = params.max_daily_loss_usd
    if risk.get("daily_loss_usd", 0.0) >= dyn_daily_loss:
        log(f"DAILY LOSS CIRCUIT BREAKER: ${risk['daily_loss_usd']:.2f} >= ${dyn_daily_loss:.2f} — halting 24h")
        risk["halted"]      = True
        risk["halt_reason"] = f"daily_loss_circuit_breaker [strategy={params.name}]"
        risk["halt_until"]  = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        approved = []   # cancel any just-approved entries

    # ── Capital floor halt ────────────────────────────────────────────────────
    dynamic_floor = peak * CAPITAL_FLOOR_PCT
    if equity < dynamic_floor:
        log(f"CAPITAL FLOOR HIT: ${equity:.0f} < ${dynamic_floor:.0f} — halting")
        risk["halted"]            = True
        risk["halt_reason"]       = f"capital_floor: ${equity:.0f} < ${dynamic_floor:.0f}"
        risk["halt_until"]        = None
        risk["capital_floor_hit"] = True
        approved = []

    # ── Write outputs ─────────────────────────────────────────────────────────
    save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso(), "entries": []})
    save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": approved})
    save_risk(risk)
    update_heartbeat()

    # ── Integrated mode: call executor directly ───────────────────────────────
    if client is not None and _EXECUTOR_AVAILABLE:
        from scanner.v6.executor import run_once as executor_run_once
        try:
            executor_run_once(client, dry)
        except Exception as exc:
            log(f"WARN: executor run_once failed: {exc}")


# ─── STATUS COMMAND ───────────────────────────────────────────────────────────

def print_status() -> None:
    strategy = get_active_strategy()
    equity   = get_equity()
    risk     = load_risk()
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    params   = _StrategyParams(strategy, equity)

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
    print(f"  Daily loss:   ${risk.get('daily_loss_usd', 0.0):.2f}")
    print(f"  Halted:       {risk.get('halted', False)} ({risk.get('halt_reason', 'n/a')})")
    print(f"  Open pos:     {len(positions)}")
    print("═══════════════════════════════════════════════════════\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    loop   = "--loop" in sys.argv
    dry    = "--dry" in sys.argv or "--paper" in sys.argv
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
               HEARTBEAT_FILE, EXITS_FILE
        BUS_DIR        = _cfg.BUS_DIR
        ENTRIES_FILE   = _cfg.ENTRIES_FILE
        APPROVED_FILE  = _cfg.APPROVED_FILE
        POSITIONS_FILE = _cfg.POSITIONS_FILE
        RISK_FILE      = _cfg.RISK_FILE
        HEARTBEAT_FILE = _cfg.HEARTBEAT_FILE
        EXITS_FILE     = _cfg.EXITS_FILE
        log("=== PAPER MODE — controller using isolated bus ===")

    BUS_DIR.mkdir(parents=True, exist_ok=True)

    # Build executor client (only in integrated --loop mode, not needed for pure gate mode)
    client = None
    if loop and _EXECUTOR_AVAILABLE and not dry:
        try:
            pk = get_env("HL_PRIVATE_KEY")
            if pk:
                from scanner.v6.executor import HL_MAIN_ADDRESS as _ADDR
                load_hl_meta()
                client = HLClient(pk, _ADDR)
                log(f"Executor client ready: {client.address[:10]}...")
            else:
                log("WARN: HL_PRIVATE_KEY not set — running gate-only mode (no execution)")
        except Exception as exc:
            log(f"WARN: could not build executor client: {exc} — gate-only mode")
    elif not _EXECUTOR_AVAILABLE:
        log(f"WARN: executor not available: {_exec_import_err_msg} — gate-only mode")

    mode_label = "DRY" if dry else "LIVE"
    log(f"=== V6 Controller starting [{mode_label}] ===")

    strategy = get_active_strategy()
    if strategy:
        log(f"Active strategy: {strategy.display} ({strategy.tier})")
    else:
        log("No active strategy — config.py fallback limits active")

    run_once(client, dry)

    if loop:
        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                run_once(client, dry)
            except Exception as exc:
                log(f"ERROR in cycle: {exc}")


if __name__ == "__main__":
    main()

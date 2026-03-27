#!/usr/bin/env python3
"""
Session Lifecycle State Machine — ZERO's session orchestrator.

Session 10: Replaces session_manager.py with a proper state machine,
typed dataclasses, result cards, near-miss retrospectives, and narrative builder.

States:
    PENDING → ACTIVE → COMPLETING → COMPLETED
    ANY → FAILED

The Session orchestrates Monitor + Controller:
  - Reads controller events to track trades
  - Reads monitor decisions for eval/reject counts
  - Persists to bus/session.json (crash recovery)
  - Appends history to bus/session_history.jsonl

Usage:
    from scanner.v6.session import SessionManager
    mgr = SessionManager(bus_dir=BUS_DIR)
    session = mgr.start_session("momentum", paper=True)
    mgr.check_session_time(session)
    result = mgr.complete_session(session)
"""

from __future__ import annotations

import json
import uuid
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from scanner.v6.strategy_loader import load_strategy, StrategyConfig

# ── Paths (defaults, overridable via bus_dir) ─────────────────────────────────
V6_DIR = Path(__file__).parent
BUS_DIR = V6_DIR / "bus"

SESSION_FILE         = BUS_DIR / "session.json"
SESSION_HISTORY_FILE = BUS_DIR / "session_history.jsonl"
NEAR_MISS_LOG_FILE   = BUS_DIR / "near_misses.jsonl"
DECISIONS_LOG_FILE   = BUS_DIR / "decisions.jsonl"
EVENTS_LOG_FILE      = BUS_DIR / "events.jsonl"
TRADES_FILE          = V6_DIR.parent.parent / "data" / "trades.jsonl"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _log(msg: str):
    ts = _now().strftime("%H:%M:%S")
    print(f"[{ts}] [SESSION] {msg}", flush=True)


def _save_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _load_jsonl(path: Path) -> list[dict]:
    results = []
    if not path.exists():
        return results
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return results


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NearMiss:
    """A signal that almost passed but didn't — retrospective detection."""
    coin: str
    actual_move_pct: float
    active_strategy: str
    would_pass: list[str]
    failing_layers: list[str]
    estimated_gain_pct: float
    timestamp: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class TimelineEvent:
    """Significant event during a session, tagged with hour offset."""
    hour: int
    event_type: str   # evaluation, entry, exit, near_miss, regime_shift
    detail: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "hour": self.hour,
            "event_type": self.event_type,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass
class SessionCost:
    """B5: Session resource cost tracking."""
    total_cycles: int = 0
    total_evaluations: int = 0
    hl_api_calls: int = 0
    hl_api_calls_by_type: dict = field(default_factory=dict)
    cpu_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    decision_log_bytes: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SessionResult:
    """Final result card produced when a session completes."""
    session_id: str
    strategy: str
    strategy_display: str
    duration_actual: timedelta
    paper: bool
    trade_count: int
    wins: int
    losses: int
    best_trade: float
    worst_trade: float
    total_pnl_usd: float
    total_pnl_pct: float
    max_drawdown_pct: float
    eval_count: int
    reject_count: int
    rejection_rate_pct: float
    near_misses: list[NearMiss]
    timeline: list[TimelineEvent]
    narrative_text: str
    started_at: str
    completed_at: str
    coins_in_scope: int
    ended_early: bool = False
    # B2: Slippage aggregates
    avg_slippage_bps: float = 0.0
    max_slippage_bps: float = 0.0
    avg_signal_to_fill_ms: float = 0.0
    fills_by_order_type: dict = field(default_factory=dict)
    # B3: Layer accuracy
    layer_accuracy: dict = field(default_factory=dict)
    # B4: Execution metrics aggregates
    avg_cycle_duration_ms: float = 0.0
    max_cycle_duration_ms: float = 0.0
    total_cycles: int = 0
    total_evaluations: int = 0
    data_stale_cycles: int = 0
    # B5: Cost
    session_cost: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "strategy": self.strategy,
            "strategy_display": self.strategy_display,
            "duration_actual_s": self.duration_actual.total_seconds(),
            "paper": self.paper,
            "trade_count": self.trade_count,
            "wins": self.wins,
            "losses": self.losses,
            "best_trade": round(self.best_trade, 2),
            "worst_trade": round(self.worst_trade, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "eval_count": self.eval_count,
            "reject_count": self.reject_count,
            "rejection_rate_pct": round(self.rejection_rate_pct, 1),
            "near_misses": [nm.to_dict() for nm in self.near_misses],
            "timeline": [te.to_dict() for te in self.timeline],
            "narrative_text": self.narrative_text,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "coins_in_scope": self.coins_in_scope,
            "ended_early": self.ended_early,
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "max_slippage_bps": round(self.max_slippage_bps, 2),
            "avg_signal_to_fill_ms": round(self.avg_signal_to_fill_ms, 1),
            "fills_by_order_type": self.fills_by_order_type,
            "layer_accuracy": self.layer_accuracy,
            "avg_cycle_duration_ms": round(self.avg_cycle_duration_ms, 1),
            "max_cycle_duration_ms": round(self.max_cycle_duration_ms, 1),
            "total_cycles": self.total_cycles,
            "total_evaluations": self.total_evaluations,
            "data_stale_cycles": self.data_stale_cycles,
            "session_cost": self.session_cost,
        }


@dataclass
class Session:
    """The live session object — tracks state through the lifecycle."""
    id: str
    strategy: str
    strategy_config: StrategyConfig
    state: str   # pending, active, completing, completed, failed
    started_at: datetime
    ends_at: datetime
    paper: bool
    trades: list = field(default_factory=list)
    active_positions: list = field(default_factory=list)
    eval_count: int = 0
    reject_count: int = 0
    near_misses: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    events: list = field(default_factory=list)
    result: Optional[SessionResult] = None
    error: str = ""

    def duration_so_far(self) -> timedelta:
        return _now() - self.started_at

    def hours_elapsed(self) -> float:
        return self.duration_so_far().total_seconds() / 3600

    def current_hour(self) -> int:
        return int(self.hours_elapsed())

    def is_expired(self) -> bool:
        return _now() >= self.ends_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "strategy": self.strategy,
            "state": self.state,
            "started_at": self.started_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "paper": self.paper,
            "trades": self.trades,
            "active_positions": self.active_positions,
            "eval_count": self.eval_count,
            "reject_count": self.reject_count,
            "near_misses": [nm.to_dict() if hasattr(nm, "to_dict") else nm for nm in self.near_misses],
            "timeline": [te.to_dict() if hasattr(te, "to_dict") else te for te in self.timeline],
            "events": self.events,
            "error": self.error,
        }


# ══════════════════════════════════════════════════════════════════════════════
# NARRATIVE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_narrative(session: Session) -> str:
    """
    Build human-readable session narrative from timeline + stats.

    This is the MARKETING — makes the session story compelling:
      "48-hour Momentum session. 2,880 cycles. 143,598 rejected.
       Hour 8: SOL emerged. Entered long at $148.20. conviction 0.85.
       Hour 18: trailing triggered. +$2.90 (+1.96%).
       Result: 1 trade. 1 win. +$2.90.
       Near miss: AVAX +6.8%. Degen would have caught it."
    """
    cfg = session.strategy_config
    duration_h = cfg.session.duration_hours
    display = cfg.display

    parts = []

    # Opening line
    parts.append(f"{duration_h}-hour {display} session.")

    # Eval stats
    if session.eval_count > 0:
        rejection_rate = session.reject_count / session.eval_count * 100
        parts.append(
            f"{session.eval_count:,} evaluations. "
            f"{session.reject_count:,} rejected ({rejection_rate:.1f}% selectivity)."
        )
    else:
        parts.append("0 evaluations.")

    # Timeline events — hour by hour
    for event in session.timeline:
        if isinstance(event, TimelineEvent):
            h = event.hour
            etype = event.event_type
            detail = event.detail
            data = event.data
        elif isinstance(event, dict):
            h = event.get("hour", 0)
            etype = event.get("event_type", "")
            detail = event.get("detail", "")
            data = event.get("data", {})
        else:
            continue

        if etype == "entry":
            coin = data.get("coin", "")
            price = data.get("price", 0)
            direction = data.get("direction", "long")
            conviction = data.get("conviction", 0)
            parts.append(
                f"Hour {h}: {coin} emerged. "
                f"Entered {direction.lower()} at ${price:,.2f}."
                + (f" conviction {conviction:.2f}." if conviction else "")
            )
        elif etype == "exit":
            coin = data.get("coin", "")
            pnl = data.get("pnl_usd", 0)
            pnl_pct = data.get("pnl_pct", 0)
            reason = data.get("reason", "")
            parts.append(
                f"Hour {h}: {reason or 'exit'} on {coin}. "
                f"{'+' if pnl >= 0 else ''}${pnl:.2f} "
                f"({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%)."
            )
        elif etype == "near_miss":
            coin = data.get("coin", "")
            move = data.get("actual_move_pct", 0)
            alt = data.get("would_pass", [])
            alt_str = ", ".join(alt) if alt else "another strategy"
            parts.append(
                f"Hour {h}: near miss — {coin} moved +{move:.1f}%. "
                f"{alt_str} would have caught it."
            )
        elif etype == "regime_shift":
            parts.append(f"Hour {h}: regime shift — {detail}.")
        elif detail:
            parts.append(f"Hour {h}: {detail}.")

    # Trade summary
    trades = session.trades
    wins = sum(1 for t in trades if _trade_won(t))
    losses = len(trades) - wins
    total_pnl = sum(_trade_pnl(t) for t in trades)

    if trades:
        parts.append(
            f"Result: {len(trades)} trade{'s' if len(trades) != 1 else ''}. "
            f"{wins} win{'s' if wins != 1 else ''}. "
            f"{losses} loss{'es' if losses != 1 else ''}. "
            f"{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}."
        )
    else:
        parts.append("Result: 0 trades. Pure observation session.")

    # Near misses summary
    near_misses = session.near_misses
    if near_misses:
        for nm in near_misses[:3]:  # top 3
            if isinstance(nm, NearMiss):
                parts.append(
                    f"Near miss: {nm.coin} +{nm.actual_move_pct:.1f}%. "
                    f"{', '.join(nm.would_pass)} would have caught it."
                )
            elif isinstance(nm, dict):
                parts.append(
                    f"Near miss: {nm.get('coin', '?')} +{nm.get('actual_move_pct', 0):.1f}%."
                )

    return " ".join(parts)


def _trade_won(trade) -> bool:
    if isinstance(trade, dict):
        return trade.get("won", trade.get("pnl_usd", 0) > 0)
    if hasattr(trade, "won"):
        return trade.won
    return False


def _trade_pnl(trade) -> float:
    if isinstance(trade, dict):
        return float(trade.get("pnl_usd", 0))
    if hasattr(trade, "pnl_usd"):
        return float(trade.pnl_usd)
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# NEAR MISS RETROSPECTIVE
# ══════════════════════════════════════════════════════════════════════════════

def detect_near_misses(session: Session, bus_dir: Path) -> list[NearMiss]:
    """
    Retrospective near-miss detection.

    Reads near_misses.jsonl from the bus, filters to this session's timeframe,
    and converts to typed NearMiss objects.
    """
    nm_file = bus_dir / "near_misses.jsonl"
    raw_misses = _load_jsonl(nm_file)
    if not raw_misses:
        return []

    session_start = session.started_at.isoformat()
    results = []
    for entry in raw_misses:
        ts = entry.get("timestamp", entry.get("ts", ""))
        if ts < session_start:
            continue
        nm = NearMiss(
            coin=entry.get("coin", ""),
            actual_move_pct=float(entry.get("actual_move_pct", entry.get("move_pct", 0))),
            active_strategy=entry.get("active_strategy", session.strategy),
            would_pass=entry.get("would_pass_strategies", entry.get("would_pass", [])),
            failing_layers=entry.get("failing_layers", []),
            estimated_gain_pct=float(entry.get("estimated_gain_pct", entry.get("actual_move_pct", 0))),
            timestamp=ts,
        )
        results.append(nm)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# TIMELINE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_timeline_from_events(session: Session, bus_dir: Path) -> list[TimelineEvent]:
    """
    Build timeline from decision log + events log.

    Scans decisions.jsonl and events.jsonl for entries/exits/near_misses/regime_shifts
    that occurred during this session's window.
    """
    start_iso = session.started_at.isoformat()
    timeline: list[TimelineEvent] = []

    # 1. Read events log
    events = _load_jsonl(bus_dir / "events.jsonl")
    for ev in events:
        ts = ev.get("ts", "")
        if ts < start_iso:
            continue
        etype = ev.get("type", "")
        hour = _hour_offset(session.started_at, ts)

        if etype in ("TRADE_OPENED", "ENTRY_EXECUTED"):
            timeline.append(TimelineEvent(
                hour=hour,
                event_type="entry",
                detail=f"{ev.get('coin', '')} {ev.get('direction', '')}",
                data=ev,
            ))
        elif etype in ("TRADE_CLOSED", "EXIT_EXECUTED"):
            timeline.append(TimelineEvent(
                hour=hour,
                event_type="exit",
                detail=f"{ev.get('coin', '')} closed",
                data=ev,
            ))
        elif etype == "NEAR_MISS":
            timeline.append(TimelineEvent(
                hour=hour,
                event_type="near_miss",
                detail=f"{ev.get('coin', '')} near miss",
                data=ev,
            ))
        elif etype == "REGIME_SHIFT":
            timeline.append(TimelineEvent(
                hour=hour,
                event_type="regime_shift",
                detail=f"regime → {ev.get('new_regime', '?')}",
                data=ev,
            ))

    # Sort by hour
    timeline.sort(key=lambda x: x.hour)
    return timeline


def _hour_offset(start: datetime, ts_str: str) -> int:
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        delta = ts - start
        return max(0, int(delta.total_seconds() / 3600))
    except (ValueError, TypeError):
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# SESSION MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class SessionManager:
    """
    Manages the session lifecycle state machine.

    ONE active session at a time. Orchestrates monitor + controller
    through the session's lifetime.
    """

    def __init__(self, bus_dir: Optional[Path] = None, strategies_dir: Optional[Path] = None):
        self.bus_dir = bus_dir or BUS_DIR
        self.strategies_dir = strategies_dir
        self._active_session: Optional[Session] = None
        self._session_file = self.bus_dir / "session.json"
        self._history_file = self.bus_dir / "session_history.jsonl"

    @property
    def active_session(self) -> Optional[Session]:
        return self._active_session

    # ── START ─────────────────────────────────────────────────────────────────

    def start_session(
        self,
        strategy_name: str,
        paper: bool = True,
    ) -> Session:
        """
        Start a new session.

        PENDING → ACTIVE. Loads YAML strategy, validates, sets timer.
        Emits SESSION_STARTED event. Returns the session.

        Raises:
            RuntimeError: if another session is already ACTIVE.
            FileNotFoundError / ValueError: if strategy is invalid.
        """
        # Block concurrent sessions
        if self._active_session is not None and self._active_session.state in ("pending", "active"):
            raise RuntimeError(
                f"Session already active: {self._active_session.id} "
                f"(strategy={self._active_session.strategy}, "
                f"state={self._active_session.state})"
            )

        # Load + validate strategy
        cfg = load_strategy(strategy_name, strategies_dir=self.strategies_dir)

        now = _now()
        session_id = str(uuid.uuid4())
        ends_at = now + timedelta(hours=cfg.session.duration_hours)

        session = Session(
            id=session_id,
            strategy=strategy_name,
            strategy_config=cfg,
            state="pending",
            started_at=now,
            ends_at=ends_at,
            paper=paper,
        )

        # PENDING → ACTIVE
        session.state = "active"

        self._active_session = session

        # Persist for crash recovery
        self._persist_session(session)

        # Emit event
        self._emit_event(session, "SESSION_STARTED", {
            "session_id": session_id,
            "strategy": strategy_name,
            "strategy_display": cfg.display,
            "paper": paper,
            "duration_hours": cfg.session.duration_hours,
            "ends_at": ends_at.isoformat(),
        })

        _log(f"Session started: {cfg.display} (id={session_id[:8]}..., "
             f"paper={paper}, ends={ends_at.strftime('%Y-%m-%d %H:%M UTC')})")

        return session

    # ── CHECK TIME ────────────────────────────────────────────────────────────

    def check_session_time(self, session: Session) -> bool:
        """
        Check if session has expired. If so, trigger completing.
        Returns True if session time is up.
        """
        if session.state != "active":
            return False
        if session.is_expired():
            _log(f"Session timer expired: {session.id[:8]}...")
            session.state = "completing"
            return True
        return False

    # ── COMPLETE ──────────────────────────────────────────────────────────────

    def complete_session(self, session: Session) -> SessionResult:
        """
        Complete a session: close positions, run retrospective, build narrative.

        ACTIVE/COMPLETING → COMPLETED.
        Emits SESSION_COMPLETED with full result.
        """
        if session.state not in ("active", "completing"):
            raise RuntimeError(f"Cannot complete session in state '{session.state}'")

        session.state = "completing"

        # Run retrospective near-miss detection
        detected_nms = detect_near_misses(session, self.bus_dir)
        session.near_misses = detected_nms

        # Build timeline from bus logs
        timeline = build_timeline_from_events(session, self.bus_dir)
        # Merge with any timeline events already recorded on the session
        existing_hours = {(te.hour, te.event_type) for te in timeline}
        for te in session.timeline:
            if isinstance(te, TimelineEvent):
                key = (te.hour, te.event_type)
                if key not in existing_hours:
                    timeline.append(te)
            elif isinstance(te, dict):
                key = (te.get("hour", 0), te.get("event_type", ""))
                if key not in existing_hours:
                    timeline.append(TimelineEvent(
                        hour=te.get("hour", 0),
                        event_type=te.get("event_type", ""),
                        detail=te.get("detail", ""),
                        data=te.get("data", {}),
                    ))
        timeline.sort(key=lambda x: x.hour)
        session.timeline = timeline

        # Build narrative
        narrative = build_narrative(session)

        # Compute result fields
        completed_at = _now_iso()
        duration_actual = _now() - session.started_at

        trades = session.trades
        wins = sum(1 for t in trades if _trade_won(t))
        losses = len(trades) - wins
        pnls = [_trade_pnl(t) for t in trades]
        total_pnl = sum(pnls)
        best_trade = max(pnls) if pnls else 0.0
        worst_trade = min(pnls) if pnls else 0.0

        # Max drawdown from cumulative PnL
        max_drawdown_pct = _compute_max_drawdown(pnls)

        # PnL percentage (against a reference equity of trades total notional)
        total_notional = sum(
            float(t.get("size_usd", 0) if isinstance(t, dict) else getattr(t, "size_usd", 0))
            for t in trades
        )
        total_pnl_pct = (total_pnl / total_notional * 100) if total_notional > 0 else 0.0

        # Rejection rate
        rejection_rate = (
            session.reject_count / session.eval_count * 100
            if session.eval_count > 0 else 0.0
        )

        # Coins in scope
        scope_map = {
            "top_20": 20, "top_50": 50, "top_100": 100, "top_200": 200,
        }
        coins_in_scope = scope_map.get(session.strategy_config.evaluation.scope, 20)

        result = SessionResult(
            session_id=session.id,
            strategy=session.strategy,
            strategy_display=session.strategy_config.display,
            duration_actual=duration_actual,
            paper=session.paper,
            trade_count=len(trades),
            wins=wins,
            losses=losses,
            best_trade=best_trade,
            worst_trade=worst_trade,
            total_pnl_usd=total_pnl,
            total_pnl_pct=total_pnl_pct,
            max_drawdown_pct=max_drawdown_pct,
            eval_count=session.eval_count,
            reject_count=session.reject_count,
            rejection_rate_pct=rejection_rate,
            near_misses=detected_nms,
            timeline=timeline,
            narrative_text=narrative,
            started_at=session.started_at.isoformat(),
            completed_at=completed_at,
            coins_in_scope=coins_in_scope,
        )

        session.state = "completed"
        session.result = result

        # Persist final state
        self._persist_session(session)
        self._append_history(session, result)

        # Emit event
        self._emit_event(session, "SESSION_COMPLETED", result.to_dict())

        _log(f"Session completed: {session.strategy_config.display} | "
             f"trades={len(trades)} | pnl=${total_pnl:.2f} | "
             f"evals={session.eval_count} | rejects={session.reject_count}")

        # Clear active session
        self._active_session = None

        return result

    # ── END EARLY ─────────────────────────────────────────────────────────────

    def end_session_early(self, session: Session) -> SessionResult:
        """
        End a session before its timer expires. Same as complete but marked ended_early=True.
        """
        result = self.complete_session(session)
        result.ended_early = True
        # Re-persist with ended_early flag
        self._persist_session(session)
        return result

    # ── FAIL ──────────────────────────────────────────────────────────────────

    def fail_session(self, session: Session, error: str) -> None:
        """
        Force-fail a session. ANY → FAILED.
        Force closes all positions (via event). Emits SESSION_FAILED. Alerts.
        """
        session.state = "failed"
        session.error = error

        self._persist_session(session)

        self._emit_event(session, "SESSION_FAILED", {
            "session_id": session.id,
            "strategy": session.strategy,
            "error": error,
        })

        _log(f"SESSION FAILED: {session.id[:8]}... — {error}")

        # Clear active session
        self._active_session = None

    # ── TRACKING (called by controller integration) ──────────────────────────

    def record_trade(self, session: Session, trade: dict) -> None:
        """Record a completed trade on the session."""
        session.trades.append(trade)
        hour = session.current_hour()
        pnl = _trade_pnl(trade)
        coin = trade.get("coin", "")
        session.timeline.append(TimelineEvent(
            hour=hour,
            event_type="exit" if trade.get("exit_price") else "entry",
            detail=f"{coin} {'exit' if trade.get('exit_price') else 'entry'}",
            data=trade,
        ))
        self._persist_session(session)

    def record_evaluation(self, session: Session, passed: bool) -> None:
        """Record an evaluation cycle (passed or rejected)."""
        session.eval_count += 1
        if not passed:
            session.reject_count += 1

    def record_entry(self, session: Session, entry_data: dict) -> None:
        """Record a trade entry on the timeline."""
        session.timeline.append(TimelineEvent(
            hour=session.current_hour(),
            event_type="entry",
            detail=f"{entry_data.get('coin', '')} {entry_data.get('direction', '')}",
            data=entry_data,
        ))

    # ── PERSISTENCE ──────────────────────────────────────────────────────────

    def _persist_session(self, session: Session) -> None:
        """Save session state to bus/session.json for crash recovery."""
        _save_atomic(self._session_file, session.to_dict())

    def _append_history(self, session: Session, result: SessionResult) -> None:
        """Append completed session to history file."""
        _append_jsonl(self._history_file, {
            "session_id": session.id,
            "strategy": session.strategy,
            "strategy_display": session.strategy_config.display,
            "paper": session.paper,
            "started_at": session.started_at.isoformat(),
            "completed_at": result.completed_at,
            "trade_count": result.trade_count,
            "wins": result.wins,
            "losses": result.losses,
            "total_pnl_usd": round(result.total_pnl_usd, 2),
            "eval_count": result.eval_count,
            "reject_count": result.reject_count,
            "rejection_rate_pct": round(result.rejection_rate_pct, 1),
            "narrative": result.narrative_text,
        })

    def _emit_event(self, session: Session, event_type: str, data: dict) -> None:
        """Emit an event to the session's event list + events.jsonl."""
        event = {"type": event_type, "ts": _now_iso(), **data}
        session.events.append(event)
        try:
            _append_jsonl(self.bus_dir / "events.jsonl", event)
        except Exception:
            pass

    # ── PUBLIC API METHODS ────────────────────────────────────────────────

    def get_history(self, limit: int = 20) -> list[dict]:
        """Read session history from JSONL file."""
        if not self._history_file.exists():
            return []
        entries = []
        try:
            for line in self._history_file.read_text().strip().split("\n"):
                if line.strip():
                    entries.append(json.loads(line))
        except Exception:
            pass
        return list(reversed(entries[-limit:]))

    def get_result(self, session_id: str) -> dict | None:
        """Get a specific session result by ID."""
        for entry in self.get_history(limit=100):
            if entry.get("session_id") == session_id:
                return entry
        return None

    def get_status(self) -> dict:
        """Get current session status."""
        session = self.active_session
        if session is None:
            return {"active": False, "session": None}
        return {
            "active": True,
            "session": session.to_dict(),
        }

    def queue_session(self, strategy_name: str, paper: bool = True) -> dict:
        """Queue a session to start after the current one ends."""
        queue_file = self.bus_dir / "session_queue.json"
        queued = {
            "strategy": strategy_name,
            "paper": paper,
            "queued_at": _now_iso(),
        }
        _save_atomic(queue_file, queued)
        return queued


# ── Utility ──────────────────────────────────────────────────────────────────

def _compute_max_drawdown(pnls: list[float]) -> float:
    """Max drawdown percentage from a sequence of trade PnLs."""
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    # Express as pct of peak (if peak > 0)
    if peak > 0:
        return max_dd / peak * 100
    return 0.0

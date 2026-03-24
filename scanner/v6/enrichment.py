"""
enrichment.py — Data Intelligence Pipeline

Captures full context at the moment of every decision, trade entry, and trade close.
Writes to Supabase decisions_enriched and trades_enriched tables.
Also maintains in-memory MAE/MFE trackers for open positions.

Never blocks trading — all writes are fire-and-forget with exception handling.
"""

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

# ─── CONFIG ───────────────────────────────────────────────────────────────────

AGENT_UUID = os.environ.get("AGENT_UUID", "4802c6f8-f862-42f1-b248-45679e1517e7")
SUPABASE_URL = os.environ.get("SUPABASE_URL", os.environ.get("NEXT_PUBLIC_SUPABASE_URL", ""))
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SCANNER_DIR = Path(__file__).parent

# Load from .env if not in environment
_env_file = Path.home() / ".config" / "openclaw" / ".env"
if (not SUPABASE_URL or not SUPABASE_KEY) and _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k == "SUPABASE_URL" and not SUPABASE_URL:
            SUPABASE_URL = v
        elif k == "NEXT_PUBLIC_SUPABASE_URL" and not SUPABASE_URL:
            SUPABASE_URL = v
        elif k == "SUPABASE_SERVICE_KEY" and not SUPABASE_KEY:
            SUPABASE_KEY = v


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [enrichment] [{ts}] {msg}", flush=True)


# ─── SUPABASE WRITE ──────────────────────────────────────────────────────────

def _supabase_insert(table: str, data: dict) -> bool:
    """Insert a row into Supabase. Returns True on success."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201, 204)
    except Exception as e:
        _log(f"write failed ({table}): {e}")
        return False


def _supabase_update(table: str, filters: dict, data: dict) -> bool:
    """Update rows in Supabase matching filters."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    query = "&".join(f"{k}=eq.{v}" for k, v in filters.items())
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="PATCH")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        _log(f"update failed ({table}): {e}")
        return False


def _async_write(table: str, data: dict):
    """Fire-and-forget insert on a background thread."""
    threading.Thread(target=_supabase_insert, args=(table, data), daemon=True).start()


def _async_update(table: str, filters: dict, data: dict):
    """Fire-and-forget update on a background thread."""
    threading.Thread(target=_supabase_update, args=(table, filters, data), daemon=True).start()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── MAE/MFE TRACKER ─────────────────────────────────────────────────────────
# Track worst and best unrealized P&L during each position's lifetime.
# Called by the executor on every cycle.

class PositionTracker:
    """Tracks MAE/MFE and regime changes for an open position."""
    __slots__ = (
        "coin", "direction", "entry_price", "entry_time",
        "mae", "mfe", "mae_pct", "mfe_pct",
        "regime_changes", "last_regime",
        "immune_checks", "immune_alerts",
        "stop_moved", "entry_regime",
    )

    def __init__(self, coin: str, direction: str, entry_price: float,
                 entry_time: str, entry_regime: str = ""):
        self.coin = coin
        self.direction = direction
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.mae = 0.0          # worst unrealized loss (negative)
        self.mfe = 0.0          # best unrealized profit (positive)
        self.mae_pct = 0.0
        self.mfe_pct = 0.0
        self.regime_changes = 0
        self.last_regime = entry_regime
        self.entry_regime = entry_regime
        self.immune_checks = 0
        self.immune_alerts = 0
        self.stop_moved = False

    def update(self, current_price: float, current_regime: str = ""):
        """Update MAE/MFE with current price."""
        if self.entry_price <= 0:
            return
        if self.direction == "LONG":
            pnl_pct = (current_price - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - current_price) / self.entry_price

        if pnl_pct < self.mae_pct:
            self.mae_pct = pnl_pct
        if pnl_pct > self.mfe_pct:
            self.mfe_pct = pnl_pct

        # Track regime changes
        if current_regime and current_regime != self.last_regime:
            self.regime_changes += 1
            self.last_regime = current_regime

    def record_immune_check(self, had_alert: bool = False):
        self.immune_checks += 1
        if had_alert:
            self.immune_alerts += 1

    def record_stop_moved(self):
        self.stop_moved = True


# Global tracker registry
_trackers: dict[str, PositionTracker] = {}
_tracker_lock = threading.Lock()


def create_tracker(coin: str, direction: str, entry_price: float,
                   entry_time: str, entry_regime: str = "") -> PositionTracker:
    """Create a new position tracker."""
    key = f"{coin}_{direction}"
    tracker = PositionTracker(coin, direction, entry_price, entry_time, entry_regime)
    with _tracker_lock:
        _trackers[key] = tracker
    return tracker


def get_tracker(coin: str, direction: str = "") -> PositionTracker | None:
    """Get tracker for a position. If direction unknown, try both."""
    with _tracker_lock:
        if direction:
            return _trackers.get(f"{coin}_{direction}")
        return _trackers.get(f"{coin}_LONG") or _trackers.get(f"{coin}_SHORT")


def remove_tracker(coin: str, direction: str = "") -> PositionTracker | None:
    """Remove and return tracker for a closed position."""
    with _tracker_lock:
        if direction:
            return _trackers.pop(f"{coin}_{direction}", None)
        return _trackers.pop(f"{coin}_LONG", None) or _trackers.pop(f"{coin}_SHORT", None)


def update_all_trackers(prices: dict[str, float], regime_map: dict[str, str] | None = None):
    """Update all trackers with current prices. Called every executor cycle."""
    with _tracker_lock:
        for key, tracker in _trackers.items():
            price = prices.get(tracker.coin)
            if price:
                regime = (regime_map or {}).get(tracker.coin, "")
                tracker.update(price, regime)


# ─── ENRICHED DECISION ───────────────────────────────────────────────────────

def record_enriched_decision(
    coin: str,
    direction: str,
    decision: str,
    reason: str,
    signal_data: dict | None = None,
    market_data: dict | None = None,
    portfolio_state: dict | None = None,
    gate_results: dict | None = None,
    eval_duration_ms: int | None = None,
):
    """Record a fully enriched decision to Supabase."""
    sd = signal_data or {}
    md = market_data or {}
    ps = portfolio_state or {}
    gr = gate_results or {}

    row = {
        "agent_id": AGENT_UUID,
        "timestamp": _now_iso(),
        "coin": coin,
        "direction": direction.lower(),
        "decision": decision,
        "reason": reason,

        # Signal context
        "regime": sd.get("regime"),
        "hurst": sd.get("hurst"),
        "dfa": sd.get("dfa"),
        "signal_quality": sd.get("quality_tier"),
        "signal_sharpe": sd.get("signal_sharpe"),
        "assembled_sharpe": sd.get("assembled_sharpe"),
        "signal_direction": sd.get("direction"),

        # Market context
        "price": md.get("price"),
        "funding_rate": md.get("funding_rate"),
        "book_depth_usd": md.get("book_depth"),
        "volume_24h": md.get("volume_24h"),

        # Portfolio context
        "equity": ps.get("equity"),
        "position_count": ps.get("position_count"),
        "positions_coins": ps.get("open_coins"),
        "available_capital": ps.get("available_capital"),

        # Gate results
        "gates_passed": gr.get("passed"),
        "gates_failed": gr.get("failed"),
        "gate_that_rejected": gr.get("rejected_by"),

        # Evaluation timing
        "evaluation_duration_ms": eval_duration_ms,

        # Signal mode
        "signal_mode": sd.get("signal_mode"),
    }

    # Strip None values to avoid Supabase column type issues
    row = {k: v for k, v in row.items() if v is not None}

    _async_write("decisions_enriched", row)
    _log(f"decision: {coin} {direction} {decision} (regime={sd.get('regime', '?')})")


# ─── ENRICHED TRADE OPEN ─────────────────────────────────────────────────────

def record_enriched_trade_open(
    coin: str,
    direction: str,
    entry_price: float,
    size_usd: float,
    leverage: float = 1.0,
    stop_price: float | None = None,
    stop_distance_pct: float | None = None,
    signal_data: dict | None = None,
    market_data: dict | None = None,
    portfolio_state: dict | None = None,
):
    """Record trade entry to trades_enriched and create MAE/MFE tracker."""
    sd = signal_data or {}
    md = market_data or {}
    ps = portfolio_state or {}

    now = _now_iso()
    entry_dt = datetime.now(timezone.utc)

    row = {
        "agent_id": AGENT_UUID,
        "coin": coin,
        "direction": direction.lower(),
        "status": "open",

        # Entry context
        "entry_price": entry_price,
        "entry_time": now,
        "entry_regime": sd.get("regime"),
        "entry_hurst": sd.get("hurst"),
        "entry_signal_sharpe": sd.get("signal_sharpe"),
        "entry_assembled_sharpe": sd.get("assembled_sharpe"),
        "entry_funding_rate": md.get("funding_rate"),
        "entry_book_depth_usd": md.get("book_depth"),
        "entry_equity": ps.get("equity"),
        "entry_position_count": ps.get("position_count"),
        "entry_utc_hour": entry_dt.hour,
        "entry_day_of_week": entry_dt.weekday(),
        "entry_signal_mode": sd.get("signal_mode"),

        # Position parameters
        "size_usd": size_usd,
        "leverage": leverage,
        "stop_price": stop_price,
        "stop_distance_pct": stop_distance_pct,
    }

    row = {k: v for k, v in row.items() if v is not None}

    _async_write("trades_enriched", row)

    # Create MAE/MFE tracker
    create_tracker(coin, direction, entry_price, now, sd.get("regime", ""))

    _log(f"trade open: {coin} {direction} @ ${entry_price:.4f} (regime={sd.get('regime', '?')})")


# ─── ENRICHED TRADE CLOSE ────────────────────────────────────────────────────

def record_enriched_trade_close(
    coin: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float | None = None,
    fees: float = 0.0,
    entry_time: str | None = None,
    exit_reason: str = "unknown",
    signal_data: dict | None = None,
    portfolio_state: dict | None = None,
):
    """Update trades_enriched with exit data + tracker metrics."""
    sd = signal_data or {}
    ps = portfolio_state or {}

    # Get tracker data
    tracker = remove_tracker(coin, direction)

    now = _now_iso()

    # Calculate hold duration
    hold_seconds = None
    if entry_time:
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            hold_seconds = int((datetime.now(timezone.utc) - entry_dt).total_seconds())
        except Exception:
            pass

    # Calculate pnl_pct if not provided
    if pnl_pct is None and entry_price > 0:
        if direction.upper() == "LONG":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price

    # Efficiency: how much of MFE we captured
    efficiency = None
    if tracker and tracker.mfe_pct > 0 and pnl_pct is not None:
        efficiency = pnl_pct / tracker.mfe_pct

    # Risk/reward: MFE / abs(MAE)
    risk_reward = None
    if tracker and tracker.mae_pct < 0 and tracker.mfe_pct > 0:
        risk_reward = tracker.mfe_pct / abs(tracker.mae_pct)

    # Build update data — match on agent_id + coin + direction + status=open
    update_data = {
        "status": "closed",
        "exit_price": exit_price,
        "exit_time": now,
        "exit_regime": sd.get("regime"),
        "exit_hurst": sd.get("hurst"),
        "exit_reason": exit_reason,
        "exit_equity": ps.get("equity"),
        "exit_signal_mode": sd.get("signal_mode"),
        "hold_duration_seconds": hold_seconds,
        "pnl": pnl,
        "pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
        "fees": fees,
        "was_profitable": pnl > 0,
        "updated_at": now,
    }

    # Add tracker data if available
    if tracker:
        update_data.update({
            "max_adverse_excursion_pct": round(tracker.mae_pct, 6),
            "max_favorable_excursion_pct": round(tracker.mfe_pct, 6),
            "regime_changes_during_hold": tracker.regime_changes,
            "immune_checks_during_hold": tracker.immune_checks,
            "immune_alerts_during_hold": tracker.immune_alerts,
            "stop_was_moved": tracker.stop_moved,
            "efficiency": round(efficiency, 4) if efficiency is not None else None,
            "risk_reward_ratio": round(risk_reward, 4) if risk_reward is not None else None,
        })

    update_data = {k: v for k, v in update_data.items() if v is not None}

    # Update the open trade record
    _async_update("trades_enriched", {
        "agent_id": AGENT_UUID,
        "coin": coin,
        "direction": direction.lower(),
        "status": "open",
    }, update_data)

    mae_str = f"MAE={tracker.mae_pct*100:.1f}%" if tracker else "?"
    mfe_str = f"MFE={tracker.mfe_pct*100:.1f}%" if tracker else "?"
    _log(f"trade close: {coin} {direction} pnl=${pnl:.2f} ({exit_reason}) {mae_str} {mfe_str}")

    # Report to collective learning network (anonymized, fire-and-forget)
    try:
        from collective import report_trade
        report_trade(
            coin=coin,
            direction=direction,
            regime=sd.get("regime"),
            pnl_pct=round(pnl_pct, 6) if pnl_pct is not None else None,
            hold_seconds=hold_seconds,
            mae_pct=round(tracker.mae_pct, 6) if tracker else None,
            mfe_pct=round(tracker.mfe_pct, 6) if tracker else None,
            exit_reason=exit_reason,
            hurst=sd.get("hurst"),
            efficiency=round(efficiency, 4) if efficiency is not None else None,
        )
    except Exception:
        pass  # never block trading for collective reporting

    # UPGRADE 9: Report regime shift to alert system
    try:
        from compounding_upgrades import report_regime_shift
        entry_regime = sd.get("regime", "")
        exit_regime = ps.get("exit_regime", ps.get("regime", ""))
        if entry_regime and exit_regime and entry_regime != exit_regime:
            report_regime_shift(coin, exit_regime, entry_regime)
    except Exception:
        pass


# ─── SYSTEM SNAPSHOT ──────────────────────────────────────────────────────────

def record_system_snapshot(
    equity: float,
    positions: list,
    signal_mode: str = "full",
    immune_status: str = "healthy",
    regimes: dict | None = None,
    btc_price: float | None = None,
):
    """Record a system state snapshot every 5 minutes."""
    row = {
        "timestamp": _now_iso(),
        "total_equity": equity,
        "total_positions": len(positions),
        "total_unrealized_pnl": sum(float(p.get("unrealizedPnl", 0)) for p in positions),
        "positions": json.dumps([{
            "coin": p.get("coin"),
            "direction": p.get("direction"),
            "pnl_pct": p.get("pnl_pct", 0),
        } for p in positions]),
        "signal_mode": signal_mode,
        "immune_status": immune_status,
        "btc_price": btc_price,
    }

    if regimes:
        row["regimes"] = json.dumps(regimes)

    row = {k: v for k, v in row.items() if v is not None}
    _async_write("system_snapshots", row)

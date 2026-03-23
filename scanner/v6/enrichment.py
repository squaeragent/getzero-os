#!/usr/bin/env python3
"""
Data Intelligence Enrichment Pipeline

Captures FULL CONTEXT at the moment of every event.
Writes to decisions_enriched, trades_enriched, system_snapshots, journal_intelligence.

This module is TELEMETRY ONLY — never affects trading decisions.
If Supabase is down, the agent trades normally.
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("enrichment")

AGENT_UUID = "4802c6f8-f862-42f1-b248-45679e1517e7"

# ─── Supabase Client ──────────────────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from supabase import create_client
        env_file = Path.home() / ".config" / "openclaw" / ".env"
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "SUPABASE_URL": url = v
                    elif k == "SUPABASE_SERVICE_KEY": key = v
        if url and key:
            _client = create_client(url, key)
        return _client
    except Exception as e:
        log.warning(f"Enrichment: Supabase unavailable: {e}")
        return None


def _safe_write(table: str, data: dict):
    """Write to Supabase, silently fail on error."""
    try:
        client = _get_client()
        if client:
            client.table(table).insert(data).execute()
    except Exception as e:
        log.warning(f"Enrichment write failed ({table}): {e}")


def _safe_update(table: str, match: dict, data: dict):
    """Update a row in Supabase, silently fail on error."""
    try:
        client = _get_client()
        if client:
            q = client.table(table).update(data)
            for k, v in match.items():
                q = q.eq(k, v)
            q.execute()
    except Exception as e:
        log.warning(f"Enrichment update failed ({table}): {e}")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─── Position Tracker (MAE/MFE during hold) ──────────────────

class PositionTracker:
    """Tracks max adverse and favorable excursion during a trade's life."""

    def __init__(self, entry_price: float, direction: str, coin: str):
        self.entry_price = entry_price
        self.direction = direction.upper()
        self.coin = coin
        self.mae = 0.0        # max adverse excursion (worst point, in USD per unit)
        self.mfe = 0.0        # max favorable excursion (best point)
        self.mae_pct = 0.0
        self.mfe_pct = 0.0
        self.regime_change_count = 0
        self.last_regime = None
        self.immune_check_count = 0
        self.immune_alert_count = 0
        self.accumulated_funding = 0.0
        self.stop_was_moved = False
        self._original_stop = None

    def update(self, current_price: float, current_regime: str = None,
               funding_payment: float = 0.0):
        """Called every evaluation cycle while position is open."""
        if self.direction == 'LONG':
            unrealized = current_price - self.entry_price
        else:
            unrealized = self.entry_price - current_price

        unrealized_pct = (unrealized / self.entry_price) * 100 if self.entry_price else 0

        # Track extremes
        if unrealized < self.mae:
            self.mae = unrealized
            self.mae_pct = unrealized_pct
        if unrealized > self.mfe:
            self.mfe = unrealized
            self.mfe_pct = unrealized_pct

        # Track regime changes
        if current_regime and current_regime != self.last_regime and self.last_regime is not None:
            self.regime_change_count += 1
        if current_regime:
            self.last_regime = current_regime

        # Track funding
        self.accumulated_funding += funding_payment

    def record_immune_check(self, had_alert: bool = False):
        """Called when the immune system checks this position."""
        self.immune_check_count += 1
        if had_alert:
            self.immune_alert_count += 1

    def set_stop(self, stop_price: float):
        """Track if stop was moved."""
        if self._original_stop is None:
            self._original_stop = stop_price
        elif abs(stop_price - self._original_stop) > 0.0001:
            self.stop_was_moved = True


# Global tracker registry
_position_trackers: dict[str, PositionTracker] = {}


def get_tracker(coin: str) -> PositionTracker | None:
    """Get the position tracker for a coin."""
    return _position_trackers.get(coin)


def create_tracker(coin: str, entry_price: float, direction: str) -> PositionTracker:
    """Create a new position tracker when opening a trade."""
    tracker = PositionTracker(entry_price, direction, coin)
    _position_trackers[coin] = tracker
    return tracker


def remove_tracker(coin: str) -> PositionTracker | None:
    """Remove and return tracker when closing a trade."""
    return _position_trackers.pop(coin, None)


# ─── Enriched Decision Recording ─────────────────────────────

def record_enriched_decision(
    coin: str,
    direction: str,
    decision: str,         # 'entered' | 'rejected' | 'closed' | 'held'
    reason: str,
    signal_data: dict = None,
    market_data: dict = None,
    portfolio_state: dict = None,
    gate_results: dict = None,
    eval_duration_ms: int = None,
    signal_mode: str = None,
):
    """Record a fully enriched decision to Supabase.
    
    Called from the evaluator after every decision.
    All context is captured NOW — can't be reconstructed later.
    """
    signal_data = signal_data or {}
    market_data = market_data or {}
    portfolio_state = portfolio_state or {}
    gate_results = gate_results or {}

    data = {
        "agent_id": AGENT_UUID,
        "timestamp": _now_iso(),
        "coin": coin,
        "direction": direction.lower(),
        "decision": decision.lower(),
        "reason": _sanitize_reason(reason),
        "reason_raw": reason[:500] if reason else None,

        # Signal context
        "regime": signal_data.get("regime"),
        "regime_code": signal_data.get("regime_code"),
        "hurst": signal_data.get("hurst"),
        "lyapunov": signal_data.get("lyapunov"),
        "dfa": signal_data.get("dfa"),
        "signal_quality": signal_data.get("quality_tier"),
        "signal_sharpe": signal_data.get("signal_sharpe"),
        "assembled_sharpe": signal_data.get("assembled_sharpe"),
        "signal_direction": signal_data.get("direction"),
        "signal_age_seconds": signal_data.get("signal_age"),

        # Market context
        "price": market_data.get("price"),
        "funding_rate": market_data.get("funding_rate"),
        "funding_annualized": market_data.get("funding_annualized"),
        "book_depth_usd": market_data.get("book_depth"),
        "spread_bps": market_data.get("spread_bps"),
        "volume_24h": market_data.get("volume_24h"),

        # Portfolio context
        "equity": portfolio_state.get("equity"),
        "position_count": portfolio_state.get("position_count"),
        "positions_coins": portfolio_state.get("open_coins", []),
        "max_positions": portfolio_state.get("max_positions"),
        "available_capital": portfolio_state.get("available_capital"),

        # Gate results
        "gates_passed": gate_results.get("passed", []),
        "gates_failed": gate_results.get("failed", []),
        "gate_that_rejected": gate_results.get("rejected_by"),
        "evaluation_duration_ms": eval_duration_ms,

        # Signal mode
        "signal_mode": signal_mode,
    }

    # Remove None values to keep payload small
    data = {k: v for k, v in data.items() if v is not None}

    threading.Thread(
        target=_safe_write,
        args=("decisions_enriched", data),
        daemon=True,
    ).start()


# ─── Enriched Trade Recording ────────────────────────────────

def record_enriched_trade_open(
    coin: str,
    direction: str,
    entry_price: float,
    size_usd: float,
    leverage: float = 1.0,
    stop_price: float = None,
    stop_distance_pct: float = None,
    signal_data: dict = None,
    market_data: dict = None,
    portfolio_state: dict = None,
    signal_mode: str = None,
):
    """Record enriched trade entry to Supabase. Creates PositionTracker."""
    signal_data = signal_data or {}
    market_data = market_data or {}
    portfolio_state = portfolio_state or {}

    now = datetime.now(timezone.utc)

    data = {
        "agent_id": AGENT_UUID,
        "coin": coin,
        "direction": direction.lower(),
        "status": "open",
        "entry_price": entry_price,
        "entry_time": now.isoformat(),
        "entry_regime": signal_data.get("regime"),
        "entry_regime_code": signal_data.get("regime_code"),
        "entry_hurst": signal_data.get("hurst"),
        "entry_lyapunov": signal_data.get("lyapunov"),
        "entry_signal_sharpe": signal_data.get("signal_sharpe"),
        "entry_assembled_sharpe": signal_data.get("assembled_sharpe"),
        "entry_funding_rate": market_data.get("funding_rate"),
        "entry_book_depth_usd": market_data.get("book_depth"),
        "entry_equity": portfolio_state.get("equity"),
        "entry_position_count": portfolio_state.get("position_count"),
        "entry_utc_hour": now.hour,
        "entry_day_of_week": now.weekday(),
        "entry_signal_mode": signal_mode,
        "size_usd": size_usd,
        "leverage": leverage,
        "stop_price": stop_price,
        "stop_distance_pct": stop_distance_pct,
    }

    data = {k: v for k, v in data.items() if v is not None}

    # Create position tracker
    tracker = create_tracker(coin, entry_price, direction)
    if stop_price:
        tracker.set_stop(stop_price)

    threading.Thread(
        target=_safe_write,
        args=("trades_enriched", data),
        daemon=True,
    ).start()


def record_enriched_trade_close(
    coin: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float = None,
    fees: float = 0,
    entry_time: str = None,
    exit_reason: str = None,
    signal_data: dict = None,
    portfolio_state: dict = None,
    signal_mode: str = None,
):
    """Record enriched trade exit. Uses PositionTracker for during-hold data."""
    signal_data = signal_data or {}
    portfolio_state = portfolio_state or {}

    tracker = remove_tracker(coin)
    now = datetime.now(timezone.utc)

    # Compute hold duration
    hold_seconds = None
    if entry_time:
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            hold_seconds = int((now - entry_dt).total_seconds())
        except (ValueError, TypeError):
            pass

    # Compute efficiency and risk/reward
    mfe = tracker.mfe if tracker else 0
    mae = tracker.mae if tracker else 0
    risk_reward = abs(mfe / mae) if mae and abs(mae) > 0.001 else None
    efficiency = (pnl / mfe) if mfe and mfe > 0.001 else None

    data = {
        "agent_id": AGENT_UUID,
        "coin": coin,
        "direction": direction.lower(),
        "status": "closed",
        "entry_price": entry_price,
        "entry_time": entry_time,

        # Exit context
        "exit_price": exit_price,
        "exit_time": now.isoformat(),
        "exit_regime": signal_data.get("regime"),
        "exit_regime_code": signal_data.get("regime_code"),
        "exit_hurst": signal_data.get("hurst"),
        "exit_lyapunov": signal_data.get("lyapunov"),
        "exit_reason": exit_reason,
        "exit_equity": portfolio_state.get("equity"),
        "exit_signal_mode": signal_mode,

        # During hold
        "hold_duration_seconds": hold_seconds,
        "pnl": round(pnl, 6) if pnl else 0,
        "pnl_pct": round(pnl_pct, 4) if pnl_pct else None,
        "fees": round(fees, 6) if fees else 0,
        "was_profitable": pnl > 0 if pnl else False,
    }

    if tracker:
        data.update({
            "funding_cost": round(tracker.accumulated_funding, 6),
            "max_adverse_excursion": round(tracker.mae, 6),
            "max_favorable_excursion": round(tracker.mfe, 6),
            "max_adverse_excursion_pct": round(tracker.mae_pct, 4),
            "max_favorable_excursion_pct": round(tracker.mfe_pct, 4),
            "regime_changes_during_hold": tracker.regime_change_count,
            "immune_checks_during_hold": tracker.immune_check_count,
            "immune_alerts_during_hold": tracker.immune_alert_count,
            "stop_was_moved": tracker.stop_was_moved,
            "risk_reward_ratio": round(risk_reward, 4) if risk_reward else None,
            "efficiency": round(efficiency, 4) if efficiency else None,
        })

    data = {k: v for k, v in data.items() if v is not None}

    threading.Thread(
        target=_safe_write,
        args=("trades_enriched", data),
        daemon=True,
    ).start()


# ─── System Snapshot ──────────────────────────────────────────

def record_system_snapshot(
    equity: float,
    positions: list = None,
    signal_mode: str = None,
    universe_coins: list = None,
    regimes: dict = None,
    immune_status: str = None,
    stops_verified: int = None,
    stops_missing: int = None,
    error_count_1h: int = None,
    btc_price: float = None,
    btc_regime: str = None,
    api_latency: dict = None,
    waitlist_count: int = None,
):
    """Record full system state snapshot. Called every 5 minutes."""
    positions_data = None
    total_unrealized = 0
    if positions:
        positions_data = []
        for p in positions:
            unrealized = p.get("unrealized_pnl", 0)
            total_unrealized += unrealized
            positions_data.append({
                "coin": p.get("coin"),
                "direction": p.get("direction"),
                "pnl": unrealized,
                "hold_seconds": p.get("hold_seconds"),
            })

    data = {
        "timestamp": _now_iso(),
        "total_equity": equity,
        "total_positions": len(positions) if positions else 0,
        "total_unrealized_pnl": round(total_unrealized, 4),
        "positions": json.dumps(positions_data) if positions_data else None,
        "signal_mode": signal_mode,
        "universe_coins": universe_coins,
        "universe_count": len(universe_coins) if universe_coins else None,
        "regimes": json.dumps(regimes) if regimes else None,
        "immune_status": immune_status,
        "stops_verified": stops_verified,
        "stops_missing": stops_missing,
        "error_count_1h": error_count_1h,
        "btc_price": btc_price,
        "btc_regime": btc_regime,
        "api_latency_ms": json.dumps(api_latency) if api_latency else None,
        "waitlist_count": waitlist_count,
    }

    data = {k: v for k, v in data.items() if v is not None}

    threading.Thread(
        target=_safe_write,
        args=("system_snapshots", data),
        daemon=True,
    ).start()


# ─── Journal Intelligence ────────────────────────────────────

def record_intelligence(
    dimension: str,       # 'trading' | 'ux' | 'infra' | 'security' | 'cost'
    pattern: str,
    evidence: dict,
    confidence: str,      # 'low' | 'medium' | 'high'
    impact: str,          # 'low' | 'medium' | 'high' | 'critical'
    proposal: str = None,
):
    """Record a pattern detection finding to the intelligence journal."""
    data = {
        "timestamp": _now_iso(),
        "dimension": dimension,
        "pattern": pattern,
        "evidence": json.dumps(evidence),
        "confidence": confidence,
        "impact": impact,
        "proposal": proposal,
        "proposal_status": "pending" if proposal else None,
    }

    data = {k: v for k, v in data.items() if v is not None}

    threading.Thread(
        target=_safe_write,
        args=("journal_intelligence", data),
        daemon=True,
    ).start()


# ─── Reason Sanitizer (TIER 0 → TIER 4) ─────────────────────

def _sanitize_reason(reason: str) -> str:
    """Sanitize internal reason for public display.
    
    TIER 0 (internal): "sharpe_floor=0.8, actual=0.42, gate=alpha_vs_cost"
    TIER 4 (public):   "expected return too low"
    """
    if not reason:
        return None
    r = reason.lower()

    # Map internal gate names to human-readable reasons
    if "capital_floor" in r or "capital floor" in r or "equity" in r:
        return "capital too low"
    if "alpha_vs_cost" in r or "alpha vs cost" in r or "sharpe" in r:
        return "expected return too low"
    if "max_positions" in r or "max positions" in r:
        return "position limit reached"
    if "book_depth" in r or "book depth" in r or "liquidity" in r:
        return "insufficient liquidity"
    if "funding" in r:
        return "funding rate unfavorable"
    if "spread" in r:
        return "spread too wide"
    if "correlation" in r:
        return "correlated with existing position"
    if "regime" in r and ("chaotic" in r or "unstable" in r):
        return "regime too chaotic"
    if "cooldown" in r:
        return "coin on cooldown"
    if "blacklist" in r:
        return "coin excluded"

    # Default: return first 100 chars, stripped of numbers that look like thresholds
    import re
    sanitized = re.sub(r'=[\d.]+', '', reason)
    sanitized = re.sub(r'[\d.]+%', '', sanitized)
    return sanitized[:100].strip()

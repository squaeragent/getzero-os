#!/usr/bin/env python3
"""
ZERO Score — The number operators obsess over.

4 dimensions × Bayesian convergence × time decay = addictive metric.

Components:
  Performance (25%) — sortino ratio + max drawdown
  Discipline  (30%) — rejection rate, stops, overrides, config stability, hold compliance, sizing
  Resilience  (25%) — regime transitions, drawdown recovery, immune health
  Consistency (20%) — positive days, distribution, win rate stability

Score range: 0.0 — 10.0
Minimum data: 20 trades + 7 days
Full confidence: 100 trades
Decay half-life: 14 days inactive
"""

import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SUPABASE_PROJECT = "fzzotmxxrcnmrqtmsesi"


def _clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _now_utc():
    return datetime.now(timezone.utc)


# ─── Supabase Query Helper ───────────────────────────────────

def _get_access_token() -> str | None:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if token:
        return token
    env_file = Path.home() / ".config" / "openclaw" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("SUPABASE_ACCESS_TOKEN"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _query(sql: str) -> list[dict] | None:
    token = _get_access_token()
    if not token:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT}/database/query",
            data=json.dumps({"query": sql}).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


# ─── Bayesian Convergence ────────────────────────────────────

def bayesian_score(raw_score: float, trade_count: int, prior: float = 5.0) -> float:
    """Score converges from prior (5.0) to actual as trades accumulate.

    At 20 trades: 80% prior, 20% actual
    At 50 trades: 50% prior, 50% actual
    At 100 trades: 0% prior, 100% actual
    """
    confidence = min(trade_count / 100, 1.0)
    return (raw_score * confidence) + (prior * (1 - confidence))


def confidence_pct(trade_count: int) -> float:
    """Confidence percentage (0-100)."""
    return min(trade_count / 100, 1.0) * 100


def projected_range(raw_score: float, trade_count: int) -> tuple[float, float]:
    """Projected score range based on uncertainty."""
    conf = min(trade_count / 100, 1.0)
    uncertainty = (1 - conf) * 2.0  # ±2.0 at 0 trades, ±0 at 100
    lo = _clamp(raw_score - uncertainty)
    hi = _clamp(raw_score + uncertainty)
    return round(lo, 1), round(hi, 1)


# ─── Time Decay ──────────────────────────────────────────────

def apply_decay(score: float, last_trade_time: datetime, now: datetime = None) -> float:
    """Score decays when the agent stops trading. Half-life: 14 days."""
    if now is None:
        now = _now_utc()
    hours_inactive = (now - last_trade_time).total_seconds() / 3600
    if hours_inactive < 24:
        return score  # active, no decay
    days_inactive = hours_inactive / 24
    half_life = 14
    decay_factor = 0.5 ** (days_inactive / half_life)
    return score * decay_factor


def decay_state(last_trade_time: datetime, now: datetime = None) -> str:
    """Returns 'active', 'decaying', or 'dead'."""
    if now is None:
        now = _now_utc()
    hours = (now - last_trade_time).total_seconds() / 3600
    if hours < 24:
        return "active"
    if hours < 60 * 24:
        return "decaying"
    return "dead"


# ─── Component 1: Performance (25%) ─────────────────────────

def calculate_performance(trades: list[dict], equity_snapshots: list[dict] = None) -> float:
    """Sortino ratio + max drawdown → 0-10 score."""
    if not trades:
        return 5.0

    pnls = [t.get("pnl", 0) for t in trades if t.get("status") == "closed"]
    if not pnls:
        return 5.0

    # Sortino ratio (downside deviation)
    mean_pnl = sum(pnls) / len(pnls)
    downside = [min(0, p - mean_pnl) for p in pnls]
    downside_dev = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 1
    sortino = mean_pnl / (downside_dev + 1e-10)

    # Score sortino: 0 → 3, 1.0 → 6, 2.0 → 8, 3.0+ → 10
    sortino_score = _clamp(3 + sortino * 2.33)

    # Max drawdown from equity snapshots or trade P&L
    max_dd_pct = 0
    if equity_snapshots:
        equities = [e.get("equity", 0) for e in equity_snapshots if e.get("equity")]
        if equities:
            peak = equities[0]
            for eq in equities:
                peak = max(peak, eq)
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd_pct = max(max_dd_pct, dd)
    else:
        # Estimate from cumulative P&L
        cum = 0
        peak = 0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = (peak - cum) / (peak + 1e-10) if peak > 0 else 0
            max_dd_pct = max(max_dd_pct, dd)

    # Score drawdown: 0% → 10, 5% → 8, 10% → 6, 20% → 4, 50%+ → 0
    dd_score = _clamp(10 - max_dd_pct * 20)

    return round((sortino_score * 0.6 + dd_score * 0.4), 2)


# ─── Component 2: Discipline (30%) ──────────────────────────

def calculate_discipline(
    decisions: list[dict] = None,
    trades: list[dict] = None,
    operator_events: list[dict] = None,
) -> float:
    """6 sub-metrics measuring trust in the machine."""
    decisions = decisions or []
    trades = trades or []
    operator_events = operator_events or []
    closed = [t for t in trades if t.get("status") == "closed"]

    # 1. Rejection rate (15%) — table stakes
    total_decisions = len(decisions)
    rejections = len([d for d in decisions if d.get("decision") in ("rejected", "blocked")])
    if total_decisions > 0:
        rej_rate = rejections / total_decisions
        rej_score = _clamp(rej_rate * 11)  # 90%+ → 9.9
    else:
        rej_score = 5.0

    # 2. Stop compliance (15%) — table stakes
    if closed:
        stops_hit = len([t for t in closed if t.get("exit_reason") in ("stop_loss", "trailing_stop")])
        manual_closes = len([t for t in closed if t.get("exit_reason") in ("manual", "emergency", "override")])
        # High stop compliance = exits are systematic
        systematic = len(closed) - manual_closes
        stop_score = _clamp((systematic / len(closed)) * 10) if closed else 5.0
    else:
        stop_score = 5.0

    # 3. Sizing consistency (15%)
    if closed:
        sizes = [t.get("size_usd", 0) for t in closed if t.get("size_usd")]
        if len(sizes) >= 3:
            mean_size = sum(sizes) / len(sizes)
            size_std = math.sqrt(sum((s - mean_size) ** 2 for s in sizes) / len(sizes))
            cv = size_std / (mean_size + 1e-10)  # coefficient of variation
            sizing_score = _clamp(10 - cv * 10)  # CV=0 → 10, CV=1 → 0
        else:
            sizing_score = 5.0
    else:
        sizing_score = 5.0

    # 4. Override rate (20%) — NEW: trust in machine
    overrides = [e for e in operator_events if e.get("type") == "manual_override"]
    override_rate = len(overrides) / max(len(closed), 1)
    override_score = _clamp(10 - override_rate * 50)

    # 5. Config stability (20%) — NEW: no tinkering
    config_changes = [e for e in operator_events if e.get("type") == "config_change"]
    days_active = max(1, len(set(
        t.get("entry_time", "")[:10] for t in trades if t.get("entry_time")
    )))
    changes_per_week = len(config_changes) / max(days_active / 7, 1)
    config_score = _clamp(10 - changes_per_week * 2)

    # 6. Hold time compliance (15%) — NEW
    if closed:
        compliant_reasons = {"signal_reversal", "stop_loss", "trailing_stop", "take_profit", "max_hold", "immune"}
        compliant = len([t for t in closed if t.get("exit_reason") in compliant_reasons])
        compliance_rate = compliant / len(closed)
        hold_score = compliance_rate * 10
    else:
        hold_score = 5.0

    score = (
        rej_score * 0.15
        + stop_score * 0.15
        + sizing_score * 0.15
        + override_score * 0.20
        + config_score * 0.20
        + hold_score * 0.15
    )
    return round(_clamp(score), 2)


# ─── Component 3: Resilience (25%) ──────────────────────────

def calculate_resilience(
    trades: list[dict] = None,
    equity_snapshots: list[dict] = None,
    immune_logs: list[dict] = None,
) -> float:
    """Regime transitions, recovery speed, immune health."""
    trades = trades or []
    equity_snapshots = equity_snapshots or []
    components = []

    # Tier 1: Regime transition survival (if enriched data available)
    enriched = [t for t in trades if t.get("entry_regime") and t.get("exit_regime")]
    if enriched:
        transitions = [t for t in enriched if t.get("entry_regime") != t.get("exit_regime")]
        if transitions:
            survived = len([t for t in transitions if t.get("was_profitable") or t.get("pnl", 0) > -0.5])
            regime_score = (survived / len(transitions)) * 10
        else:
            regime_score = 7.0  # no transitions = stable environment
        components.append((regime_score, 0.4))

    # Tier 2: Drawdown recovery (always available)
    if equity_snapshots and len(equity_snapshots) >= 5:
        equities = [e.get("equity", 0) for e in equity_snapshots if e.get("equity")]
        if equities:
            # Count recovery speed: how fast does equity recover after drawdowns?
            in_dd = False
            dd_start = 0
            recovery_times = []
            peak = equities[0]

            for i, eq in enumerate(equities):
                if eq > peak:
                    if in_dd:
                        recovery_times.append(i - dd_start)
                        in_dd = False
                    peak = eq
                elif (peak - eq) / peak > 0.03:  # 3%+ drawdown
                    if not in_dd:
                        in_dd = True
                        dd_start = i

            if recovery_times:
                avg_recovery = sum(recovery_times) / len(recovery_times)
                # Fast recovery (< 10 snapshots) = 10, slow (50+) = 2
                recovery_score = _clamp(10 - (avg_recovery - 5) * 0.2)
            else:
                recovery_score = 7.0  # no significant drawdowns
            components.append((recovery_score, 0.3))
    else:
        # Fallback: use trade outcomes
        closed = [t for t in trades if t.get("status") == "closed"]
        if closed:
            # Consecutive losses recovery
            max_streak = 0
            streak = 0
            for t in closed:
                if t.get("pnl", 0) < 0:
                    streak += 1
                    max_streak = max(max_streak, streak)
                else:
                    streak = 0
            # Max losing streak: 0-1 → 10, 3 → 7, 5 → 5, 10+ → 2
            streak_score = _clamp(10 - max_streak * 1.0)
            components.append((streak_score, 0.3))
        else:
            components.append((5.0, 0.3))

    # Tier 3: Immune health
    if immune_logs:
        total_checks = len(immune_logs)
        alerts = len([l for l in immune_logs if l.get("alert")])
        alert_rate = alerts / max(total_checks, 1)
        immune_score = _clamp(10 - alert_rate * 20)
        components.append((immune_score, 0.3))
    else:
        # Fallback: volatility survival
        closed = [t for t in trades if t.get("status") == "closed"]
        if closed:
            profitable = len([t for t in closed if t.get("pnl", 0) > 0])
            win_rate = profitable / len(closed)
            vol_score = _clamp(win_rate * 12)  # 50% WR → 6, 80% → 9.6
            components.append((vol_score, 0.3))
        else:
            components.append((5.0, 0.3))

    if not components:
        return 5.0

    total_weight = sum(w for _, w in components)
    score = sum(s * (w / total_weight) for s, w in components)
    return round(_clamp(score), 2)


# ─── Component 4: Consistency (20%) ──────────────────────────

def calculate_consistency(trades: list[dict] = None, equity_snapshots: list[dict] = None) -> float:
    """Positive days, distribution, win rate stability."""
    trades = trades or []
    closed = [t for t in trades if t.get("status") == "closed"]

    if len(closed) < 5:
        return 5.0

    # 1. Positive day ratio (40%)
    daily_pnl: dict[str, float] = {}
    for t in closed:
        day = (t.get("exit_time") or t.get("entry_time") or "")[:10]
        if day:
            daily_pnl[day] = daily_pnl.get(day, 0) + t.get("pnl", 0)

    if daily_pnl:
        positive_days = sum(1 for v in daily_pnl.values() if v > 0)
        pos_ratio = positive_days / len(daily_pnl)
        pos_score = _clamp(pos_ratio * 12)  # 50% → 6, 75% → 9
    else:
        pos_score = 5.0

    # 2. P&L distribution (30%) — penalize extreme outliers
    pnls = [t.get("pnl", 0) for t in closed]
    mean_pnl = sum(pnls) / len(pnls)
    std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)) if len(pnls) > 1 else 0
    # Count outliers (> 3 std from mean)
    outliers = sum(1 for p in pnls if abs(p - mean_pnl) > 3 * std_pnl) if std_pnl > 0 else 0
    outlier_rate = outliers / len(pnls)
    dist_score = _clamp(10 - outlier_rate * 50)

    # 3. Win rate stability (30%) — rolling 20-trade win rate variance
    if len(closed) >= 20:
        window = 20
        rolling_wrs = []
        for i in range(len(closed) - window + 1):
            chunk = closed[i : i + window]
            wins = sum(1 for t in chunk if t.get("pnl", 0) > 0)
            rolling_wrs.append(wins / window)
        if rolling_wrs:
            wr_std = math.sqrt(sum((w - sum(rolling_wrs) / len(rolling_wrs)) ** 2 for w in rolling_wrs) / len(rolling_wrs))
            # Low variance = consistent. wr_std < 0.05 → 10, > 0.2 → 3
            wr_score = _clamp(10 - wr_std * 35)
        else:
            wr_score = 5.0
    else:
        wr_score = 5.0

    score = pos_score * 0.4 + dist_score * 0.3 + wr_score * 0.3
    return round(_clamp(score), 2)


# ─── Main Score Calculator ───────────────────────────────────

WEIGHTS = {
    "performance": 0.25,
    "discipline": 0.30,
    "resilience": 0.25,
    "consistency": 0.20,
}


def calculate_zero_score(
    agent_id: str = None,
    trades: list[dict] = None,
    decisions: list[dict] = None,
    equity_snapshots: list[dict] = None,
    operator_events: list[dict] = None,
    immune_logs: list[dict] = None,
) -> dict:
    """Calculate the complete ZERO Score for an operator.

    Returns full breakdown including components, confidence, decay state.
    """
    trades = trades or []
    decisions = decisions or []
    closed = [t for t in trades if t.get("status") == "closed"]
    trade_count = len(closed)

    # Minimum data check
    if trade_count < 20:
        days_active = len(set(
            (t.get("entry_time") or "")[:10] for t in trades if t.get("entry_time")
        ))
        if days_active < 7:
            return {
                "score": None,
                "message": f"Need {20 - trade_count} more trades and {max(0, 7 - days_active)} more days",
                "trade_count": trade_count,
                "days_active": days_active,
                "min_trades": 20,
                "min_days": 7,
            }

    # Calculate components
    perf = calculate_performance(closed, equity_snapshots)
    disc = calculate_discipline(decisions, trades, operator_events)
    res = calculate_resilience(trades, equity_snapshots, immune_logs)
    cons = calculate_consistency(trades, equity_snapshots)

    # Raw composite
    raw = (
        perf * WEIGHTS["performance"]
        + disc * WEIGHTS["discipline"]
        + res * WEIGHTS["resilience"]
        + cons * WEIGHTS["consistency"]
    )
    raw = _clamp(raw)

    # Bayesian convergence
    score = bayesian_score(raw, trade_count)
    conf = confidence_pct(trade_count)
    lo, hi = projected_range(raw, trade_count)

    # Time decay
    last_trade = None
    for t in sorted(closed, key=lambda x: x.get("exit_time", ""), reverse=True):
        lt = t.get("exit_time")
        if lt:
            try:
                last_trade = datetime.fromisoformat(lt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
            break

    now = _now_utc()
    effective = score
    state = "active"
    if last_trade:
        effective = apply_decay(score, last_trade, now)
        state = decay_state(last_trade, now)

    # Weakest component
    components = {
        "performance": perf,
        "discipline": disc,
        "resilience": res,
        "consistency": cons,
    }
    weakest = min(components, key=components.get)

    # Days active
    trade_days = set()
    for t in trades:
        d = (t.get("entry_time") or "")[:10]
        if d:
            trade_days.add(d)
    days_active = len(trade_days)

    # Rank label
    if effective >= 9.0:
        rank_label = "legendary"
    elif effective >= 8.0:
        rank_label = "exceptional"
    elif effective >= 7.0:
        rank_label = "above standard"
    elif effective >= 6.0:
        rank_label = "solid"
    elif effective >= 5.0:
        rank_label = "developing"
    elif effective >= 4.0:
        rank_label = "below standard"
    else:
        rank_label = "needs work"

    return {
        "score": round(score, 1),
        "effective_score": round(effective, 1),
        "raw_score": round(raw, 2),
        "components": {k: round(v, 1) for k, v in components.items()},
        "weakest": weakest,
        "confidence": round(conf, 0),
        "projected_range": [lo, hi],
        "trade_count": trade_count,
        "days_active": days_active,
        "decay_state": state,
        "rank_label": rank_label,
        "last_trade": last_trade.isoformat() if last_trade else None,
        "weights": WEIGHTS,
    }


# ─── Score from Supabase ─────────────────────────────────────

def _rest_fetch(table: str, params: str) -> list[dict]:
    """Fetch from Supabase REST API with service key."""
    env_file = Path.home() / ".config" / "openclaw" / ".env"
    url = key = None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("SUPABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("SUPABASE_SERVICE_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url or not key:
        return []
    try:
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}?{params}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def score_from_db(agent_id: str = "4802c6f8-f862-42f1-b248-45679e1517e7") -> dict:
    """Calculate ZERO Score from Supabase data."""
    aid = f"agent_id=eq.{agent_id}"

    # Try enriched trades first, fall back to regular
    trades_data = _rest_fetch(
        "trades_enriched",
        f"{aid}&select=coin,direction,status,entry_price,exit_price,pnl,pnl_pct,fees,size_usd,exit_reason,entry_time,exit_time,was_profitable,entry_regime,exit_regime,regime_changes_during_hold&order=entry_time.desc&limit=500"
    )
    if not trades_data:
        trades_data = _rest_fetch(
            "trades",
            f"{aid}&select=coin,direction,status,entry_price,exit_price,pnl,fees,size_usd,exit_reason,entry_time,exit_time&order=entry_time.desc&limit=500"
        )

    # Decisions
    decisions_data = _rest_fetch(
        "decisions_enriched",
        f"{aid}&select=coin,direction,decision,reason,timestamp&order=timestamp.desc&limit=1000"
    )
    if not decisions_data:
        decisions_data = _rest_fetch(
            "decisions",
            f"{aid}&select=coin,direction,decision,reason,timestamp&order=timestamp.desc&limit=1000"
        )

    # Equity snapshots
    equity_data = _rest_fetch(
        "equity_snapshots",
        f"{aid}&select=equity,timestamp&order=timestamp.desc&limit=500"
    )

    return calculate_zero_score(
        agent_id=agent_id,
        trades=trades_data,
        decisions=decisions_data,
        equity_snapshots=equity_data,
    )


# ─── Terminal Display ─────────────────────────────────────────

def format_terminal(result: dict) -> str:
    """Format score for terminal display."""
    if result.get("score") is None:
        return f"ZERO SCORE: insufficient data\n{result.get('message', '')}"

    s = result["effective_score"]
    conf = result["confidence"]
    comp = result["components"]
    lo, hi = result["projected_range"]
    tc = result["trade_count"]
    days = result["days_active"]
    state = result["decay_state"]
    weakest = result["weakest"]
    rank = result["rank_label"]

    # Confidence bar
    filled = int(conf / 100 * 16)
    bar = "█" * filled + "░" * (16 - filled)

    # Score bar
    score_filled = int(s / 10 * 20)
    score_bar = "█" * score_filled + "░" * (20 - score_filled)

    lines = [
        f"ZERO SCORE: {s}",
        f"    {score_bar}",
        f"",
        f"  performance .. {comp['performance']}",
        f"  discipline ... {comp['discipline']}",
        f"  resilience ... {comp['resilience']}",
        f"  consistency .. {comp['consistency']}",
        f"",
        f"  WEAKEST: {weakest}",
        f"",
        f"  confidence: {bar} {conf:.0f}%  ({tc}/{100} trades)",
        f"  projected range: {lo} — {hi}",
        f"  {tc} trades · {days} days · {'●' if state == 'active' else '○'} {rank}",
    ]

    if conf < 100:
        remaining = 100 - tc
        lines.append(f"  Your score is stabilizing. {remaining} more trades until full confidence.")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Calculating ZERO Score from database...")
    result = score_from_db()
    print(format_terminal(result))

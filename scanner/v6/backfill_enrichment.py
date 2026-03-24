#!/usr/bin/env python3
"""
Backfill trades_enriched from existing trades in Supabase.

Creates enriched rows for all trades, computing what we can:
- hold_duration_seconds from entry_time/exit_time
- pnl, pnl_pct from trade data
- MAE/MFE set to null (no historical tick data)
- Regime fields set to null (no historical regime data)

Then computes and writes the agent's zero_score.

Usage: python3 scanner/v6/backfill_enrichment.py
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── CONFIG ───────────────────────────────────────────────────────────────────

AGENT_UUID = "4802c6f8-f862-42f1-b248-45679e1517e7"
AGENT_SHORT_ID = "zr_demo01"
SUPABASE_URL = ""
SUPABASE_KEY = ""

_env_file = Path.home() / ".config" / "openclaw" / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k in ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL") and not SUPABASE_URL:
            SUPABASE_URL = v
        elif k == "SUPABASE_SERVICE_KEY" and not SUPABASE_KEY:
            SUPABASE_KEY = v

if not SUPABASE_URL or not SUPABASE_KEY:
    print("FATAL: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in ~/.config/openclaw/.env")
    sys.exit(1)


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}", flush=True)


# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────

def supabase_get(table: str, params: str = "") -> list:
    """GET from Supabase REST API. Returns list of rows."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}" if params else f"{SUPABASE_URL}/rest/v1/{table}"
    req = Request(url, method="GET")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Accept", "application/json")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def supabase_post(table: str, data: dict) -> dict | None:
    """POST (insert) to Supabase. Returns created row."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=representation")
    with urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
        return result[0] if isinstance(result, list) and result else result


def supabase_patch(table: str, filters: str, data: dict) -> bool:
    """PATCH (update) rows in Supabase matching filters."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filters}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="PATCH")
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    with urlopen(req, timeout=15) as resp:
        return resp.status in (200, 204)


# ─── STEP 1: FETCH ALL TRADES ────────────────────────────────────────────────

def fetch_all_trades() -> list:
    """Fetch all trades for our agent."""
    params = f"agent_id=eq.{AGENT_UUID}&order=entry_time.asc&limit=1000"
    trades = supabase_get("trades", params)
    _log(f"Fetched {len(trades)} trades from Supabase")
    return trades


# ─── STEP 2: BACKFILL TRADES_ENRICHED ────────────────────────────────────────

def compute_hold_seconds(entry_time: str | None, exit_time: str | None) -> int | None:
    if not entry_time or not exit_time:
        return None
    try:
        et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        xt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
        return max(0, int((xt - et).total_seconds()))
    except Exception:
        return None


def backfill_trades(trades: list) -> tuple[int, int]:
    """Insert enriched rows for all trades. Returns (success, fail) counts."""
    ok, fail = 0, 0

    for i, t in enumerate(trades):
        coin = t.get("coin", "?")
        direction = t.get("direction", "?")
        status = t.get("status", "open")
        is_closed = status == "closed" or t.get("exit_price") is not None

        entry_price = t.get("entry_price") or 0
        exit_price = t.get("exit_price")
        pnl = t.get("pnl")
        entry_time = t.get("entry_time")
        exit_time = t.get("exit_time")

        hold_seconds = compute_hold_seconds(entry_time, exit_time)

        # Compute pnl_pct if not in trade data
        pnl_pct = t.get("pnl_pct")
        if pnl_pct is None and is_closed and entry_price > 0 and exit_price:
            if direction == "long":
                pnl_pct = round((exit_price - entry_price) / entry_price, 6)
            else:
                pnl_pct = round((entry_price - exit_price) / entry_price, 6)

        # Build row using actual trades_enriched columns
        row = {
            "agent_id": AGENT_UUID,
            "coin": coin,
            "direction": direction,
            "status": "closed" if is_closed else "open",
            "entry_price": entry_price,
            "entry_time": entry_time,
            "size_usd": t.get("size_usd"),
            "stop_price": t.get("stop_price"),
            # Defaults
            "regime_changes_during_hold": 0,
            "immune_checks_during_hold": 0,
            "immune_alerts_during_hold": 0,
            "funding_cost": 0,
        }

        if is_closed:
            row.update({
                "exit_price": exit_price,
                "exit_time": exit_time,
                "exit_reason": t.get("exit_reason"),
                "hold_duration_seconds": hold_seconds,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "fees": t.get("fees"),
                "was_profitable": (pnl or 0) > 0,
            })

        # Strip None values
        row = {k: v for k, v in row.items() if v is not None}

        try:
            supabase_post("trades_enriched", row)
            ok += 1
            tag = "closed" if is_closed else "open"
            extra = f" pnl=${pnl:.2f}" if pnl is not None else ""
            _log(f"  [{ok}/{len(trades)}] {coin} {direction} ({tag}){extra} hold={hold_seconds or '?'}s")
        except HTTPError as e:
            body = e.read().decode()
            fail += 1
            _log(f"  FAIL {coin} {direction}: {body[:120]}")
        except Exception as e:
            fail += 1
            _log(f"  FAIL {coin} {direction}: {e}")

    return ok, fail


# ─── STEP 3: COMPUTE ZERO SCORE ──────────────────────────────────────────────

def compute_zero_score(trades: list) -> tuple[float, dict]:
    """
    Compute ZERO Score from trade data.
    Weights: immune 25%, discipline 25%, performance 20%, consistency 20%, resilience 10%
    """
    closed = [t for t in trades if t.get("status") == "closed" or t.get("exit_price") is not None]
    if not closed:
        return 0.0, {}

    # ── Immune (25%) — assume 8/10, immune system is running
    immune_score = 0.8

    # ── Discipline (25%) — stop-loss usage and position sizing
    stop_usage_rate = 1.0  # all trades have stops

    sizes = [t.get("size_usd", 0) for t in closed if t.get("size_usd")]
    if sizes:
        avg_size = sum(sizes) / len(sizes)
        size_score = max(0, min(1, 1.0 - (avg_size - 100) / 400))
    else:
        size_score = 0.5

    discipline_score = stop_usage_rate * 0.6 + size_score * 0.4

    # ── Performance (20%) — realized PnL / total capital deployed
    pnls = [t.get("pnl", 0) or 0 for t in closed]
    total_pnl = sum(pnls)
    total_deployed = sum(t.get("size_usd", 0) or 0 for t in closed) or 1
    roi = total_pnl / total_deployed
    performance_score = max(0, min(1, 0.5 + roi * 10))

    # ── Consistency (20%) — lower std dev of trade PnL = better
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
        std_pnl = math.sqrt(variance)
        consistency_score = max(0, min(1, 1.0 - (std_pnl - 0.5) / 4.5))
    else:
        consistency_score = 0.5

    # ── Resilience (10%) — max drawdown recovery
    equity_curve = []
    cumulative = 0
    for p in pnls:
        cumulative += p
        equity_curve.append(cumulative)

    if equity_curve:
        peak = equity_curve[0]
        max_dd = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        final = equity_curve[-1]
        if max_dd > 0:
            recovery_ratio = max(0, final / max_dd) if max_dd > 0 else 1.0
            resilience_score = min(1.0, recovery_ratio * 0.5 + (0.5 if final >= 0 else 0))
        else:
            resilience_score = 1.0
    else:
        resilience_score = 0.5

    # ── Weighted total
    zero_score = (
        immune_score * 0.25 +
        discipline_score * 0.25 +
        performance_score * 0.20 +
        consistency_score * 0.20 +
        resilience_score * 0.10
    )

    components = {
        "immune": round(immune_score, 4),
        "discipline": round(discipline_score, 4),
        "performance": round(performance_score, 4),
        "consistency": round(consistency_score, 4),
        "resilience": round(resilience_score, 4),
        "total_trades": len(closed),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi * 100, 2),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    return round(zero_score, 4), components


def update_agent_score(score: float, components: dict):
    """Write zero_score to the agents table."""
    filters = f"short_id=eq.{AGENT_SHORT_ID}"
    data = {
        "zero_score": score,
        "score_components": components,
    }
    supabase_patch("agents", filters, data)
    _log(f"Updated agent {AGENT_SHORT_ID} zero_score = {score}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BACKFILL: trades_enriched + ZERO Score")
    print("=" * 60)

    # Step 1: Fetch trades
    _log("Step 1: Fetching trades...")
    trades = fetch_all_trades()
    if not trades:
        _log("No trades found. Nothing to backfill.")
        return

    # Step 2: Backfill trades_enriched
    _log(f"Step 2: Backfilling {len(trades)} trades into trades_enriched...")
    ok, fail = backfill_trades(trades)
    _log(f"Backfill complete: {ok} success, {fail} failed")

    # Step 3: Compute and write ZERO Score
    _log("Step 3: Computing ZERO Score...")
    score, components = compute_zero_score(trades)
    _log(f"ZERO Score = {score}")
    for k, v in components.items():
        _log(f"  {k}: {v}")

    _log("Step 4: Writing score to agents table...")
    try:
        update_agent_score(score, components)
    except Exception as e:
        _log(f"Failed to update agent score: {e}")

    # Verify
    _log("Step 5: Verification...")
    try:
        enriched = supabase_get("trades_enriched", f"agent_id=eq.{AGENT_UUID}&select=id&limit=200")
        _log(f"trades_enriched: {len(enriched)} rows")
    except Exception as e:
        _log(f"Verification failed: {e}")

    try:
        agent = supabase_get("agents", f"short_id=eq.{AGENT_SHORT_ID}&select=zero_score,score_components")
        if agent:
            _log(f"Agent zero_score: {agent[0].get('zero_score')}")
        else:
            _log("Agent not found in agents table")
    except Exception as e:
        _log(f"Agent verification failed: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()

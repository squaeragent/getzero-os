#!/usr/bin/env /opt/homebrew/bin/python3
"""
ZERO OS — Signal Logger
========================
Logs every signal that fires (regardless of filter) with full indicator
context, then looks up price outcome after 24h to build a training dataset.

Writes:
    scanner/memory/signal_outcomes.jsonl  — all fires + eventual outcome

Usage (from hypothesis_generator.py):
    from scanner.indicators.signal_logger import log_all_fires, resolve_outcomes

    # After evaluating signals:
    log_all_fires(fired_signals, world_state)

    # (Optional) Resolve yesterday's pending outcomes:
    resolve_outcomes()
"""

import json
import math
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent
SCANNER_DIR   = SCRIPT_DIR.parent
MEMORY_DIR    = SCANNER_DIR / "memory"
OUTCOMES_FILE = MEMORY_DIR / "signal_outcomes.jsonl"
DRIFT_LOG     = SCRIPT_DIR / "drift_log.jsonl"

# ─── Logging ─────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


# ─── Hyperliquid price lookup ─────────────────────────────────────────────────

def _fetch_price_at(coin: str, timestamp_ms: int) -> float | None:
    """
    Fetch the close price of `coin` nearest to `timestamp_ms` (1h candle).
    Returns None on error.
    """
    try:
        url     = "https://api.hyperliquid.xyz/info"
        start   = timestamp_ms - 3_600_000   # 1h before
        end     = timestamp_ms + 3_600_000   # 1h after
        payload = json.dumps({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": "1h",
                    "startTime": start, "endTime": end}
        }).encode()
        req  = urllib.request.Request(url, data=payload,
                                       headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if resp:
            # Pick candle closest to target timestamp
            best = min(resp, key=lambda c: abs(c["t"] - timestamp_ms))
            return float(best["c"])
    except Exception:
        pass
    return None


# ─── Core: log fires ─────────────────────────────────────────────────────────

def log_all_fires(fired_signals: list, world_state: dict) -> None:
    """
    Write every fired signal to signal_outcomes.jsonl.

    fired_signals: list of dicts, each must contain at minimum:
        {
            "coin":         str,
            "direction":    "LONG" | "SHORT",
            "signal_name":  str,
            "score":        float,     # optional
            "passed_filter": bool,     # optional — did it survive filters?
        }

    world_state: the world_state dict from scanner/bus/world_state.json
        Used to capture indicator snapshot at fire time.
    """
    if not fired_signals:
        return

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    ts_now = datetime.now(timezone.utc)
    ts_iso = ts_now.isoformat()
    ts_ms  = int(ts_now.timestamp() * 1000)

    # Resolve timestamp ~24h from now for outcome check
    outcome_check_ms = ts_ms + 86_400_000  # +24h

    written = 0
    with open(OUTCOMES_FILE, "a") as f:
        for sig in fired_signals:
            coin = sig.get("coin", "UNKNOWN")

            # Snapshot all indicator values for this coin from world_state
            coin_ws      = world_state.get("coins", {}).get(coin, {})
            indicators   = coin_ws.get("indicators", {})
            own_inds     = coin_ws.get("indicators_own", {})
            regime       = coin_ws.get("regime", "unknown")
            funding      = coin_ws.get("funding", {})
            liquidity    = coin_ws.get("liquidity", {})

            record = {
                # ── Signal metadata ──
                "id":              f"{coin}_{sig.get('signal_name', 'UNK')}_{ts_ms}",
                "timestamp":       ts_iso,
                "timestamp_ms":    ts_ms,
                "coin":            coin,
                "direction":       sig.get("direction"),
                "signal_name":     sig.get("signal_name"),
                "score":           sig.get("score"),
                "passed_filter":   sig.get("passed_filter", True),

                # ── Market context at fire time ──
                "regime":          regime,
                "funding_rate":    funding.get("rate"),
                "liquidity_score": liquidity.get("score"),
                "tradeable":       liquidity.get("tradeable"),

                # ── Indicator snapshot (Envy source) ──
                "indicators":      {k: round(v, 6) if isinstance(v, float) else v
                                    for k, v in indicators.items()},

                # ── Our own computed indicators (if available) ──
                "indicators_own":  {k: round(v, 6) if isinstance(v, float) else v
                                    for k, v in own_inds.items()},

                # ── Outcome (filled in later by resolve_outcomes) ──
                "outcome_check_ms":  outcome_check_ms,
                "entry_price":       None,
                "exit_price":        None,
                "price_change_pct":  None,
                "outcome":           "pending",
            }

            f.write(json.dumps(record) + "\n")
            written += 1

    _log(f"[signal_logger] Logged {written} signal fires to {OUTCOMES_FILE}")


# ─── Resolve pending outcomes ─────────────────────────────────────────────────

def resolve_outcomes(max_resolve: int = 50) -> int:
    """
    Scan signal_outcomes.jsonl for pending entries whose outcome_check_ms
    has passed, fetch HL prices, and update the outcome.

    Returns number of outcomes resolved.
    """
    if not OUTCOMES_FILE.exists():
        return 0

    now_ms   = int(time.time() * 1000)
    records  = []
    resolved = 0

    with open(OUTCOMES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    updated = []
    for rec in records:
        if rec.get("outcome") != "pending":
            updated.append(rec)
            continue

        check_ms = rec.get("outcome_check_ms", 0)
        if check_ms > now_ms:
            updated.append(rec)
            continue

        # Time to resolve this one
        coin         = rec.get("coin")
        ts_ms        = rec.get("timestamp_ms")
        direction    = rec.get("direction", "LONG")

        if ts_ms and coin and coin != "UNKNOWN":
            entry_price = _fetch_price_at(coin, ts_ms)
            exit_price  = _fetch_price_at(coin, check_ms)

            if entry_price and exit_price and entry_price > 0:
                pct_change = (exit_price - entry_price) / entry_price * 100
                if direction == "LONG":
                    outcome = "win" if pct_change > 0 else "loss"
                else:
                    outcome = "win" if pct_change < 0 else "loss"

                rec["entry_price"]      = round(entry_price, 6)
                rec["exit_price"]       = round(exit_price, 6)
                rec["price_change_pct"] = round(pct_change, 4)
                rec["outcome"]          = outcome
                resolved += 1
            else:
                rec["outcome"] = "data_unavailable"
                resolved += 1
        else:
            rec["outcome"] = "data_unavailable"
            resolved += 1

        updated.append(rec)

        if resolved >= max_resolve:
            break

        time.sleep(0.2)

    if resolved > 0:
        # Rewrite the file atomically
        tmp = OUTCOMES_FILE.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            for rec in updated:
                f.write(json.dumps(rec) + "\n")
        tmp.replace(OUTCOMES_FILE)
        _log(f"[signal_logger] Resolved {resolved} pending outcomes")

    return resolved


# ─── Drift logger (for perception.py integration) ────────────────────────────

def log_indicator_drift(coin: str, indicator: str,
                        theirs: float, ours: float) -> None:
    """
    Log a delta between Envy (theirs) and our computed value (ours).
    Appended to scanner/indicators/drift_log.jsonl.
    """
    if theirs is None or ours is None:
        return
    if math.isnan(theirs) or math.isnan(ours):
        return

    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    delta = ours - theirs
    delta_pct = (delta / theirs * 100) if theirs != 0 else None

    rec = {
        "timestamp": ts,
        "coin":      coin,
        "indicator": indicator,
        "theirs":    round(theirs, 6),
        "ours":      round(ours, 6),
        "delta":     round(delta, 6),
        "delta_pct": round(delta_pct, 4) if delta_pct is not None else None,
    }
    with open(DRIFT_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ─── Entrypoint (standalone outcome resolution) ────────────────────────────────

if __name__ == "__main__":
    _log("Running outcome resolution pass...")
    n = resolve_outcomes()
    _log(f"Done. Resolved {n} outcomes.")

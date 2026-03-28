#!/usr/bin/env python3
"""
ZERO OS — Regime Transition Predictor
Runs every 5 min. Reads regime_history.jsonl (last 50 lines) and predicts
upcoming regime transitions per coin using drift pattern detection.

Signals:
  1. Hurst drift      — hurst_24h dropping/rising over last 6 readings
  2. Hurst-DFA divergence — indicators disagree on trending/reverting
  3. Lyapunov surge   — chaos rising fast
  4. Regime flicker   — many regime changes in lookback window
  5. Cross-indicator disagreement — hurst_trend != dfa_trend

Output: scanner/bus/regime_predictions.json
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    save_json, make_logger, update_heartbeat,
    SCANNER_DIR, BUS_DIR,
)

# ─── PATHS ───
AGENT_DIR   = Path(__file__).parent

REGIME_HISTORY_FILE    = BUS_DIR / "regime_history.jsonl"
REGIME_PREDICTIONS_FILE = BUS_DIR / "regime_predictions.json"

CYCLE_SECONDS = 300   # 5 minutes
LOOKBACK      = 20    # entries to analyse per coin
TAIL_LINES    = 50    # only read last N lines from potentially large file


# ─── LOGGING ───
log = make_logger("REGIME_TRANSITION")


# ─── HEARTBEAT ───
def write_heartbeat():
    update_heartbeat("regime_transition")


# ─── READ REGIME HISTORY (EFFICIENTLY) ───
def read_recent_regime_history(tail_lines: int = TAIL_LINES) -> list:
    """
    Read only the last `tail_lines` lines of regime_history.jsonl.
    Returns list of parsed dicts, oldest first.
    Efficient for large files — reads from end without loading full file.
    """
    if not REGIME_HISTORY_FILE.exists():
        return []

    entries = []
    try:
        with open(REGIME_HISTORY_FILE, "rb") as f:
            # Seek to end, walk back to find last N newlines
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return []

            chunk_size = min(65536, file_size)  # 64KB chunks
            buf = b""
            pos = file_size
            lines_found = 0

            while pos > 0 and lines_found < tail_lines + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                buf = chunk + buf
                lines_found = buf.count(b"\n")

            # Split and take last tail_lines non-empty lines
            raw_lines = buf.decode("utf-8", errors="replace").splitlines()
            raw_lines = [l.strip() for l in raw_lines if l.strip()]
            raw_lines = raw_lines[-tail_lines:]

        for line in raw_lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError as e:
        log(f"Failed to read regime_history: {e}")

    return entries


# ─── EXTRACT SIGNAL FAMILY (shared helper, mirrors genealogy.py) ───
def extract_signal_family(signal_name: str) -> str:
    """Extract the conceptual family from a full signal name."""
    if not signal_name:
        return signal_name
    parts = signal_name.split("_")
    family_parts = []
    for p in parts:
        # Stop at version/variant markers
        if any(p.startswith(prefix) for prefix in ["V", "EX", "Q", "MH"]):
            break
        # Stop at direction indicators if they're at the end
        if p in ("LONG", "SHORT") and family_parts:
            break
        family_parts.append(p)
    return "_".join(family_parts) if family_parts else signal_name


# ─── CORE PREDICTOR ───
def predict_transitions(recent_entries: list, lookback: int = LOOKBACK) -> dict:
    """
    For each coin, analyse last N regime_history entries.
    Detect drift patterns that precede transitions.

    Returns dict: {coin: prediction_dict}
    """
    if not recent_entries:
        return {}

    # Collect all coin names seen across entries
    all_coins: set = set()
    for entry in recent_entries:
        all_coins.update(entry.get("coins", {}).keys())

    # Use only the last `lookback` entries
    window = recent_entries[-lookback:]

    predictions = {}

    for coin in all_coins:
        coin_history = [entry.get("coins", {}).get(coin, {}) for entry in window]
        # Remove entries where this coin was absent
        coin_history = [h for h in coin_history if h]

        if not coin_history:
            continue

        # ── Extract time series ──
        hursts  = [h.get("hurst_24h",    0.5)   for h in coin_history]
        dfas    = [h.get("dfa_24h",      0.5)   for h in coin_history]
        lyaps   = [h.get("lyapunov_24h", 1.5)   for h in coin_history]
        regimes = [h.get("regime",       "stable") for h in coin_history]
        hurst_trends = [h.get("hurst_trend", "flat") for h in coin_history]
        dfa_trends   = [h.get("dfa_trend",   "flat") for h in coin_history]

        transition_probability = 0.0
        predicted_direction    = None

        # ── Signal 1: Hurst drift (slope over last 6 readings) ──
        hurst_slope = None
        if len(hursts) >= 6:
            hurst_slope = (hursts[-1] - hursts[-6]) / 6  # per-reading change
            if abs(hurst_slope) > 0.012:  # >0.07 over 6 readings
                transition_probability += 0.3
                predicted_direction = "destabilizing" if hurst_slope < 0 else "stabilizing"

        # ── Signal 2: Hurst-DFA divergence ──
        # trending: > 0.55, reverting: < 0.45; divergence = they disagree
        if len(hursts) >= 3 and len(dfas) >= 3:
            h_last = hursts[-1]
            d_last = dfas[-1]
            h_trending  = h_last > 0.55
            h_reverting = h_last < 0.45
            d_trending  = d_last > 0.55
            d_reverting = d_last < 0.45
            divergent = (h_trending and d_reverting) or (h_reverting and d_trending)
            if divergent:
                transition_probability += 0.1  # mild signal on its own
                if predicted_direction is None:
                    predicted_direction = "destabilizing"

        # ── Signal 3: Lyapunov surge ──
        lyap_delta = None
        if len(lyaps) >= 3:
            lyap_delta = lyaps[-1] - lyaps[-3]
            if lyap_delta > 0.3:
                transition_probability += 0.25
                predicted_direction = "destabilizing"

        # ── Signal 4: Regime flicker (changes in last 10 readings) ──
        flicker_window = regimes[-10:]
        regime_changes = sum(
            1 for i in range(1, len(flicker_window))
            if flicker_window[i] != flicker_window[i - 1]
        )
        if regime_changes >= 2:
            transition_probability += 0.2

        # ── Signal 5: Cross-indicator disagreement ──
        disagreements = sum(
            1 for i in range(len(hurst_trends))
            if hurst_trends[i] != dfa_trends[i]
        )
        if disagreements >= 3:
            transition_probability += 0.15

        # ── Clamp ──
        transition_probability = min(1.0, transition_probability)

        predictions[coin] = {
            "transition_probability": round(transition_probability, 3),
            "predicted_direction":    predicted_direction,
            "hurst_slope":            round(hurst_slope, 5) if hurst_slope is not None else None,
            "lyap_delta":             round(lyap_delta, 4)  if lyap_delta is not None else None,
            "regime_flicker":         regime_changes,
            "indicator_disagreement": disagreements,
            "current_regime":         regimes[-1] if regimes else "unknown",
        }

    return predictions


# ─── RUN CYCLE ───
def run_cycle():
    ts    = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()

    log("=" * 60)
    log(f"Regime Transition Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")

    recent_entries = read_recent_regime_history(TAIL_LINES)
    log(f"  Loaded {len(recent_entries)} recent regime history entries")

    if not recent_entries:
        log("  No regime history — skipping")
        write_heartbeat()
        return

    predictions = predict_transitions(recent_entries, lookback=LOOKBACK)

    if not predictions:
        log("  No coins found in history — skipping")
        write_heartbeat()
        return

    # ── Derived fields ──
    high_risk_coins = [
        coin for coin, pred in predictions.items()
        if pred["transition_probability"] > 0.5
    ]
    probs = [p["transition_probability"] for p in predictions.values()]
    market_stability = round(1.0 - (sum(probs) / len(probs)), 4) if probs else 1.0

    output = {
        "timestamp":        ts_iso,
        "predictions":      predictions,
        "high_risk_coins":  high_risk_coins,
        "market_stability": market_stability,
    }

    save_json(REGIME_PREDICTIONS_FILE, output)
    write_heartbeat()

    log(f"  Coins analysed:  {len(predictions)}")
    log(f"  High-risk coins: {high_risk_coins}")
    log(f"  Market stability: {market_stability:.3f}")

    if high_risk_coins:
        for coin in high_risk_coins:
            p = predictions[coin]
            log(
                f"    ⚠ {coin:8s}  prob={p['transition_probability']:.0%}  "
                f"dir={p['predicted_direction'] or '?'}  "
                f"regime={p['current_regime']}"
            )

    log(f"  Written to {REGIME_PREDICTIONS_FILE}")
    log("=" * 60)


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Regime Transition Predictor starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

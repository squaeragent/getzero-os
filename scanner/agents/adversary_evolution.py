#!/usr/bin/env python3
"""
ZERO OS — Adversary Evolution Agent
Reads counterfactual_log.jsonl and adjusts attack weights based on precision.

Runs every 2 hours via supervisor.

Logic:
  For each attack, compute precision = correct_kills / (correct_kills + false_kills)
  If precision > 0.7 (attack is good): strengthen weight * 1.2
  If precision < 0.4 (attack is bad): weaken weight * 0.6
  Require 20+ samples per attack before adjusting.
  Write evolved weights to scanner/bus/evolved_weights.json.

Usage:
  python3 scanner/agents/adversary_evolution.py        # single run
  python3 scanner/agents/adversary_evolution.py --loop # continuous 7200s cycle
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    load_json, save_json, read_jsonl, make_logger, update_heartbeat,
    BUS_DIR, MEMORY_DIR,
)

log = make_logger("EVOLUTION")

# ─── PATHS ───
COUNTERFACTUAL_LOG = MEMORY_DIR / "counterfactual_log.jsonl"
EVOLVED_WEIGHTS_FILE = BUS_DIR / "evolved_weights.json"

CYCLE_SECONDS = 7200       # 2 hours
MIN_SAMPLES = 10           # minimum data points per attack before evolving
SEVERITY_THRESHOLD = 0.2   # attack severity must exceed this to count


# ─── DEFAULT WEIGHTS (mirrors adversary.py) ───
DEFAULT_WEIGHTS = {
    "similar_failure":          1.5,
    "kill_condition_proximity": 1.0,
    "portfolio_stress":         1.0,
    "confidence_vs_antithesis": 1.0,
    "regime_mismatch":          1.0,
    "funding_headwind":         1.0,
    "oi_divergence":            1.0,
    "rule_based":               1.2,
    "macro_regime":             1.2,
    "session_risk":             1.0,
}


# ─── LOAD COUNTERFACTUAL LOG ───
def load_counterfactual_log():
    """Load all resolved counterfactual records from JSONL."""
    return read_jsonl(COUNTERFACTUAL_LOG)


# ─── COMPUTE ATTACK PRECISION ───
def compute_attack_stats(records):
    """
    For each attack, count correct kills and false kills.
    Only count entries where the attack had severity > SEVERITY_THRESHOLD.

    Returns dict: attack_name → {"correct": int, "false": int, "precision": float, "samples": int}
    """
    stats = {}

    for rec in records:
        # Use `adversary_correct` field (set by counterfactual agent).
        # Skip inconclusive records — they don't count toward accuracy stats.
        resolution = rec.get("resolution")  # "correct_kill", "false_kill", "inconclusive"
        adversary_correct = rec.get("adversary_correct")  # True, False, or None
        would_have_won = rec.get("would_have_won")        # True, False, or None

        # Skip inconclusive entries (neither condition was clearly met)
        if resolution == "inconclusive" or adversary_correct is None:
            continue

        killing_attacks = rec.get("killing_attacks", [])

        for atk in killing_attacks:
            name = atk.get("attack", "")
            severity = atk.get("severity", 0.0)

            if not name or severity <= SEVERITY_THRESHOLD:
                continue

            if name not in stats:
                stats[name] = {"correct": 0, "false": 0}

            if adversary_correct:
                stats[name]["correct"] += 1
            elif would_have_won:
                # False kill: adversary killed it but trade would have won
                stats[name]["false"] += 1
            # If neither (pnl unavailable), don't count

    # Compute precision
    result = {}
    for name, counts in stats.items():
        total = counts["correct"] + counts["false"]
        precision = counts["correct"] / total if total > 0 else 0.0
        result[name] = {
            "correct": counts["correct"],
            "false": counts["false"],
            "samples": total,
            "precision": round(precision, 4),
        }

    return result


# ─── EVOLVE WEIGHTS ───
def evolve_weights(attack_stats):
    """
    Compute evolved weight for each attack based on precision.
    Only modifies attacks with >= MIN_SAMPLES data points.

    Returns dict: attack_name → evolved_weight_float
    """
    evolved = {}

    for attack_name, default_weight in DEFAULT_WEIGHTS.items():
        astats = attack_stats.get(attack_name)

        if astats is None or astats["samples"] < MIN_SAMPLES:
            evolved[attack_name] = default_weight
            continue

        precision = astats["precision"]

        if precision > 0.7:
            new_weight = default_weight * 1.2
            direction = "strengthened"
        elif precision < 0.4:
            new_weight = default_weight * 0.6
            direction = "weakened"
        else:
            new_weight = default_weight
            direction = "neutral"

        # Clamp
        new_weight = max(0.3, min(2.5, new_weight))
        evolved[attack_name] = round(new_weight, 4)

        if astats["samples"] >= MIN_SAMPLES:
            log(
                f"{attack_name}: precision {precision*100:.0f}% "
                f"({astats['samples']} samples) → weight {default_weight} → {new_weight:.4f} "
                f"({direction})"
            )

    return evolved


# ─── MAIN RUN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    log("=" * 60)
    log(f"Evolution Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log("=" * 60)

    records = load_counterfactual_log()
    log(f"Loaded {len(records)} resolved counterfactuals")

    if not records:
        log("No counterfactual data yet — nothing to evolve")
        update_heartbeat("adversary_evolution")
        return

    attack_stats = compute_attack_stats(records)
    log(f"Attack stats computed for {len(attack_stats)} attacks")

    evolved = evolve_weights(attack_stats)

    # Build output with full detail
    now_iso = ts.isoformat()
    weights_output = {
        "timestamp": now_iso,
        "data_points": len(records),
        "weights": {},
    }

    for attack_name, default_weight in DEFAULT_WEIGHTS.items():
        astats = attack_stats.get(attack_name, {})
        samples = astats.get("samples", 0)
        precision = astats.get("precision", None)
        evolved_w = evolved.get(attack_name, default_weight)

        weights_output["weights"][attack_name] = {
            "default": default_weight,
            "evolved": evolved_w,
            "precision": precision,
            "samples": samples,
        }

    save_json(EVOLVED_WEIGHTS_FILE, weights_output)
    log(f"Written evolved weights to {EVOLVED_WEIGHTS_FILE}")

    # Summary
    changed = [
        (k, v) for k, v in weights_output["weights"].items()
        if v["evolved"] != v["default"] and v["samples"] >= MIN_SAMPLES
    ]
    if changed:
        log(f"Changed weights: {len(changed)}")
        for name, w in changed:
            delta = w["evolved"] - w["default"]
            direction = "↑ strengthened" if delta > 0 else "↓ weakened"
            log(f"  {name}: {w['default']} → {w['evolved']} {direction} (precision={w['precision']:.0%}, n={w['samples']})")
    else:
        log("No weight changes (insufficient data or all neutral)")

    update_heartbeat("adversary_evolution")
    log("=" * 60)


# ─── ENTRYPOINT ───
def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Adversary evolution starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                update_heartbeat("adversary_evolution")
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

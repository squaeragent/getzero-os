#!/usr/bin/env python3
"""
ZERO OS — Agent 6B: Parameter Evolution (Cognitive Loop Phase 5)
Replaces signal_evolution_agent.py with data-driven rule generation.

Analyzes closed trades every 10 minutes. Generates testable rules after 20+ trades.
Manages full rule lifecycle: proposed → probation → active → killed.

Inputs:
  scanner/data/live/closed.jsonl         — all closed trades
  scanner/memory/observations.jsonl      — structured observations from observer
  scanner/memory/rules/*.json            — rule lifecycle files
  scanner/bus/world_state.json           — current world model
  scanner/bus/signal_weights.json        — current signal weights (reference)
  scanner/memory/meta.json               — tracks evolution cycles

Outputs:
  scanner/memory/rules/proposed.json     — newly generated rule proposals
  scanner/memory/rules/probation.json    — rules being tested
  scanner/memory/rules/active.json       — rules that passed validation
  scanner/memory/rules/killed.json       — rules that failed validation
  scanner/bus/signal_weights.json        — updated signal weights (backward compat)
  scanner/bus/heartbeat.json             — heartbeat

Usage:
  python3 scanner/agents/parameter_evolution.py           # single run
  python3 scanner/agents/parameter_evolution.py --loop    # continuous 10-min cycle
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"
MEMORY_DIR = SCANNER_DIR / "memory"
RULES_DIR = MEMORY_DIR / "rules"

CLOSED_FILE = LIVE_DIR / "closed.jsonl"
OBSERVATIONS_FILE = MEMORY_DIR / "observations.jsonl"
WORLD_STATE_FILE = BUS_DIR / "world_state.json"
SIGNAL_WEIGHTS_FILE = BUS_DIR / "signal_weights.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
META_FILE = MEMORY_DIR / "meta.json"

PROPOSED_FILE = RULES_DIR / "proposed.json"
ACTIVE_FILE = RULES_DIR / "active.json"
PROBATION_FILE = RULES_DIR / "probation.json"
KILLED_FILE = RULES_DIR / "killed.json"

# ─── CONFIG ───
CYCLE_SECONDS = 600           # 10 minutes
MIN_TRADES_FOR_ANALYSIS = 20  # start rule generation after 20 trades
MIN_TRADES_FOR_PROBATION = 10 # need 10 trades matching rule conditions
REGIME_WR_BOOST_THRESHOLD = 60.0   # WR > 60%: propose boost
REGIME_WR_BLOCK_THRESHOLD = 30.0   # WR < 30%: propose block
DIRECTION_LONG_PENALIZE = 35.0     # LONG WR < 35%: reduce confidence
DIRECTION_SHORT_BOOST = 65.0       # SHORT WR > 65%: boost confidence
MAX_ACTIVE_RULES = 50              # cap active rules
FATIGUE_WINDOW_HOURS = 24          # signal fatigue window

# Weight thresholds (backward compat with signal_evolution_agent)
REGIME_WR_PENALIZE = 40.0
REGIME_WR_BOOST_WEIGHT = 60.0
MIN_REGIME_TRADES = 3
MIN_OVERALL_TRADES = 5
FATIGUE_THRESHOLD = 3
FATIGUE_MULTIPLIER = 0.7


# ─── LOGGING ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [PARAM_EVOLUTION] {msg}")


# ─── FILE HELPERS ───
def load_json_safe(path, default=None):
    if default is None:
        default = {}
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return default
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_jsonl(path, max_lines=2000):
    """Load lines from a JSONL file."""
    p = Path(path)
    if not p.exists():
        return []
    lines = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return lines[-max_lines:]


# ─── META ───
def load_meta():
    return load_json_safe(META_FILE, {
        "last_reflection": None,
        "last_evolution_analysis": None,
        "reflection_cycle": 0,
        "evolution_generation": 0,
        "total_trades_at_last_evolution": 0,
    })


def save_meta(meta):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    save_json(META_FILE, meta)


# ─── RULE STORE HELPERS ───
def load_rules(path):
    rules = load_json_safe(path, default=[])
    if not isinstance(rules, list):
        return []
    return rules


def save_rules(path, rules):
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    save_json(path, rules)


def next_rule_id():
    """Generate a unique rule ID based on all existing rules."""
    all_ids = set()
    for f in [PROPOSED_FILE, ACTIVE_FILE, PROBATION_FILE, KILLED_FILE]:
        for r in load_rules(f):
            all_ids.add(r.get("id", ""))
    n = len(all_ids) + 1
    rule_id = f"RULE_{n:03d}"
    while rule_id in all_ids:
        n += 1
        rule_id = f"RULE_{n:03d}"
    return rule_id


def rule_exists(condition, action, proposed, probation, active):
    """Check if a rule with same condition+action already exists."""
    for rules in [proposed, probation, active]:
        for r in rules:
            if r.get("condition") == condition and r.get("action") == action:
                return True
    return False


def make_rule(condition, action, value, evidence, source="parameter_evolution", generation=1):
    """Build a rule dict."""
    return {
        "id": next_rule_id(),
        "condition": condition,
        "action": action,
        "value": value,
        "evidence": evidence,
        "generation": generation,
        "created": datetime.now(timezone.utc).isoformat(),
        "trades_tested": 0,
        "status": "proposed",
        "source": source,
    }


# ─── TRADE ANALYSIS ───
def load_closed_trades():
    trades = load_jsonl(CLOSED_FILE, max_lines=2000)
    trades.sort(key=lambda t: t.get("exit_time", ""))
    return trades


def load_observations():
    return load_jsonl(OBSERVATIONS_FILE, max_lines=1000)


def compute_stats_for_group(trades):
    """Return (win_rate, count, net_pnl) for a group of trades."""
    if not trades:
        return 0.0, 0, 0.0
    wins = sum(1 for t in trades if t.get("pnl_dollars", t.get("pnl_usd", 0)) > 0)
    count = len(trades)
    net = sum(t.get("pnl_dollars", t.get("pnl_usd", 0)) for t in trades)
    return round(wins / count * 100, 1), count, round(net, 2)


# ─── RULE GENERATION ANALYSES ───

def analyze_by_regime(trades):
    """Win rate per regime → propose boost/block rules."""
    proposals = []
    regime_groups = {}
    for t in trades:
        r = t.get("regime") or t.get("metadata", {}).get("regime", "unknown")
        regime_groups.setdefault(r, []).append(t)

    for regime, group in regime_groups.items():
        if regime == "unknown" or len(group) < 3:
            continue
        wr, count, _ = compute_stats_for_group(group)
        log(f"    Regime '{regime}': {wr}% WR over {count} trades")

        if wr > REGIME_WR_BOOST_THRESHOLD:
            proposals.append({
                "condition": f"regime == '{regime}'",
                "action": "boost_confidence",
                "value": 0.10,
                "evidence": f"{wr}% WR over {count} trades in {regime} regime",
            })
        elif wr < REGIME_WR_BLOCK_THRESHOLD:
            proposals.append({
                "condition": f"regime == '{regime}'",
                "action": "reduce_confidence",
                "value": 0.15,
                "evidence": f"Only {wr}% WR over {count} trades in {regime} regime",
            })

    return proposals


def analyze_by_direction(trades):
    """Win rate by LONG/SHORT → propose direction rules."""
    proposals = []
    long_trades = [t for t in trades if t.get("direction", t.get("side", "")).upper() == "LONG"]
    short_trades = [t for t in trades if t.get("direction", t.get("side", "")).upper() == "SHORT"]

    if long_trades:
        wr, count, _ = compute_stats_for_group(long_trades)
        log(f"    LONG: {wr}% WR over {count} trades")
        if wr < DIRECTION_LONG_PENALIZE:
            proposals.append({
                "condition": "direction == 'LONG'",
                "action": "reduce_confidence",
                "value": 0.15,
                "evidence": f"LONG WR only {wr}% over {count} trades",
            })

    if short_trades:
        wr, count, _ = compute_stats_for_group(short_trades)
        log(f"    SHORT: {wr}% WR over {count} trades")
        if wr > DIRECTION_SHORT_BOOST:
            proposals.append({
                "condition": "direction == 'SHORT'",
                "action": "boost_confidence",
                "value": 0.10,
                "evidence": f"SHORT WR {wr}% over {count} trades",
            })

    return proposals


def analyze_by_pattern(trades):
    """Win rate per signal/pattern → propose pattern rules."""
    proposals = []
    pattern_groups = {}
    for t in trades:
        # Try multiple field names
        pattern = (
            t.get("signal") or
            t.get("pattern") or
            t.get("metadata", {}).get("signal") or
            "unknown"
        )
        # Normalize: take first 50 chars to avoid giant key names
        pattern = str(pattern)[:50]
        pattern_groups.setdefault(pattern, []).append(t)

    for pattern, group in pattern_groups.items():
        if pattern == "unknown" or len(group) < 3:
            continue
        wr, count, _ = compute_stats_for_group(group)

        if wr > 70.0:
            proposals.append({
                "condition": f"pattern == '{pattern}'",
                "action": "boost_confidence",
                "value": 0.10,
                "evidence": f"Pattern '{pattern}' wins {wr}% over {count} trades",
            })
        elif wr < 25.0:
            proposals.append({
                "condition": f"pattern == '{pattern}'",
                "action": "reduce_confidence",
                "value": 0.15,
                "evidence": f"Pattern '{pattern}' only wins {wr}% over {count} trades",
            })

    return proposals


def analyze_hold_duration(trades):
    """Compare hold time for wins vs losses."""
    proposals = []

    def avg_hold(tlist):
        holds = []
        for t in tlist:
            h = t.get("hold_hours") or t.get("hold_duration_hours")
            if h is None:
                # Try computing from entry/exit
                try:
                    entry = t.get("entry_time", "")
                    exit_ = t.get("exit_time", "")
                    if entry and exit_:
                        dt_e = datetime.fromisoformat(entry.replace("Z", "+00:00"))
                        dt_x = datetime.fromisoformat(exit_.replace("Z", "+00:00"))
                        h = (dt_x - dt_e).total_seconds() / 3600
                except Exception:
                    continue
            if h is not None:
                holds.append(float(h))
        return sum(holds) / len(holds) if holds else None

    wins = [t for t in trades if t.get("pnl_dollars", t.get("pnl_usd", 0)) > 0]
    losses = [t for t in trades if t.get("pnl_dollars", t.get("pnl_usd", 0)) < 0]

    avg_win_h = avg_hold(wins)
    avg_loss_h = avg_hold(losses)

    if avg_win_h and avg_loss_h:
        log(f"    Hold duration: wins avg {avg_win_h:.1f}h, losses avg {avg_loss_h:.1f}h")

        if avg_win_h > 6 and avg_loss_h < 3:
            proposals.append({
                "condition": "hold_duration_hours < 3",
                "action": "reduce_confidence",
                "value": 0.10,
                "evidence": (
                    f"Winners avg {avg_win_h:.1f}h but losses cut at {avg_loss_h:.1f}h "
                    f"— stops may be too tight"
                ),
            })
        elif avg_loss_h > 10 and avg_win_h < 5:
            proposals.append({
                "condition": "hold_duration_hours > 10",
                "action": "reduce_size",
                "value": 0.10,
                "evidence": (
                    f"Losses held {avg_loss_h:.1f}h vs winners at {avg_win_h:.1f}h "
                    f"— max hold too long for losers"
                ),
            })

    return proposals


def analyze_exit_reasons(trades):
    """Which exit reasons correlate with smallest losses?"""
    proposals = []
    exit_groups = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_groups.setdefault(reason, []).append(t)

    log(f"    Exit reasons: {list(exit_groups.keys())}")

    for reason, group in exit_groups.items():
        if len(group) < 3:
            continue
        wr, count, net = compute_stats_for_group(group)

        # If a specific exit reason has very low WR, flag it
        if wr < 20.0 and count >= 3:
            proposals.append({
                "condition": f"exit_reason == '{reason}'",
                "action": "reduce_size",
                "value": 0.10,
                "evidence": (
                    f"Exit reason '{reason}' shows only {wr}% WR "
                    f"over {count} trades (net ${net:.2f})"
                ),
            })

    return proposals


# ─── RULE LIFECYCLE MANAGEMENT ───

def promote_proposed_to_probation(proposed, probation):
    """Move proposed rules to probation for testing."""
    now_iso = datetime.now(timezone.utc).isoformat()
    newly_promoted = []
    still_proposed = []

    for rule in proposed:
        # Promote rules that haven't been tested yet
        if rule.get("status") == "proposed" and rule.get("trades_tested", 0) == 0:
            rule["status"] = "probation"
            rule["probation_start"] = now_iso
            rule["probation_wins"] = 0
            rule["probation_losses"] = 0
            newly_promoted.append(rule)
        else:
            still_proposed.append(rule)

    probation.extend(newly_promoted)
    log(f"  Promoted {len(newly_promoted)} rules to probation")
    return still_proposed, probation


def evaluate_probation_rules(probation, trades):
    """Check if probation rules should become active or be killed."""
    active = load_rules(ACTIVE_FILE)
    killed = load_rules(KILLED_FILE)
    now_iso = datetime.now(timezone.utc).isoformat()

    still_probation = []

    for rule in probation:
        condition = rule.get("condition", "")
        action = rule.get("action", "")

        # Find trades that match this rule's condition
        matching_trades = find_matching_trades(trades, condition)

        if len(matching_trades) < MIN_TRADES_FOR_PROBATION:
            # Not enough data yet — keep in probation
            rule["trades_tested"] = len(matching_trades)
            still_probation.append(rule)
            continue

        wr, count, net = compute_stats_for_group(matching_trades)
        rule["trades_tested"] = count
        rule["probation_wr"] = wr
        rule["probation_net"] = net

        # Decision: promote to active or kill
        if wr >= 55.0 and net >= 0:
            # Rule is working — promote to active
            rule["status"] = "active"
            rule["activated"] = now_iso
            active.append(rule)
            log(f"  PROMOTED to active: {rule['id']} (WR={wr}%, net=${net:.2f})")
        elif wr < 35.0 or net < -5.0:
            # Rule is hurting — kill it
            rule["status"] = "killed"
            rule["killed_at"] = now_iso
            rule["kill_reason"] = f"Probation failed: WR={wr}% net=${net:.2f}"
            killed.append(rule)
            log(f"  KILLED from probation: {rule['id']} (WR={wr}%, net=${net:.2f})")
        else:
            # Neutral — keep testing
            still_probation.append(rule)

    save_rules(ACTIVE_FILE, active[-MAX_ACTIVE_RULES:])  # cap active rules
    save_rules(KILLED_FILE, killed)
    return still_probation


def review_active_rules(trades):
    """Check if active rules should be killed (checked every 50 trades)."""
    active = load_rules(ACTIVE_FILE)
    killed = load_rules(KILLED_FILE)
    now_iso = datetime.now(timezone.utc).isoformat()

    surviving = []
    for rule in active:
        # Only review rules with enough new trade data
        condition = rule.get("condition", "")
        matching = find_matching_trades(trades, condition)
        total_tested = len(matching)

        if total_tested < 50:
            surviving.append(rule)
            continue

        wr, count, net = compute_stats_for_group(matching)

        # Kill if performance degraded significantly
        if wr < 30.0 and count >= 20:
            rule["status"] = "killed"
            rule["killed_at"] = now_iso
            rule["kill_reason"] = f"Active rule degraded: WR={wr}% over {count} trades"
            killed.append(rule)
            log(f"  KILLED active rule: {rule['id']} (degraded WR={wr}%)")
        else:
            rule["trades_tested"] = total_tested
            surviving.append(rule)

    save_rules(ACTIVE_FILE, surviving)
    save_rules(KILLED_FILE, killed)
    return surviving


def find_matching_trades(trades, condition):
    """Find trades that match a rule condition (simple text matching)."""
    matching = []
    condition_lower = condition.lower()

    for t in trades:
        # Parse condition string into key-value lookups
        regime = (t.get("regime") or t.get("metadata", {}).get("regime", "")).lower()
        direction = t.get("direction", t.get("side", "")).upper().lower()
        pattern = str(t.get("signal") or t.get("pattern") or "").lower()
        exit_reason = t.get("exit_reason", "").lower()

        matched = False

        # Simple substring matching for common condition patterns
        if f"regime == '{regime}'" in condition_lower or f'regime == "{regime}"' in condition_lower:
            matched = True
        elif f"direction == '{direction}'" in condition_lower or f'direction == "{direction}"' in condition_lower:
            matched = True
        elif f"pattern == '{pattern}'" in condition_lower or f'pattern == "{pattern}"' in condition_lower:
            matched = True
        elif f"exit_reason == '{exit_reason}'" in condition_lower or f'exit_reason == "{exit_reason}"' in condition_lower:
            matched = True

        if matched:
            matching.append(t)

    return matching


# ─── SIGNAL WEIGHTS (backward compat) ───
def rebuild_signal_weights(trades):
    """
    Maintain backward compat signal_weights.json output.
    This preserves the output format signal_evolution_agent.py produced.
    """
    from pathlib import Path as _Path

    # Build performance matrix: signal → {regime → {trades, wins, pnls}}
    matrix = {}
    for t in trades:
        signal = t.get("signal")
        if not signal:
            continue
        regime = t.get("regime") or t.get("metadata", {}).get("regime", "unknown")
        pnl = t.get("pnl_dollars", t.get("pnl_usd", 0))
        matrix.setdefault(signal, {}).setdefault(regime, {"trades": 0, "wins": 0, "pnls": []})
        matrix[signal][regime]["trades"] += 1
        if pnl > 0:
            matrix[signal][regime]["wins"] += 1
        matrix[signal][regime]["pnls"].append(pnl)

    # Compute fatigue
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=FATIGUE_WINDOW_HOURS)).isoformat()
    fatigue = {}
    for t in trades:
        signal = t.get("signal")
        if signal and t.get("entry_time", "") >= cutoff:
            fatigue[signal] = fatigue.get(signal, 0) + 1

    # Dominant regime
    world = load_json_safe(WORLD_STATE_FILE, {})
    regime_counts = {}
    for c in world.get("coins", {}).values():
        r = c.get("regime", "stable")
        regime_counts[r] = regime_counts.get(r, 0) + 1
    dominant = max(regime_counts, key=regime_counts.get) if regime_counts else "stable"

    weights = {}
    performance = {}

    for signal, regime_map in matrix.items():
        # Get regime-specific or overall stats
        all_pnls = []
        total_wins = 0
        total_trades = 0
        for rdata in regime_map.values():
            all_pnls.extend(rdata["pnls"])
            total_wins += rdata["wins"]
            total_trades += rdata["trades"]

        wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

        # Check regime-specific first
        w = 1.0
        if dominant in regime_map and regime_map[dominant]["trades"] >= MIN_REGIME_TRADES:
            r_wr = regime_map[dominant]["wins"] / regime_map[dominant]["trades"] * 100
            w = 0.5 if r_wr < REGIME_WR_PENALIZE else (1.2 if r_wr > REGIME_WR_BOOST_WEIGHT else 1.0)
        elif total_trades >= MIN_OVERALL_TRADES:
            w = 0.5 if wr < REGIME_WR_PENALIZE else (1.2 if wr > REGIME_WR_BOOST_WEIGHT else 1.0)

        # Fatigue penalty
        if fatigue.get(signal, 0) > FATIGUE_THRESHOLD:
            w *= FATIGUE_MULTIPLIER

        weights[signal] = round(w, 3)

        # Performance record
        avg_pnl = sum(all_pnls) / len(all_pnls) if all_pnls else 0
        n = len(all_pnls)
        if n > 1:
            mean = avg_pnl
            stdev = (sum((p - mean) ** 2 for p in all_pnls) / (n - 1)) ** 0.5
            sharpe = mean / stdev if stdev > 0 else 0
        else:
            sharpe = 0

        performance[signal] = {
            "total_trades": total_trades,
            "win_rate": round(wr, 1),
            "avg_pnl": round(avg_pnl, 4),
            "sharpe": round(sharpe, 3),
        }

    ts_iso = datetime.now(timezone.utc).isoformat()
    output = {
        "timestamp": ts_iso,
        "weights": weights,
        "performance": performance,
        "fatigue": {k: v for k, v in fatigue.items() if v > 0},
    }

    BUS_DIR.mkdir(parents=True, exist_ok=True)
    save_json(SIGNAL_WEIGHTS_FILE, output)
    log(f"  Signal weights: {len(weights)} signals updated")
    return weights


# ─── HEARTBEAT ───
def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = load_json_safe(HEARTBEAT_FILE, {})
    heartbeat["parameter_evolution"] = ts
    save_json(HEARTBEAT_FILE, heartbeat)


# ─── MAIN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    log("=" * 60)
    log(f"Parameter Evolution Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log("=" * 60)

    # Load trades
    trades = load_closed_trades()
    log(f"  Closed trades: {len(trades)}")

    # Always update signal weights (backward compat)
    if trades:
        rebuild_signal_weights(trades)

    # Check if enough trades for rule generation
    meta = load_meta()
    total_at_last = meta.get("total_trades_at_last_evolution", 0)
    generation = meta.get("evolution_generation", 0)
    new_trades_since = len(trades) - total_at_last

    log(f"  Trades since last analysis: {new_trades_since} (total={len(trades)}, min={MIN_TRADES_FOR_ANALYSIS})")

    if len(trades) < MIN_TRADES_FOR_ANALYSIS:
        log(f"  Not enough trades for analysis yet (need {MIN_TRADES_FOR_ANALYSIS})")
        write_heartbeat()
        log("=" * 60)
        return

    if new_trades_since < 5 and total_at_last > 0:
        log(f"  Only {new_trades_since} new trades since last analysis — skipping")
        write_heartbeat()
        log("=" * 60)
        return

    log(f"  Running rule generation (generation {generation + 1})...")

    # ── RULE GENERATION ──
    log("  Analyzing by regime...")
    regime_proposals = analyze_by_regime(trades)

    log("  Analyzing by direction...")
    direction_proposals = analyze_by_direction(trades)

    log("  Analyzing by pattern...")
    pattern_proposals = analyze_by_pattern(trades)

    log("  Analyzing hold duration...")
    hold_proposals = analyze_hold_duration(trades)

    log("  Analyzing exit reasons...")
    exit_proposals = analyze_exit_reasons(trades)

    all_proposals = (
        regime_proposals + direction_proposals +
        pattern_proposals + hold_proposals + exit_proposals
    )
    log(f"  Total raw proposals: {len(all_proposals)}")

    # Load existing rule sets
    proposed = load_rules(PROPOSED_FILE)
    probation = load_rules(PROBATION_FILE)
    active = load_rules(ACTIVE_FILE)

    # Filter out duplicates
    new_rules = []
    for p in all_proposals:
        if not rule_exists(p["condition"], p["action"], proposed, probation, active):
            rule = make_rule(
                condition=p["condition"],
                action=p["action"],
                value=p["value"],
                evidence=p["evidence"],
                source="parameter_evolution",
                generation=generation + 1,
            )
            new_rules.append(rule)

    log(f"  New (non-duplicate) proposals: {len(new_rules)}")

    # Add new rules to proposed
    proposed.extend(new_rules)
    save_rules(PROPOSED_FILE, proposed)

    # ── RULE LIFECYCLE ──
    log("  Managing rule lifecycle...")

    # Promote proposed → probation
    proposed, probation = promote_proposed_to_probation(proposed, probation)
    save_rules(PROPOSED_FILE, proposed)

    # Evaluate probation → active or killed
    probation = evaluate_probation_rules(probation, trades)
    save_rules(PROBATION_FILE, probation)

    # Review active rules for degradation
    active = review_active_rules(trades)

    # Update meta
    generation += 1
    meta["evolution_generation"] = generation
    meta["total_trades_at_last_evolution"] = len(trades)
    meta["last_evolution_analysis"] = ts_iso
    save_meta(meta)

    # ── SUMMARY ──
    proposed_count = len(load_rules(PROPOSED_FILE))
    probation_count = len(load_rules(PROBATION_FILE))
    active_count = len(load_rules(ACTIVE_FILE))
    killed_count = len(load_rules(KILLED_FILE))

    log(f"  Rules: {proposed_count} proposed | {probation_count} probation | {active_count} active | {killed_count} killed")
    log(f"  New rules added: {len(new_rules)}")
    log(f"  Generation: {generation}")

    if active:
        log("  Active rules:")
        for r in active[:5]:
            log(f"    [{r['id']}] {r['condition']} → {r['action']} | {r['evidence'][:60]}")

    write_heartbeat()
    log("=" * 60)


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Parameter Evolution Agent starting in loop mode (every {CYCLE_SECONDS}s)")
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

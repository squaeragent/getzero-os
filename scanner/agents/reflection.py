#!/usr/bin/env python3
"""
ZERO OS — Agent 9: Reflection (Cognitive Loop Phase 5)
LLM-based self-assessment via local Ollama. Runs every 6 hours.

Two cycles:
  1. Deep Reflection (every 6h) — llama3:70b analyzes observations, proposes rules
  2. Narrative (every 30min)   — llama3:8b summarizes current market state

Inputs:
  scanner/memory/observations.jsonl       — trade observations from observer
  scanner/memory/episodes/               — full episode data
  scanner/bus/world_state.json           — current world model
  scanner/data/live/closed.jsonl         — all closed trades
  scanner/data/live/positions.json       — current positions
  scanner/memory/rules/active.json       — currently active rules
  scanner/memory/meta.json               — tracks last reflection cycle

Outputs:
  scanner/memory/reflections/reflection_YYYYMMDD_HHMM.json  — reflection results
  scanner/memory/rules/proposed.json                         — rule proposals
  scanner/memory/narratives/latest.json                      — current market narrative
  scanner/bus/heartbeat.json                                 — heartbeat

Usage:
  python3 scanner/agents/reflection.py           # single run
  python3 scanner/agents/reflection.py --loop    # continuous loop
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    load_json, save_json, read_jsonl, make_logger, update_heartbeat,
    SCANNER_DIR, BUS_DIR, DATA_DIR, LIVE_DIR, MEMORY_DIR, EPISODES_DIR,
    OBSERVATIONS_FILE, WORLD_STATE_FILE, HEARTBEAT_FILE,
)

# ─── PATHS ───
RULES_DIR = MEMORY_DIR / "rules"
REFLECTIONS_DIR = MEMORY_DIR / "reflections"
NARRATIVES_DIR = MEMORY_DIR / "narratives"

CLOSED_FILE = LIVE_DIR / "closed.jsonl"
POSITIONS_FILE = LIVE_DIR / "positions.json"
META_FILE = MEMORY_DIR / "meta.json"
PROPOSED_RULES_FILE = RULES_DIR / "proposed.json"
ACTIVE_RULES_FILE = RULES_DIR / "active.json"
NARRATIVE_FILE = NARRATIVES_DIR / "latest.json"

# ─── CONFIG ───
CYCLE_SECONDS_REFLECTION = 21600   # 6 hours
CYCLE_SECONDS_NARRATIVE = 1800      # 30 minutes
OLLAMA_URL = "http://localhost:11434"
MODEL_REFLECTION = "llama3:70b-instruct-q4_K_M"
MODEL_NARRATIVE = "llama3:8b-instruct-q4_0"
TIMEOUT_REFLECTION = 300  # 300s for 70b
TIMEOUT_NARRATIVE = 60    # 60s for 8b


# ─── LOGGING ───
log = make_logger("REFLECTION")


# ─── OLLAMA ───
def ollama_generate(prompt, model=MODEL_REFLECTION, timeout=TIMEOUT_REFLECTION):
    """Call Ollama local LLM. Returns response string or None on failure."""
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 2048,
        }
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            return result.get("response", "")
    except urllib.error.URLError as e:
        log(f"[warn] Ollama connection failed: {e}")
        return None
    except Exception as e:
        log(f"[warn] Ollama generation failed: {e}")
        return None


def check_ollama_available():
    """Check if Ollama is running."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# ─── META TRACKING ───
def load_meta():
    return load_json(META_FILE, {
        "last_reflection": None,
        "last_evolution_analysis": None,
        "reflection_cycle": 0,
        "evolution_generation": 0,
        "total_trades_at_last_evolution": 0,
    })


def save_meta(meta):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    save_json(META_FILE, meta)


# ─── DATA LOADERS ───
def load_observations():
    """Load all observations from observations.jsonl."""
    return read_jsonl(OBSERVATIONS_FILE, max_lines=500)


def load_observations_since(last_reflection_iso):
    """Load observations recorded after last_reflection timestamp."""
    all_obs = load_observations()
    if not last_reflection_iso:
        return all_obs
    return [
        o for o in all_obs
        if o.get("timestamp", "") > last_reflection_iso
    ]


def load_closed_trades():
    """Load closed trades from live trading."""
    trades = read_jsonl(CLOSED_FILE, max_lines=1000)
    trades.sort(key=lambda t: t.get("exit_time", ""))
    return trades


def load_positions():
    """Load current open positions."""
    pos = load_json(POSITIONS_FILE, default=[])
    if isinstance(pos, dict):
        return list(pos.values())
    return pos


def load_world_state():
    """Load current world state."""
    return load_json(WORLD_STATE_FILE, {})


def load_active_rules():
    """Load currently active rules."""
    rules = load_json(ACTIVE_RULES_FILE, default=[])
    if not isinstance(rules, list):
        return []
    return rules


def load_proposed_rules():
    """Load proposed rules."""
    rules = load_json(PROPOSED_RULES_FILE, default=[])
    if not isinstance(rules, list):
        return []
    return rules


# ─── PORTFOLIO ANALYSIS ───
def compute_portfolio_stats(trades):
    """Compute win rate and net PnL from closed trades."""
    if not trades:
        return 0.0, 0, 0.0

    wins = sum(1 for t in trades if t.get("pnl_dollars", t.get("pnl_usd", 0)) > 0)
    total = len(trades)
    win_rate = (wins / total * 100) if total > 0 else 0.0
    net_pnl = sum(t.get("pnl_dollars", t.get("pnl_usd", 0)) for t in trades)
    return round(win_rate, 1), total, round(net_pnl, 2)


def format_observations(observations, max_obs=20):
    """Format observations for LLM prompt."""
    if not observations:
        return "No recent observations."
    lines = []
    for obs in observations[-max_obs:]:
        coin = obs.get("coin", "?")
        direction = obs.get("direction", "?")
        outcome = obs.get("outcome", "?")
        pnl = obs.get("pnl_dollars", obs.get("pnl_usd", 0))
        regime = obs.get("regime", "?")
        pattern = obs.get("pattern", obs.get("signal", "?"))
        hold_h = obs.get("hold_hours", obs.get("hold_duration_hours", "?"))
        exit_reason = obs.get("exit_reason", "?")
        lines.append(
            f"  [{outcome}] {coin} {direction} | regime={regime} | "
            f"pnl=${pnl:.2f} | hold={hold_h}h | exit={exit_reason} | signal={str(pattern)[:50]}"
        )
    return "\n".join(lines)


def format_portfolio(positions):
    """Format current positions for LLM prompt."""
    if not positions:
        return "No open positions."
    lines = []
    for p in positions:
        coin = p.get("coin", p.get("symbol", "?"))
        direction = p.get("side", p.get("direction", "?"))
        size = p.get("size_usd", p.get("notional", 0))
        pnl = p.get("unrealized_pnl", p.get("upnl", 0))
        lines.append(f"  {coin} {direction} ${size:.0f} | upnl=${pnl:.2f}")
    return "\n".join(lines)


def format_world_summary(world_state):
    """Summarize world state for narrative prompt."""
    coins = world_state.get("coins", {})
    n_trending = sum(1 for c in coins.values() if c.get("regime") == "trending")
    n_chaotic = sum(1 for c in coins.values() if c.get("regime") == "chaotic")
    n_stable = sum(1 for c in coins.values() if c.get("regime") == "stable")
    n_shift = sum(1 for c in coins.values() if c.get("transition", False))
    n_elevated = sum(1 for c in coins.values() if c.get("spread_elevated", False))
    n_funding_extreme = sum(
        1 for c in coins.values()
        if abs(c.get("funding_rate", 0)) > 0.0005
    )
    # Top movers by abs pnl or price change
    movers = sorted(
        [(k, abs(v.get("price_change_pct", 0))) for k, v in coins.items()],
        key=lambda x: x[1], reverse=True
    )[:5]
    top_movers = ", ".join(f"{m[0]}({m[1]:.1f}%)" for m in movers if m[1] > 0)

    return {
        "n_trending": n_trending,
        "n_chaotic": n_chaotic,
        "n_stable": n_stable,
        "n_shift": n_shift,
        "n_elevated": n_elevated,
        "n_funding_extreme": n_funding_extreme,
        "top_movers": top_movers or "none",
        "total_coins": len(coins),
    }


# ─── RULE PARSING ───
def parse_rule_proposals(llm_response, source="reflection"):
    """Extract RULE: statements from LLM response."""
    proposals = []
    lines = llm_response.split("\n")

    # Find lines starting with "RULE:" or "Rule:"
    rule_pattern = re.compile(r'^(?:RULE|Rule)\s*\d*[:.]?\s*(.+)', re.IGNORECASE)

    for i, line in enumerate(lines):
        match = rule_pattern.match(line.strip())
        if not match:
            continue

        rule_text = match.group(1).strip()

        # Try to extract IF/THEN/BECAUSE parts
        condition = ""
        action_text = ""
        evidence = ""

        if_match = re.search(r'IF\s+(.+?)(?:\s+THEN|\s+→|$)', rule_text, re.IGNORECASE)
        then_match = re.search(r'(?:THEN|→)\s+(.+?)(?:\s+BECAUSE|$)', rule_text, re.IGNORECASE)
        because_match = re.search(r'BECAUSE\s+(.+)', rule_text, re.IGNORECASE)

        if if_match:
            condition = if_match.group(1).strip()
        if then_match:
            action_text = then_match.group(1).strip()
        if because_match:
            evidence = because_match.group(1).strip()

        if not condition:
            condition = rule_text[:100]
        if not action_text:
            action_text = "adjust_confidence"

        # Determine action type from text
        action = "adjust_confidence"
        value = 0.0
        action_lower = action_text.lower()
        if "boost" in action_lower or "increase" in action_lower:
            action = "boost_confidence"
            value = 0.10
        elif "reduce" in action_lower or "decrease" in action_lower or "penaliz" in action_lower:
            action = "reduce_confidence"
            value = 0.10
        elif "block" in action_lower or "avoid" in action_lower or "skip" in action_lower:
            action = "kill"
            value = 1.0
        elif "size" in action_lower:
            if "boost" in action_lower or "larger" in action_lower:
                action = "boost_size"
                value = 0.10
            else:
                action = "reduce_size"
                value = 0.10

        # Generate rule ID
        existing = load_proposed_rules()
        rule_num = len(existing) + len(proposals) + 1
        rule_id = f"RULE_{rule_num:03d}"

        proposals.append({
            "id": rule_id,
            "condition": condition,
            "action": action,
            "value": value,
            "action_description": action_text,
            "evidence": evidence or "LLM-proposed pattern",
            "generation": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "trades_tested": 0,
            "status": "proposed",
            "source": source,
            "raw_rule": rule_text,
        })

    return proposals


def extract_self_assessment(llm_response):
    """Extract key insights from LLM response text."""
    response_lower = llm_response.lower()

    # Look for patterns indicating mistakes, edges
    mistakes = []
    edges = []
    patterns = []

    lines = llm_response.split("\n")
    in_losses = False
    in_wins = False

    for line in lines:
        line_lower = line.lower()
        if "loss" in line_lower or "mistake" in line_lower or "trap" in line_lower:
            in_losses = True
            in_wins = False
        elif "win" in line_lower or "edge" in line_lower or "opportunit" in line_lower:
            in_wins = True
            in_losses = False

        if in_losses and len(line.strip()) > 20:
            mistakes.append(line.strip()[:120])
        elif in_wins and len(line.strip()) > 20:
            edges.append(line.strip()[:120])

        if "pattern" in line_lower and len(line.strip()) > 20:
            patterns.append(line.strip()[:120])

    return {
        "biggest_mistake": mistakes[0] if mistakes else "Not identified",
        "biggest_edge": edges[0] if edges else "Not identified",
        "confidence_calibration": "Pending more trade data" if not mistakes and not edges else "Analyzed",
        "patterns": patterns[:5],
    }


# ─── NARRATIVE CYCLE (every 30 min) ───
def generate_narrative():
    """Generate a short market narrative via Ollama llama3:8b."""
    log("Generating market narrative (llama3:8b)...")

    world_state = load_world_state()
    positions = load_positions()

    ws = format_world_summary(world_state)
    positions_summary = format_portfolio(positions)

    narrative_prompt = f"""Summarize the current crypto market state in 3-4 sentences for a trading system.

Market data:
- {ws['n_trending']} coins trending, {ws['n_chaotic']} chaotic, {ws['n_stable']} stable, {ws['n_shift']} shifting
- {ws['n_elevated']} elevated spreads
- {ws['n_funding_extreme']} extreme funding rates
- Top movers: {ws['top_movers']}
- Current positions: {positions_summary}

Focus on: regime distribution shifts, risk factors, opportunities.
Be direct and specific. No hedging."""

    if not check_ollama_available():
        log("[warn] Ollama unavailable — skipping narrative")
        narrative_text = "Ollama unavailable — narrative skipped."
    else:
        narrative_text = ollama_generate(
            narrative_prompt,
            model=MODEL_NARRATIVE,
            timeout=TIMEOUT_NARRATIVE
        )
        if not narrative_text:
            narrative_text = "Ollama generation failed — narrative unavailable."
        else:
            log(f"Narrative generated ({len(narrative_text)} chars)")

    narrative_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "narrative": narrative_text,
        "regime_summary": {
            "trending": ws["n_trending"],
            "chaotic": ws["n_chaotic"],
            "stable": ws["n_stable"],
            "shifting": ws["n_shift"],
        },
        "positions_count": len(positions),
        "elevated_spreads": ws["n_elevated"],
        "extreme_funding": ws["n_funding_extreme"],
        "top_movers": ws["top_movers"],
    }

    NARRATIVES_DIR.mkdir(parents=True, exist_ok=True)
    save_json(NARRATIVE_FILE, narrative_data)
    log(f"Narrative written to {NARRATIVE_FILE}")
    return narrative_text


# ─── REFLECTION CYCLE (every 6h) ───
def run_reflection_cycle():
    """Run the full 6-hour deep reflection cycle."""
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    ts_str = ts.strftime("%Y%m%d_%H%M")

    log("=" * 60)
    log(f"Deep Reflection Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log("=" * 60)

    # Load meta to get last reflection time
    meta = load_meta()
    last_reflection = meta.get("last_reflection")
    cycle_number = meta.get("reflection_cycle", 0) + 1

    # Load data
    observations = load_observations_since(last_reflection)
    all_trades = load_closed_trades()
    positions = load_positions()
    active_rules = load_active_rules()

    log(f"  Observations since last reflection: {len(observations)}")
    log(f"  Total closed trades: {len(all_trades)}")
    log(f"  Open positions: {len(positions)}")
    log(f"  Active rules: {len(active_rules)}")

    # Compute portfolio stats
    win_rate, total_trades, net_pnl = compute_portfolio_stats(all_trades)

    # Format for prompt
    formatted_obs = format_observations(observations)
    portfolio_summary = format_portfolio(positions)
    active_rules_summary = json.dumps(
        [{"id": r.get("id"), "condition": r.get("condition"), "action": r.get("action")}
         for r in active_rules[:10]],
        indent=2
    ) if active_rules else "No active rules yet."

    # Build reflection prompt
    n = len(observations)
    prompt = f"""You are the reflection layer of an autonomous trading system called ZERO OS.

RECENT OBSERVATIONS (last {n} trades):
{formatted_obs}

CURRENT PORTFOLIO:
{portfolio_summary}

CURRENT RULES (active):
{active_rules_summary}

WIN RATE: {win_rate}% over {total_trades} trades
NET P&L: ${net_pnl}

Analyze the following:
1. What patterns do you see in recent losses? Be specific about coins, regimes, signals.
2. What patterns do you see in recent wins?
3. Are we overweight in any direction, regime, or signal type?
4. What edges should we exploit more?
5. What traps should we avoid?
6. Propose 1-3 specific, testable rules. Format each as:
   RULE: IF [condition] THEN [action] BECAUSE [evidence]

Be concise. Use data from the observations. No speculation without evidence.
"""

    # Call Ollama
    if not check_ollama_available():
        log("[warn] Ollama unavailable — writing reflection with fallback")
        llm_response = "Ollama unavailable"
        rule_proposals = []
    else:
        log(f"  Calling Ollama {MODEL_REFLECTION} (timeout={TIMEOUT_REFLECTION}s)...")
        llm_response = ollama_generate(prompt, model=MODEL_REFLECTION, timeout=TIMEOUT_REFLECTION)
        if not llm_response:
            log("[warn] Ollama generation returned empty — using fallback")
            llm_response = "Ollama unavailable"
            rule_proposals = []
        else:
            log(f"  LLM response: {len(llm_response)} chars")
            rule_proposals = parse_rule_proposals(llm_response, source="reflection")
            log(f"  Rule proposals extracted: {len(rule_proposals)}")

    # Extract self-assessment
    self_assessment = extract_self_assessment(llm_response)

    # Build reflection object
    reflection = {
        "timestamp": ts_iso,
        "cycle": cycle_number,
        "observations_since_last": n,
        "total_trades_analyzed": total_trades,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "llm_analysis": llm_response,
        "patterns_noticed": self_assessment.get("patterns", []),
        "rule_proposals": [
            {"id": r["id"], "condition": r["condition"], "action": r["action"],
             "evidence": r["evidence"]}
            for r in rule_proposals
        ],
        "self_assessment": {
            "biggest_mistake": self_assessment["biggest_mistake"],
            "biggest_edge": self_assessment["biggest_edge"],
            "confidence_calibration": self_assessment["confidence_calibration"],
        }
    }

    # Save reflection file
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    reflection_path = REFLECTIONS_DIR / f"reflection_{ts_str}.json"
    save_json(reflection_path, reflection)
    log(f"  Reflection saved: {reflection_path}")

    # Merge rule proposals into proposed.json
    if rule_proposals:
        existing_proposed = load_proposed_rules()
        # Avoid duplicate IDs
        existing_ids = {r.get("id") for r in existing_proposed}
        new_proposals = [r for r in rule_proposals if r["id"] not in existing_ids]
        existing_proposed.extend(new_proposals)
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        save_json(PROPOSED_RULES_FILE, existing_proposed)
        log(f"  Added {len(new_proposals)} new rule proposals to proposed.json")

    # Update meta
    meta["last_reflection"] = ts_iso
    meta["reflection_cycle"] = cycle_number
    save_meta(meta)

    log(f"  Cycle {cycle_number} complete. Win rate: {win_rate}% | PnL: ${net_pnl}")
    log("=" * 60)

    return reflection


# ─── HEARTBEAT ───
def write_heartbeat():
    update_heartbeat("reflection")


# ─── MAIN LOOP ───
def main():
    loop_mode = "--loop" in sys.argv

    if not loop_mode:
        # Single run: do both narrative and reflection
        generate_narrative()
        run_reflection_cycle()
        write_heartbeat()
        return

    log(f"Reflection Agent starting in loop mode")
    log(f"  Deep reflection every {CYCLE_SECONDS_REFLECTION}s (6h)")
    log(f"  Narrative every {CYCLE_SECONDS_NARRATIVE}s (30min)")

    last_reflection = 0.0
    last_narrative = 0.0

    while True:
        now = time.time()

        try:
            # Narrative cycle — every 30 minutes
            if now - last_narrative >= CYCLE_SECONDS_NARRATIVE:
                generate_narrative()
                last_narrative = time.time()
                write_heartbeat()

            # Deep reflection cycle — every 6 hours
            if now - last_reflection >= CYCLE_SECONDS_REFLECTION:
                run_reflection_cycle()
                last_reflection = time.time()
                write_heartbeat()

        except Exception as e:
            log(f"[error] Cycle failed: {e}")
            import traceback
            traceback.print_exc()
            write_heartbeat()

        # Sleep 60s between checks — narrative wakes it every ~30 min
        time.sleep(60)


if __name__ == "__main__":
    main()

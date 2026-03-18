#!/usr/bin/env python3
"""
ZERO OS — Agent 8: Observer
Closes the cognitive loop by:
  A) Monitoring kill conditions on OPEN positions every 2 min.
     Writes kill signals to scanner/bus/kill_signals.json for the execution agent.
  B) Recording structured observations when trades close.
     Appends to scanner/memory/observations.jsonl and updates episode files.

Reads:
  scanner/data/live/positions.json  — open positions
  scanner/data/live/closed.jsonl    — closed trade log
  scanner/bus/world_state.json      — current world model (regime, hurst, funding…)
  scanner/bus/hypotheses.json       — hypotheses with kill conditions
  scanner/bus/adversary.json        — adversary verdicts
  scanner/memory/episodes/          — episode files (hypothesis details)

Writes:
  scanner/bus/kill_signals.json        — active kill signals for execution agent
  scanner/memory/observations.jsonl   — append-only observation log
  scanner/bus/heartbeat.json          — "observer" key
  scanner/memory/episodes/<id>.json   — updated with observations

Usage:
  python3 scanner/agents/observer.py           # single run
  python3 scanner/agents/observer.py --loop    # continuous 2-min cycle
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ── Upgrade 3: session classification (mirrors perception.py) ──
def get_trading_session(utc_hour):
    if 0 <= utc_hour < 7:
        return "ASIA"
    elif 7 <= utc_hour < 13:
        return "EUROPE"
    elif 13 <= utc_hour < 20:
        return "US"
    else:
        return "LATE_US"

# ─── PATHS ───────────────────────────────────────────────────────────────────
AGENT_DIR   = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR     = SCANNER_DIR / "bus"
DATA_DIR    = SCANNER_DIR / "data"
LIVE_DIR    = DATA_DIR / "live"
MEMORY_DIR  = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"

POSITIONS_FILE    = LIVE_DIR / "positions.json"
CLOSED_FILE       = LIVE_DIR / "closed.jsonl"
WORLD_STATE_FILE  = BUS_DIR  / "world_state.json"
HYPOTHESES_FILE   = BUS_DIR  / "hypotheses.json"
ADVERSARY_FILE    = BUS_DIR  / "adversary.json"
KILL_SIGNALS_FILE = BUS_DIR  / "kill_signals.json"
OBSERVATIONS_FILE  = MEMORY_DIR / "observations.jsonl"
CALIBRATION_FILE   = MEMORY_DIR / "calibration.jsonl"   # Upgrade 2
HEARTBEAT_FILE     = BUS_DIR  / "heartbeat.json"
LOG_FILE           = LIVE_DIR / "observer.log"

CYCLE_SECONDS = 120  # 2 minutes

# ─── LOGGING ─────────────────────────────────────────────────────────────────
def log(msg: str):
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] [OBS] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ─── GENERIC I/O ─────────────────────────────────────────────────────────────
def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return default
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> list:
    records = []
    if not path.exists():
        return records
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE, {})
    hb["observer"] = datetime.now(timezone.utc).isoformat()
    save_json(HEARTBEAT_FILE, hb)


# ─── HYPOTHESIS LOOKUP ───────────────────────────────────────────────────────
def _load_all_hypotheses() -> list:
    """Return combined list of hypotheses from bus file + all episode files."""
    hyps = []
    # Bus hypotheses
    bus_data = load_json(HYPOTHESES_FILE, {})
    hyps.extend(bus_data.get("hypotheses", []))

    # Episode files (more complete / versioned)
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    for ep_file in EPISODES_DIR.glob("*.json"):
        ep = load_json(ep_file, {})
        if ep.get("hypothesis_id"):
            hyps.append(ep)

    # De-duplicate by hypothesis_id (episode file wins over bus copy)
    seen = {}
    for h in hyps:
        hid = h.get("hypothesis_id")
        if hid:
            seen[hid] = h  # later write wins → episode overrides bus
    return list(seen.values())


def find_hypothesis(position: dict, all_hyps: list) -> dict:
    """
    Match position to hypothesis.
    Priority: hypothesis_id stored on position → coin+direction+signal match.
    """
    # 1. Direct ID match
    hyp_id = position.get("hypothesis_id")
    if hyp_id:
        for h in all_hyps:
            if h.get("hypothesis_id") == hyp_id:
                return h

    # 2. coin + direction + signal match (most recent)
    coin      = position.get("coin")
    direction = position.get("direction")
    signal    = position.get("signal", "")
    matches = [
        h for h in all_hyps
        if h.get("coin") == coin
        and h.get("direction") == direction
        and (not signal or h.get("signal") == signal)
    ]
    if matches:
        # Return most recent by hypothesis_id timestamp suffix
        return sorted(matches, key=lambda h: h.get("hypothesis_id", ""), reverse=True)[0]

    return {}


# ─── WORLD STATE HELPERS ─────────────────────────────────────────────────────
def _coin_world(coin: str, world_state: dict) -> dict:
    return world_state.get("coins", {}).get(coin, {})


def get_coin_regime(coin: str, world_state: dict) -> str:
    return _coin_world(coin, world_state).get("regime", "unknown")


def get_coin_hurst(coin: str, world_state: dict) -> float | None:
    indicators = _coin_world(coin, world_state).get("indicators", {})
    v = indicators.get("HURST_24H")
    if v is not None:
        return float(v)
    return None


def get_coin_funding_direction(coin: str, world_state: dict) -> str:
    funding = _coin_world(coin, world_state).get("funding", {})
    return funding.get("velocity_direction", "unknown")


def get_coin_spread_status(coin: str, world_state: dict) -> str:
    spread = _coin_world(coin, world_state).get("spread", {})
    return spread.get("status", "NORMAL")


def get_coin_liquidity_tradeable(coin: str, world_state: dict) -> bool:
    liq = _coin_world(coin, world_state).get("liquidity", {})
    return liq.get("tradeable", True)


def get_coin_pattern(coin: str, world_state: dict) -> str:
    tf = _coin_world(coin, world_state).get("timeframe", {})
    return tf.get("pattern", "NEUTRAL")


# ─── KILL CONDITION PARSER ────────────────────────────────────────────────────
def _check_kill_condition(condition: str, coin: str, direction: str,
                           world_state: dict) -> tuple[bool, str]:
    """
    Parse a natural-language kill condition and evaluate it against world_state.
    Returns (triggered: bool, description: str).
    """
    kw = condition.lower()

    # ── REGIME ──────────────────────────────────────────────────────────────
    if "regime" in kw:
        regime = get_coin_regime(coin, world_state)
        # Determine which regime(s) to trigger on
        bad_regimes = []
        if "chaotic" in kw:
            bad_regimes.append("chaotic")
        if "shift" in kw:
            bad_regimes.append("shift")
        if not bad_regimes:
            bad_regimes = ["chaotic", "shift"]  # default
        triggered = regime in bad_regimes
        desc = f"regime={regime} (trigger if {bad_regimes})"
        return triggered, desc

    # ── HURST ────────────────────────────────────────────────────────────────
    if "hurst" in kw:
        hurst = get_coin_hurst(coin, world_state)
        if hurst is None:
            return False, "HURST_24H not in world_state"
        # Extract threshold from condition text, e.g. "drops below 0.5"
        m = re.search(r'(\d+\.?\d*)', kw)
        threshold = float(m.group(1)) if m else 0.5
        triggered = hurst < threshold
        desc = f"HURST_24H={hurst:.4f} (trigger if < {threshold})"
        return triggered, desc

    # ── FUNDING ──────────────────────────────────────────────────────────────
    if "funding" in kw:
        fund_dir = get_coin_funding_direction(coin, world_state)
        # Funding "reverses" means velocity is "intensifying" AND direction opposes position
        is_intensifying = fund_dir == "intensifying"
        if not is_intensifying:
            return False, f"funding velocity={fund_dir} (not intensifying)"
        # Check opposition
        # "intensifying long" means funding strongly positive → hurts SHORT positions
        # "intensifying short" means funding strongly negative → hurts LONG positions
        # We look at the actual rate sign or explicit mention in condition
        coin_data = _coin_world(coin, world_state)
        funding_rate = coin_data.get("funding", {}).get("rate", 0)
        if direction == "LONG" and funding_rate < -0.0005:
            # Strongly negative funding (shorts paying) while we're LONG → unusual but ok
            # The real threat: strongly positive funding while SHORT (longs collect, shorts pay)
            triggered = False
        elif direction == "SHORT" and funding_rate > 0.0005:
            # Positive funding intensifying → LONG bias, BAD for SHORT
            triggered = True
        elif direction == "LONG" and funding_rate < -0.0005:
            # Negative intensifying → SHORT bias, BAD for LONG
            triggered = True
        else:
            # Check for explicit mentions in kill condition string
            if "long" in kw and direction == "SHORT":
                triggered = True  # "intensifying long" kills SHORT
            elif "short" in kw and direction == "LONG":
                triggered = True  # "intensifying short" kills LONG
            else:
                triggered = False
        desc = f"funding velocity={fund_dir}, rate={funding_rate:.6f}"
        return triggered, desc

    # ── SPREAD / MM_SETUP ────────────────────────────────────────────────────
    if "spread" in kw or "mm_setup" in kw.replace("_", ""):
        spread_status = get_coin_spread_status(coin, world_state)
        triggered = spread_status in ("MM_SETUP", "MANIPULATION")
        desc = f"spread_status={spread_status}"
        return triggered, desc

    # ── LIQUIDITY ────────────────────────────────────────────────────────────
    if "liquidity" in kw:
        tradeable = get_coin_liquidity_tradeable(coin, world_state)
        triggered = not tradeable
        desc = f"liquidity tradeable={tradeable}"
        return triggered, desc

    # ── TIMEFRAME / PATTERN ──────────────────────────────────────────────────
    if "timeframe" in kw or "pattern" in kw or "confirmation" in kw:
        pattern = get_coin_pattern(coin, world_state)
        # Determine which patterns would kill this position
        # LONG positions killed by CONFIRMATION_SHORT / DIVERGENCE_BEAR / TRAP_LONG
        # SHORT positions killed by CONFIRMATION_LONG / DIVERGENCE_BULL / TRAP_SHORT
        kill_patterns_long  = ["CONFIRMATION_SHORT", "DIVERGENCE_BEAR", "TRAP_LONG"]
        kill_patterns_short = ["CONFIRMATION_LONG",  "DIVERGENCE_BULL", "TRAP_SHORT"]
        # Also check for explicit pattern name in the condition
        explicit_pattern = None
        for p in ["CONFIRMATION_SHORT", "CONFIRMATION_LONG", "DIVERGENCE_BEAR",
                  "DIVERGENCE_BULL", "TRAP_LONG", "TRAP_SHORT", "NEUTRAL"]:
            if p.lower() in kw:
                explicit_pattern = p
                break
        if explicit_pattern:
            triggered = pattern == explicit_pattern
        elif direction == "LONG":
            triggered = pattern in kill_patterns_long
        else:
            triggered = pattern in kill_patterns_short
        desc = f"pattern={pattern}"
        return triggered, desc

    # ── UNKNOWN ──────────────────────────────────────────────────────────────
    return False, f"unrecognised kill condition: '{condition}'"


def _kill_urgency(condition: str) -> str:
    """Return 'immediate' or 'warning' based on condition severity."""
    kw = condition.lower()
    if any(w in kw for w in ["chaotic", "mm_setup", "manipulation", "liquidity"]):
        return "immediate"
    return "warning"


# ─── RESPONSIBILITY A: KILL CONDITION MONITORING ──────────────────────────────
def check_kill_conditions():
    """
    Evaluate kill conditions for every open position against current world_state.
    Writes triggered signals to kill_signals.json.
    """
    positions   = load_json(POSITIONS_FILE, [])
    world_state = load_json(WORLD_STATE_FILE, {})
    all_hyps    = _load_all_hypotheses()

    if not positions:
        log("No open positions — skipping kill condition check")
        return

    ws_ts = world_state.get("timestamp", "unknown")
    log(f"Checking kill conditions for {len(positions)} positions (world_state @{ws_ts[:19]})")

    new_signals = []
    existing    = load_json(KILL_SIGNALS_FILE, {})
    old_signals = existing.get("signals", [])

    # Index existing signals so we don't duplicate
    existing_keys = {(s["coin"], s["direction"]) for s in old_signals}

    for pos in positions:
        coin      = pos["coin"]
        direction = pos["direction"]
        hyp       = find_hypothesis(pos, all_hyps)
        kill_conds = hyp.get("kill_conditions", [])
        hyp_id     = hyp.get("hypothesis_id", pos.get("hypothesis_id", "unknown"))

        if not kill_conds:
            log(f"  {coin} {direction}: no kill conditions found (hyp_id={hyp_id})")
            continue

        for kc in kill_conds:
            triggered, desc = _check_kill_condition(kc, coin, direction, world_state)
            if triggered:
                key = (coin, direction)
                urgency = _kill_urgency(kc)
                log(f"  KILL SIGNAL: {coin} {direction} — '{kc}' → {desc} [{urgency}]")

                signal_rec = {
                    "coin":          coin,
                    "direction":     direction,
                    "reason":        "kill_condition_hit",
                    "kill_condition": kc,
                    "hypothesis_id": hyp_id,
                    "urgency":       urgency,
                    "detail":        desc,
                }
                # Only add if not already signalled (execution agent may not have run yet)
                if key not in existing_keys:
                    new_signals.append(signal_rec)
                    existing_keys.add(key)
                else:
                    log(f"    (already signalled for {coin} {direction}, skipping duplicate)")
                break  # One signal per position is enough — execution will close it
            else:
                log(f"  {coin} {direction}: '{kc[:40]}...' → {desc} — OK")

    # Merge new signals with unconsumed existing ones
    all_signals = old_signals + new_signals
    if all_signals:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signals":   all_signals,
        }
        save_json(KILL_SIGNALS_FILE, payload)
        if new_signals:
            log(f"  → Wrote {len(new_signals)} new kill signal(s) to kill_signals.json")
    else:
        log("  → No kill conditions triggered")


# ─── ADVERSARY LOOKUP ─────────────────────────────────────────────────────────
def _adversary_verdict_for(hyp_id: str, coin: str, direction: str) -> str:
    """Return adversary verdict ('KILLED', 'PROCEED_WITH_CAUTION', 'WEAK', 'OK') for a hypothesis."""
    adv_data = load_json(ADVERSARY_FILE, {})
    results  = adv_data.get("results", [])
    for r in results:
        if r.get("hypothesis_id") == hyp_id:
            return r.get("verdict", "OK")
        if r.get("coin") == coin and r.get("direction") == direction:
            return r.get("verdict", "OK")
    return "OK"


# ─── OBSERVATION BUILDER ──────────────────────────────────────────────────────
def _build_world_snapshot(coin: str, world_state: dict) -> dict:
    return {
        "regime":           get_coin_regime(coin, world_state),
        "hurst":            get_coin_hurst(coin, world_state),
        "funding_direction": get_coin_funding_direction(coin, world_state),
        "spread_status":    get_coin_spread_status(coin, world_state),
        "pattern":          get_coin_pattern(coin, world_state),
    }


def _determine_thesis_outcome(trade: dict, hyp: dict) -> tuple[bool, bool]:
    """
    Determine if thesis / anti-thesis proved correct.
    thesis_correct   = win (price moved in predicted direction)
    anti_thesis_correct = loss AND anti-thesis prediction came true
    This is heuristic — we don't have full post-mortem data, so we use pnl sign
    and match against anti-thesis text for known patterns.
    """
    pnl = trade.get("pnl_pct", 0)
    outcome = "win" if pnl > 0 else "loss"
    thesis_correct = outcome == "win"

    # Anti-thesis correct if:
    # 1. Trade lost
    # 2. Exit reason matches something the anti-thesis warned about
    anti_thesis = hyp.get("anti_thesis", "")
    exit_reason  = trade.get("exit_reason", "")
    anti_correct = False
    if outcome == "loss" and anti_thesis:
        kw = anti_thesis.lower()
        er = exit_reason.lower()
        # Pattern matching between anti-thesis text and exit reason
        if "hurst" in kw and ("exit_expression" in er or "stop" in er):
            anti_correct = True
        elif "overbought" in kw and "exit_expression" in er:
            anti_correct = True
        elif "pattern" in kw and "alignment" in er:
            anti_correct = True
        elif "momentum" in kw and ("stop" in er or "trailing" in er):
            anti_correct = True

    return thesis_correct, anti_correct


def _which_kill_condition_hit(trade: dict, hyp: dict) -> str | None:
    """
    Return the kill condition label that most likely triggered the close,
    or None if it was a normal exit.
    """
    exit_reason = trade.get("exit_reason", "")
    if exit_reason == "kill_condition":
        # Check kill_signals history if we have it — for now read from closed trade
        return trade.get("kill_condition_hit", "kill_condition (from signal)")
    return None


def _generate_lesson(outcome: str, anti_correct: bool, anti_thesis: str,
                     kill_hit: str | None, thesis_correct: bool,
                     regime: str, direction: str,
                     adversary_was_right: bool, hold_hours: float,
                     exit_reason: str) -> str:
    """Generate a concise human-readable lesson from the trade outcome."""
    if outcome == "loss":
        if anti_correct and anti_thesis:
            short_anti = anti_thesis[:80] + ("…" if len(anti_thesis) > 80 else "")
            return f"Anti-thesis '{short_anti}' was correct — {exit_reason}"
        if kill_hit:
            return f"Kill condition '{kill_hit}' triggered as predicted"
        if adversary_was_right:
            return f"Adversary was right — trade failed despite approval ({exit_reason})"
        return f"loss after {hold_hours:.1f}h — {exit_reason}"

    # Win
    if thesis_correct:
        return f"Thesis held — {regime} regime supported {direction}"
    if adversary_was_right is False:  # explicitly False, not None
        return f"Adversary was wrong — trade succeeded despite low survival score"
    return f"win after {hold_hours:.1f}h — {exit_reason}"


def _build_observation(trade: dict, hyp: dict, world_state: dict) -> dict:
    """Build a structured observation from a closed trade + hypothesis + world state."""
    coin      = trade.get("coin", "?")
    direction = trade.get("direction", "?")
    pnl_pct   = trade.get("pnl_pct", 0)
    pnl_usd   = trade.get("pnl_usd", 0)
    outcome   = "win" if pnl_pct > 0 else "loss"

    # Times
    entry_time = trade.get("entry_time", "")
    exit_time  = trade.get("exit_time", "")
    hold_hours = 0.0
    if entry_time and exit_time:
        try:
            ent = datetime.fromisoformat(entry_time)
            ext = datetime.fromisoformat(exit_time)
            hold_hours = round((ext - ent).total_seconds() / 3600, 2)
        except Exception:
            pass

    thesis_correct, anti_correct = _determine_thesis_outcome(trade, hyp)
    kill_hit = _which_kill_condition_hit(trade, hyp)
    exit_reason = trade.get("exit_reason", "unknown")

    # Adversary verdict
    hyp_id   = hyp.get("hypothesis_id", trade.get("hypothesis_id", "unknown"))
    verdict  = _adversary_verdict_for(hyp_id, coin, direction)
    # adversary_was_right: adversary warned CAUTION/WEAK/KILLED AND trade lost
    adversary_warned  = verdict in ("WEAK", "PROCEED_WITH_CAUTION", "KILLED")
    adversary_was_right = adversary_warned and outcome == "loss"

    world_snap = _build_world_snapshot(coin, world_state)
    regime     = world_snap.get("regime", "unknown")

    anti_thesis = hyp.get("anti_thesis", "")
    lesson = _generate_lesson(
        outcome, anti_correct, anti_thesis, kill_hit,
        thesis_correct, regime, direction,
        adversary_was_right, hold_hours, exit_reason
    )

    return {
        "trade_id":            f"{coin}_{direction}_{entry_time[:19].replace(':', '-').replace('T', '_')}",
        "hypothesis_id":       hyp_id,
        "observed_at":         datetime.now(timezone.utc).isoformat(),
        "coin":                coin,
        "direction":           direction,
        "signal":              trade.get("signal", ""),
        "outcome":             outcome,
        "pnl_pct":             pnl_pct,
        "pnl_usd":             pnl_usd,
        "hold_hours":          hold_hours,
        "exit_reason":         exit_reason,
        "entry_time":          entry_time,
        "exit_time":           exit_time,
        "thesis_correct":      thesis_correct,
        "anti_thesis_correct": anti_correct,
        "kill_condition_hit":  kill_hit,
        "adversary_verdict":   verdict,
        "adversary_was_right": adversary_was_right,
        "world_state_at_exit": world_snap,
        "lesson":              lesson,
    }


def _update_episode(hyp_id: str, observation: dict):
    """Append observation to the episode file for this hypothesis."""
    if not hyp_id or hyp_id == "unknown":
        return
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    ep_file = EPISODES_DIR / f"{hyp_id}.json"
    ep = load_json(ep_file, {})
    if not ep:
        # Episode doesn't exist yet — create minimal stub
        ep = {"hypothesis_id": hyp_id}
    observations = ep.get("observations", [])
    observations.append(observation)
    ep["observations"] = observations
    ep["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(ep_file, ep)
    log(f"  Updated episode {hyp_id}.json with observation")


# ─── STATE TRACKING ──────────────────────────────────────────────────────────
_STATE_FILE = MEMORY_DIR / "observer_state.json"


def _load_state() -> dict:
    return load_json(_STATE_FILE, {"closed_line_count": 0})


def _save_state(state: dict):
    save_json(_STATE_FILE, state)


# ─── RESPONSIBILITY B: OBSERVATION RECORDING ─────────────────────────────────
def record_observations():
    """
    Monitor closed.jsonl for new entries since last run.
    Build and append structured observations for each new closed trade.
    """
    state       = _load_state()
    last_count  = state.get("closed_line_count", 0)
    closed_all  = read_jsonl(CLOSED_FILE)
    total       = len(closed_all)

    if total <= last_count:
        log(f"No new closed trades ({total} total, {last_count} already processed)")
        return

    new_trades  = closed_all[last_count:]
    log(f"New closed trades: {len(new_trades)} (total={total}, prev={last_count})")

    all_hyps    = _load_all_hypotheses()
    world_state = load_json(WORLD_STATE_FILE, {})

    for trade in new_trades:
        coin      = trade.get("coin", "?")
        direction = trade.get("direction", "?")
        log(f"  Observing: {coin} {direction} | {trade.get('exit_reason','?')} | {trade.get('pnl_pct',0):+.2f}%")

        hyp = find_hypothesis(trade, all_hyps)
        if not hyp:
            log(f"    No hypothesis found for {coin} {direction} — using empty")
        observation = _build_observation(trade, hyp, world_state)

        # Upgrade 3: tag observation with trading session
        observation["session"] = get_trading_session(datetime.now(timezone.utc).hour)

        append_jsonl(OBSERVATIONS_FILE, observation)
        log(f"    → {observation['outcome'].upper()} | lesson: {observation['lesson'][:80]}")

        # Upgrade 2: append calibration entry
        now_iso = datetime.now(timezone.utc).isoformat()
        pnl_pct_val = trade.get("pnl_pct", 0)
        pnl_val = trade.get("pnl_usd", 0)
        calibration_entry = {
            "timestamp":            now_iso,
            "hypothesis_id":        observation["hypothesis_id"],
            "predicted_confidence": hyp.get("confidence", 0.5),
            "actual_outcome":       1 if pnl_pct_val > 0 else 0,
            "pnl_pct":              pnl_pct_val,
            "regime":               world_state.get("coins", {}).get(
                                        trade.get("coin", ""), {}
                                    ).get("regime", "unknown"),
            "direction":            trade.get("direction", ""),
        }
        append_jsonl(CALIBRATION_FILE, calibration_entry)
        log(f"    → calibration entry written (conf={calibration_entry['predicted_confidence']:.2f}, outcome={calibration_entry['actual_outcome']})")

        _update_episode(observation["hypothesis_id"], observation)

    # Persist updated line count
    state["closed_line_count"] = total
    _save_state(state)
    log(f"Observations recorded. Total closed now: {total}")


# ─── MAIN CYCLE ──────────────────────────────────────────────────────────────
def run_cycle():
    log("--- Observer cycle ---")
    check_kill_conditions()
    record_observations()
    update_heartbeat()
    log("--- Observer cycle complete ---")


def main():
    loop = "--loop" in sys.argv
    log(f"=== ZERO OS Observer {'(loop)' if loop else '(single)'} ===")

    if loop:
        log(f"Looping every {CYCLE_SECONDS}s")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle error: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

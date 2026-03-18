#!/usr/bin/env python3
"""
ZERO OS — Hypothesis Genealogy Agent
Runs every 15 min. Scans all episode files and builds family-level statistics.

Family key: (signal_type_prefix, regime, direction)
  - signal_type_prefix: first meaningful parts of signal name before V/EX/Q/MH markers
  - e.g. ICHIMOKU_BEARISH_DOJI from ICHIMOKU_BEARISH_DOJI_SHORT_V1_EX0_EX5_Q1

Families aggregate:
  - total_instances, traded, killed, kill_rate
  - win_rate, avg_pnl_pct, avg_hold_hours, best/worst_pnl_pct
  - mature: total_instances >= 20

Output: scanner/bus/genealogy.json
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ─── PATHS ───
AGENT_DIR    = Path(__file__).parent
SCANNER_DIR  = AGENT_DIR.parent
BUS_DIR      = SCANNER_DIR / "bus"
MEMORY_DIR   = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"
LIVE_DIR     = SCANNER_DIR / "data" / "live"

GENEALOGY_FILE  = BUS_DIR / "genealogy.json"
HEARTBEAT_FILE  = BUS_DIR / "heartbeat.json"
OBSERVATIONS_FILE = MEMORY_DIR / "observations.jsonl"
CLOSED_FILE     = LIVE_DIR / "closed.jsonl"

CYCLE_SECONDS   = 900   # 15 minutes
MATURE_THRESHOLD = 20   # instances needed to call a family "mature"
MIN_TRADED_FOR_STATS = 5  # need this many traded to compute win_rate


# ─── LOGGING ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [GENEALOGY] {msg}")


# ─── FILE HELPERS ───
def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = {}
    if HEARTBEAT_FILE.exists():
        try:
            with open(HEARTBEAT_FILE) as f:
                heartbeat = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    heartbeat["genealogy"] = ts
    save_json(HEARTBEAT_FILE, heartbeat)


# ─── SIGNAL FAMILY EXTRACTION ───
def extract_signal_family(signal_name: str) -> str:
    """
    Extract the conceptual family from a full signal name.
    Stops at version/variant markers: V, EX, Q, MH
    Stops at standalone direction tokens: LONG, SHORT (if not first token)

    Examples:
      ICHIMOKU_BEARISH_DOJI_SHORT_V1_EX0  → ICHIMOKU_BEARISH_DOJI
      ARCH_SOCIAL_EXHAUSTION_LONG         → ARCH_SOCIAL_EXHAUSTION
      BB_REVERSAL_SHORT                   → BB_REVERSAL
      TRIPLE_CONVERGENCE_V2_MH3           → TRIPLE_CONVERGENCE
      SINGLE_WORD                         → SINGLE_WORD
    """
    if not signal_name:
        return signal_name
    parts = signal_name.split("_")
    family_parts = []
    for p in parts:
        # Stop at version/variant markers (V1, V2, EX0, EX5, Q1, Q2, MH1, MH3, etc.)
        if any(p.startswith(prefix) and (len(p) == 1 or p[1:].isdigit())
               for prefix in ("V", "EX", "Q", "MH")):
            break
        # Stop at direction indicators if they're NOT the first token
        if p in ("LONG", "SHORT") and family_parts:
            break
        family_parts.append(p)
    return "_".join(family_parts) if family_parts else signal_name


# ─── LOAD EPISODES ───
def load_all_episodes() -> list:
    """Load all episode JSON files from memory/episodes/."""
    episodes = []
    if not EPISODES_DIR.exists():
        return episodes

    for ep_file in EPISODES_DIR.glob("*.json"):
        try:
            with open(ep_file) as f:
                ep = json.load(f)
            ep["_file"] = str(ep_file)
            ep["_id"]   = ep_file.stem
            episodes.append(ep)
        except (json.JSONDecodeError, OSError):
            pass
    return episodes


# ─── LOAD OUTCOMES FROM OBSERVATIONS / CLOSED ───
def load_observations() -> dict:
    """
    Load observations.jsonl and build a lookup by hypothesis_id or trade identifiers.
    Returns dict: {hypothesis_id: obs_dict} + flat list for fuzzy matching.
    """
    obs_by_id: dict = {}
    obs_all:   list = []

    if OBSERVATIONS_FILE.exists():
        try:
            with open(OBSERVATIONS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obs = json.loads(line)
                        hyp_id = obs.get("hypothesis_id") or obs.get("id")
                        if hyp_id:
                            obs_by_id[hyp_id] = obs
                        obs_all.append(obs)
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    return obs_by_id, obs_all


def load_closed_trades() -> list:
    """Load closed trades for outcome matching."""
    trades = []
    if not CLOSED_FILE.exists():
        return trades
    try:
        with open(CLOSED_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return trades


def build_outcome_lookup(obs_by_id: dict, obs_all: list, closed_trades: list) -> dict:
    """
    Build {hypothesis_id: {win: bool, pnl_pct: float, hold_hours: float}} lookup.
    Priority: observations.jsonl > closed.jsonl (match by signal+coin+direction).
    """
    outcomes: dict = {}

    # From observations
    for hyp_id, obs in obs_by_id.items():
        outcome_str = obs.get("outcome", "")
        pnl_pct     = obs.get("pnl_pct",   obs.get("return_pct", obs.get("return", 0)))
        hold_hours  = obs.get("hold_hours", obs.get("hold_h",     obs.get("duration_h", None)))
        try:
            pnl_pct    = float(pnl_pct)
        except (TypeError, ValueError):
            pnl_pct = 0.0
        try:
            hold_hours = float(hold_hours) if hold_hours is not None else None
        except (TypeError, ValueError):
            hold_hours = None

        outcomes[hyp_id] = {
            "win":        outcome_str == "win" or pnl_pct > 0,
            "pnl_pct":    pnl_pct,
            "hold_hours": hold_hours,
        }

    # From closed.jsonl — index by signal+coin+direction for fuzzy match
    closed_lookup: dict = {}  # (signal, coin, direction) → list of trades
    for trade in closed_trades:
        key = (trade.get("signal", ""), trade.get("coin", ""), trade.get("direction", ""))
        closed_lookup.setdefault(key, []).append(trade)

    return outcomes, closed_lookup


# ─── BUILD FAMILIES ───
def build_families(episodes: list, outcomes: dict, closed_lookup: dict) -> dict:
    """
    Build family stats dict keyed by "family|regime|direction".
    """
    raw: dict = {}  # key → {meta + episode lists}

    for ep in episodes:
        signal    = ep.get("signal", "")
        regime    = ep.get("regime", "unknown")
        direction = ep.get("direction", "?")

        if not signal:
            continue

        family = extract_signal_family(signal)
        key    = f"{family}|{regime}|{direction}"

        if key not in raw:
            raw[key] = {
                "family":    family,
                "regime":    regime,
                "direction": direction,
                "episodes":  [],
                "traded":    [],
                "killed":    [],
            }

        ep_id   = ep.get("_id", ep.get("hypothesis_id", ""))
        ep_file = ep.get("_file", "")

        adversary_data = ep.get("adversary", {})
        verdict        = adversary_data.get("verdict", "")

        raw[key]["episodes"].append(ep_id)

        if verdict == "KILLED":
            raw[key]["killed"].append(ep_id)
        else:
            raw[key]["traded"].append(ep_id)

    # ── Compute statistics per family ──
    families: dict = {}

    for key, fam in raw.items():
        total  = len(fam["episodes"])
        traded = len(fam["traded"])
        killed = len(fam["killed"])

        kill_rate = round(killed / total, 4) if total > 0 else 0.0

        # Collect outcome data for traded episodes
        wins       = 0
        pnls       = []
        holds      = []
        best_pnl   = None
        worst_pnl  = None

        for ep_id in fam["traded"]:
            if ep_id in outcomes:
                o = outcomes[ep_id]
                if o["win"]:
                    wins += 1
                pnl = o["pnl_pct"]
                pnls.append(pnl)
                if o["hold_hours"] is not None:
                    holds.append(o["hold_hours"])
                best_pnl  = max(best_pnl,  pnl) if best_pnl  is not None else pnl
                worst_pnl = min(worst_pnl, pnl) if worst_pnl is not None else pnl

        # If we have few direct matches, fuzzy-match from closed trades
        if len(pnls) < MIN_TRADED_FOR_STATS:
            ck = (fam["family"], fam.get("coin_hint", ""), fam["direction"])
            # Try broader match: by family (signal prefix) + direction
            for (sig, coin, dir_), trade_list in closed_lookup.items():
                if extract_signal_family(sig) == fam["family"] and dir_ == fam["direction"]:
                    for trade in trade_list:
                        pnl_raw = trade.get("pnl_pct", trade.get("pnl_dollars", trade.get("pnl_usd", None)))
                        if pnl_raw is None:
                            continue
                        try:
                            pnl = float(pnl_raw)
                        except (TypeError, ValueError):
                            continue
                        pnls.append(pnl)
                        if pnl > 0:
                            wins += 1
                        best_pnl  = max(best_pnl,  pnl) if best_pnl  is not None else pnl
                        worst_pnl = min(worst_pnl, pnl) if worst_pnl is not None else pnl

        # Compute aggregates
        traded_total = max(traded, len(pnls))
        win_rate     = round(wins / traded_total, 4) if traded_total > 0 else None
        avg_pnl      = round(sum(pnls) / len(pnls), 4) if pnls else None
        avg_hold     = round(sum(holds) / len(holds), 2) if holds else None

        if traded_total < MIN_TRADED_FOR_STATS:
            win_rate = None
            avg_pnl  = None

        stat = {
            "family":         fam["family"],
            "regime":         fam["regime"],
            "direction":      fam["direction"],
            "total_instances": total,
            "traded":         traded,
            "killed":         killed,
            "kill_rate":      kill_rate,
            "mature":         total >= MATURE_THRESHOLD,
        }

        if win_rate is not None:
            stat["win_rate"]     = win_rate
            stat["avg_pnl_pct"]  = avg_pnl
            stat["avg_hold_hours"] = avg_hold
            stat["best_pnl_pct"] = round(best_pnl,  4) if best_pnl  is not None else None
            stat["worst_pnl_pct"]= round(worst_pnl, 4) if worst_pnl is not None else None

        families[key] = stat

    return families


# ─── RUN CYCLE ───
def run_cycle():
    ts     = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()

    log("=" * 60)
    log(f"Genealogy Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")

    # Load episodes
    episodes = load_all_episodes()
    log(f"  Loaded {len(episodes)} episodes")

    if not episodes:
        log("  No episodes found — writing empty genealogy")
        save_json(GENEALOGY_FILE, {
            "timestamp":      ts_iso,
            "families":       {},
            "total_families": 0,
            "mature_families": 0,
        })
        write_heartbeat()
        return

    # Load outcomes
    obs_by_id, obs_all = load_observations()
    closed_trades       = load_closed_trades()
    outcomes, closed_lookup = build_outcome_lookup(obs_by_id, obs_all, closed_trades)
    log(f"  Outcome data: {len(outcomes)} from observations, {len(closed_trades)} closed trades")

    # Build families
    families = build_families(episodes, outcomes, closed_lookup)

    total_families  = len(families)
    mature_families = sum(1 for f in families.values() if f.get("mature"))

    output = {
        "timestamp":       ts_iso,
        "families":        families,
        "total_families":  total_families,
        "mature_families": mature_families,
    }

    save_json(GENEALOGY_FILE, output)
    write_heartbeat()

    log(f"  Total families:  {total_families}")
    log(f"  Mature families: {mature_families}")

    # Log top mature families by instance count
    mature = [(k, v) for k, v in families.items() if v.get("mature")]
    mature.sort(key=lambda x: x[1]["total_instances"], reverse=True)
    for key, fam in mature[:5]:
        wr_str = f"WR={fam['win_rate']:.0%}" if fam.get("win_rate") is not None else "WR=?"
        log(
            f"    {key[:50]:52s}  n={fam['total_instances']}  "
            f"kill={fam['kill_rate']:.0%}  {wr_str}"
        )

    log(f"  Written to {GENEALOGY_FILE}")
    log("=" * 60)


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Genealogy Agent starting in loop mode (every {CYCLE_SECONDS}s)")
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

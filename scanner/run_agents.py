#!/usr/bin/env python3
"""
ZERO OS — Agent Supervisor
Runs all agents in the correct order, monitors health, restarts on failure.

Usage:
  python3 scanner/run_agents.py          # run all agents
  python3 scanner/run_agents.py --dry    # show what would run
  python3 scanner/run_agents.py --check  # check agent health

Agent execution order:
  1. Perception (2-min)          — replaces 5 agents: regime + liquidity + spread + timeframe + funding
  2. Hypothesis (10-min)         — structured hypothesis reasoning with rule application
  3. Adversary (5-min)           — kills weak hypotheses (+ rule-based attacks)
  4. Correlation (5-min)         — reads candidates + regime
  5. Risk (2-min)                — reads positions + HL
  6. Parameter Evolution (10-min)— replaces signal_evolution; data-driven rule generation
  7. Execution (5-min)           — reads approved + risk + liquidity + spread, acts last
  8. Observer (2-min)            — kill condition monitoring + observation recording
  9. Reflection (6h)             — LLM-based self-assessment via Ollama; proposes rules
 10. Counterfactual (30-min)    — resolves killed hypotheses; determines adversary accuracy
 11. Adversary Evolution (2h)   — adjusts attack weights based on counterfactual precision
 12. TAAPI Fetcher (15-min)     — fetches TAAPI snapshot for cross-source disagreement attack
 13. Macro Plugin (15-min)      — free macro: F&G, CoinGecko global, DVOL, options expiry, FOMC
"""

import json
import subprocess
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent
AGENTS_DIR = SCANNER_DIR / "agents"
SENSES_DIR = SCANNER_DIR / "senses"
BUS_DIR = SCANNER_DIR / "bus"

PYTHON = "/opt/homebrew/bin/python3"

# Agent definitions: (name, script, cycle_sec, stale_after_min)
# Phase 1 Cognitive Loop: regime + liquidity + spread + cross_timeframe + funding
# are merged into the single perception agent.
AGENTS = [
    ("perception",          AGENTS_DIR / "perception.py",            120,    5),  # replaces 5 agents
    ("hypothesis",          AGENTS_DIR / "hypothesis_generator.py",  600,   20),  # Phase 2: replaces harvester
    ("adversary",           AGENTS_DIR / "adversary.py",             300,   10),  # Phase 3: kills weak hypotheses
    ("correlation",         AGENTS_DIR / "correlation_agent.py",     300,   10),
    ("risk",                AGENTS_DIR / "risk_agent.py",            120,    5),
    ("parameter_evolution", AGENTS_DIR / "parameter_evolution.py",   600,   20),  # Phase 5: replaces evolution
    ("execution",           AGENTS_DIR / "execution_agent.py",       300,   10),
    ("observer",            AGENTS_DIR / "observer.py",              120,    5),   # Phase 4: closes cognitive loop
    ("reflection",          AGENTS_DIR / "reflection.py",          21600, 1440),  # Phase 5: Ollama reflection (6h)
    ("counterfactual",      AGENTS_DIR / "counterfactual.py",       1800,   90),  # Meta-learning: resolves killed hypotheses (30-min)
    ("adversary_evolution", AGENTS_DIR / "adversary_evolution.py",  7200,  240),  # Meta-learning: evolves attack weights (2h)
    ("regime_transition",   AGENTS_DIR / "regime_transition.py",     300,   10),  # Predictive regime transition detection (5-min)
    ("genealogy",           AGENTS_DIR / "genealogy.py",             900,   30),  # Hypothesis genealogy + family learning (15-min)
    ("envy_cache",          AGENTS_DIR / "envy_cache.py",            900,   30),  # ENVY snapshot cache — resilience layer (15-min)
    ("indicator_delta",     AGENTS_DIR / "indicator_delta.py",       900,   30),  # Indicator delta monitor — parallel own computation (15-min)
    ("taapi_fetcher",       AGENTS_DIR / "taapi_fetcher.py",         900,   30),  # TAAPI snapshot — cross-source disagreement feed (15-min)
    ("pattern_scanner",     AGENTS_DIR / "pattern_scanner.py",       900,   30),  # TAAPI candlestick pattern signals (15-min)
    ("macro_plugin",        SENSES_DIR / "macro_plugin.py",          900,   30),  # Free macro: F&G, DVOL, options expiry, FOMC (15-min)
    ("hl_enrichment",       AGENTS_DIR / "hl_enrichment.py",          120,    5),  # HL enrichment: premium, impact spread, OI — single call (2-min)
    ("pack_refresher",      AGENTS_DIR / "pack_refresher.py",       7200,  240),  # Signal pack refresher — pulls new packs from ENVY API (2h)
    ("ws_stream",           AGENTS_DIR / "ws_stream.py",              30,    3),  # WebSocket indicator stream — real-time 15s push
    ("realtime_evaluator",  AGENTS_DIR / "realtime_evaluator.py",    30,    3),  # Real-time signal evaluation against WS data (30s)
]

processes = {}
running = True


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] SUPERVISOR: {msg}")


def check_heartbeat():
    hb_file = BUS_DIR / "heartbeat.json"
    if not hb_file.exists():
        return {}
    try:
        with open(hb_file) as f:
            return json.load(f)
    except Exception:
        return {}


def is_stale(ts_str, stale_minutes):
    if not ts_str:
        return True
    try:
        dt = datetime.fromisoformat(ts_str)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        return age > stale_minutes
    except Exception:
        return True


def start_agent(name, script, cycle_sec):
    if not script.exists():
        log(f"  [{name}] script not found: {script}")
        return None
    log(f"  [{name}] starting (cycle={cycle_sec}s)...")
    proc = subprocess.Popen(
        [PYTHON, str(script), "--loop"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def run_once(name, script):
    """Run agent once (non-loop) for manual check."""
    if not script.exists():
        print(f"  [{name}] NOT FOUND")
        return
    result = subprocess.run([PYTHON, str(script)], capture_output=True, text=True, timeout=60)
    print(result.stdout)
    if result.returncode != 0:
        print(f"  [{name}] ERROR: {result.stderr[:200]}")


def check_health():
    """Print current health of all agents."""
    heartbeats = check_heartbeat()
    risk_file = BUS_DIR / "risk.json"
    regimes_file = BUS_DIR / "regimes.json"

    print("\n=== AGENT HEALTH ===")
    for name, _, _, stale_min in AGENTS:
        hb = heartbeats.get(name)
        stale = is_stale(hb, stale_min)
        status = "⚠️  STALE" if stale else "✅ OK"
        print(f"  {name:12s} {status}  last={hb[:19] if hb else 'never'}")

    print("\n=== RISK STATE ===")
    if risk_file.exists():
        with open(risk_file) as f:
            risk = json.load(f)
        print(f"  Status: {risk.get('status','?').upper()}")
        print(f"  Account: ${risk.get('account_value',0):.2f} | Drawdown: {risk.get('drawdown_pct',0):.2f}%")
        print(f"  Throttle: {risk.get('throttle',1.0)} | Kill: {risk.get('kill_all',False)}")
    else:
        print("  risk.json not found")

    print("\n=== REGIME SUMMARY ===")
    if regimes_file.exists():
        with open(regimes_file) as f:
            regimes = json.load(f)
        coins = regimes.get("coins", {})
        counts = {}
        transitions = []
        for coin, d in coins.items():
            r = d.get("regime", "?")
            counts[r] = counts.get(r, 0) + 1
            if d.get("transition") and d.get("transition_age_min", 999) < 30:
                transitions.append(f"{coin}({d['prev_regime']}→{r})")
        print(f"  {dict(sorted(counts.items()))}")
        if transitions:
            print(f"  Recent transitions: {', '.join(transitions[:5])}")
    else:
        print("  regimes.json not found")

    print("\n=== CANDIDATES / APPROVED ===")
    cand_file = BUS_DIR / "candidates.json"
    appr_file = BUS_DIR / "approved.json"
    if cand_file.exists() and cand_file.stat().st_size > 2:
        with open(cand_file) as f:
            cands = json.load(f)
        print(f"  Candidates: {len(cands.get('candidates', []))}")
    else:
        print("  Candidates: 0 (hypothesis generator not run)")
    if appr_file.exists():
        with open(appr_file) as f:
            appr = json.load(f)
        approved = appr.get("approved", [])
        blocked = appr.get("blocked", [])
        print(f"  Approved: {len(approved)} | Blocked: {len(blocked)}")
        for a in approved[:3]:
            print(f"    ✓ {a.get('coin')} {a.get('direction')}")

    print()


def signal_handler(sig, frame):
    global running
    log("Shutdown signal received")
    running = False
    for name, proc in processes.items():
        if proc and proc.poll() is None:
            log(f"  Terminating {name}...")
            proc.terminate()
    sys.exit(0)


def main():
    if "--check" in sys.argv:
        check_health()
        return

    if "--dry" in sys.argv:
        print("Would start:")
        for name, script, cycle_sec, _ in AGENTS:
            exists = "✅" if script.exists() else "❌ missing"
            print(f"  {name:12s} {exists}  cycle={cycle_sec}s  {script}")
        return

    # Run-once mode (no --loop)
    if "--once" in sys.argv:
        for name, script, cycle_sec, _ in AGENTS:
            print(f"\n--- {name} ---")
            run_once(name, script)
        return

    log("ZERO OS Agent Supervisor starting")
    BUS_DIR.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start all agents in loop mode
    for name, script, cycle_sec, _ in AGENTS:
        proc = start_agent(name, script, cycle_sec)
        if proc:
            processes[name] = proc

    log(f"Started {len(processes)} agents")

    # Monitor loop — check health every 2 minutes, restart dead agents
    heartbeat_check_interval = 120
    last_check = time.time()

    while running:
        time.sleep(10)
        now = time.time()

        if now - last_check >= heartbeat_check_interval:
            last_check = now
            heartbeats = check_heartbeat()

            for name, script, cycle_sec, stale_min in AGENTS:
                proc = processes.get(name)

                # Check if process died
                if proc and proc.poll() is not None:
                    log(f"  [{name}] died (exit={proc.returncode}), restarting...")
                    new_proc = start_agent(name, script, cycle_sec)
                    if new_proc:
                        processes[name] = new_proc
                    continue

                # Check if heartbeat is stale (process might be hung)
                hb = heartbeats.get(name)
                if proc and proc.poll() is None and is_stale(hb, stale_min):
                    log(f"  [{name}] heartbeat stale (>{stale_min}min), restarting...")
                    proc.terminate()
                    time.sleep(2)
                    new_proc = start_agent(name, script, cycle_sec)
                    if new_proc:
                        processes[name] = new_proc

            # Log brief status
            risk_file = BUS_DIR / "risk.json"
            if risk_file.exists():
                try:
                    with open(risk_file) as f:
                        risk = json.load(f)
                    status = risk.get("status", "?")
                    acct = risk.get("account_value", 0)
                    dd = risk.get("drawdown_pct", 0)
                    kill = risk.get("kill_all", False)
                    log(f"Risk: {status.upper()} | ${acct:.2f} | DD {dd:.1f}% | kill={kill}")

                    if kill:
                        log("KILL ALL triggered by Risk Agent — halting new trades")
                except Exception:
                    pass


if __name__ == "__main__":
    main()

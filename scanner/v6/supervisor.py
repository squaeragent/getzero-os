#!/usr/bin/env python3
"""
V6 Supervisor — runs all components with health monitoring and auto-restart.

Components:
  strategy_manager  — refreshes ENVY strategies every 6h
  evaluator         — WebSocket signal evaluation (continuous)
  risk_guard        — position limit checks (every 5s)
  executor          — HL execution (every 5s)
"""

import json
import os
import sys
import time
import subprocess
import signal
from pathlib import Path
from datetime import datetime, timezone

V6_DIR   = Path(__file__).parent
BUS_DIR  = V6_DIR / "bus"
PYTHON   = sys.executable
LOG_FILE = V6_DIR / "supervisor.log"

# Component definitions: (name, script, cycle_seconds, stale_threshold_seconds)
COMPONENTS = [
    ("strategy_manager", V6_DIR / "strategy_manager.py", 21600, 900),   # 6h cycle, stale if >15min
    ("risk_guard",       V6_DIR / "risk_guard.py",           5,    60),  # 5s cycle, stale if >60s
    ("executor",         V6_DIR / "executor.py",              5,    60),  # 5s cycle, stale if >60s
    # evaluator runs as a background loop (WebSocket — managed separately)
]

processes = {}


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg):
    line = f"[{ts()}] SUPERVISOR: {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception as _e:
        pass  # swallowed: {_e}


def load_heartbeat():
    hb_file = BUS_DIR / "heartbeat.json"
    if hb_file.exists():
        try:
            with open(hb_file) as f:
                return json.load(f)
        except Exception as _e:
            pass  # swallowed: {_e}
    return {}


def is_stale(name, threshold_seconds):
    hb = load_heartbeat()
    ts_str = hb.get(name)
    if not ts_str:
        return True
    try:
        last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last).total_seconds()
        return age > threshold_seconds
    except Exception as _e:
        return True


def run_once(name, script):
    """Run a component once (for strategy_manager and cycle-based agents)."""
    try:
        result = subprocess.run(
            [PYTHON, str(script)],
            capture_output=True, text=True, timeout=300
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                print(f"  [{name}] {line}", flush=True)
        if result.returncode != 0 and result.stderr:
            log(f"  [{name}] ERROR: {result.stderr[:300]}")
        return result.returncode == 0
    except subprocess.TimeoutExpired as _e:
        log(f"  [{name}] TIMEOUT after 300s")
        return False
    except Exception as e:
        log(f"  [{name}] FAILED: {e}")
        return False


def start_evaluator():
    """Start the evaluator as a long-running background process."""
    name = "evaluator"
    if name in processes:
        proc = processes[name]
        if proc.poll() is None:
            return  # Already running
        else:
            log(f"evaluator exited with code {proc.returncode}, restarting")

    log("Starting evaluator (WebSocket loop)...")
    try:
        proc = subprocess.Popen(
            [PYTHON, str(V6_DIR / "evaluator.py"), "--loop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        processes[name] = proc
        log(f"evaluator PID: {proc.pid}")
    except Exception as e:
        log(f"Failed to start evaluator: {e}")


def start_immune():
    """Start the immune system monitor as a background process."""
    name = "immune"
    if name in processes:
        proc = processes[name]
        if proc.poll() is None:
            return
        else:
            log(f"immune exited with code {proc.returncode}, restarting")

    log("Starting immune system monitor...")
    try:
        proc = subprocess.Popen(
            [PYTHON, str(V6_DIR / "immune.py"), "--loop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        processes[name] = proc
        log(f"immune PID: {proc.pid}")
    except Exception as e:
        log(f"Failed to start immune: {e}")


def check_evaluator():
    """Check evaluator health and restart if needed."""
    start_evaluator()  # start if not running

    proc = processes.get("evaluator")
    if proc and proc.poll() is not None:
        log(f"evaluator died (code {proc.returncode}), restarting")
        del processes["evaluator"]
        start_evaluator()


def signal_handler(sig, frame):
    log("Shutting down...")
    for name, proc in processes.items():
        try:
            proc.terminate()
            log(f"Terminated {name} (PID {proc.pid})")
        except Exception as _e:
            pass  # swallowed: {_e}
    sys.exit(0)


def run_health_check():
    hb = load_heartbeat()
    now = datetime.now(timezone.utc)
    status = []
    for name, _, _, stale_thresh in COMPONENTS:
        ts_str = hb.get(name)
        if ts_str:
            try:
                last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = int((now - last).total_seconds())
                flag = "✅" if age < stale_thresh else "⚠️ STALE"
                status.append(f"{name:20s}: {age:4d}s {flag}")
            except Exception as _e:
                status.append(f"{name:20s}: parse error")
        else:
            status.append(f"{name:20s}: no heartbeat")
    # Evaluator (long-running)
    proc = processes.get("evaluator")
    if proc and proc.poll() is None:
        ts_str = hb.get("evaluator")
        if ts_str:
            try:
                last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = int((now - last).total_seconds())
                flag = "✅" if age < 120 else "⚠️ STALE"
                status.append(f"{'evaluator':20s}: {age:4d}s {flag}")
            except Exception as _e:
                status.append(f"{'evaluator':20s}: running (no hb)")
        else:
            status.append(f"{'evaluator':20s}: running (no hb yet)")
    else:
        status.append(f"{'evaluator':20s}: DEAD")
    log("Health: " + " | ".join(status))


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Ensure directories exist
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    (V6_DIR / "data").mkdir(parents=True, exist_ok=True)

    log("=" * 60)
    log("V6 Supervisor starting")
    log(f"Python: {PYTHON}")
    log("=" * 60)

    # Initial strategy refresh
    log("Initial strategy refresh...")
    run_once("strategy_manager", V6_DIR / "strategy_manager.py")

    # Start evaluator (WebSocket loop)
    start_evaluator()

    # Start immune system (monitoring loop)
    start_immune()
    time.sleep(3)  # Give them time to start

    last_strategy_refresh = time.time()
    last_health_check = time.time()
    cycle_times = {name: 0 for name, _, _, _ in COMPONENTS}
    cycle_times["strategy_manager"] = time.time()  # already ran

    log("All components started. Entering main loop.")

    while True:
        now = time.time()

        # Run risk_guard and executor on their cycles
        for name, script, cycle_s, stale_thresh in COMPONENTS:
            if name == "strategy_manager":
                continue  # handled separately below
            if now - cycle_times.get(name, 0) >= cycle_s:
                run_once(name, script)
                cycle_times[name] = now

        # Strategy refresh every 2h
        if now - last_strategy_refresh >= 7200:
            log("Refreshing strategies (2h interval)...")
            run_once("strategy_manager", V6_DIR / "strategy_manager.py")
            last_strategy_refresh = now

        # Check evaluator and immune health
        check_evaluator()
        start_immune()  # restart if died

        # Health check every 5 minutes
        if now - last_health_check >= 300:
            run_health_check()
            last_health_check = now

        # Log rotation: truncate if > 10MB
        log_file = V6_DIR / "supervisor.log"
        try:
            if log_file.exists() and log_file.stat().st_size > 10 * 1024 * 1024:
                lines = log_file.read_text().splitlines()
                # Keep last 1000 lines
                log_file.write_text("\n".join(lines[-1000:]) + "\n")
                log(f"Log rotated: kept last 1000 lines")
        except Exception as _e:
            pass

        time.sleep(5)


if __name__ == "__main__":
    main()

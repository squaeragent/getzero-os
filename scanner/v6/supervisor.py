#!/usr/bin/env python3
"""
V6 Supervisor — runs all components with health monitoring and auto-restart.

Components:
  local_evaluator   — SmartProvider signal evaluation (continuous, HL public API)
  controller        — unified engine: risk checks + execution (every 5s)
  market_monitor    — market regime tracking (every 5min)

Session 8b: risk_guard + executor absorbed into controller.py.
Legacy mode kept for emergency rollback only (--legacy flag).
"""

import json
import os
import sys
import time
import subprocess
import signal
import threading
from pathlib import Path
from datetime import datetime, timezone

# Supabase bridge — telemetry only
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from supabase_bridge import bridge as _sb
except Exception:
    _sb = None

V6_DIR   = Path(__file__).parent
BUS_DIR  = V6_DIR / "bus"
PYTHON   = sys.executable
LOG_FILE = V6_DIR / "supervisor.log"

# Session expiry — graceful fallback
try:
    from scanner.v6.session_manager_legacy import check_session_expiry as _check_session_expiry
    _SESSION_AVAILABLE = True
except ImportError:
    _SESSION_AVAILABLE = False

# ── Supervisor mode ───────────────────────────────────────────────────────────
# --controller : unified controller mode (replaces separate risk_guard + executor)
#                Runs scanner/v6/controller.py which is the spec-compliant gate.
# --legacy      : old mode — run risk_guard + executor as separate processes (default fallback)
#
# Controller mode is the preferred path for new sessions with YAML strategy configs.
# Legacy mode is kept as a fallback for backward compatibility and hotfix scenarios.
_USE_CONTROLLER = "--controller" in sys.argv

# Component definitions: (name, script, cycle_seconds, stale_threshold_seconds)
if _USE_CONTROLLER:
    # Unified controller replaces risk_guard + executor
    COMPONENTS = [
        ("controller",     V6_DIR / "controller.py",           5,    60),  # 5s cycle, stale if >60s
        ("market_monitor", V6_DIR / "market_monitor.py",      300,   600),  # 5min cycle, stale if >10min
    ]
else:
    # Legacy: separate risk_guard + executor (default — always works)
    COMPONENTS = [
        ("risk_guard",       V6_DIR / "risk_guard_legacy.py",   5,    60),  # legacy — renamed
        ("executor",         V6_DIR / "executor_v6_legacy.py",  5,    60),  # legacy — renamed
        ("market_monitor",   V6_DIR / "market_monitor.py",    300,   600),  # 5min cycle, stale if >10min
    ]

processes = {}


def _drain_output(proc, name):
    """Drain stdout from a subprocess and log each line. Runs in a thread."""
    try:
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            if stripped:
                print(f"  [{name}] {stripped}", flush=True)
    except (ValueError, OSError):
        pass  # pipe closed


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg):
    line = f"[{ts()}] SUPERVISOR: {msg}"
    print(line, flush=True)
    # NOTE: don't also write to LOG_FILE — launchd already redirects stdout there
    # Writing both causes every line to appear twice


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


def write_heartbeat(name: str):
    """Write a heartbeat entry for a component. Used when supervisor manages the cycle."""
    hb_file = BUS_DIR / "heartbeat.json"
    try:
        hb = {}
        if hb_file.exists():
            try:
                with open(hb_file) as f:
                    hb = json.load(f)
            except Exception:
                pass
        hb[name] = datetime.now(timezone.utc).isoformat()
        tmp = hb_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(hb, f, indent=2)
        tmp.replace(hb_file)
    except Exception as e:
        log(f"WARN: failed to write heartbeat for {name}: {e}")


def run_once(name, script):
    """Run a component once (for cycle-based agents)."""
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
    """Start the monitor as a long-running background process.

    Session 9: replaced local_evaluator with monitor.py (7-layer evaluation, signal state machine).
    Legacy evaluator preserved as local_evaluator_legacy.py.
    """
    name = "monitor"
    if name in processes:
        proc = processes[name]
        if proc.poll() is None:
            return  # Already running
        else:
            log(f"monitor exited with code {proc.returncode}, restarting")
            del processes[name]

    log("Starting monitor (7-layer evaluation, signal state machine)...")
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [PYTHON, str(V6_DIR / "monitor.py"), "--loop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        processes[name] = proc
        t = threading.Thread(target=_drain_output, args=(proc, name), daemon=True)
        t.start()
        log(f"monitor PID: {proc.pid}")
    except Exception as e:
        log(f"Failed to start monitor: {e}")


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
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [PYTHON, str(V6_DIR / "immune.py"), "--loop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        processes[name] = proc
        t = threading.Thread(target=_drain_output, args=(proc, name), daemon=True)
        t.start()
        log(f"immune PID: {proc.pid}")
    except Exception as e:
        log(f"Failed to start immune: {e}")


def check_evaluator():
    """Check monitor health and restart if needed. (Session 9: replaced local_evaluator)"""
    start_evaluator()  # start if not running

    proc = processes.get("monitor")
    if proc and proc.poll() is not None:
        log(f"monitor died (code {proc.returncode}), restarting")
        del processes["monitor"]
        start_evaluator()


def signal_handler(sig, frame):
    log("Shutting down...")
    # Mark agent as stopped in Supabase
    if _sb:
        _sb.mark_stopped("signal_shutdown")
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
    # Monitor (long-running, Session 9: replaced local_evaluator)
    proc = processes.get("monitor")
    if proc and proc.poll() is None:
        ts_str = hb.get("monitor")
        if ts_str:
            try:
                last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = int((now - last).total_seconds())
                flag = "✅" if age < 120 else "⚠️ STALE"
                status.append(f"{'monitor':20s}: {age:4d}s {flag}")
            except Exception as _e:
                status.append(f"{'monitor':20s}: running (no hb)")
        else:
            status.append(f"{'monitor':20s}: running (no hb yet)")
    else:
        status.append(f"{'monitor':20s}: DEAD")
    log("Health: " + " | ".join(status))


def main():
    # PID lock — prevent dual supervisors
    pid_file = V6_DIR / "supervisor.pid"
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)  # check if alive
            print(f"FATAL: supervisor already running (pid={old_pid}). Kill it first or remove {pid_file}")
            sys.exit(1)
        except ProcessLookupError:
            pass  # stale PID file, safe to continue
    pid_file.write_text(str(os.getpid()))

    def _cleanup_pid(*_):
        pid_file.unlink(missing_ok=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    import atexit
    atexit.register(_cleanup_pid)

    # Paper mode: --paper flag or PAPER_MODE env var
    paper_mode = "--paper" in sys.argv or os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes")
    if paper_mode:
        os.environ["PAPER_MODE"] = "1"

    # In paper mode, redirect supervisor's BUS_DIR to paper bus
    # so heartbeat reads/writes match the components' paper-isolated paths
    global BUS_DIR
    if paper_mode:
        from scanner.v6.config import PAPER_BUS_DIR, PAPER_DATA_DIR
        BUS_DIR = PAPER_BUS_DIR
        PAPER_BUS_DIR.mkdir(parents=True, exist_ok=True)
        PAPER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure directories exist
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    (V6_DIR / "data").mkdir(parents=True, exist_ok=True)

    mode_label = "PAPER" if paper_mode else "LIVE"
    ctrl_label = "CONTROLLER" if _USE_CONTROLLER else "LEGACY (risk_guard+executor)"
    log("=" * 60)
    log(f"V6 Supervisor starting [{mode_label}] [{ctrl_label}]")
    if _USE_CONTROLLER:
        log("Controller mode: strategy YAML risk checks active")
        log("  Use --legacy flag to fall back to risk_guard + executor")
    else:
        log("Legacy mode: risk_guard + executor running separately")
        log("  Use --controller flag to enable unified controller mode")
    log(f"Python: {PYTHON}")
    log("=" * 60)

    # Mark agent as running in Supabase
    if _sb:
        _sb.mark_running({"preset": "balanced", "version": "v6"})

    # Start local evaluator (SmartProvider loop)
    start_evaluator()

    # Start immune system (monitoring loop)
    start_immune()
    time.sleep(3)  # Give them time to start

    last_health_check = time.time()
    # last_portfolio_export removed — static export killed (website uses live API)
    cycle_times = {name: 0 for name, _, _, _ in COMPONENTS}

    log("All components started. Entering main loop.")

    while True:
        now = time.time()

        # Run risk_guard, executor, market_monitor on their cycles
        for name, script, cycle_s, stale_thresh in COMPONENTS:
            if now - cycle_times.get(name, 0) >= cycle_s:
                run_once(name, script)
                cycle_times[name] = now

        # Check local_evaluator and immune health
        check_evaluator()
        start_immune()  # restart if died

        # Check session expiry — auto-completes expired sessions
        if _SESSION_AVAILABLE:
            try:
                _check_session_expiry()
            except Exception as e:
                log(f"WARN: session expiry check failed: {e}")

        # REMOVED: export_portfolio.py wrote static JSON to wrong repo (getzero-os/public/).
        # Website (zeroos-app) fetches live data from api.getzero.dev → localhost:8420.
        # No static export needed.

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

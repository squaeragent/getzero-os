"""Shared utilities for ZERO OS agents.

Single source of truth for JSON I/O, logging, environment loading,
heartbeat management, and trading session classification.
All agents should import from this module instead of reimplementing.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Standard paths — all agents derive from these
# ---------------------------------------------------------------------------
SCANNER_DIR = Path(__file__).parent
AGENTS_DIR = SCANNER_DIR / "agents"
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"
MEMORY_DIR = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# Bus files
POSITIONS_FILE = BUS_DIR / "positions.json"
WORLD_STATE_FILE = BUS_DIR / "world_state.json"
CANDIDATES_FILE = BUS_DIR / "candidates.json"
HYPOTHESES_FILE = BUS_DIR / "hypotheses.json"
ADVERSARY_FILE = BUS_DIR / "adversary.json"
APPROVED_FILE = BUS_DIR / "approved.json"
RISK_FILE = BUS_DIR / "risk.json"
SIGNALS_FILE = BUS_DIR / "signals.json"
KILL_SIGNALS_FILE = BUS_DIR / "kill_signals.json"
REGIMES_FILE = BUS_DIR / "regimes.json"

# Memory files
OBSERVATIONS_FILE = MEMORY_DIR / "observations.jsonl"
CALIBRATION_FILE = MEMORY_DIR / "calibration.jsonl"
SIGNAL_OUTCOMES_FILE = MEMORY_DIR / "signal_outcomes.jsonl"

# Data files
CLOSED_FILE = DATA_DIR / "closed.jsonl"
CLOSED_FILE_LIVE = LIVE_DIR / "closed.jsonl"

# ---------------------------------------------------------------------------
# JSON I/O — unified implementations
# ---------------------------------------------------------------------------


def load_json(path, default=None):
    """Load JSON file safely, returning default on any error.

    Handles missing files, empty files, and decode errors gracefully.
    """
    if default is None:
        default = {}
    p = Path(path)
    if not p.exists():
        return default
    try:
        if p.stat().st_size == 0:
            return default
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return default


def save_json(path, data, indent=2):
    """Write JSON to file, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=indent)


def save_json_atomic(path, data, indent=2):
    """Write JSON atomically — crash-safe via tempfile + rename."""
    path_str = str(path)
    dir_name = os.path.dirname(path_str) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, path_str)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# Backward-compatible alias
load_json_safe = load_json

# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------


def append_jsonl(path, record):
    """Append a single JSON record as a line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path, max_lines=None):
    """Read JSONL file, optionally returning only the last N records."""
    p = Path(path)
    if not p.exists():
        return []
    records = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    if max_lines is not None:
        return records[-max_lines:]
    return records


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def make_logger(agent_name, log_file=None):
    """Create a log function for an agent.

    Returns a callable that prints timestamped messages with the agent prefix
    and optionally writes to a log file.

    Usage:
        log = make_logger("ADVERSARY")
        log("Starting cycle")
        # -> [2026-03-28 14:30:45 UTC] [ADVERSARY] Starting cycle

        log = make_logger("OBS", log_file=Path("logs/observer.log"))
        log("Detected regime change")
        # -> prints + appends to file
    """
    def _log(msg):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] [{agent_name}] {msg}"
        print(line)
        if log_file is not None:
            try:
                Path(log_file).parent.mkdir(parents=True, exist_ok=True)
                with open(log_file, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass
    return _log


# ---------------------------------------------------------------------------
# Environment / API key loading
# ---------------------------------------------------------------------------

_ENV_PATH = os.path.expanduser("~/getzero-os/.env")


def load_env(env_path=None):
    """Load all key=value pairs from .env file.

    Handles 'export KEY=val', 'KEY=val', quoted values, and comments.
    Returns a dict of all parsed variables.
    """
    path = env_path or _ENV_PATH
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def load_api_key(key_name="ENVY_API_KEY"):
    """Load a specific API key from environment or .env file.

    Checks os.environ first, then falls back to ~/getzero-os/.env.
    Raises RuntimeError if not found.
    """
    val = os.environ.get(key_name)
    if val:
        return val
    env = load_env()
    val = env.get(key_name)
    if val:
        return val
    raise RuntimeError(f"{key_name} not found in env or {_ENV_PATH}")


# ---------------------------------------------------------------------------
# Trading session classification
# ---------------------------------------------------------------------------


def get_trading_session(utc_hour):
    """Classify UTC hour into trading session.

    Returns one of: ASIA (0-7), EUROPE (7-13), US (13-20), LATE_US (20-24).
    """
    if 0 <= utc_hour < 7:
        return "ASIA"
    elif 7 <= utc_hour < 13:
        return "EUROPE"
    elif 13 <= utc_hour < 20:
        return "US"
    else:
        return "LATE_US"


# ---------------------------------------------------------------------------
# Heartbeat management
# ---------------------------------------------------------------------------


def update_heartbeat(agent_name, heartbeat_file=None):
    """Update the shared heartbeat file with current timestamp for this agent."""
    hb_file = Path(heartbeat_file) if heartbeat_file else HEARTBEAT_FILE
    hb = load_json(hb_file, {})
    hb[agent_name] = datetime.now(timezone.utc).isoformat()
    try:
        save_json_atomic(str(hb_file), hb)
    except OSError:
        # Heartbeat is best-effort — don't crash the agent
        pass

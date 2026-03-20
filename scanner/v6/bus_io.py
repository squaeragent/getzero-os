"""
Shared file I/O with locking for the V6 bus system.
All bus file reads/writes should use these functions.
"""
import fcntl
import json
import os
from pathlib import Path


def load_json(path: Path, default=None) -> dict:
    """Read JSON — no locking (for non-contended files)."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as _e:
        return default


def load_json_locked(path: Path, default=None) -> dict:
    """Read JSON with shared lock to prevent partial reads."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as _e:
        return default


def save_json_atomic(path: Path, data: dict):
    """Write JSON atomically with fsync (no lock)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def save_json_locked(path: Path, data: dict):
    """Write JSON with exclusive lock + fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, record: dict):
    """Append a single JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

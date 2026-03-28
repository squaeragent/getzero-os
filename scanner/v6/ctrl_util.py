"""
Controller utility functions — logging, time, JSON I/O.

Zero internal dependencies (only scanner.v6.config for paths).
This is the leaf of the controller dependency graph.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import scanner.v6.config as _cfg


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [CTRL] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def update_heartbeat() -> None:
    hb = load_json(_cfg.HEARTBEAT_FILE, {})
    hb["controller"] = now_iso()
    save_json_atomic(_cfg.HEARTBEAT_FILE, hb)

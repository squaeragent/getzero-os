"""Shared utilities for ZERO OS agents."""
import json
import os
import tempfile


def save_json_atomic(path, data, indent=2):
    """Write JSON atomically — crash-safe via tempfile + rename."""
    path_str = str(path)
    dir_name = os.path.dirname(path_str) or "."
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, path_str)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_json_safe(path, default=None):
    """Load JSON file safely, returning default on any error."""
    try:
        with open(str(path)) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

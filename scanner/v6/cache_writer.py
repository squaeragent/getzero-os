#!/usr/bin/env python3
"""
Cache Writer — writes engine state to Supabase every 60 seconds.

Decouples the website from the engine's real-time performance.
Website reads from cache (sub-50ms). Engine writes on its own schedule.

Usage:
  python -m scanner.v6.cache_writer          # loop (default)
  python -m scanner.v6.cache_writer --once   # single run
  python -m scanner.v6.cache_writer --loop   # explicit loop
"""

import json
import logging
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── Setup ───────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.config import load_env, get_env

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] CACHE: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("zero.cache_writer")

# ─── Config ──────────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8420"
WRITE_INTERVAL = 60  # seconds

ENDPOINTS = {
    "heat":        "/v6/heat",
    "regime":      "/v6/regime",
    "approaching": "/v6/approaching",
    "brief":       "/v6/brief",
    "health":      "/v6/engine/health",
    "sessions":    "/v6/session/status",
    "collective":  "/v6/collective",
}

# ─── Engine fetch ────────────────────────────────────────────────────────────

def fetch_endpoint(path: str, timeout: float = 3.0) -> dict | list | None:
    """Fetch JSON from the engine API. Returns None on failure."""
    url = f"{API_BASE}{path}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        log.warning("fetch %s failed: %s", path, e)
        return None
    except json.JSONDecodeError as e:
        log.warning("fetch %s bad JSON: %s", path, e)
        return None


# ─── Supabase upsert ────────────────────────────────────────────────────────

def cache_upsert(supabase_url: str, supabase_key: str, key: str, data) -> bool:
    """Upsert a cache row into engine_cache table. Returns True on success."""
    url = f"{supabase_url}/rest/v1/engine_cache"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    body = json.dumps({
        "key": key,
        "data": data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        log.warning("upsert '%s' failed: %s", key, e)
        return False


# ─── Table setup ─────────────────────────────────────────────────────────────

def ensure_table(supabase_url: str, supabase_key: str) -> bool:
    """Create engine_cache table if it doesn't exist via Supabase RPC."""
    url = f"{supabase_url}/rest/v1/rpc/exec_sql"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    sql = (
        "CREATE TABLE IF NOT EXISTS engine_cache ("
        "  key text PRIMARY KEY,"
        "  data jsonb NOT NULL,"
        "  updated_at timestamptz DEFAULT now()"
        ")"
    )
    body = json.dumps({"query": sql}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("engine_cache table ensured")
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log.warning(
            "Could not auto-create engine_cache table: %s — "
            "create it manually in Supabase dashboard:\n"
            "  CREATE TABLE engine_cache (key text PRIMARY KEY, data jsonb NOT NULL, updated_at timestamptz DEFAULT now());",
            e,
        )
        return False


# ─── Main loop ───────────────────────────────────────────────────────────────

def write_all(supabase_url: str, supabase_key: str) -> dict:
    """Fetch all endpoints and write to Supabase. Returns {key: success}."""
    results = {}
    for key, path in ENDPOINTS.items():
        data = fetch_endpoint(path)
        if data is None:
            results[key] = False
            continue
        ok = cache_upsert(supabase_url, supabase_key, key, data)
        results[key] = ok
    return results


def run_once(supabase_url: str, supabase_key: str):
    """Single cache write pass."""
    results = write_all(supabase_url, supabase_key)
    ok = sum(1 for v in results.values() if v)
    fail = sum(1 for v in results.values() if not v)
    log.info("wrote %d/%d keys (failed: %d)", ok, len(results), fail)
    if fail:
        failed = [k for k, v in results.items() if not v]
        log.info("  failed: %s", ", ".join(failed))
    return results


def run_loop(supabase_url: str, supabase_key: str):
    """Continuous cache writer — writes every WRITE_INTERVAL seconds."""
    log.info("starting cache writer loop (interval=%ds)", WRITE_INTERVAL)
    while True:
        try:
            run_once(supabase_url, supabase_key)
        except Exception as e:
            log.error("write_all crashed: %s", e)
        time.sleep(WRITE_INTERVAL)


def main():
    env = load_env()
    supabase_url = env.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = env.get("SUPABASE_SERVICE_KEY", "")

    if not supabase_url or not supabase_key:
        log.error("SUPABASE_URL or SUPABASE_SERVICE_KEY missing from ~/getzero-os/.env")
        sys.exit(1)

    log.info("supabase: %s", supabase_url)
    log.info("engine:   %s", API_BASE)

    # Try to create the table (best-effort)
    ensure_table(supabase_url, supabase_key)

    if "--once" in sys.argv:
        run_once(supabase_url, supabase_key)
    else:
        run_loop(supabase_url, supabase_key)


if __name__ == "__main__":
    main()

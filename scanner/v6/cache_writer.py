#!/usr/bin/env python3
"""
zero▮ cache writer — writes engine state to local JSON files every 60 seconds.
Website reads from api.getzero.dev/v6/cache/* (new endpoints).
Decouples the website from real-time engine performance.

Usage:
  python3 scanner/v6/cache_writer.py          # loop mode (default)
  python3 scanner/v6/cache_writer.py --once   # single write
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "data" / "cache"
API_BASE = "http://localhost:8420"
INTERVAL = 60  # seconds

ENDPOINTS = {
    "heat": "/v6/heat",
    "regime": "/v6/regime",
    "approaching": "/v6/approaching",
    "brief": "/v6/brief",
    "health": "/health",
    "sessions": "/v6/session/status",
    "collective": "/v6/collective",
    "engine_stats": "/v6/engine/stats",
}

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] CACHE: {msg}", flush=True)

def fetch(endpoint: str, timeout: float = 3.0):
    try:
        req = urllib.request.Request(f"{API_BASE}{endpoint}")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        return None

def write_cache():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    failed = []
    
    for key, endpoint in ENDPOINTS.items():
        timeout = 30.0 if key in ('heat', 'regime') else 3.0
        data = fetch(endpoint, timeout=timeout)
        if data is None:
            failed.append(key)
            continue
        
        cache_entry = {
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "key": key,
        }
        
        cache_file = CACHE_DIR / f"{key}.json"
        try:
            cache_file.write_text(json.dumps(cache_entry, default=str))
            written += 1
        except Exception as e:
            log(f"write {key} failed: {e}")
            failed.append(key)
    
    log(f"wrote {written}/{len(ENDPOINTS)} keys" + (f" (failed: {', '.join(failed)})" if failed else ""))
    return written

def main():
    log(f"cache dir: {CACHE_DIR}")
    log(f"engine:    {API_BASE}")
    
    if "--once" in sys.argv:
        write_cache()
        return
    
    log(f"starting loop (every {INTERVAL}s)")
    while True:
        try:
            write_cache()
        except Exception as e:
            log(f"cycle error: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()

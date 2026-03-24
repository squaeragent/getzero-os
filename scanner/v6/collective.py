"""
collective.py — Collective Learning Client

Reports anonymized trade outcomes to the zero network.
Fetches learned weights from /api/intelligence/sync.

Data submitted: coin, direction, regime, indicator votes, pnl_pct,
hold time, MAE/MFE. NO dollars, NO wallet, NO identity.
"""

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

AGENT_UUID = os.environ.get("AGENT_UUID", "4802c6f8-f862-42f1-b248-45679e1517e7")
SYNC_URL = os.environ.get("ZERO_SYNC_URL", "https://getzero.dev/api/intelligence")

# Anonymize agent ID
AGENT_HASH = hashlib.sha256(AGENT_UUID.encode()).hexdigest()[:16]

# Cache learned weights
_cached_weights: dict | None = None
_cache_time: float = 0
CACHE_TTL = 3600  # 1 hour


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [collective] [{ts}] {msg}", flush=True)


def _post(endpoint: str, data: dict) -> bool:
    """POST to the intelligence API. Fire-and-forget."""
    url = f"{SYNC_URL}/{endpoint}"
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201, 204)
    except Exception as e:
        _log(f"report failed ({endpoint}): {e}")
        return False


def _get(endpoint: str) -> dict | None:
    """GET from the intelligence API."""
    url = f"{SYNC_URL}/{endpoint}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        _log(f"sync failed ({endpoint}): {e}")
        return None


def report_trade(
    coin: str,
    direction: str,
    regime: str | None = None,
    indicator_votes: dict | None = None,
    pnl_pct: float | None = None,
    hold_seconds: int | None = None,
    mae_pct: float | None = None,
    mfe_pct: float | None = None,
    exit_reason: str | None = None,
    hurst: float | None = None,
    efficiency: float | None = None,
):
    """Report a completed trade to the collective (async, fire-and-forget)."""
    data = {
        "agent_hash": AGENT_HASH,
        "coin": coin,
        "direction": direction.lower(),
        "regime": regime,
        "indicator_votes": indicator_votes,
        "pnl_pct": pnl_pct,
        "hold_seconds": hold_seconds,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "exit_reason": exit_reason,
        "hurst": hurst,
        "efficiency": efficiency,
    }
    # Strip None values
    data = {k: v for k, v in data.items() if v is not None}
    # V6: Only live agents with verified fees contribute
    try:
        from vulnerability_fixes import can_contribute
        agent_info = {"mode": "live"}  # default for local agent
        config_f = Path.home() / ".zeroos" / "config.json"
        if config_f.exists():
            cfg = json.loads(config_f.read_text())
            agent_info["mode"] = cfg.get("mode", "live")
            agent_info["last_fee_payment"] = cfg.get("last_fee_payment")
        if not can_contribute(agent_info):
            _log(f"skipped collective report: agent not eligible (mode={agent_info.get('mode')})")
            return
    except Exception:
        pass  # never block reporting if security module fails

    threading.Thread(target=_post, args=("report", data), daemon=True).start()
    _log(f"reported: {coin} {direction} pnl={pnl_pct or '?'}")


def get_learned_weights() -> dict:
    """Fetch latest learned weights from the network. Cached for 1 hour."""
    global _cached_weights, _cache_time

    now = time.time()
    if _cached_weights and (now - _cache_time) < CACHE_TTL:
        return _cached_weights

    result = _get("sync")
    if result and "weights" in result:
        _cached_weights = result
        _cache_time = now
        source = result.get("source", "?")
        _log(f"synced: source={source} tier={result.get('tier', '?')}")
        return result

    # Fallback: return cached or empty
    return _cached_weights or {"weights": {}, "source": "cache_miss"}


def get_blacklist() -> list[str]:
    """Get current blacklisted coins."""
    data = get_learned_weights()
    return data.get("blacklist", [])


def get_regime_consensus() -> dict:
    """Get regime consensus across the network."""
    data = get_learned_weights()
    return data.get("regime_consensus", {})

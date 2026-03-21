#!/usr/bin/env python3
"""
Signal Cache — persistent local cache for signal/strategy/portfolio data.

Stores every API response locally so the system can survive API outages.
Cache directory: ~/.zeroos/cache/
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


CACHE_BASE = Path("~/.zeroos/cache").expanduser()
SIGNALS_DIR = CACHE_BASE / "signals"
STRATEGIES_DIR = CACHE_BASE / "strategies"
PORTFOLIO_DIR = CACHE_BASE / "portfolio"
META_FILE = CACHE_BASE / "meta.json"

# Freshness thresholds (seconds)
FRESH_MAX = 3600         # 0-1h: trade normally
AGING_MAX = 14400        # 1-4h: reduce position sizes by 50%
STALE_MAX = 43200        # 4-12h: no new entries, manage existing
# 12h+: expired → protection mode


def _ensure_dirs():
    for d in (SIGNALS_DIR, STRATEGIES_DIR, PORTFOLIO_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _cache_age(path: Path) -> float:
    """Seconds since file was last modified."""
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return float("inf")


def freshness(age_seconds: float) -> str:
    """Classify cache age into freshness category."""
    if age_seconds <= FRESH_MAX:
        return "fresh"
    elif age_seconds <= AGING_MAX:
        return "aging"
    elif age_seconds <= STALE_MAX:
        return "stale"
    return "expired"


class SignalCache:
    """Persistent local cache for signal API responses."""

    def __init__(self):
        _ensure_dirs()

    # ── Save ─────────────────────────────────────────────────────────────

    def save_signals(self, coin: str, data: dict):
        path = SIGNALS_DIR / f"{coin}_latest.json"
        _save_json(path, data)

    def save_strategy(self, coin: str, data: dict):
        path = STRATEGIES_DIR / f"{coin}_strategy.json"
        _save_json(path, data)

    def save_portfolio(self, data: dict):
        path = PORTFOLIO_DIR / "optimization.json"
        _save_json(path, data)

    def save_metadata(self):
        meta = _load_json(META_FILE)
        meta["last_save"] = datetime.now(timezone.utc).isoformat()
        meta["save_count"] = meta.get("save_count", 0) + 1
        _save_json(META_FILE, meta)

    # ── Load ─────────────────────────────────────────────────────────────

    def load_signals(self, coin: str) -> dict:
        path = SIGNALS_DIR / f"{coin}_latest.json"
        if not path.exists():
            return {}
        age = _cache_age(path)
        data = _load_json(path)
        data["_cache_age_seconds"] = age
        data["_cache_freshness"] = freshness(age)
        return data

    def load_strategy(self, coin: str) -> dict:
        path = STRATEGIES_DIR / f"{coin}_strategy.json"
        if not path.exists():
            return {}
        age = _cache_age(path)
        data = _load_json(path)
        data["_cache_age_seconds"] = age
        data["_cache_freshness"] = freshness(age)
        return data

    def load_portfolio(self) -> dict:
        path = PORTFOLIO_DIR / "optimization.json"
        if not path.exists():
            return {}
        age = _cache_age(path)
        data = _load_json(path)
        data["_cache_age_seconds"] = age
        data["_cache_freshness"] = freshness(age)
        return data

    # ── Query ────────────────────────────────────────────────────────────

    def overall_freshness(self) -> str:
        """Worst freshness across all cached data."""
        worst = "fresh"
        order = ["fresh", "aging", "stale", "expired"]
        for d in (SIGNALS_DIR, STRATEGIES_DIR):
            if not d.exists():
                return "expired"
            files = list(d.glob("*.json"))
            if not files:
                return "expired"
            for f in files:
                age = _cache_age(f)
                f_val = freshness(age)
                if order.index(f_val) > order.index(worst):
                    worst = f_val
        return worst

    def cached_coins(self) -> list[str]:
        """List coins with cached signals."""
        if not SIGNALS_DIR.exists():
            return []
        coins = []
        for f in SIGNALS_DIR.glob("*_latest.json"):
            coin = f.stem.replace("_latest", "")
            coins.append(coin)
        return sorted(coins)

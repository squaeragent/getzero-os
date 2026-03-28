"""
Public REST API — unauthenticated endpoints for collective, arena, and agent profiles.
All data sourced from JSON files in scanner/v6/data/.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

DATA_DIR = Path(__file__).parent / "data"

router = APIRouter(tags=["public"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(filename: str) -> list | dict:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


# ── GET /v6/collective ───────────────────────────────────────────────────────

@router.get("/v6/collective")
def get_collective():
    agents = _load_json("collective_agents.json")
    if not agents:
        return {
            "agents_online": 0,
            "regime": {"dominant": "NEUTRAL", "long_pct": 0, "short_pct": 0, "neutral_pct": 0},
            "fear_greed": {"value": 50, "label": "NEUTRAL"},
            "coin_consensus": [],
            "convergence_active": [],
            "season_accuracy": {"convergence_events": 0, "accurate": 0, "accuracy_pct": 0, "false_positives": 0},
        }

    agents_online = len(agents)

    # Aggregate all evaluations across agents per coin
    coin_map: dict[str, dict] = {}
    direction_totals = {"LONG": 0, "SHORT": 0, "NEUTRAL": 0}

    for agent in agents:
        for ev in agent.get("evaluations", []):
            coin = ev["coin"]
            d = ev["direction"]
            direction_totals[d] = direction_totals.get(d, 0) + 1
            if coin not in coin_map:
                coin_map[coin] = {"LONG": 0, "SHORT": 0, "NEUTRAL": 0}
            coin_map[coin][d] = coin_map[coin].get(d, 0) + 1

    total_evals = sum(direction_totals.values()) or 1
    long_pct = round(direction_totals["LONG"] / total_evals * 100)
    short_pct = round(direction_totals["SHORT"] / total_evals * 100)
    neutral_pct = 100 - long_pct - short_pct

    dominant = "SHORT" if short_pct >= long_pct and short_pct >= neutral_pct else (
        "LONG" if long_pct >= neutral_pct else "NEUTRAL"
    )

    # Fear/greed: short_pct maps to fear (high short = fear)
    fg_value = max(0, min(100, 100 - short_pct))
    if fg_value <= 20:
        fg_label = "EXTREME FEAR"
    elif fg_value <= 40:
        fg_label = "FEAR"
    elif fg_value <= 60:
        fg_label = "NEUTRAL"
    elif fg_value <= 80:
        fg_label = "GREED"
    else:
        fg_label = "EXTREME GREED"

    # Per-coin consensus
    coin_consensus = []
    for coin, counts in coin_map.items():
        total = counts["LONG"] + counts["SHORT"] + counts["NEUTRAL"]
        if total == 0:
            continue
        lp = round(counts["LONG"] / total * 100)
        sp = round(counts["SHORT"] / total * 100)
        np_ = 100 - lp - sp
        direction = "SHORT" if sp >= lp and sp >= np_ else ("LONG" if lp >= np_ else "NEUTRAL")
        coin_consensus.append({
            "coin": coin,
            "long_pct": lp,
            "short_pct": sp,
            "neutral_pct": np_,
            "agent_count": total,
            "direction": direction,
        })

    # Sort by conviction strength (abs difference)
    coin_consensus.sort(key=lambda c: abs(c["short_pct"] - c["long_pct"]), reverse=True)
    coin_consensus = coin_consensus[:50]

    # Convergence: coins where dominant direction > 70%
    convergence_active = []
    for c in coin_consensus:
        max_pct = max(c["long_pct"], c["short_pct"])
        if max_pct > 70:
            mean_pct = 50
            std_pct = 15
            sigma = round((max_pct - mean_pct) / std_pct, 1)
            convergence_active.append({
                "coin": c["coin"],
                "direction": c["direction"],
                "pct": max_pct,
                "sigma": sigma,
            })

    # Season accuracy from history
    history = _load_json("collective_history.json")
    total_events = len(history)
    accurate_count = sum(1 for e in history if e.get("accurate"))
    accuracy_pct = round(accurate_count / total_events * 100) if total_events else 0

    return {
        "agents_online": agents_online,
        "regime": {"dominant": dominant, "long_pct": long_pct, "short_pct": short_pct, "neutral_pct": neutral_pct},
        "fear_greed": {"value": fg_value, "label": fg_label},
        "coin_consensus": coin_consensus,
        "convergence_active": convergence_active,
        "season_accuracy": {
            "convergence_events": total_events,
            "accurate": accurate_count,
            "accuracy_pct": accuracy_pct,
            "false_positives": total_events - accurate_count,
        },
    }


# ── GET /v6/collective/history ───────────────────────────────────────────────

@router.get("/v6/collective/history")
def get_collective_history():
    history = _load_json("collective_history.json")
    return {"events": history[:30]}


# ── GET /v6/arena/public ─────────────────────────────────────────────────────

@router.get("/v6/arena/public")
def get_arena_public():
    agents = _load_json("arena_agents.json")
    matches = _load_json("arena_matches.json")

    if not agents:
        return {
            "season": {"number": 1, "day": 0, "started": None},
            "leaderboard": [],
            "live_matches": [],
            "recent_results": [],
            "hall_of_records": {},
        }

    # Sort agents by score descending for leaderboard
    sorted_agents = sorted(agents, key=lambda a: a.get("score", 0), reverse=True)
    leaderboard = []
    for i, a in enumerate(sorted_agents[:20]):
        leaderboard.append({
            "rank": i + 1,
            "handle": a["handle"],
            "pnl": a.get("track_record", {}).get("total_pnl", 0),
            "score": a.get("score", 0),
            "class": a.get("class", "novice"),
            "sessions": a.get("track_record", {}).get("sessions", 0),
            "hl_address": a.get("hl_address", ""),
        })

    # Season info
    season_start = "2026-03-05"
    from datetime import datetime, timezone
    started = datetime(2026, 3, 5, tzinfo=timezone.utc)
    day = (datetime.now(timezone.utc) - started).days

    # Recent results: last 5 completed matches
    recent = sorted(matches, key=lambda m: m.get("date", ""), reverse=True)[:5]

    # Hall of records
    hall = {
        "longest_streak": {"value": 47, "holder": "zero/balanced"},
        "highest_session": {"value": 94.20, "holder": "momentum/apex"},
        "most_immune_saves": {"value": 23, "holder": "regime-hunter"},
        "best_regime_read": {"value": "5/5", "holder": "cold-harbor"},
        "highest_signal": {"value": 9.4, "holder": "night-trader-ai"},
    }

    return {
        "season": {"number": 1, "day": day, "started": season_start},
        "leaderboard": leaderboard,
        "live_matches": [],
        "recent_results": recent,
        "hall_of_records": hall,
    }


# ── GET /v6/arena/match/{match_id} ──────────────────────────────────────────

@router.get("/v6/arena/match/{match_id}")
def get_arena_match(match_id: str):
    matches = _load_json("arena_matches.json")
    for m in matches:
        if m.get("match_id") == match_id:
            return m
    raise HTTPException(status_code=404, detail=f"Match {match_id} not found")


# ── GET /v6/agent/public/{name_path:path} ────────────────────────────────────
# Handles both /v6/agent/public/{name} and /v6/agent/public/{name}/matches
# using a single catch-all to support handles with slashes (e.g. "zero/balanced")

@router.get("/v6/agent/public/{name_path:path}")
def get_agent_public(name_path: str):
    want_matches = name_path.endswith("/matches")
    name = name_path.removesuffix("/matches") if want_matches else name_path

    agents = _load_json("arena_agents.json")
    agent = next((a for a in agents if a.get("handle") == name), None)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {name} not found")

    if want_matches:
        matches = _load_json("arena_matches.json")
        agent_matches = [
            m for m in matches
            if m.get("winner", {}).get("handle") == name or m.get("loser", {}).get("handle") == name
        ]
        return {"matches": agent_matches}

    return agent

# ── Cache endpoints (serve pre-computed data for website SSR) ──────────────

import os
from pathlib import Path as _CachePath

_CACHE_DIR = _CachePath(__file__).parent / "data" / "cache"

@router.get("/v6/cache/{key}")
async def get_cache(key: str):
    """Serve cached engine data. Sub-10ms reads. Used by website SSR."""
    allowed = {"heat", "regime", "approaching", "brief", "health", "sessions", "collective"}
    if key not in allowed:
        return {"error": "unknown cache key"}
    
    cache_file = _CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return {"data": None, "stale": True, "error": "no cache yet"}
    
    try:
        import json as _json
        entry = _json.loads(cache_file.read_text())
        return entry
    except Exception:
        return {"data": None, "stale": True, "error": "cache read failed"}

@router.get("/v6/cache/all")
async def get_all_cache():
    """Serve all cached data in one request. For website SSR batch fetch."""
    import json as _json
    result = {}
    for key in ["heat", "regime", "approaching", "brief", "health", "sessions", "collective"]:
        cache_file = _CACHE_DIR / f"{key}.json"
        try:
            if cache_file.exists():
                entry = _json.loads(cache_file.read_text())
                result[key] = entry
            else:
                result[key] = None
        except Exception:
            result[key] = None
    return result

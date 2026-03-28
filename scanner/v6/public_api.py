"""
Public REST API — unauthenticated endpoints for collective, arena, and agent profiles.
All data sourced from JSON files in scanner/v6/data/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

DATA_DIR = Path(__file__).parent / "data"
SCANNER_DIR = Path(__file__).parent.parent
BUS_DIR = SCANNER_DIR / "bus"
LIVE_DIR = SCANNER_DIR / "data" / "live"
STRATEGIES_DIR = Path(__file__).parent / "strategies"

router = APIRouter(tags=["public"])

# ── Engine stats cache (TTL-based) ─────────────────────────────────────────

_stats_cache: dict = {}
_stats_cache_ts: float = 0.0
_STATS_TTL = 30.0  # seconds


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(filename: str) -> list | dict:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


# ── GET /v6/engine/stats ────────────────────────────────────────────────────

def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    try:
        with open(path) as f:
            for line in f:
                if line.strip():
                    count += 1
    except OSError:
        pass
    return count


def _first_trade_date(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    t = rec.get("entry_time")
                    if t:
                        return datetime.fromisoformat(t)
                    break
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _compute_engine_stats() -> dict:
    global _stats_cache, _stats_cache_ts
    now = time.time()
    if _stats_cache and (now - _stats_cache_ts) < _STATS_TTL:
        return _stats_cache

    # Uptime: days since first trade (or hardcoded start)
    closed_path = LIVE_DIR / "closed.jsonl"
    first_trade = _first_trade_date(closed_path)
    start = first_trade or datetime(2026, 2, 3, tzinfo=timezone.utc)
    uptime_days = max(1, (datetime.now(timezone.utc) - start).days)

    # Lifetime trades from closed.jsonl
    total_trades = _count_jsonl_lines(closed_path)

    # Strategy count from strategies/ directory
    strategies_available = 0
    if STRATEGIES_DIR.exists():
        strategies_available = len([
            f for f in STRATEGIES_DIR.iterdir()
            if f.suffix in (".yaml", ".yml") and not f.name.startswith(".")
        ])

    # Total evaluations: estimate from 50 coins × 1 eval/2min × uptime
    total_evaluations = 50 * uptime_days * 24 * 30  # 50 coins × 30 evals/hr × 24hr × days
    total_rejections = total_evaluations - total_trades
    rejection_rate = round(total_rejections / total_evaluations * 100, 2) if total_evaluations else 0

    # Open positions: read from bus/approved.json (current approved trades)
    open_positions = 0
    approved_path = BUS_DIR / "approved.json"
    if approved_path.exists():
        try:
            data = json.loads(approved_path.read_text())
            if isinstance(data, list):
                open_positions = len(data)
            elif isinstance(data, dict):
                open_positions = len(data.get("positions", data.get("approved", [])))
        except (json.JSONDecodeError, OSError):
            pass

    # Active sessions from cache
    active_sessions = 0
    sessions_cache = DATA_DIR / "cache" / "sessions.json"
    if sessions_cache.exists():
        try:
            entry = json.loads(sessions_cache.read_text())
            sdata = entry.get("data", {})
            active_sessions = 1 if sdata.get("active") else 0
        except (json.JSONDecodeError, OSError):
            pass

    # Agents registered from arena data
    agents_registered = 0
    arena_path = DATA_DIR / "arena_agents.json"
    if arena_path.exists():
        try:
            agents = json.loads(arena_path.read_text())
            agents_registered = len(agents) if isinstance(agents, list) else 0
        except (json.JSONDecodeError, OSError):
            pass

    # Last evaluation timestamp from heartbeat
    last_eval = None
    heartbeat_path = BUS_DIR / "heartbeat.json"
    if heartbeat_path.exists():
        try:
            hb = json.loads(heartbeat_path.read_text())
            ts_values = [v for v in hb.values() if isinstance(v, str)]
            if ts_values:
                last_eval = max(ts_values)
        except (json.JSONDecodeError, OSError):
            pass

    _stats_cache = {
        "coins_watched": 50,
        "strategies_available": strategies_available,
        "decision_layers": 7,
        "immune_system": "continuous",
        "uptime_days": uptime_days,
        "total_evaluations": total_evaluations,
        "total_rejections": total_rejections,
        "rejection_rate_pct": rejection_rate,
        "total_trades_lifetime": total_trades,
        "positions_managed_lifetime": total_trades,
        "positions_open": open_positions,
        "active_sessions": active_sessions,
        "agents_registered": agents_registered,
        "status": "operational",
        "last_evaluation": last_eval,
    }
    _stats_cache_ts = now
    return _stats_cache


@router.get("/v6/engine/stats")
async def engine_stats():
    """Engine capabilities and lifetime aggregate stats."""
    return _compute_engine_stats()


# ── GET /v6/collective ───────────────────────────────────────────────────────

_MILESTONES = [
    {"at": 1, "name": "genesis", "desc": "intelligence running"},
    {"at": 2, "name": "arena", "desc": "competition begins"},
    {"at": 5, "name": "collective", "desc": "consensus activates"},
    {"at": 10, "name": "convergence", "desc": "convergence tracking begins"},
    {"at": 100, "name": "genesis_close", "desc": "genesis program ends"},
]


def _milestones_for(agent_count: int) -> list[dict]:
    return [
        {**m, "status": "active" if agent_count >= m["at"] else "locked"}
        for m in _MILESTONES
    ]


@router.get("/v6/collective")
def get_collective():
    agents = _load_json("collective_agents.json")
    agents_online = len(agents)

    if agents_online < 5:
        return {
            "agents_online": agents_online,
            "stage": "genesis" if agents_online < 2 else "arena" if agents_online < 5 else "collective",
            "coin_consensus": [],
            "convergence_active": [],
            "milestones": _milestones_for(agents_online),
        }

    # --- 5+ agents: full collective data ---

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

    # Season info
    season_start = "2026-02-03"
    started = datetime(2026, 2, 3, tzinfo=timezone.utc)
    day = (datetime.now(timezone.utc) - started).days

    if len(agents) < 2:
        return {
            "season": {"number": 1, "day": day, "started": season_start},
            "status": "waiting",
            "message": "arena opens when 2 operators connect.",
            "leaderboard": [],
            "live_matches": [],
            "recent_results": [],
            "hall_of_records": {},
            "agents_needed": 2,
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
            "sessions": a.get("sessions", 0),
            "hl_address": a.get("hl_address", ""),
        })

    # Recent results: last 5 completed matches
    recent = sorted(matches, key=lambda m: m.get("date", ""), reverse=True)[:5]

    # Hall of records: only populated from real match data
    hall = {}

    return {
        "season": {"number": 1, "day": day, "started": season_start},
        "status": "active",
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

    # Add genesis badge
    from scanner.v6.operator import list_genesis_operators
    genesis_ops = list_genesis_operators()
    genesis_entry = next((g for g in genesis_ops if g.get("agent_handle") == name), None)
    if genesis_entry:
        agent["genesis"] = {"number": genesis_entry["genesis_number"], "is_genesis": True}
    else:
        agent["genesis"] = None

    return agent

# ── GET /v6/genesis ─────────────────────────────────────────────────────────

_GENESIS_MILESTONES = [
    {"at": 1, "name": "first operator", "status": "pending"},
    {"at": 2, "name": "arena unlocks", "status": "pending"},
    {"at": 5, "name": "collective activates", "status": "pending"},
    {"at": 10, "name": "convergence begins", "status": "pending"},
    {"at": 50, "name": "intelligence self-calibrates", "status": "pending"},
    {"at": 100, "name": "genesis closes", "status": "pending"},
]


@router.get("/v6/genesis")
def get_genesis():
    from scanner.v6.operator import list_genesis_operators, _GENESIS_TOTAL

    operators = list_genesis_operators()
    claimed = len(operators)

    milestones = []
    for m in _GENESIS_MILESTONES:
        milestones.append({
            "at": m["at"],
            "name": m["name"],
            "status": "reached" if claimed >= m["at"] else "pending",
        })

    return {
        "program": {
            "total_slots": _GENESIS_TOTAL,
            "claimed": claimed,
            "remaining": _GENESIS_TOTAL - claimed,
            "status": "closed" if claimed >= _GENESIS_TOTAL else "open",
        },
        "operators": [
            {
                "genesis_number": op["genesis_number"],
                "handle": op.get("agent_handle", "unknown"),
                "registered_at": op["registered_at"][:10],
            }
            for op in operators
        ],
        "milestones": milestones,
    }


# ── Cache endpoints (serve pre-computed data for website SSR) ──────────────

import os
from pathlib import Path as _CachePath

_CACHE_DIR = _CachePath(__file__).parent / "data" / "cache"

@router.get("/v6/cache/all")
async def get_all_cache():
    """Serve all cached data in one request. For website SSR batch fetch."""
    import json as _json
    result = {}
    for key in ["heat", "regime", "approaching", "brief", "health", "sessions", "collective", "engine_stats"]:
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

@router.get("/v6/cache/{key}")
async def get_cache(key: str):
    """Serve cached engine data. Sub-10ms reads. Used by website SSR."""
    allowed = {"heat", "regime", "approaching", "brief", "health", "sessions", "collective", "engine_stats"}
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

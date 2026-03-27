"""
Agent registry — auto-registers agents on first MCP connection.
Stores agent profiles in scanner/v6/data/agents.json.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

AGENTS_FILE = Path(__file__).parent / "data" / "agents.json"


@dataclass
class AgentProfile:
    agent_id: str              # UUID assigned on registration
    operator_id: str           # operator who owns the agent
    registered_at: str         # ISO timestamp
    display_name: str          # auto-generated or operator-set
    sessions_completed: int    # total sessions
    total_trades: int          # total trades across all sessions
    win_rate: float            # overall win rate
    best_strategy: str         # most profitable strategy
    current_mode: str          # comfort/sport/track
    current_strategy: str      # active strategy or "idle"
    class_name: str            # novice/apprentice/operator/veteran/elite
    total_score: float         # from progression system
    streak_current: int        # current win streak
    streak_best: int           # all-time best
    milestones_earned: int     # out of 15
    public_url: str            # getzero.dev/agent/{agent_id}
    last_active: str           # ISO timestamp of last session


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, dict] = {}
        self._load()

    def register_or_get(self, operator_id: str) -> AgentProfile:
        """Auto-register on first connection, return existing on subsequent."""
        if operator_id in self._agents:
            return self._update_profile(operator_id)
        return self._create_profile(operator_id)

    def _create_profile(self, operator_id: str) -> AgentProfile:
        """Create new agent profile with UUID."""
        agent_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        profile = AgentProfile(
            agent_id=agent_id,
            operator_id=operator_id,
            registered_at=now,
            display_name=f"agent-{agent_id[:4]}",
            sessions_completed=0,
            total_trades=0,
            win_rate=0.0,
            best_strategy="none",
            current_mode="comfort",
            current_strategy="idle",
            class_name="novice",
            total_score=0.0,
            streak_current=0,
            streak_best=0,
            milestones_earned=0,
            public_url=f"https://getzero.dev/agent/{agent_id}",
            last_active=now,
        )
        self._agents[operator_id] = asdict(profile)
        self._save()
        return profile

    def _update_profile(self, operator_id: str) -> AgentProfile:
        """Refresh profile stats from progression + session data."""
        data = self._agents[operator_id]

        try:
            from scanner.v6.api import ZeroAPI
            from scanner.v6.progression import ProgressionEngine

            api = ZeroAPI()
            pe = ProgressionEngine(api, operator_id)

            # Pull latest stats
            score = pe.get_score()
            streak = pe.get_streak()
            milestones = pe.get_milestones()
            reputation = pe.get_reputation()

            # Session status for current strategy
            status = api.session_status(operator_id)
            current_strategy = "idle"
            current_mode = data.get("current_mode", "comfort")
            if status.get("active"):
                session = status.get("session", {})
                current_strategy = session.get("strategy", "idle")
                current_mode = session.get("mode", current_mode)

            data["sessions_completed"] = reputation.sessions_completed
            data["total_trades"] = reputation.total_trades
            data["win_rate"] = round(
                (reputation.total_trades and reputation.score.performance) or 0.0, 1
            )
            data["best_strategy"] = reputation.favorite_strategy
            data["current_mode"] = current_mode
            data["current_strategy"] = current_strategy
            data["class_name"] = score.class_name
            data["total_score"] = score.total
            data["streak_current"] = streak.current
            data["streak_best"] = streak.best
            data["milestones_earned"] = sum(1 for m in milestones if m.achieved)
            data["last_active"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            # If progression/API unavailable, just update last_active
            data["last_active"] = datetime.now(timezone.utc).isoformat()

        self._agents[operator_id] = data
        self._save()
        return AgentProfile(**data)

    def get_all_agents(self) -> list[dict]:
        """Return all registered agent profiles (for leaderboard/arena)."""
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> dict | None:
        """Get a specific agent by ID."""
        for agent_data in self._agents.values():
            if agent_data.get("agent_id") == agent_id:
                return agent_data
        return None

    def get_agent_count(self) -> int:
        """Total registered agents."""
        return len(self._agents)

    def _load(self):
        if AGENTS_FILE.exists():
            try:
                self._agents = json.loads(AGENTS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self._agents = {}

    def _save(self):
        AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AGENTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._agents, indent=2, default=str))
        tmp.replace(AGENTS_FILE)

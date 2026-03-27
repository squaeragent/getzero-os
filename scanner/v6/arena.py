"""
Arena -- leaderboard and competitive ranking.
All data from local agent_registry + progression.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class LeaderboardEntry:
    rank: int
    agent_id: str
    display_name: str
    class_name: str           # novice -> elite
    total_score: float        # from progression
    win_rate: float
    sessions_completed: int
    streak_current: int
    best_strategy: str
    is_you: bool              # highlight the requesting agent


@dataclass
class ArenaStats:
    total_agents: int
    active_this_week: int     # agents with sessions in last 7 days
    total_sessions: int       # across all agents
    total_trades: int         # across all agents
    avg_win_rate: float       # network average
    top_strategy: str         # most popular strategy this week
    your_rank: int            # requesting agent's rank
    your_percentile: float    # top X%


class Arena:
    """Leaderboard and competitive ranking from local operator data."""

    def __init__(self, api, progression_engine=None):
        self.api = api

    # ---- internal helpers ------------------------------------------------

    def _collect_agents(self) -> list[dict]:
        """Collect all known agents from the AgentRegistry.

        Falls back to progression data for the default operator
        if no registry is available.
        """
        agents: list[dict] = []

        try:
            from scanner.v6.agent_registry import AgentRegistry
            registry = AgentRegistry()
            all_agents = registry.get_all_agents()
            if all_agents:
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                week_ago = now - timedelta(days=7)

                for a in all_agents:
                    # Determine if active this week from last_active
                    active_this_week = False
                    last_active = a.get("last_active", "")
                    if last_active:
                        try:
                            dt = datetime.fromisoformat(last_active)
                            active_this_week = dt >= week_ago
                        except (ValueError, TypeError):
                            pass

                    agents.append({
                        "agent_id": a.get("operator_id", a.get("agent_id", "unknown")),
                        "display_name": a.get("display_name", "unknown"),
                        "class_name": a.get("class_name", "novice"),
                        "total_score": a.get("total_score", 0.0),
                        "win_rate": a.get("win_rate", 0.0),
                        "sessions_completed": a.get("sessions_completed", 0),
                        "streak_current": a.get("streak_current", 0),
                        "best_strategy": a.get("best_strategy", "none"),
                        "total_trades": a.get("total_trades", 0),
                        "active_this_week": active_this_week,
                    })
                return agents
        except Exception:
            pass

        # Fallback: single default operator from progression
        try:
            score = self.api.get_score("op_default")
            rep = self.api.get_reputation("op_default")
            streak = rep.get("streak", {})
            agents.append({
                "agent_id": "op_default",
                "display_name": "Default",
                "class_name": score.get("class_name", "novice"),
                "total_score": score.get("total", 0.0),
                "win_rate": round(rep.get("score", {}).get("performance", 0.0), 1),
                "sessions_completed": rep.get("sessions_completed", 0),
                "streak_current": streak.get("current", 0),
                "best_strategy": rep.get("favorite_strategy", "none"),
                "total_trades": rep.get("total_trades", 0),
                "active_this_week": True,
            })
        except Exception:
            pass

        return agents

    # ---- public API ------------------------------------------------------

    def get_leaderboard(self, limit: int = 10, requester_id: str = "op_default") -> list[LeaderboardEntry]:
        """Top agents by total_score."""
        agents = self._collect_agents()
        agents.sort(key=lambda a: a["total_score"], reverse=True)

        entries: list[LeaderboardEntry] = []
        for i, a in enumerate(agents[:limit]):
            entries.append(LeaderboardEntry(
                rank=i + 1,
                agent_id=a["agent_id"],
                display_name=a["display_name"],
                class_name=a["class_name"],
                total_score=a["total_score"],
                win_rate=a["win_rate"],
                sessions_completed=a["sessions_completed"],
                streak_current=a["streak_current"],
                best_strategy=a["best_strategy"],
                is_you=(a["agent_id"] == requester_id),
            ))

        # If requester not in top N, append them at their actual rank
        requester_in_list = any(e.is_you for e in entries)
        if not requester_in_list and agents:
            for i, a in enumerate(agents):
                if a["agent_id"] == requester_id:
                    entries.append(LeaderboardEntry(
                        rank=i + 1,
                        agent_id=a["agent_id"],
                        display_name=a["display_name"],
                        class_name=a["class_name"],
                        total_score=a["total_score"],
                        win_rate=a["win_rate"],
                        sessions_completed=a["sessions_completed"],
                        streak_current=a["streak_current"],
                        best_strategy=a["best_strategy"],
                        is_you=True,
                    ))
                    break

        return entries

    def get_stats(self, operator_id: str = "op_default") -> ArenaStats:
        """Arena-wide stats + requesting agent's position."""
        agents = self._collect_agents()

        total_agents = len(agents)
        active_this_week = sum(1 for a in agents if a.get("active_this_week"))
        total_sessions = sum(a.get("sessions_completed", 0) for a in agents)
        total_trades = sum(a.get("total_trades", 0) for a in agents)

        # Average win rate across agents with trades
        agents_with_trades = [a for a in agents if a.get("total_trades", 0) > 0]
        avg_wr = (
            sum(a["win_rate"] for a in agents_with_trades) / len(agents_with_trades)
            if agents_with_trades else 0.0
        )

        # Top strategy this week
        strat_counts: dict[str, int] = {}
        for a in agents:
            if a.get("active_this_week"):
                strat = a.get("best_strategy", "none")
                strat_counts[strat] = strat_counts.get(strat, 0) + 1
        top_strategy = max(strat_counts, key=strat_counts.get) if strat_counts else "none"

        # Requesting agent's rank + percentile
        agents.sort(key=lambda a: a["total_score"], reverse=True)
        your_rank = total_agents  # worst case
        for i, a in enumerate(agents):
            if a["agent_id"] == operator_id:
                your_rank = i + 1
                break

        your_percentile = round(
            ((total_agents - your_rank) / total_agents) * 100, 1
        ) if total_agents > 0 else 0.0

        return ArenaStats(
            total_agents=total_agents,
            active_this_week=active_this_week,
            total_sessions=total_sessions,
            total_trades=total_trades,
            avg_win_rate=round(avg_wr, 1),
            top_strategy=top_strategy,
            your_rank=your_rank,
            your_percentile=your_percentile,
        )

    def get_rivalry(self, operator_id: str = "op_default") -> dict:
        """Get the agent ranked just above you for head-to-head comparison."""
        agents = self._collect_agents()
        agents.sort(key=lambda a: a["total_score"], reverse=True)

        your_idx = None
        for i, a in enumerate(agents):
            if a["agent_id"] == operator_id:
                your_idx = i
                break

        if your_idx is None or len(agents) == 0:
            return {"rival": None, "you": None, "message": "no agents found"}

        if your_idx == 0:
            return {"rival": None, "you": agents[0], "message": "you are #1. no rival above you."}

        rival = agents[your_idx - 1]
        you = agents[your_idx]
        gap = round(rival["total_score"] - you["total_score"], 1)

        return {
            "rival": {
                "rank": your_idx,  # rival's rank (1-indexed, above you)
                "agent_id": rival["agent_id"],
                "display_name": rival["display_name"],
                "class_name": rival["class_name"],
                "total_score": rival["total_score"],
                "win_rate": rival["win_rate"],
                "sessions_completed": rival["sessions_completed"],
                "streak_current": rival["streak_current"],
                "best_strategy": rival["best_strategy"],
            },
            "you": {
                "rank": your_idx + 1,
                "agent_id": you["agent_id"],
                "display_name": you["display_name"],
                "class_name": you["class_name"],
                "total_score": you["total_score"],
                "win_rate": you["win_rate"],
                "sessions_completed": you["sessions_completed"],
                "streak_current": you["streak_current"],
                "best_strategy": you["best_strategy"],
            },
            "gap": gap,
            "message": f"beat them by {gap} points to move up.",
        }

    def get_weekly_movers(self) -> list[dict]:
        """Agents who improved the most this week.

        Since we don't store historical score snapshots yet, we
        approximate by ranking active-this-week agents by total_score.
        """
        agents = self._collect_agents()
        active = [a for a in agents if a.get("active_this_week")]
        active.sort(key=lambda a: a["total_score"], reverse=True)
        return [
            {
                "agent_id": a["agent_id"],
                "display_name": a["display_name"],
                "total_score": a["total_score"],
                "sessions_completed": a["sessions_completed"],
            }
            for a in active[:5]
        ]

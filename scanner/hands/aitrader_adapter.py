"""
ZERO OS — AI-Trader HandAdapter.

Auto-publishes trading signals to ai4trade.ai marketplace.
Syncs opens, closes, and position updates.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from scanner.core.interfaces import Decision
from scanner.hands.base import HandAdapter

API_BASE = "https://ai4trade.ai/api"


def _load_token() -> str:
    token = os.environ.get("AI_TRADER_TOKEN")
    if token:
        return token
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("AI_TRADER_TOKEN="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _api_post(endpoint: str, data: dict, token: str) -> dict:
    url = f"{API_BASE}{endpoint}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _decision_to_action(decision: Decision) -> str:
    """Map Decision action to AI-Trader action."""
    if decision.action == "LONG":
        return "buy"
    elif decision.action == "SHORT":
        return "short"
    elif decision.action == "CLOSE":
        # Check metadata for direction to determine cover vs sell
        direction = decision.metadata.get("close_direction", "")
        if direction == "SHORT":
            return "cover"
        return "sell"
    return ""


class AITraderAdapter(HandAdapter):
    """Publishes trading signals to ai4trade.ai marketplace."""

    name = "aitrader"

    def __init__(self, token: str | None = None):
        self._token = token

    def _get_token(self) -> str:
        if not self._token:
            self._token = _load_token()
        return self._token

    def execute(self, decisions: list[Decision]) -> list[dict]:
        token = self._get_token()
        if not token:
            return [{"decision_id": d.id, "status": "skipped", "reason": "no_token"} for d in decisions]

        results = []
        for d in decisions:
            action = _decision_to_action(d)
            if not action:
                results.append({"decision_id": d.id, "status": "skipped", "reason": "unmappable_action"})
                continue

            # Build content string with reasoning
            reasoning = d.reasoning or {}
            content_parts = [
                f"{d.action} {d.coin}",
                f"Confidence: {d.confidence:.0%}",
                f"Regime: {d.regime}",
            ]
            if "adversary_score" in reasoning:
                content_parts.append(f"Adversary survival: {reasoning['adversary_score']:.2f}")
            if "hypothesis" in reasoning:
                content_parts.append(reasoning["hypothesis"])
            content_parts.append(f"Stop: {d.stop_pct:.1f}%")

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

            signal_data = {
                "market": "crypto",
                "action": action,
                "symbol": d.coin,
                "price": d.metadata.get("entry_price", 0),
                "quantity": d.metadata.get("quantity", 1),
                "content": " | ".join(content_parts),
                "executed_at": now,
            }

            try:
                resp = _api_post("/signals/realtime", signal_data, token)
                results.append({
                    "decision_id": d.id,
                    "status": "published",
                    "signal_id": resp.get("signal_id"),
                    "follower_count": resp.get("follower_count", 0),
                })
            except Exception as e:
                results.append({"decision_id": d.id, "status": "error", "error": str(e)})

        return results

    def get_positions(self) -> list[dict]:
        """Get positions from AI-Trader platform."""
        token = self._get_token()
        if not token:
            return []
        try:
            req = urllib.request.Request(
                f"{API_BASE}/positions",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data.get("positions", [])
        except Exception:
            return []

    def health_check(self) -> dict:
        token = self._get_token()
        if not token:
            return {"name": self.name, "status": "no_token"}
        try:
            req = urllib.request.Request(
                f"{API_BASE}/claw/agents/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return {
                "name": self.name,
                "status": "ok",
                "agent_id": data.get("id"),
                "agent_name": data.get("name"),
                "points": data.get("points"),
            }
        except Exception as e:
            return {"name": self.name, "status": "error", "error": str(e)}

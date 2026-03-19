"""
ZERO OS — Telegram HandAdapter.

Sends trade decision alerts via Telegram. Does not execute trades.
Extracted from scanner/agents/execution_agent.py.
"""

from __future__ import annotations

import json
import os
import urllib.request

from scanner.core.interfaces import Decision
from scanner.hands.base import HandAdapter

TELEGRAM_CHAT_ID = "133058580"


def _get_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _send_message(token: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=10)


def _format_decision(d: Decision) -> str:
    emoji = {"LONG": "\U0001f7e2", "SHORT": "\U0001f534", "CLOSE": "\U0001f7e1", "WAIT": "\u23f8"}.get(d.action, "\u2753")
    lines = [
        f"{emoji} <b>{d.coin} {d.action}</b>",
        f"Confidence: {d.confidence:.0%} | Size: {d.size_pct:.0%}",
        f"Stop: {d.stop_pct:.1f}% | Regime: {d.regime}",
    ]
    if d.reasoning:
        for key in ("hypothesis", "adversary_score"):
            if key in d.reasoning:
                lines.append(f"{key}: {d.reasoning[key]}")
    return "\n".join(lines)


class TelegramAdapter(HandAdapter):
    """Sends formatted trade alerts via Telegram."""

    name = "telegram"

    def __init__(self, chat_id: str | None = None):
        self._chat_id = chat_id or TELEGRAM_CHAT_ID
        self._token: str | None = None

    def _get_token(self) -> str:
        if self._token is None:
            self._token = _get_bot_token()
        return self._token

    def execute(self, decisions: list[Decision]) -> list[dict]:
        token = self._get_token()
        if not token:
            return [{"decision_id": d.id, "status": "skipped", "reason": "no_token"} for d in decisions]

        results = []
        for d in decisions:
            try:
                text = _format_decision(d)
                _send_message(token, text)
                results.append({"decision_id": d.id, "status": "sent"})
            except Exception as e:
                results.append({"decision_id": d.id, "status": "error", "error": str(e)})
        return results

    def get_positions(self) -> list[dict]:
        return []

    def health_check(self) -> dict:
        token = self._get_token()
        if not token:
            return {"name": self.name, "status": "no_token"}
        return {"name": self.name, "status": "ok"}

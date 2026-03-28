"""
ZERO OS — Supabase Client
Minimal httpx-based client for Supabase REST API (PostgREST).
No supabase-py dependency. All calls are fire-and-forget safe.

Credentials loaded from ~/getzero-os/.env:
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY=eyJ...
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("zero.supabase")

# ─── LAZY IMPORTS ─────────────────────────────────────────────────────────────
# httpx is imported lazily so Supabase being absent never crashes the system.

def _get_httpx():
    try:
        import httpx
        return httpx
    except ImportError:
        log.warning("httpx not installed — Supabase client disabled. Run: pip install httpx")
        return None


# ─── CONFIG ───────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    env_path = Path("~/getzero-os/.env").expanduser()
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class SupabaseClient:
    """Thin PostgREST wrapper. Never raises — all errors are logged and swallowed."""

    def __init__(self):
        env = _load_env()
        self.url = env.get("SUPABASE_URL", "").rstrip("/")
        self.key = env.get("SUPABASE_SERVICE_KEY", "")
        self._enabled = bool(self.url and self.key)
        if not self._enabled:
            log.warning(
                "SUPABASE_URL or SUPABASE_SERVICE_KEY missing from ~/getzero-os/.env — "
                "Supabase persistence disabled."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def _post(self, table: str, payload: dict | list, *, upsert_on: str | None = None) -> bool:
        """POST to PostgREST table. Returns True on success."""
        if not self._enabled:
            return False
        httpx = _get_httpx()
        if httpx is None:
            return False
        try:
            headers = dict(self._headers)
            if upsert_on:
                headers["Prefer"] = f"resolution=merge-duplicates,return=minimal"
                headers["on_conflict"] = upsert_on
            resp = httpx.post(
                f"{self.url}/rest/v1/{table}",
                headers=headers,
                content=json.dumps(payload),
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("Supabase %s POST failed: %s %s", table, resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("Supabase %s POST error: %s", table, exc)
            return False

    def _patch(self, table: str, filters: dict[str, str], payload: dict) -> bool:
        """PATCH (update) rows matching filters."""
        if not self._enabled:
            return False
        httpx = _get_httpx()
        if httpx is None:
            return False
        try:
            params = {k: f"eq.{v}" for k, v in filters.items()}
            resp = httpx.patch(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers,
                params=params,
                content=json.dumps(payload),
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("Supabase %s PATCH failed: %s %s", table, resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("Supabase %s PATCH error: %s", table, exc)
            return False

    def _delete(self, table: str, filters: dict[str, str]) -> bool:
        """DELETE rows matching filters."""
        if not self._enabled:
            return False
        httpx = _get_httpx()
        if httpx is None:
            return False
        try:
            params = {k: f"eq.{v}" for k, v in filters.items()}
            resp = httpx.delete(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers,
                params=params,
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("Supabase %s DELETE failed: %s %s", table, resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("Supabase %s DELETE error: %s", table, exc)
            return False

    def _get(self, table: str, params: dict | None = None, limit: int = 100) -> list[dict]:
        """GET rows from table. Returns [] on failure."""
        if not self._enabled:
            return []
        httpx = _get_httpx()
        if httpx is None:
            return []
        try:
            headers = dict(self._headers)
            headers["Prefer"] = "return=representation"
            q: dict[str, Any] = {"limit": limit}
            if params:
                q.update(params)
            resp = httpx.get(
                f"{self.url}/rest/v1/{table}",
                headers=headers,
                params=q,
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("Supabase %s GET failed: %s %s", table, resp.status_code, resp.text[:200])
                return []
            return resp.json()
        except Exception as exc:
            log.warning("Supabase %s GET error: %s", table, exc)
            return []

    # ─── WRITES ───────────────────────────────────────────────────────────────

    def insert_trade(self, closed: dict) -> bool:
        """Insert a closed trade record. Maps from execution_agent's closed dict."""
        try:
            payload = {
                "coin": closed["coin"],
                "direction": closed["direction"],
                "entry_price": closed.get("entry_price"),
                "exit_price": closed.get("exit_price"),
                "size_usd": closed.get("size_usd"),
                "pnl_dollars": closed.get("pnl_usd") or closed.get("pnl_after_fees"),
                "pnl_pct": closed.get("pnl_pct"),
                "entry_time": closed.get("entry_time"),
                "exit_time": closed.get("exit_time") or datetime.now(timezone.utc).isoformat(),
                "exit_reason": closed.get("exit_reason"),
                "signal": closed.get("signal"),
                "sharpe": closed.get("sharpe"),
                "win_rate": closed.get("win_rate"),
                "strategy_version": closed.get("strategy_version", 5),
                "adversary_verdict": closed.get("adversary_verdict"),
                "survival_score": closed.get("survival_score"),
                "regime": closed.get("regime"),
                "session": closed.get("session"),
                "hl_order_id": closed.get("hl_order_id"),
                "stop_loss_pct": closed.get("stop_loss_pct"),
                "fees_usd": closed.get("fees_usd", 0),
                "metadata": closed.get("exec_quality") or {},
            }
            return self._post("trades", payload)
        except Exception as exc:
            log.warning("insert_trade error: %s", exc)
            return False

    def upsert_position(self, pos: dict) -> bool:
        """Upsert an open position. Uses coin as unique key."""
        try:
            payload = {
                "coin": pos["coin"],
                "direction": pos["direction"],
                "entry_price": pos.get("entry_price"),
                "size_usd": pos.get("size_usd"),
                "entry_time": pos.get("entry_time"),
                "signal": pos.get("signal"),
                "sharpe": pos.get("sharpe"),
                "win_rate": pos.get("win_rate"),
                "stop_loss_pct": pos.get("stop_loss_pct"),
                "trailing_stop_price": pos.get("stop_loss"),
                "peak_price": pos.get("peak_pnl_pct"),
                "adversary_verdict": pos.get("adversary_verdict"),
                "survival_score": pos.get("survival_score"),
                "exit_expression": pos.get("exit_expression"),
                "max_hold_hours": pos.get("max_hold_hours"),
                "hl_order_id": pos.get("hl_order_id"),
                "metadata": pos.get("exec_quality") or {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            headers_override = dict(self._headers)
            headers_override["Prefer"] = "resolution=merge-duplicates,return=minimal"
            # Use upsert via on_conflict
            httpx = _get_httpx()
            if not httpx or not self._enabled:
                return False
            resp = httpx.post(
                f"{self.url}/rest/v1/positions",
                headers=headers_override,
                params={"on_conflict": "coin"},
                content=json.dumps(payload),
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("upsert_position failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("upsert_position error: %s", exc)
            return False

    def delete_position(self, coin: str) -> bool:
        """Delete a closed position by coin."""
        try:
            return self._delete("positions", {"coin": coin})
        except Exception as exc:
            log.warning("delete_position error: %s", exc)
            return False

    def insert_equity_snapshot(self, snapshot: dict) -> bool:
        """Insert an equity curve data point."""
        try:
            payload = {
                "equity_usd": snapshot.get("account_value", None) if snapshot.get("account_value") is not None else snapshot.get("equity_usd", snapshot.get("equity", 0)),
                "unrealized_pnl": snapshot.get("unrealized_pnl", 0),
                "realized_pnl": snapshot.get("realized_pnl_today") or snapshot.get("realized_pnl", 0),
                "open_positions": snapshot.get("open_positions", 0),
                "strategy_version": snapshot.get("strategy_version", 5),
                "recorded_at": snapshot.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
            }
            return self._post("equity_snapshots", payload)
        except Exception as exc:
            log.warning("insert_equity_snapshot error: %s", exc)
            return False

    def insert_signal(self, signal: dict) -> bool:
        """Insert an adversary signal evaluation record."""
        try:
            payload = {
                "coin": signal["coin"],
                "direction": signal["direction"],
                "signal_name": signal.get("signal_name") or signal.get("signal"),
                "sharpe": signal.get("sharpe"),
                "win_rate": signal.get("win_rate"),
                "adversary_verdict": signal.get("adversary_verdict"),
                "survival_score": signal.get("survival_score"),
                "attacks": signal.get("attacks", []),
                "regime": signal.get("regime"),
                "was_approved": signal.get("was_approved", False),
                "was_traded": signal.get("was_traded", False),
                "evaluated_at": signal.get("evaluated_at") or datetime.now(timezone.utc).isoformat(),
            }
            return self._post("signals", payload)
        except Exception as exc:
            log.warning("insert_signal error: %s", exc)
            return False

    def insert_counterfactual(self, episode: dict) -> bool:
        """Upsert a counterfactual episode by episode_id."""
        try:
            payload = {
                "episode_id": episode["episode_id"],
                "coin": episode["coin"],
                "direction": episode["direction"],
                "adversary_correct": episode.get("adversary_correct"),
                "resolution": episode.get("resolution"),
                "would_have_won": episode.get("would_have_won"),
                "pnl_at_hold_pct": episode.get("pnl_at_hold_pct"),
                "max_hold_hours": episode.get("max_hold_hours"),
                "killing_attacks": episode.get("killing_attacks", []),
                "dominant_attack": episode.get("dominant_attack"),
                "kill_time": episode.get("kill_time"),
                "resolved_at": episode.get("resolved_at") or datetime.now(timezone.utc).isoformat(),
            }
            headers_override = dict(self._headers)
            headers_override["Prefer"] = "resolution=merge-duplicates,return=minimal"
            httpx = _get_httpx()
            if not httpx or not self._enabled:
                return False
            resp = httpx.post(
                f"{self.url}/rest/v1/counterfactual_log",
                headers=headers_override,
                params={"on_conflict": "episode_id"},
                content=json.dumps(payload),
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("insert_counterfactual failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("insert_counterfactual error: %s", exc)
            return False

    def update_heartbeat(self, agent: str, ts: str | None = None) -> bool:
        """Upsert agent heartbeat timestamp."""
        try:
            now = ts or datetime.now(timezone.utc).isoformat()
            payload = {
                "agent": agent,
                "last_heartbeat": now,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            headers_override = dict(self._headers)
            headers_override["Prefer"] = "resolution=merge-duplicates,return=minimal"
            httpx = _get_httpx()
            if not httpx or not self._enabled:
                return False
            resp = httpx.post(
                f"{self.url}/rest/v1/agent_heartbeats",
                headers=headers_override,
                params={"on_conflict": "agent"},
                content=json.dumps(payload),
                timeout=8.0,
            )
            if resp.status_code >= 400:
                log.warning("update_heartbeat failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("update_heartbeat error: %s", exc)
            return False

    # ─── READS ────────────────────────────────────────────────────────────────

    def get_recent_trades(self, limit: int = 50, coin: str | None = None) -> list[dict]:
        """Fetch recent closed trades, newest first."""
        try:
            params: dict[str, Any] = {"order": "entry_time.desc"}
            if coin:
                params["coin"] = f"eq.{coin}"
            return self._get("trades", params, limit=limit)
        except Exception as exc:
            log.warning("get_recent_trades error: %s", exc)
            return []

    def get_open_positions(self) -> list[dict]:
        """Fetch all open positions."""
        try:
            return self._get("positions", {}, limit=50)
        except Exception as exc:
            log.warning("get_open_positions error: %s", exc)
            return []

    def get_equity_history(self, limit: int = 200) -> list[dict]:
        """Fetch equity curve snapshots, newest first."""
        try:
            params: dict[str, Any] = {"order": "recorded_at.desc"}
            return self._get("equity_snapshots", params, limit=limit)
        except Exception as exc:
            log.warning("get_equity_history error: %s", exc)
            return []

    def health_check(self) -> dict:
        """Ping Supabase and return status dict."""
        if not self._enabled:
            return {"status": "disabled", "reason": "missing credentials"}
        httpx = _get_httpx()
        if httpx is None:
            return {"status": "disabled", "reason": "httpx not installed"}
        try:
            resp = httpx.get(
                f"{self.url}/rest/v1/agent_heartbeats",
                headers=self._headers,
                params={"limit": 1},
                timeout=5.0,
            )
            if resp.status_code < 400:
                return {"status": "ok", "url": self.url}
            return {"status": "error", "code": resp.status_code, "body": resp.text[:100]}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}


# ─── SINGLETON ────────────────────────────────────────────────────────────────
# Import this in agents: from scanner.supabase.client import supabase
supabase = SupabaseClient()

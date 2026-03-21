#!/usr/bin/env python3
"""
Telemetry Client — Pushes agent data to getzero.dev dashboard API (opt-in).

This module is TELEMETRY ONLY. It never affects trading decisions.
If telemetry is disabled or the API is down, the agent trades normally.

Usage:
  from telemetry_client import telemetry
  telemetry.push_equity(749.15, unrealized_pnl=-0.56, positions_count=3)
  telemetry.push_decision("BTC", "long", "entered", "passed all checks", sharpe=3.14)
  telemetry.push_trade_open(position_dict)
  telemetry.push_trade_close("BTC", "long", 87500.0, 87800.0, 0.51, 0.12, "signal_exit")
  telemetry.push_health("evaluator", "healthy", {"ws_connected": True})

Configuration:
  Token loaded from ~/.zeroos/config.yaml  telemetry.token
  If no token is set, telemetry is disabled (opt-in).
"""

import json
import logging
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("telemetry_client")

# ─── Rate limiting ────────────────────────────────────────────
_last_equity_ts: float = 0
EQUITY_INTERVAL = 60  # max 1 equity push per 60s


def _load_token() -> str | None:
    """Load telemetry token from ~/.zeroos/config.yaml."""
    config_path = Path.home() / ".zeroos" / "config.yaml"
    if not config_path.exists():
        return None
    try:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("telemetry") or {}).get("token")
    except Exception:
        # yaml not installed or config malformed — skip
        try:
            # Fallback: simple text parse for token line
            for line in config_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("token:"):
                    val = line.split(":", 1)[1].strip().strip("'\"")
                    if val:
                        return val
        except Exception:
            pass
    return None


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class TelemetryClient:
    """Non-blocking HTTP client that pushes telemetry to getzero.dev."""

    def __init__(self, dashboard_url: str = "https://getzero.dev", token: str = None):
        self.url = dashboard_url.rstrip("/")
        self.token = token if token is not None else _load_token()
        self.enabled = self.token is not None

    def _post(self, endpoint: str, payload: dict):
        """POST JSON to the dashboard API. Runs in a daemon thread. Never raises."""
        if not self.enabled:
            return

        def _do():
            try:
                url = f"{self.url}/api/telemetry/{endpoint}"
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.token}",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                log.debug(f"Telemetry push to {endpoint} failed: {e}")

        threading.Thread(target=_do, daemon=True).start()

    # ─── Public API ───────────────────────────────────────────

    def push_equity(self, equity: float, unrealized_pnl: float = 0,
                    positions_count: int = 0):
        """Push equity snapshot. Rate-limited to once per 60s."""
        global _last_equity_ts
        now = time.time()
        if now - _last_equity_ts < EQUITY_INTERVAL:
            return
        _last_equity_ts = now

        self._post("equity", {
            "timestamp": _now_iso(),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "positions_count": positions_count,
        })

    def push_decision(self, coin: str, direction: str, decision: str,
                      reason: str, sharpe: float = None):
        """Push a trading decision."""
        payload = {
            "timestamp": _now_iso(),
            "coin": coin,
            "direction": direction,
            "decision": decision,
            "reason": reason[:200] if reason else None,
        }
        if sharpe is not None:
            payload["sharpe"] = round(sharpe, 4)
        self._post("decision", payload)

    def push_trade_open(self, position: dict):
        """Push a trade open event."""
        self._post("trade", {
            "event": "open",
            "timestamp": _now_iso(),
            "coin": position.get("coin"),
            "direction": position.get("direction", "long"),
            "entry_price": position.get("entry_price"),
            "size_usd": position.get("size_usd"),
        })

    def push_trade_close(self, coin: str, direction: str, entry_price: float,
                         exit_price: float, pnl: float, fees: float = 0,
                         reason: str = None):
        """Push a trade close event."""
        self._post("trade", {
            "event": "close",
            "timestamp": _now_iso(),
            "coin": coin,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 4) if pnl else 0,
            "fees": round(fees, 6) if fees else 0,
            "reason": reason[:100] if reason else None,
        })

    def push_health(self, component: str, status: str = "healthy",
                    details: dict = None):
        """Push a health check event."""
        self._post("health", {
            "timestamp": _now_iso(),
            "component": component,
            "status": status,
            "details": details,
        })


# ─── Singleton ────────────────────────────────────────────────
telemetry = TelemetryClient()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s")
    print(f"Telemetry enabled: {telemetry.enabled}")
    print(f"Dashboard URL: {telemetry.url}")
    if telemetry.enabled:
        print("Sending test health ping...")
        telemetry.push_health("test", "healthy", {"test": True})
        time.sleep(2)
        print("Done.")
    else:
        print("No token found in ~/.zeroos/config.yaml — telemetry disabled.")

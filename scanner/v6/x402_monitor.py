#!/usr/bin/env python3
"""
x402 Balance Monitor — tracks API cost and estimates days remaining.

Monitors the x402 USDC wallet balance and daily spend rate.
Returns status: healthy / warning / low / depleted.

Thresholds:
  HEALTHY:  7+ days remaining
  WARNING:  3-7 days remaining → log it
  LOW:      1-3 days remaining → Telegram alert + reduce API frequency
  DEPLETED: 0 days remaining  → switch to BASIC mode
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

MONITOR_DIR = Path("~/.zeroos/monitor").expanduser()
COST_HISTORY_FILE = MONITOR_DIR / "cost_history.json"
BALANCE_HISTORY_FILE = MONITOR_DIR / "balance_history.json"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [X402MON] {msg}", flush=True)


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


class X402Monitor:
    """Monitors x402 wallet balance and estimates runway."""

    def __init__(self):
        MONITOR_DIR.mkdir(parents=True, exist_ok=True)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from scanner.v6.x402 import get_client
                self._client = get_client()
            except Exception:
                pass
        return self._client

    def record_balance(self) -> float:
        """Query current balance and record it. Returns balance or -1."""
        client = self._get_client()
        if not client:
            return -1.0

        balance = client.get_usdc_balance()
        if balance < 0:
            return balance

        history = _load_json(BALANCE_HISTORY_FILE, {"records": []})
        records = history.get("records", [])
        records.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "balance": balance,
        })
        # Keep last 30 days (720 records at 1h intervals)
        records = records[-720:]
        history["records"] = records
        _save_json(BALANCE_HISTORY_FILE, history)

        return balance

    def estimate_daily_cost(self) -> float:
        """Estimate daily API cost from balance history."""
        history = _load_json(BALANCE_HISTORY_FILE, {"records": []})
        records = history.get("records", [])
        if len(records) < 2:
            return 0.0

        # Use last 7 days of data
        now = time.time()
        week_ago = now - 7 * 86400
        recent = [r for r in records if _parse_ts(r.get("ts", "")) > week_ago]
        if len(recent) < 2:
            recent = records[-10:]  # fallback to last 10 records

        if len(recent) < 2:
            return 0.0

        first_bal = recent[0]["balance"]
        last_bal = recent[-1]["balance"]
        first_ts = _parse_ts(recent[0]["ts"])
        last_ts = _parse_ts(recent[-1]["ts"])

        elapsed_days = (last_ts - first_ts) / 86400
        if elapsed_days <= 0:
            return 0.0

        spent = first_bal - last_bal
        if spent <= 0:
            return 0.0  # balance increased (topped up)

        return spent / elapsed_days

    def estimate_days_remaining(self) -> float:
        """Estimate how many days of API usage remain."""
        client = self._get_client()
        if not client:
            return -1.0

        balance = client.get_usdc_balance()
        if balance <= 0:
            return 0.0

        daily_cost = self.estimate_daily_cost()
        if daily_cost <= 0:
            return 999.0  # unknown cost, assume healthy

        return balance / daily_cost

    def get_status(self) -> dict:
        """Get current x402 status.

        Returns:
            dict with keys: status, balance, daily_cost, days_remaining, message
        """
        client = self._get_client()
        if not client:
            return {
                "status": "unavailable",
                "balance": -1,
                "daily_cost": 0,
                "days_remaining": -1,
                "message": "x402 client not available",
            }

        balance = self.record_balance()
        daily_cost = self.estimate_daily_cost()
        days_remaining = balance / daily_cost if daily_cost > 0 else 999.0

        if balance <= 0:
            status = "depleted"
            message = "x402 wallet empty — switching to BASIC mode"
        elif days_remaining <= 1:
            status = "depleted"
            message = f"<1 day remaining (${balance:.2f} left, ${daily_cost:.2f}/day)"
        elif days_remaining <= 3:
            status = "low"
            message = f"{days_remaining:.1f} days remaining — reduce API frequency"
        elif days_remaining <= 7:
            status = "warning"
            message = f"{days_remaining:.1f} days remaining — consider topping up"
        else:
            status = "healthy"
            message = f"{days_remaining:.0f} days remaining"

        return {
            "status": status,
            "balance": round(balance, 4),
            "daily_cost": round(daily_cost, 4),
            "days_remaining": round(days_remaining, 1),
            "message": message,
        }

    def should_reduce_frequency(self) -> bool:
        """True if API call frequency should be reduced (LOW status)."""
        status = self.get_status()
        return status["status"] in ("low", "depleted")

    def is_depleted(self) -> bool:
        """True if wallet is depleted — switch to BASIC mode."""
        status = self.get_status()
        return status["status"] == "depleted"


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0

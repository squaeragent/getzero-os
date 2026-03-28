"""
Trade logging — Telegram alerts, rejection/near-miss/decision logs.

Depends on: ctrl_util (log, now_iso, append_jsonl), config.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import scanner.v6.config as _cfg
from scanner.v6.ctrl_util import log, now_iso, append_jsonl

# Module-level constants (for backward-compat imports from controller.py)
REJECTION_LOG_FILE = _cfg.BUS_DIR / "rejections.jsonl"
NEAR_MISS_LOG_FILE = _cfg.BUS_DIR / "near_misses.jsonl"
DECISION_LOG_FILE  = _cfg.BUS_DIR / "decisions.jsonl"

# Telegram alert dedup
_alert_history: dict[str, float] = {}


def send_alert(message: str) -> None:
    """Send Telegram message. Never raises. Suppressed in paper mode. Rate-limited."""
    import os
    if os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes"):
        log(f"[PAPER] Alert suppressed: {message[:80]}")
        return
    alert_key = message[:60]
    now = time.time()
    if alert_key in _alert_history and (now - _alert_history[alert_key]) < _cfg.ALERT_COOLDOWN:
        return
    _alert_history[alert_key] = now
    try:
        token = _cfg.get_env(_cfg.TELEGRAM_BOT_TOKEN_ENV)
        if not token:
            return
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id":                  _cfg.TELEGRAM_CHAT_ID,
            "text":                     message,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError) as e:
        log(f"WARN: Telegram failed: {e}")


def log_rejection(coin: str, direction: str, reason: str,
                  gate: str = "controller") -> None:
    try:
        entry = {
            "ts": now_iso(), "coin": coin, "dir": direction,
            "reason": reason, "gate": gate,
        }
        path = REJECTION_LOG_FILE
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARN: failed to write rejection log: {e}")


def log_near_miss(entry: dict, reason: str, strategy_name: str) -> None:
    """Log signals that almost passed risk gates — useful for tuning."""
    try:
        record = {
            "ts":           now_iso(),
            "coin":         entry.get("coin"),
            "direction":    entry.get("direction"),
            "signal_name":  entry.get("signal_name"),
            "consensus":    entry.get("consensus_layers"),
            "failed_gate":  reason,
            "strategy":     strategy_name,
            "near_miss":    True,
        }
        path = NEAR_MISS_LOG_FILE
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARN: failed to write near miss log: {e}")


def log_decision(
    coin: str,
    strategy: str,
    layers_passed: int,
    verdict: str,
    price: float,
    reason: str,
    session_id: str = "",
) -> None:
    """Decision log — every evaluation verdict (approved, rejected, near_miss)."""
    try:
        record = {
            "ts":           now_iso(),
            "coin":         coin,
            "strategy":     strategy,
            "layers_passed": layers_passed,
            "verdict":      verdict,
            "price":        price,
            "reason":       reason,
            "session_id":   session_id,
        }
        append_jsonl(DECISION_LOG_FILE, record)
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARN: failed to write decision log: {e}")

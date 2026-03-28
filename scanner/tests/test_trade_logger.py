"""Tests for scanner/v6/trade_logger.py — alerts, rejection/near-miss/decision logs."""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from scanner.v6.trade_logger import (
    send_alert,
    log_rejection,
    log_near_miss,
    log_decision,
    _alert_history,
    REJECTION_LOG_FILE,
    NEAR_MISS_LOG_FILE,
    DECISION_LOG_FILE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SEND_ALERT
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendAlert:
    """Telegram alert sending."""

    def setup_method(self):
        _alert_history.clear()

    def test_paper_mode_suppresses_alert(self):
        """Alert is suppressed in PAPER_MODE."""
        with patch.dict("os.environ", {"PAPER_MODE": "true"}):
            send_alert("test alert")  # should not raise or send

    def test_no_token_no_send(self):
        """No Telegram token → no network call."""
        with patch.dict("os.environ", {}, clear=True), \
             patch("scanner.v6.trade_logger._cfg.get_env", return_value=""), \
             patch("scanner.v6.trade_logger.urllib.request.urlopen") as mock_url:
            send_alert("test")
            mock_url.assert_not_called()

    def test_dedup_within_cooldown(self):
        """Same message within cooldown is suppressed."""
        with patch.dict("os.environ", {}, clear=True), \
             patch("scanner.v6.trade_logger._cfg.get_env", return_value="fake_token"), \
             patch("scanner.v6.trade_logger._cfg.TELEGRAM_CHAT_ID", "123"), \
             patch("scanner.v6.trade_logger._cfg.ALERT_COOLDOWN", 300), \
             patch("scanner.v6.trade_logger.urllib.request.urlopen") as mock_url:
            send_alert("duplicate message here")
            send_alert("duplicate message here")
            # Only first call should go through
            assert mock_url.call_count == 1

    def test_different_messages_both_sent(self):
        """Different messages are not deduped."""
        with patch.dict("os.environ", {}, clear=True), \
             patch("scanner.v6.trade_logger._cfg.get_env", return_value="fake_token"), \
             patch("scanner.v6.trade_logger._cfg.TELEGRAM_CHAT_ID", "123"), \
             patch("scanner.v6.trade_logger._cfg.ALERT_COOLDOWN", 300), \
             patch("scanner.v6.trade_logger.urllib.request.urlopen") as mock_url:
            send_alert("message A " + "x" * 60)
            send_alert("message B " + "y" * 60)
            assert mock_url.call_count == 2

    def test_network_error_does_not_raise(self):
        """Network error is swallowed, not raised."""
        import urllib.error
        with patch.dict("os.environ", {}, clear=True), \
             patch("scanner.v6.trade_logger._cfg.get_env", return_value="fake_token"), \
             patch("scanner.v6.trade_logger._cfg.TELEGRAM_CHAT_ID", "123"), \
             patch("scanner.v6.trade_logger._cfg.ALERT_COOLDOWN", 300), \
             patch("scanner.v6.trade_logger.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timeout")):
            send_alert("test")  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# LOG_REJECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogRejection:
    """Rejection log writing."""

    def test_writes_rejection(self, tmp_path):
        """log_rejection writes a JSONL entry."""
        log_file = tmp_path / "rejections.jsonl"
        with patch("scanner.v6.trade_logger.REJECTION_LOG_FILE", log_file):
            log_rejection("BTC", "LONG", "low sharpe", gate="risk")
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["coin"] == "BTC"
        assert record["dir"] == "LONG"
        assert record["reason"] == "low sharpe"
        assert record["gate"] == "risk"
        assert "ts" in record

    def test_default_gate(self, tmp_path):
        """Default gate is 'controller'."""
        log_file = tmp_path / "rejections.jsonl"
        with patch("scanner.v6.trade_logger.REJECTION_LOG_FILE", log_file):
            log_rejection("ETH", "SHORT", "cooldown")
        record = json.loads(log_file.read_text().strip())
        assert record["gate"] == "controller"

    def test_appends_multiple(self, tmp_path):
        """Multiple rejections append as separate lines."""
        log_file = tmp_path / "rejections.jsonl"
        with patch("scanner.v6.trade_logger.REJECTION_LOG_FILE", log_file):
            log_rejection("BTC", "LONG", "reason1")
            log_rejection("ETH", "SHORT", "reason2")
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# LOG_NEAR_MISS
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogNearMiss:
    """Near-miss log writing."""

    def test_writes_near_miss(self, tmp_path):
        """log_near_miss writes a JSONL entry with expected fields."""
        log_file = tmp_path / "near_misses.jsonl"
        entry = {
            "coin": "SOL",
            "direction": "LONG",
            "signal_name": "momentum_breakout",
            "consensus_layers": 3,
        }
        with patch("scanner.v6.trade_logger.NEAR_MISS_LOG_FILE", log_file):
            log_near_miss(entry, "volume_too_low", "aggressive")
        record = json.loads(log_file.read_text().strip())
        assert record["coin"] == "SOL"
        assert record["direction"] == "LONG"
        assert record["failed_gate"] == "volume_too_low"
        assert record["strategy"] == "aggressive"
        assert record["near_miss"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# LOG_DECISION
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogDecision:
    """Decision log writing."""

    def test_writes_decision(self, tmp_path):
        """log_decision writes a JSONL entry with all fields."""
        log_file = tmp_path / "decisions.jsonl"
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", log_file):
            log_decision(
                coin="BTC",
                strategy="momentum",
                layers_passed=4,
                verdict="approved",
                price=65000.0,
                reason="all gates passed",
                session_id="sess_123",
            )
        record = json.loads(log_file.read_text().strip())
        assert record["coin"] == "BTC"
        assert record["strategy"] == "momentum"
        assert record["layers_passed"] == 4
        assert record["verdict"] == "approved"
        assert record["price"] == 65000.0
        assert record["reason"] == "all gates passed"
        assert record["session_id"] == "sess_123"
        assert "ts" in record

    def test_session_id_optional(self, tmp_path):
        """session_id defaults to empty string."""
        log_file = tmp_path / "decisions.jsonl"
        with patch("scanner.v6.trade_logger.DECISION_LOG_FILE", log_file):
            log_decision("ETH", "conservative", 2, "rejected", 3500.0, "low sharpe")
        record = json.loads(log_file.read_text().strip())
        assert record["session_id"] == ""

"""Tests for scanner/v6/ctrl_util.py — logging, time, JSON I/O, heartbeat."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from scanner.v6.ctrl_util import (
    log,
    now_iso,
    load_json,
    save_json_atomic,
    append_jsonl,
    update_heartbeat,
)


# ═══════════════════════════════════════════════════════════════════════════════
# LOG
# ═══════════════════════════════════════════════════════════════════════════════

class TestLog:
    """Controller log output."""

    def test_log_outputs_message(self, capsys):
        """log() prints to stdout with CTRL prefix."""
        log("test message")
        captured = capsys.readouterr()
        assert "[CTRL] test message" in captured.out

    def test_log_has_timestamp(self, capsys):
        """log() includes UTC timestamp."""
        log("hello")
        captured = capsys.readouterr()
        # Pattern: [YYYY-MM-DD HH:MM:SS UTC]
        assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]", captured.out)

    def test_log_flushes(self, capsys):
        """log() output is immediately available (flush=True)."""
        log("flush test")
        captured = capsys.readouterr()
        assert "flush test" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# NOW_ISO
# ═══════════════════════════════════════════════════════════════════════════════

class TestNowIso:
    """ISO 8601 timestamp generation."""

    def test_returns_string(self):
        assert isinstance(now_iso(), str)

    def test_iso_format(self):
        """Timestamp matches ISO 8601 pattern."""
        ts = now_iso()
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)

    def test_utc_timezone(self):
        """Timestamp is in UTC."""
        ts = now_iso()
        assert "+00:00" in ts or "Z" in ts


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD_JSON / SAVE_JSON_ATOMIC / APPEND_JSONL
# ═══════════════════════════════════════════════════════════════════════════════

class TestJsonIO:
    """JSON I/O from ctrl_util (same interface as bus_io)."""

    def test_load_existing(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert load_json(f) == {"key": "value"}

    def test_load_missing_returns_default(self, tmp_path):
        assert load_json(tmp_path / "nope.json") == {}

    def test_load_corrupt_returns_default(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{broken")
        assert load_json(f) == {}

    def test_save_and_load_round_trip(self, tmp_path):
        f = tmp_path / "data.json"
        data = {"positions": [{"coin": "BTC"}]}
        save_json_atomic(f, data)
        assert load_json(f) == data

    def test_append_jsonl(self, tmp_path):
        f = tmp_path / "log.jsonl"
        append_jsonl(f, {"n": 1})
        append_jsonl(f, {"n": 2})
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["n"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# UPDATE_HEARTBEAT
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateHeartbeat:
    """Controller heartbeat updates."""

    def test_creates_heartbeat_file(self, tmp_path):
        """update_heartbeat creates the heartbeat file."""
        hb_file = tmp_path / "heartbeat.json"
        with patch("scanner.v6.ctrl_util._cfg") as mock_cfg:
            mock_cfg.HEARTBEAT_FILE = hb_file
            update_heartbeat()
        assert hb_file.exists()

    def test_heartbeat_has_controller_key(self, tmp_path):
        """Heartbeat file contains 'controller' key."""
        hb_file = tmp_path / "heartbeat.json"
        with patch("scanner.v6.ctrl_util._cfg") as mock_cfg:
            mock_cfg.HEARTBEAT_FILE = hb_file
            update_heartbeat()
        data = json.loads(hb_file.read_text())
        assert "controller" in data

    def test_heartbeat_timestamp_is_iso(self, tmp_path):
        """Controller heartbeat value is ISO timestamp."""
        hb_file = tmp_path / "heartbeat.json"
        with patch("scanner.v6.ctrl_util._cfg") as mock_cfg:
            mock_cfg.HEARTBEAT_FILE = hb_file
            update_heartbeat()
        data = json.loads(hb_file.read_text())
        assert re.match(r"\d{4}-\d{2}-\d{2}T", data["controller"])

    def test_heartbeat_preserves_other_keys(self, tmp_path):
        """update_heartbeat preserves existing keys in heartbeat file."""
        hb_file = tmp_path / "heartbeat.json"
        hb_file.write_text('{"agent_alpha": "2026-03-28T00:00:00+00:00"}')
        with patch("scanner.v6.ctrl_util._cfg") as mock_cfg:
            mock_cfg.HEARTBEAT_FILE = hb_file
            update_heartbeat()
        data = json.loads(hb_file.read_text())
        assert "agent_alpha" in data
        assert "controller" in data

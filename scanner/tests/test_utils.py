"""Tests for scanner/utils.py — shared agent utilities."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from scanner.utils import (
    load_json,
    save_json,
    save_json_atomic,
    append_jsonl,
    read_jsonl,
    make_logger,
    load_env,
    get_trading_session,
    update_heartbeat,
)


# ═══════════════════════════════════════════════════════════════════════════════
# JSON I/O
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadJson:
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

    def test_load_custom_default(self, tmp_path):
        assert load_json(tmp_path / "nope.json", default={"x": 1}) == {"x": 1}


class TestSaveJson:
    def test_save_and_load(self, tmp_path):
        f = tmp_path / "test.json"
        save_json(str(f), {"key": "val"})
        assert load_json(f) == {"key": "val"}


class TestReadJsonl:
    def test_read_existing(self, tmp_path):
        f = tmp_path / "log.jsonl"
        f.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}\n')
        records = read_jsonl(f)
        assert len(records) == 3
        assert records[0]["n"] == 1

    def test_read_missing_returns_empty(self, tmp_path):
        assert read_jsonl(tmp_path / "nope.jsonl") == []

    def test_max_lines_returns_last_n(self, tmp_path):
        f = tmp_path / "log.jsonl"
        f.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}\n')
        records = read_jsonl(f, max_lines=2)
        assert len(records) == 2
        assert records[0]["n"] == 2
        assert records[1]["n"] == 3

    def test_skips_invalid_lines(self, tmp_path):
        f = tmp_path / "log.jsonl"
        f.write_text('{"n": 1}\nBROKEN\n{"n": 3}\n')
        records = read_jsonl(f)
        assert len(records) == 2

    def test_skips_blank_lines(self, tmp_path):
        f = tmp_path / "log.jsonl"
        f.write_text('{"n": 1}\n\n\n{"n": 2}\n')
        records = read_jsonl(f)
        assert len(records) == 2


class TestAppendJsonl:
    def test_appends(self, tmp_path):
        f = tmp_path / "log.jsonl"
        append_jsonl(str(f), {"n": 1})
        append_jsonl(str(f), {"n": 2})
        records = read_jsonl(f)
        assert len(records) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# MAKE_LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

class TestMakeLogger:
    def test_prints_with_prefix(self, capsys):
        log = make_logger("TEST_AGENT")
        log("hello world")
        captured = capsys.readouterr()
        assert "[TEST_AGENT] hello world" in captured.out

    def test_has_timestamp(self, capsys):
        log = make_logger("AGENT")
        log("msg")
        captured = capsys.readouterr()
        assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]", captured.out)

    def test_writes_to_log_file(self, tmp_path):
        log_file = tmp_path / "agent.log"
        log = make_logger("AGENT", log_file=log_file)
        log("test message")
        content = log_file.read_text()
        assert "[AGENT] test message" in content


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD_ENV
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadEnv:
    def test_parses_key_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n")
        result = load_env(str(env_file))
        assert result["KEY1"] == "value1"
        assert result["KEY2"] == "value2"

    def test_handles_export_prefix(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("export MY_KEY=hello\n")
        result = load_env(str(env_file))
        assert result["MY_KEY"] == "hello"

    def test_skips_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nKEY=val\n")
        result = load_env(str(env_file))
        assert "#" not in str(result.keys())
        assert result["KEY"] == "val"

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_env(str(tmp_path / "nope.env"))
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════════
# GET_TRADING_SESSION
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetTradingSession:
    def test_asia(self):
        for hour in [0, 3, 6]:
            assert get_trading_session(hour) == "ASIA"

    def test_europe(self):
        for hour in [7, 10, 12]:
            assert get_trading_session(hour) == "EUROPE"

    def test_us(self):
        for hour in [13, 16, 19]:
            assert get_trading_session(hour) == "US"

    def test_late_us(self):
        for hour in [20, 22, 23]:
            assert get_trading_session(hour) == "LATE_US"

    def test_boundary_hours(self):
        """Boundaries: 0→ASIA, 7→EUROPE, 13→US, 20→LATE_US."""
        assert get_trading_session(0) == "ASIA"
        assert get_trading_session(7) == "EUROPE"
        assert get_trading_session(13) == "US"
        assert get_trading_session(20) == "LATE_US"


# ═══════════════════════════════════════════════════════════════════════════════
# UPDATE_HEARTBEAT
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateHeartbeat:
    def test_creates_heartbeat(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        update_heartbeat("test_agent", heartbeat_file=hb_file)
        data = json.loads(hb_file.read_text())
        assert "test_agent" in data

    def test_preserves_other_agents(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        hb_file.write_text('{"other_agent": "2026-03-28T00:00:00+00:00"}')
        update_heartbeat("new_agent", heartbeat_file=hb_file)
        data = json.loads(hb_file.read_text())
        assert "other_agent" in data
        assert "new_agent" in data

    def test_heartbeat_is_iso_timestamp(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        update_heartbeat("agent", heartbeat_file=hb_file)
        data = json.loads(hb_file.read_text())
        assert re.match(r"\d{4}-\d{2}-\d{2}T", data["agent"])

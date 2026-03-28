"""
Bus I/O tests — verify atomic writes, locking, and JSON safety.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.bus_io import (
    load_json,
    save_json_atomic,
    load_json_locked,
    save_json_locked,
    append_jsonl,
)


class TestLoadJson:
    """Test JSON loading with defaults and error handling."""

    def test_load_existing_file(self, tmp_path):
        """Reads valid JSON."""
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert load_json(f) == {"key": "value"}

    def test_load_missing_file_returns_default(self, tmp_path):
        """Missing file returns default dict."""
        f = tmp_path / "nope.json"
        assert load_json(f) == {}

    def test_load_missing_file_custom_default(self, tmp_path):
        """Missing file returns custom default."""
        f = tmp_path / "nope.json"
        assert load_json(f, default={"x": 1}) == {"x": 1}

    def test_load_corrupt_json_returns_default(self, tmp_path):
        """Corrupt JSON returns default, doesn't crash."""
        f = tmp_path / "bad.json"
        f.write_text("{broken json!!!")
        result = load_json(f)
        assert result == {}

    def test_load_empty_file_returns_default(self, tmp_path):
        """Empty file returns default."""
        f = tmp_path / "empty.json"
        f.write_text("")
        result = load_json(f)
        assert result == {}


class TestSaveJsonAtomic:
    """Test atomic JSON writes."""

    def test_writes_valid_json(self, tmp_path):
        """Written file is valid JSON."""
        f = tmp_path / "out.json"
        data = {"positions": [{"coin": "BTC", "size": 0.01}]}
        save_json_atomic(f, data)

        assert f.exists()
        assert json.loads(f.read_text()) == data

    def test_no_temp_file_left(self, tmp_path):
        """Atomic write cleans up .tmp file."""
        f = tmp_path / "out.json"
        save_json_atomic(f, {"x": 1})

        assert not f.with_suffix(".tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        """Overwrites existing file correctly."""
        f = tmp_path / "out.json"
        f.write_text('{"old": true}')
        save_json_atomic(f, {"new": True})

        assert json.loads(f.read_text()) == {"new": True}

    def test_nested_data(self, tmp_path):
        """Handles nested structures."""
        f = tmp_path / "nested.json"
        data = {
            "positions": [
                {"coin": "BTC", "meta": {"regime": "trending", "signals": [1, 2, 3]}}
            ],
            "timestamp": "2026-03-27T00:00:00Z"
        }
        save_json_atomic(f, data)
        assert json.loads(f.read_text()) == data

    def test_creates_parent_dirs(self, tmp_path):
        """Creates parent directories if they don't exist."""
        f = tmp_path / "deep" / "nested" / "file.json"
        save_json_atomic(f, {"ok": True})
        assert f.exists()
        assert json.loads(f.read_text()) == {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD JSON LOCKED
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadJsonLocked:
    """Test locked JSON reading (shared lock)."""

    def test_load_existing_file(self, tmp_path):
        """Reads valid JSON with lock."""
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert load_json_locked(f) == {"key": "value"}

    def test_load_missing_file_returns_default(self, tmp_path):
        """Missing file returns default dict."""
        f = tmp_path / "nope.json"
        assert load_json_locked(f) == {}

    def test_load_missing_file_custom_default(self, tmp_path):
        """Missing file returns custom default."""
        f = tmp_path / "nope.json"
        assert load_json_locked(f, default={"x": 1}) == {"x": 1}

    def test_load_corrupt_json_returns_default(self, tmp_path):
        """Corrupt JSON returns default, doesn't crash."""
        f = tmp_path / "bad.json"
        f.write_text("{broken json!!!")
        assert load_json_locked(f) == {}

    def test_load_empty_file_returns_default(self, tmp_path):
        """Empty file returns default."""
        f = tmp_path / "empty.json"
        f.write_text("")
        assert load_json_locked(f) == {}

    def test_load_nested_data(self, tmp_path):
        """Handles nested structures correctly."""
        f = tmp_path / "nested.json"
        data = {"positions": [{"coin": "BTC", "meta": {"regime": "trending"}}]}
        f.write_text(json.dumps(data))
        assert load_json_locked(f) == data


# ═══════════════════════════════════════════════════════════════════════════════
# SAVE JSON LOCKED
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveJsonLocked:
    """Test locked JSON writing (exclusive lock)."""

    def test_writes_valid_json(self, tmp_path):
        """Written file is valid JSON."""
        f = tmp_path / "out.json"
        data = {"positions": [{"coin": "ETH", "size": 1.5}]}
        save_json_locked(f, data)
        assert json.loads(f.read_text()) == data

    def test_no_temp_file_left(self, tmp_path):
        """Atomic write cleans up .tmp file."""
        f = tmp_path / "out.json"
        save_json_locked(f, {"x": 1})
        assert not f.with_suffix(".tmp").exists()

    def test_lock_file_created(self, tmp_path):
        """Lock file is created during write."""
        f = tmp_path / "out.json"
        save_json_locked(f, {"x": 1})
        assert f.with_suffix(".lock").exists()

    def test_overwrites_existing(self, tmp_path):
        """Overwrites existing file correctly."""
        f = tmp_path / "out.json"
        f.write_text('{"old": true}')
        save_json_locked(f, {"new": True})
        assert json.loads(f.read_text()) == {"new": True}

    def test_creates_parent_dirs(self, tmp_path):
        """Creates parent directories if needed."""
        f = tmp_path / "deep" / "nested" / "file.json"
        save_json_locked(f, {"ok": True})
        assert f.exists()
        assert json.loads(f.read_text()) == {"ok": True}

    def test_round_trip_with_locked_read(self, tmp_path):
        """save_json_locked → load_json_locked round-trip."""
        f = tmp_path / "round.json"
        data = {"positions": [{"coin": "SOL", "pnl": -0.5}], "ts": "2026-03-28"}
        save_json_locked(f, data)
        assert load_json_locked(f) == data


# ═══════════════════════════════════════════════════════════════════════════════
# APPEND JSONL
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppendJsonl:
    """Test JSONL append operations."""

    def test_appends_single_record(self, tmp_path):
        """Appends one JSON line."""
        f = tmp_path / "log.jsonl"
        append_jsonl(f, {"event": "trade", "coin": "BTC"})
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"event": "trade", "coin": "BTC"}

    def test_appends_multiple_records(self, tmp_path):
        """Multiple appends create multiple lines."""
        f = tmp_path / "log.jsonl"
        append_jsonl(f, {"n": 1})
        append_jsonl(f, {"n": 2})
        append_jsonl(f, {"n": 3})
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 3
        assert [json.loads(l)["n"] for l in lines] == [1, 2, 3]

    def test_each_line_is_valid_json(self, tmp_path):
        """Every line is independently parseable JSON."""
        f = tmp_path / "log.jsonl"
        records = [
            {"ts": "2026-03-28T00:00:00Z", "coin": "BTC"},
            {"ts": "2026-03-28T00:01:00Z", "coin": "ETH"},
        ]
        for r in records:
            append_jsonl(f, r)
        for line in f.read_text().strip().split("\n"):
            parsed = json.loads(line)  # should not raise
            assert "ts" in parsed

    def test_creates_parent_dirs(self, tmp_path):
        """Creates parent directories if needed."""
        f = tmp_path / "deep" / "nested" / "log.jsonl"
        append_jsonl(f, {"ok": True})
        assert f.exists()

    def test_appends_to_existing_file(self, tmp_path):
        """Appends to file that already has content."""
        f = tmp_path / "log.jsonl"
        f.write_text('{"existing": true}\n')
        append_jsonl(f, {"new": True})
        lines = f.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"existing": True}
        assert json.loads(lines[1]) == {"new": True}

    def test_handles_nested_data(self, tmp_path):
        """Handles nested structures in JSONL records."""
        f = tmp_path / "log.jsonl"
        record = {"trade": {"coin": "BTC", "signals": [1, 2, 3]}, "meta": {"v": 6}}
        append_jsonl(f, record)
        assert json.loads(f.read_text().strip()) == record

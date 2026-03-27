#!/usr/bin/env python3
"""
Tests for agent registry — auto-registration and public profiles.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.agent_registry import AgentRegistry, AgentProfile, AGENTS_FILE


@pytest.fixture
def clean_agents(tmp_path, monkeypatch):
    """Use a temp agents file."""
    import scanner.v6.agent_registry as reg_mod
    agents_file = tmp_path / "data" / "agents.json"
    monkeypatch.setattr(reg_mod, "AGENTS_FILE", agents_file)
    return tmp_path


class TestAutoRegistration:
    def test_first_connection_creates_profile(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_alice")

        assert isinstance(profile, AgentProfile)
        assert profile.operator_id == "op_alice"
        assert profile.class_name == "novice"
        assert profile.sessions_completed == 0
        assert profile.current_mode == "comfort"
        assert profile.current_strategy == "idle"

    def test_subsequent_calls_return_existing(self, clean_agents):
        registry = AgentRegistry()
        first = registry.register_or_get("op_bob")
        second = registry.register_or_get("op_bob")

        assert first.agent_id == second.agent_id
        assert first.registered_at == second.registered_at

    def test_agent_id_is_unique(self, clean_agents):
        registry = AgentRegistry()
        a = registry.register_or_get("op_a")
        b = registry.register_or_get("op_b")

        assert a.agent_id != b.agent_id

    def test_agent_id_is_short_uuid(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")

        assert len(profile.agent_id) == 8

    def test_public_url_format(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")

        assert profile.public_url == f"https://getzero.dev/agent/{profile.agent_id}"

    def test_display_name_auto_generated(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")

        assert profile.display_name.startswith("agent-")
        assert len(profile.display_name) == 10  # "agent-" + 4 chars


class TestProfileUpdates:
    def test_update_refreshes_stats(self, clean_agents):
        """Profile updates pull from progression engine when available."""
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")
        assert profile.sessions_completed == 0

        # Second call triggers _update_profile (progression may fail gracefully)
        updated = registry.register_or_get("op_test")
        assert updated.agent_id == profile.agent_id


class TestGetAllAgents:
    def test_returns_list(self, clean_agents):
        registry = AgentRegistry()
        registry.register_or_get("op_a")
        registry.register_or_get("op_b")

        agents = registry.get_all_agents()
        assert isinstance(agents, list)
        assert len(agents) == 2

    def test_list_contains_agent_data(self, clean_agents):
        registry = AgentRegistry()
        registry.register_or_get("op_test")

        agents = registry.get_all_agents()
        assert agents[0]["operator_id"] == "op_test"
        assert "agent_id" in agents[0]


class TestGetAgent:
    def test_get_by_id(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")

        found = registry.get_agent(profile.agent_id)
        assert found is not None
        assert found["agent_id"] == profile.agent_id

    def test_get_unknown_returns_none(self, clean_agents):
        registry = AgentRegistry()
        assert registry.get_agent("nonexistent") is None


class TestAgentCount:
    def test_empty_registry(self, clean_agents):
        registry = AgentRegistry()
        assert registry.get_agent_count() == 0

    def test_after_registrations(self, clean_agents):
        registry = AgentRegistry()
        registry.register_or_get("op_a")
        registry.register_or_get("op_b")
        registry.register_or_get("op_c")

        assert registry.get_agent_count() == 3


class TestPersistence:
    def test_save_and_load_roundtrip(self, clean_agents, monkeypatch):
        import scanner.v6.agent_registry as reg_mod

        # Create and register
        registry1 = AgentRegistry()
        profile = registry1.register_or_get("op_persist")

        # Create new registry instance (loads from file)
        registry2 = AgentRegistry()
        loaded = registry2.get_agent(profile.agent_id)

        assert loaded is not None
        assert loaded["operator_id"] == "op_persist"
        assert loaded["agent_id"] == profile.agent_id

    def test_file_created_on_save(self, clean_agents, monkeypatch):
        import scanner.v6.agent_registry as reg_mod
        agents_file = reg_mod.AGENTS_FILE

        assert not agents_file.exists()

        registry = AgentRegistry()
        registry.register_or_get("op_test")

        assert agents_file.exists()
        data = json.loads(agents_file.read_text())
        assert "op_test" in data


class TestProfileCardRendering:
    """Test that profile card data is structured correctly for rendering."""

    def test_profile_has_all_card_fields(self, clean_agents):
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")
        from dataclasses import asdict
        data = asdict(profile)

        required_fields = [
            "agent_id", "display_name", "class_name", "total_score",
            "sessions_completed", "total_trades", "win_rate", "best_strategy",
            "streak_current", "streak_best", "milestones_earned",
            "current_mode", "current_strategy", "public_url",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"


class TestAPIEndpoints:
    """Test the REST API integration (unit-level, no server)."""

    def test_profile_endpoint_data(self, clean_agents):
        """Simulates what the /v6/agent/profile endpoint returns."""
        registry = AgentRegistry()
        profile = registry.register_or_get("op_api_test")
        from dataclasses import asdict
        result = asdict(profile)

        assert result["operator_id"] == "op_api_test"
        assert "public_url" in result
        assert result["class_name"] == "novice"

    def test_agent_by_id_endpoint(self, clean_agents):
        """Simulates what /v6/agent/{id} returns."""
        registry = AgentRegistry()
        profile = registry.register_or_get("op_test")

        result = registry.get_agent(profile.agent_id)
        assert result is not None
        assert result["operator_id"] == "op_test"

    def test_list_agents_endpoint(self, clean_agents):
        """Simulates what /v6/agents returns."""
        registry = AgentRegistry()
        registry.register_or_get("op_a")
        registry.register_or_get("op_b")

        agents = registry.get_all_agents()
        count = registry.get_agent_count()

        assert len(agents) == 2
        assert count == 2

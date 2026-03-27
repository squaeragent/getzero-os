#!/usr/bin/env python3
"""
Tests for multi-operator isolation.

Verifies that two operators get separate state and can't see each other's data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.operator import (
    OperatorContext,
    resolve_operator,
    register_operator,
    list_operators,
    plan_allows_strategy,
    get_allowed_strategies,
    _DEFAULT_OPERATOR_ID,
    _REGISTRY_FILE,
)


@pytest.fixture
def clean_registry(tmp_path, monkeypatch):
    """Use a temp registry file."""
    import scanner.v6.operator as op_mod
    reg_file = tmp_path / "operators.json"
    monkeypatch.setattr(op_mod, "_REGISTRY_FILE", reg_file)
    monkeypatch.setattr(op_mod, "BUS_DIR", tmp_path / "bus")
    (tmp_path / "bus").mkdir()
    return tmp_path


class TestOperatorContext:
    def test_default_operator(self, clean_registry):
        ctx = resolve_operator(_DEFAULT_OPERATOR_ID)
        assert ctx.operator_id == _DEFAULT_OPERATOR_ID
        assert ctx.is_default is True
        assert ctx.plan == "scale"

    def test_unknown_operator_returns_default(self, clean_registry):
        ctx = resolve_operator("unknown_123")
        assert ctx.is_default is True

    def test_register_and_resolve(self, clean_registry):
        ctx = register_operator(
            operator_id="op_alice",
            wallet_address="0xAlice",
            plan="pro",
        )
        assert ctx.operator_id == "op_alice"
        assert ctx.plan == "pro"
        assert ctx.bus_dir.exists()
        assert "op_alice" in str(ctx.bus_dir)

        # Resolve should find it
        resolved = resolve_operator("op_alice")
        assert resolved.operator_id == "op_alice"
        assert resolved.plan == "pro"
        assert resolved.is_default is False


class TestOperatorIsolation:
    def test_separate_bus_dirs(self, clean_registry):
        ctx_a = register_operator("op_a", "0xA", plan="free")
        ctx_b = register_operator("op_b", "0xB", plan="pro")

        assert ctx_a.bus_dir != ctx_b.bus_dir
        assert "op_a" in str(ctx_a.bus_dir)
        assert "op_b" in str(ctx_b.bus_dir)

    def test_session_isolation(self, clean_registry):
        """Two operators' sessions don't interfere."""
        from scanner.v6.session import SessionManager

        ctx_a = register_operator("op_a", "0xA", plan="scale")
        ctx_b = register_operator("op_b", "0xB", plan="scale")

        sm_a = SessionManager(bus_dir=ctx_a.bus_dir)
        sm_b = SessionManager(bus_dir=ctx_b.bus_dir)

        # Start session on A
        session_a = sm_a.start_session("momentum", paper=True)
        assert session_a is not None

        # B should see NO session
        assert sm_b.active_session is None

        # End A's session
        sm_a.end_session_early(session_a)

    def test_position_file_isolation(self, clean_registry):
        """Position files are in separate directories."""
        ctx_a = register_operator("op_a", "0xA")
        ctx_b = register_operator("op_b", "0xB")

        pos_a = ctx_a.bus_dir / "positions.json"
        pos_b = ctx_b.bus_dir / "positions.json"

        # Write different positions
        pos_a.write_text(json.dumps({"positions": [{"coin": "BTC", "owner": "A"}]}))
        pos_b.write_text(json.dumps({"positions": [{"coin": "ETH", "owner": "B"}]}))

        # Read back — should be isolated
        data_a = json.loads(pos_a.read_text())
        data_b = json.loads(pos_b.read_text())

        assert data_a["positions"][0]["coin"] == "BTC"
        assert data_b["positions"][0]["coin"] == "ETH"


class TestPlanGating:
    def test_free_plan(self):
        assert plan_allows_strategy("free", "momentum") is True
        assert plan_allows_strategy("free", "defense") is True
        assert plan_allows_strategy("free", "watch") is True
        assert plan_allows_strategy("free", "degen") is False
        assert plan_allows_strategy("free", "apex") is False

    def test_pro_plan(self):
        assert plan_allows_strategy("pro", "momentum") is True
        assert plan_allows_strategy("pro", "degen") is True
        assert plan_allows_strategy("pro", "sniper") is False
        assert plan_allows_strategy("pro", "apex") is False

    def test_scale_plan(self):
        assert plan_allows_strategy("scale", "momentum") is True
        assert plan_allows_strategy("scale", "apex") is True
        assert plan_allows_strategy("scale", "sniper") is True

    def test_get_allowed(self):
        free = get_allowed_strategies("free")
        assert len(free) == 3
        scale = get_allowed_strategies("scale")
        assert len(scale) == 9


class TestListOperators:
    def test_list_includes_default(self, clean_registry):
        ops = list_operators()
        assert any(o["is_default"] for o in ops)

    def test_list_includes_registered(self, clean_registry):
        register_operator("op_test", "0xTest")
        ops = list_operators()
        ids = {o["operator_id"] for o in ops}
        assert "op_test" in ids


class TestApiPlanGating:
    def test_start_session_blocked_by_plan(self, clean_registry):
        """Free plan operator can't use degen strategy."""
        from scanner.v6.api import ZeroAPI
        import scanner.v6.operator as op_mod

        ctx = register_operator("op_free", "0xFree", plan="free")
        api = ZeroAPI(bus_dir=clean_registry / "bus")

        result = api.start_session("op_free", "degen")
        assert "error" in result
        assert "higher plan" in result["error"].lower()
        assert result["plan"] == "free"

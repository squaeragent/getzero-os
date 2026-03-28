#!/usr/bin/env python3
"""
Operator context — per-operator isolation for multi-tenant engine.

Every operator gets:
  - Unique bus directory (bus/{operator_id}/)
  - Own wallet address
  - Own plan (free/pro/scale)
  - Isolated session state, positions, and history

V1: Single operator. Default context points to existing bus/.
V2: Multiple operators. Each resolved from database.

Usage:
    from scanner.v6.operator import OperatorContext, resolve_operator

    ctx = resolve_operator("op_123")
    session_mgr = SessionManager(bus_dir=ctx.bus_dir)
    monitor = Monitor(bus_dir=ctx.bus_dir)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from scanner.v6.config import BUS_DIR


@dataclass
class OperatorContext:
    """Per-operator state and configuration."""
    operator_id: str
    wallet_address: str
    api_wallet: str
    bus_dir: Path           # bus/{operator_id}/ — all state lives here
    plan: str               # "free" | "pro" | "scale"
    is_default: bool = True  # True for V1 single operator

    def ensure_bus_dir(self) -> None:
        """Create bus directory if it doesn't exist."""
        self.bus_dir.mkdir(parents=True, exist_ok=True)


# ── V1: Single operator (default) ───────────────────────────────────────────

_DEFAULT_OPERATOR_ID = "op_default"

def _default_context() -> OperatorContext:
    """V1: default operator context pointing to existing bus/."""
    return OperatorContext(
        operator_id=_DEFAULT_OPERATOR_ID,
        wallet_address=os.environ.get("HYPERLIQUID_MAIN_ADDRESS", ""),
        api_wallet=os.environ.get("HYPERLIQUID_API_WALLET", ""),
        bus_dir=BUS_DIR,
        plan="scale",  # V1: single operator gets full access
        is_default=True,
    )


# ── Operator Registry ───────────────────────────────────────────────────────

_REGISTRY_FILE = BUS_DIR.parent / "operators.json"

def _load_registry() -> dict:
    """Load operator registry from disk."""
    if not _REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(_REGISTRY_FILE.read_text())
    except Exception:
        return {}


def _save_registry(registry: dict) -> None:
    """Save operator registry to disk."""
    _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_FILE.write_text(json.dumps(registry, indent=2))


def register_operator(
    operator_id: str,
    wallet_address: str,
    api_wallet: str = "",
    plan: str = "free",
) -> OperatorContext:
    """Register a new operator. Creates their bus directory."""
    registry = _load_registry()

    bus_dir = BUS_DIR.parent / "operators" / operator_id / "bus"
    bus_dir.mkdir(parents=True, exist_ok=True)

    registry[operator_id] = {
        "wallet_address": wallet_address,
        "api_wallet": api_wallet,
        "plan": plan,
        "bus_dir": str(bus_dir),
    }
    _save_registry(registry)

    return OperatorContext(
        operator_id=operator_id,
        wallet_address=wallet_address,
        api_wallet=api_wallet,
        bus_dir=bus_dir,
        plan=plan,
        is_default=False,
    )


def resolve_operator(operator_id: str) -> OperatorContext:
    """
    Resolve operator_id to OperatorContext.

    V1: if operator_id is default or not found, returns default context.
    V2: looks up from registry/database.
    """
    if operator_id == _DEFAULT_OPERATOR_ID:
        return _default_context()

    registry = _load_registry()
    if operator_id in registry:
        entry = registry[operator_id]
        return OperatorContext(
            operator_id=operator_id,
            wallet_address=entry["wallet_address"],
            api_wallet=entry.get("api_wallet", ""),
            bus_dir=Path(entry["bus_dir"]),
            plan=entry.get("plan", "free"),
            is_default=False,
        )

    # Fallback: return default context (V1 behavior)
    return _default_context()


def list_operators() -> list[dict]:
    """List all registered operators."""
    registry = _load_registry()
    operators = [{"operator_id": _DEFAULT_OPERATOR_ID, "plan": "scale", "is_default": True}]
    for op_id, entry in registry.items():
        operators.append({
            "operator_id": op_id,
            "plan": entry.get("plan", "free"),
            "is_default": False,
        })
    return operators


# ── Plan Gating ──────────────────────────────────────────────────────────────

_PLAN_STRATEGIES = {
    "free":  {"momentum", "defense", "watch"},
    "pro":   {"momentum", "defense", "watch", "degen", "scout", "funding"},
    "scale": {"momentum", "defense", "watch", "degen", "scout", "funding", "sniper", "fade", "apex"},
}

def plan_allows_strategy(plan: str, strategy_name: str) -> bool:
    """Check if an operator's plan allows a specific strategy."""
    allowed = _PLAN_STRATEGIES.get(plan, _PLAN_STRATEGIES["free"])
    return strategy_name in allowed


def get_allowed_strategies(plan: str) -> set[str]:
    """Get the set of strategies allowed for a plan."""
    return _PLAN_STRATEGIES.get(plan, _PLAN_STRATEGIES["free"])


# ── Genesis Program ─────────────────────────────────────────────────────────

_GENESIS_FILE = Path(__file__).parent / "data" / "genesis_operators.json"
_GENESIS_TOTAL = 100


def _load_genesis() -> list[dict]:
    if not _GENESIS_FILE.exists():
        return []
    try:
        return json.loads(_GENESIS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_genesis(operators: list[dict]) -> None:
    _GENESIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GENESIS_FILE.write_text(json.dumps(operators, indent=2))


def get_genesis_number(operator_id: str) -> int | None:
    """Return genesis number for an operator, or None if not genesis."""
    for op in _load_genesis():
        if op["operator_id"] == operator_id:
            return op["genesis_number"]
    return None


def register_genesis_operator(operator_id: str, agent_handle: str) -> int | None:
    """Assign next genesis number. Returns number or None if program full."""
    operators = _load_genesis()

    # Already registered
    for op in operators:
        if op["operator_id"] == operator_id:
            return op["genesis_number"]

    if len(operators) >= _GENESIS_TOTAL:
        return None

    from datetime import datetime, timezone
    genesis_number = len(operators) + 1
    operators.append({
        "operator_id": operator_id,
        "genesis_number": genesis_number,
        "registered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "agent_handle": agent_handle,
    })
    _save_genesis(operators)
    return genesis_number


def list_genesis_operators() -> list[dict]:
    """Return all genesis operators."""
    return _load_genesis()

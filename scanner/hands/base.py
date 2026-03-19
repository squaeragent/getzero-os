"""
ZERO OS — HandAdapter base class.

All execution adapters inherit from this and implement execute() / get_positions().
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from scanner.core.interfaces import Decision


class HandAdapter(ABC):
    """Base class for execution adapters."""

    name: str = "base"

    @abstractmethod
    def execute(self, decisions: list[Decision]) -> list[dict]:
        """Execute trading decisions. Returns list of execution results."""
        ...

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return current open positions."""
        ...

    def health_check(self) -> dict:
        """Return adapter health status."""
        return {"name": self.name, "status": "ok"}

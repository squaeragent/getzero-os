"""
ZERO OS — SensePlugin base class.

All data source plugins inherit from this and implement fetch().
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from scanner.core.interfaces import Observation


class SensePlugin(ABC):
    """Base class for data source plugins."""

    name: str = "base"

    @abstractmethod
    def fetch(self, coins: list[str]) -> list[Observation]:
        """Fetch observations for the given coins."""
        ...

    def health_check(self) -> dict:
        """Return plugin health status."""
        return {"name": self.name, "status": "ok"}

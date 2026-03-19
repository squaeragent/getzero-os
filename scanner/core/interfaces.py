"""
ZERO OS — Core data interfaces.

Three foundational types that flow through the system:
  Observation  — single data point from any source (SENSES output)
  Decision     — trading decision (MIND output)
  WorldState   — complete world model built from observations
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """Single data point from any source."""

    coin: str
    dimension: str        # e.g. "chaos.hurst", "technical.rsi_24h", "funding.rate"
    value: float
    confidence: float     # 0-1
    source: str           # "envy", "hyperliquid", "talib", "own"
    timestamp: float      # unix epoch
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "dimension": self.dimension,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Observation:
        return cls(
            coin=d["coin"],
            dimension=d["dimension"],
            value=d["value"],
            confidence=d["confidence"],
            source=d["source"],
            timestamp=d["timestamp"],
            metadata=d.get("metadata", {}),
        )


@dataclass
class Decision:
    """Output from the Mind — a trading decision."""

    id: str               # unique decision ID
    coin: str
    action: str           # "LONG", "SHORT", "CLOSE", "WAIT"
    confidence: float     # 0-1
    stop_pct: float
    size_pct: float       # 0-1, fraction of max position
    reasoning: dict       # hypothesis, adversary_score, attacks_survived, etc.
    ttl_hours: float
    exit_conditions: list
    regime: str
    timestamp: float
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "coin": self.coin,
            "action": self.action,
            "confidence": self.confidence,
            "stop_pct": self.stop_pct,
            "size_pct": self.size_pct,
            "reasoning": self.reasoning,
            "ttl_hours": self.ttl_hours,
            "exit_conditions": self.exit_conditions,
            "regime": self.regime,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Decision:
        return cls(
            id=d["id"],
            coin=d["coin"],
            action=d["action"],
            confidence=d["confidence"],
            stop_pct=d["stop_pct"],
            size_pct=d["size_pct"],
            reasoning=d.get("reasoning", {}),
            ttl_hours=d["ttl_hours"],
            exit_conditions=d.get("exit_conditions", []),
            regime=d.get("regime", "unknown"),
            timestamp=d["timestamp"],
            metadata=d.get("metadata", {}),
        )


@dataclass
class WorldState:
    """Complete world model built from observations."""

    observations: dict    # coin -> dimension -> Observation
    macro: dict           # macro state
    timestamp: float

    def to_dict(self) -> dict:
        obs_dict = {}
        for coin, dims in self.observations.items():
            obs_dict[coin] = {
                dim: obs.to_dict() for dim, obs in dims.items()
            }
        return {
            "observations": obs_dict,
            "macro": self.macro,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorldState:
        observations: dict[str, dict[str, Observation]] = {}
        for coin, dims in d.get("observations", {}).items():
            observations[coin] = {
                dim: Observation.from_dict(obs) for dim, obs in dims.items()
            }
        return cls(
            observations=observations,
            macro=d.get("macro", {}),
            timestamp=d["timestamp"],
        )

    def get_observation(self, coin: str, dimension: str) -> Observation | None:
        return self.observations.get(coin, {}).get(dimension)

    def add_observation(self, obs: Observation) -> None:
        if obs.coin not in self.observations:
            self.observations[obs.coin] = {}
        self.observations[obs.coin][obs.dimension] = obs

"""Conviction velocity tracker — how fast conviction is building per coin."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HISTORY_FILE = Path(__file__).parent / "data" / "conviction_history.json"
MAX_READINGS = 12  # 6 hours at 30min intervals


@dataclass
class ConvictionReading:
    timestamp: str  # ISO format
    consensus: int  # 0-7
    direction: str  # SHORT/LONG/NONE
    conviction: float  # 0.0-1.0


class ConvictionTracker:
    """Tracks conviction history and calculates velocity."""

    def __init__(self):
        self.history: dict[str, list[dict]] = {}
        self._load()

    def record(self, coin: str, consensus: int, direction: str, conviction: float):
        """Record a new conviction reading. Skips consensus 0."""
        if consensus == 0:
            return
        reading = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "consensus": consensus,
            "direction": direction,
            "conviction": conviction,
        }
        if coin not in self.history:
            self.history[coin] = []
        self.history[coin].append(reading)
        # Cap at MAX_READINGS
        if len(self.history[coin]) > MAX_READINGS:
            self.history[coin] = self.history[coin][-MAX_READINGS:]
        self._save()

    def get_velocity(self, coin: str) -> float:
        """Calculate layers gained/lost per hour over recent readings."""
        readings = self.history.get(coin, [])
        if len(readings) < 2:
            return 0.0
        # Use up to last 3 readings for velocity
        recent = readings[-3:] if len(readings) >= 3 else readings
        earliest = recent[0]
        latest = recent[-1]
        t0 = datetime.fromisoformat(earliest["timestamp"])
        t1 = datetime.fromisoformat(latest["timestamp"])
        hours = (t1 - t0).total_seconds() / 3600
        if hours <= 0:
            return 0.0
        return (latest["consensus"] - earliest["consensus"]) / hours

    def get_velocity_label(self, velocity: float) -> str:
        """Classify velocity into a human label."""
        if velocity >= 2.0:
            return "ACCELERATING"
        if velocity >= 0.5:
            return "BUILDING"
        if velocity > -0.5:
            return "STEADY"
        if velocity > -2.0:
            return "DECELERATING"
        return "RETREATING"

    def get_acceleration_alerts(self, threshold: float = 1.5) -> list[dict]:
        """Return coins with velocity >= threshold layers/hour."""
        alerts = []
        for coin in self.history:
            velocity = self.get_velocity(coin)
            if velocity >= threshold:
                readings = self.history[coin]
                latest = readings[-1]
                alerts.append({
                    "coin": coin,
                    "velocity": round(velocity, 2),
                    "velocity_label": self.get_velocity_label(velocity),
                    "consensus": latest["consensus"],
                    "direction": latest["direction"],
                })
        return alerts

    def estimate_time_to_threshold(self, coin: str, target: int = 5) -> Optional[str]:
        """Estimate time to reach target consensus at current velocity."""
        readings = self.history.get(coin, [])
        if not readings:
            return None
        velocity = self.get_velocity(coin)
        if velocity <= 0:
            return None
        current = readings[-1]["consensus"]
        if current >= target:
            return None
        gap = target - current
        hours = gap / velocity
        minutes = hours * 60
        # Round to nearest 15 min
        rounded = max(15, round(minutes / 15) * 15)
        if rounded < 60:
            return f"~{rounded} min"
        h = rounded / 60
        if h == int(h):
            return f"~{int(h)} hour{'s' if int(h) > 1 else ''}"
        return f"~{h:.1f} hours"

    def get_coin_data(self, coin: str) -> dict:
        """Get full velocity data for a coin."""
        velocity = self.get_velocity(coin)
        readings = self.history.get(coin, [])
        peak = max((r["consensus"] for r in readings), default=0)
        return {
            "velocity": round(velocity, 2),
            "velocity_label": self.get_velocity_label(velocity),
            "peak_consensus": peak,
            "time_to_threshold": self.estimate_time_to_threshold(coin),
            "readings_count": len(readings),
        }

    def _load(self):
        """Load history from conviction_history.json."""
        if HISTORY_FILE.exists():
            try:
                self.history = json.loads(HISTORY_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self.history = {}
        else:
            self.history = {}

    def _save(self):
        """Save history to conviction_history.json."""
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(self.history, indent=2) + "\n")

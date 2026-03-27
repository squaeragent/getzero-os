#!/usr/bin/env python3
"""
Strategy Loader — load, validate, and serve YAML strategy configs.

Usage:
  from scanner.v6.strategy_loader import load_strategy, list_strategies, get_active_strategy

  cfg = load_strategy("momentum")
  cfg.risk.max_positions     # -> 5
  cfg.risk.position_size_pct # -> 10
  cfg.evaluation.min_regime  # -> ["trending", "stable"]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Optional YAML parser — fall back to safe pure-python loader if pyyaml missing
try:
    import yaml as _yaml
    def _load_yaml(text: str) -> dict:
        return _yaml.safe_load(text)
except ImportError:
    import re as _re
    def _load_yaml(text: str) -> dict:  # type: ignore[misc]
        """Minimal YAML parser for flat/nested configs (no anchors, no multi-doc)."""
        raise ImportError(
            "PyYAML is required: pip install pyyaml\n"
            "Cannot parse strategy configs without it."
        )

STRATEGIES_DIR = Path(__file__).parent / "strategies"

# ─── DATACLASSES ─────────────────────────────────────────────────────────────

@dataclass
class SessionConfig:
    duration_hours: int


@dataclass
class EvaluationConfig:
    scope: str                    # e.g. "top_50"
    consensus_threshold: int      # 1-7
    directions: list[str]         # ["long", "short"]
    min_regime: list[str]         # ["trending", "stable", ...]


@dataclass
class RiskConfig:
    max_positions: int
    position_size_pct: float      # % of equity per position
    stop_loss_pct: float          # hard stop %
    reserve_pct: float            # % of equity to keep uninvested
    max_daily_loss_pct: float     # daily circuit-breaker %
    max_hold_hours: int           # force exit after N hours
    entry_end_action: str         # "hold" | "close"


@dataclass
class ExitsConfig:
    trailing_stop: bool
    trailing_activation_pct: float
    trailing_distance_pct: float
    regime_shift_exit: bool
    time_exit: bool


@dataclass
class UnlockConfig:
    score_minimum: float


VALID_MODES = {"comfort", "sport", "track"}


@dataclass
class ModeConfig:
    push_on: list[str]
    approval_required: bool
    approval_timeout_seconds: int  # 0 means no timeout (only relevant when approval_required)
    heat_push_interval_hours: Optional[float]  # None = no heat pushes
    approaching_push: bool

    def to_dict(self) -> dict:
        return {
            "push_on": self.push_on,
            "approval_required": self.approval_required,
            "approval_timeout_seconds": self.approval_timeout_seconds,
            "heat_push_interval_hours": self.heat_push_interval_hours,
            "approaching_push": self.approaching_push,
        }


# Default mode configs (used when YAML doesn't define modes section)
_DEFAULT_MODES: dict[str, ModeConfig] = {
    "comfort": ModeConfig(
        push_on=["entry", "exit", "brief", "circuit_breaker"],
        approval_required=False,
        approval_timeout_seconds=0,
        heat_push_interval_hours=None,
        approaching_push=False,
    ),
    "sport": ModeConfig(
        push_on=["entry", "exit", "brief", "approaching", "heat_shift", "regime_shift", "circuit_breaker"],
        approval_required=False,
        approval_timeout_seconds=0,
        heat_push_interval_hours=2,
        approaching_push=True,
    ),
    "track": ModeConfig(
        push_on=["entry", "exit", "brief", "approaching", "heat_shift", "regime_shift", "eval_candidate", "circuit_breaker"],
        approval_required=True,
        approval_timeout_seconds=300,
        heat_push_interval_hours=1,
        approaching_push=True,
    ),
}


@dataclass
class StrategyConfig:
    name: str
    display: str
    session: SessionConfig
    evaluation: EvaluationConfig
    risk: RiskConfig
    exits: ExitsConfig
    unlock: UnlockConfig
    tier: str                     # "free" | "pro" | "scale"
    modes: dict[str, ModeConfig] = field(default_factory=lambda: dict(_DEFAULT_MODES))

    # ── convenience helpers ────────────────────────────────────────────────

    def allows_direction(self, direction: str) -> bool:
        """Check if this strategy allows a given trade direction (case-insensitive)."""
        return direction.upper() in [d.upper() for d in self.evaluation.directions]

    def allows_regime(self, regime: str) -> bool:
        """Check if this strategy allows a given market regime."""
        if not self.evaluation.min_regime:
            return True
        # Map detailed regime to category, fall back to exact match
        category = _REGIME_MAP.get(regime.lower(), regime.lower())
        return category in [r.lower() for r in self.evaluation.min_regime]

    def reserve_usd(self, equity: float) -> float:
        """Minimum cash reserve in USD that must remain uninvested."""
        return equity * (self.risk.reserve_pct / 100.0)

    def max_position_usd(self, equity: float) -> float:
        """Max position size in USD from strategy config."""
        return equity * (self.risk.position_size_pct / 100.0)

    def daily_loss_limit_usd(self, equity: float) -> float:
        """Daily circuit-breaker threshold in USD."""
        return equity * (self.risk.max_daily_loss_pct / 100.0)

    def is_watch_only(self) -> bool:
        """True for Watch strategy — no positions, observe only."""
        return self.risk.max_positions == 0

    def get_mode_config(self, mode: str) -> ModeConfig:
        """Get ModeConfig for a drive mode. Raises ValueError for invalid mode."""
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Valid: {sorted(VALID_MODES)}")
        return self.modes.get(mode, _DEFAULT_MODES[mode])


# ─── VALIDATION ──────────────────────────────────────────────────────────────

# Required top-level keys
# Map detailed regime labels (13 from RegimeClassifier) to strategy-level categories (4)
_REGIME_MAP: dict[str, str] = {
    "strong_trend":      "trending",
    "moderate_trend":    "trending",
    "weak_trend":        "trending",
    "transition":        "trending",
    "strong_revert":     "reverting",
    "moderate_revert":   "reverting",
    "weak_revert":       "reverting",
    "random_quiet":      "stable",
    "random_volatile":   "chaotic",
    "chaotic_trend":     "chaotic",
    "chaotic_flat":      "chaotic",
    "divergent":         "chaotic",
    "insufficient_data": "stable",
}

_REQUIRED_TOP = {"name", "display", "session", "evaluation", "risk", "exits", "unlock", "tier"}

# Required sub-keys per section
_REQUIRED_SESSION    = {"duration_hours"}
_REQUIRED_EVALUATION = {"scope", "consensus_threshold", "directions", "min_regime"}
_REQUIRED_RISK       = {
    "max_positions", "position_size_pct", "stop_loss_pct",
    "reserve_pct", "max_daily_loss_pct", "max_hold_hours", "entry_end_action",
}
_REQUIRED_EXITS      = {
    "trailing_stop", "trailing_activation_pct", "trailing_distance_pct",
    "regime_shift_exit", "time_exit",
}
_REQUIRED_UNLOCK     = {"score_minimum"}

_VALID_TIERS         = {"free", "pro", "scale"}
_VALID_DIRECTIONS    = {"long", "short"}
_VALID_REGIMES       = {"trending", "stable", "reverting", "chaotic"}
_VALID_ENTRY_END     = {"hold", "close"}

_VALID_SCOPES = {
    "top_20", "top_50", "top_100", "top_200",
}


def _validate_raw(raw: dict, path: Path) -> None:
    """Raise ValueError with a clear message if the YAML is malformed or incomplete."""
    label = path.name

    missing_top = _REQUIRED_TOP - set(raw.keys())
    if missing_top:
        raise ValueError(f"[{label}] Missing top-level keys: {sorted(missing_top)}")

    # Section validators
    _check_section(raw["session"],    _REQUIRED_SESSION,    label, "session")
    _check_section(raw["evaluation"], _REQUIRED_EVALUATION, label, "evaluation")
    _check_section(raw["risk"],       _REQUIRED_RISK,       label, "risk")
    _check_section(raw["exits"],      _REQUIRED_EXITS,      label, "exits")
    _check_section(raw["unlock"],     _REQUIRED_UNLOCK,     label, "unlock")

    # Value constraints
    tier = raw.get("tier", "")
    if tier not in _VALID_TIERS:
        raise ValueError(f"[{label}] tier must be one of {_VALID_TIERS}, got: {tier!r}")

    eval_cfg = raw["evaluation"]
    threshold = eval_cfg.get("consensus_threshold", 0)
    if not (1 <= int(threshold) <= 7):
        raise ValueError(f"[{label}] evaluation.consensus_threshold must be 1-7, got: {threshold}")

    scope = eval_cfg.get("scope", "")
    if scope not in _VALID_SCOPES:
        raise ValueError(f"[{label}] evaluation.scope must be one of {_VALID_SCOPES}, got: {scope!r}")

    directions = eval_cfg.get("directions", [])
    if not isinstance(directions, list) or not directions:
        raise ValueError(f"[{label}] evaluation.directions must be a non-empty list")
    bad_dirs = set(d.lower() for d in directions) - _VALID_DIRECTIONS
    if bad_dirs:
        raise ValueError(f"[{label}] Invalid directions: {bad_dirs}. Valid: {_VALID_DIRECTIONS}")

    regimes = eval_cfg.get("min_regime", [])
    if not isinstance(regimes, list):
        raise ValueError(f"[{label}] evaluation.min_regime must be a list")
    bad_reg = set(r.lower() for r in regimes) - _VALID_REGIMES
    if bad_reg:
        raise ValueError(f"[{label}] Invalid regimes: {bad_reg}. Valid: {_VALID_REGIMES}")

    risk_cfg = raw["risk"]
    entry_end = risk_cfg.get("entry_end_action", "")
    if entry_end not in _VALID_ENTRY_END:
        raise ValueError(
            f"[{label}] risk.entry_end_action must be one of {_VALID_ENTRY_END}, got: {entry_end!r}"
        )

    max_pos = risk_cfg.get("max_positions", -1)
    if int(max_pos) < 0:
        raise ValueError(f"[{label}] risk.max_positions must be >= 0, got: {max_pos}")

    for pct_field in ("position_size_pct", "stop_loss_pct", "reserve_pct", "max_daily_loss_pct"):
        val = float(risk_cfg.get(pct_field, -1))
        if val < 0:
            raise ValueError(f"[{label}] risk.{pct_field} must be >= 0, got: {val}")

    duration = int(raw["session"].get("duration_hours", 0))
    if duration <= 0:
        raise ValueError(f"[{label}] session.duration_hours must be > 0, got: {duration}")

    max_hold = int(risk_cfg.get("max_hold_hours", 0))
    if max_hold <= 0:
        raise ValueError(f"[{label}] risk.max_hold_hours must be > 0, got: {max_hold}")

    name = raw.get("name", "")
    if not name or not isinstance(name, str):
        raise ValueError(f"[{label}] name must be a non-empty string")


def _check_section(section: dict, required: set, label: str, section_name: str) -> None:
    if not isinstance(section, dict):
        raise ValueError(f"[{label}] '{section_name}' must be a mapping/dict")
    missing = required - set(section.keys())
    if missing:
        raise ValueError(f"[{label}] Missing keys in '{section_name}': {sorted(missing)}")


# ─── PARSING ─────────────────────────────────────────────────────────────────

def _parse_modes(raw_modes: Optional[dict]) -> dict[str, ModeConfig]:
    """Parse modes section from YAML. Returns defaults if not present."""
    if not raw_modes:
        return dict(_DEFAULT_MODES)
    modes = {}
    for mode_name in VALID_MODES:
        if mode_name in raw_modes:
            m = raw_modes[mode_name]
            heat_interval = m.get("heat_push_interval_hours")
            if heat_interval is not None:
                heat_interval = float(heat_interval)
            modes[mode_name] = ModeConfig(
                push_on=list(m.get("push_on", [])),
                approval_required=bool(m.get("approval_required", False)),
                approval_timeout_seconds=int(m.get("approval_timeout_seconds", 0)),
                heat_push_interval_hours=heat_interval,
                approaching_push=bool(m.get("approaching_push", False)),
            )
        else:
            modes[mode_name] = _DEFAULT_MODES[mode_name]
    return modes


def _parse(raw: dict) -> StrategyConfig:
    """Convert raw YAML dict → StrategyConfig dataclass (after validation)."""
    ev = raw["evaluation"]
    rk = raw["risk"]
    ex = raw["exits"]

    return StrategyConfig(
        name    = raw["name"],
        display = raw["display"],
        tier    = raw["tier"],

        session = SessionConfig(
            duration_hours = int(raw["session"]["duration_hours"]),
        ),

        evaluation = EvaluationConfig(
            scope               = ev["scope"],
            consensus_threshold = int(ev["consensus_threshold"]),
            directions          = [d.lower() for d in ev["directions"]],
            min_regime          = [r.lower() for r in ev["min_regime"]],
        ),

        risk = RiskConfig(
            max_positions      = int(rk["max_positions"]),
            position_size_pct  = float(rk["position_size_pct"]),
            stop_loss_pct      = float(rk["stop_loss_pct"]),
            reserve_pct        = float(rk["reserve_pct"]),
            max_daily_loss_pct = float(rk["max_daily_loss_pct"]),
            max_hold_hours     = int(rk["max_hold_hours"]),
            entry_end_action   = rk["entry_end_action"],
        ),

        exits = ExitsConfig(
            trailing_stop          = bool(ex["trailing_stop"]),
            trailing_activation_pct= float(ex["trailing_activation_pct"]),
            trailing_distance_pct  = float(ex["trailing_distance_pct"]),
            regime_shift_exit      = bool(ex["regime_shift_exit"]),
            time_exit              = bool(ex["time_exit"]),
        ),

        unlock = UnlockConfig(
            score_minimum = float(raw["unlock"]["score_minimum"]),
        ),
        modes = _parse_modes(raw.get("modes")),
    )


# ─── PUBLIC API ──────────────────────────────────────────────────────────────

def load_strategy(name: str, strategies_dir: Optional[Path] = None) -> StrategyConfig:
    """Load and validate a strategy by name. Raises ValueError if not found or invalid.

    Args:
        name:           Strategy name (e.g. "momentum", "degen")
        strategies_dir: Override default strategies/ directory (for tests)

    Returns:
        StrategyConfig dataclass.

    Raises:
        FileNotFoundError: Strategy YAML does not exist.
        ValueError:        YAML is malformed or fails validation.
    """
    dir_ = strategies_dir or STRATEGIES_DIR
    path = dir_ / f"{name}.yaml"
    if not path.exists():
        available = list_strategies(dir_)
        raise FileNotFoundError(
            f"Strategy '{name}' not found at {path}. Available: {available}"
        )
    text = path.read_text()
    raw  = _load_yaml(text)
    _validate_raw(raw, path)
    return _parse(raw)


def list_strategies(strategies_dir: Optional[Path] = None) -> list[str]:
    """Return sorted list of available strategy names (without .yaml extension)."""
    dir_ = strategies_dir or STRATEGIES_DIR
    if not dir_.exists():
        return []
    return sorted(p.stem for p in dir_.glob("*.yaml"))


def get_mode_config(strategy: str, mode: str, strategies_dir: Optional[Path] = None) -> ModeConfig:
    """Load a strategy and return its ModeConfig for the given drive mode.

    Raises:
        FileNotFoundError: Strategy doesn't exist.
        ValueError:        Invalid mode name or YAML is malformed.
    """
    cfg = load_strategy(strategy, strategies_dir=strategies_dir)
    return cfg.get_mode_config(mode)


def load_all_strategies(strategies_dir: Optional[Path] = None) -> dict[str, StrategyConfig]:
    """Load every strategy in the directory. Skips malformed configs with a warning."""
    dir_ = strategies_dir or STRATEGIES_DIR
    configs: dict[str, StrategyConfig] = {}
    for name in list_strategies(dir_):
        try:
            configs[name] = load_strategy(name, dir_)
        except Exception as exc:
            import sys
            print(f"[strategy_loader] WARN: skipping '{name}': {exc}", file=sys.stderr)
    return configs


# Active strategy state file (written by session_manager or controller)
_ACTIVE_STRATEGY_FILE = Path(__file__).parent / "bus" / "active_strategy.json"


def get_active_strategy(bus_dir: Optional[Path] = None) -> Optional[StrategyConfig]:
    """Read the currently active strategy from bus/active_strategy.json.

    Returns None if no strategy is active (idle / watch-only state).
    Falls back gracefully if the file is missing or malformed.
    """
    active_file = (bus_dir / "active_strategy.json") if bus_dir else _ACTIVE_STRATEGY_FILE
    if not active_file.exists():
        return None
    try:
        raw_json = json.loads(active_file.read_text())
        name = raw_json.get("strategy")
        if not name:
            return None
        return load_strategy(name)
    except Exception as exc:
        import sys
        print(f"[strategy_loader] WARN: could not load active strategy: {exc}", file=sys.stderr)
        return None


def set_active_strategy(name: str, bus_dir: Optional[Path] = None) -> StrategyConfig:
    """Activate a strategy by writing bus/active_strategy.json.

    Validates the strategy before writing.

    Returns:
        The loaded StrategyConfig.

    Raises:
        FileNotFoundError / ValueError: if strategy is invalid.
    """
    from datetime import datetime, timezone
    cfg = load_strategy(name)                         # validates before writing
    active_file = (bus_dir / "active_strategy.json") if bus_dir else _ACTIVE_STRATEGY_FILE
    active_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "strategy":   name,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "display":    cfg.display,
        "tier":       cfg.tier,
    }
    active_file.write_text(json.dumps(payload, indent=2))
    return cfg


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys

    args = _sys.argv[1:]

    if not args or args[0] == "list":
        strategies = list_strategies()
        if not strategies:
            print(f"No strategies found in {STRATEGIES_DIR}")
        else:
            print(f"Available strategies ({len(strategies)}):")
            for s in strategies:
                try:
                    cfg = load_strategy(s)
                    print(f"  {cfg.name:12s} | {cfg.tier:5s} | {cfg.display}")
                except Exception as e:
                    print(f"  {s:12s} | ERROR: {e}")

    elif args[0] == "validate":
        names = args[1:] or list_strategies()
        errors = 0
        for name in names:
            try:
                cfg = load_strategy(name)
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")
                errors += 1
        _sys.exit(errors)

    elif args[0] == "show" and len(args) >= 2:
        name = args[1]
        try:
            cfg = load_strategy(name)
            print(f"Strategy: {cfg.display} ({cfg.tier})")
            print(f"  Session: {cfg.session.duration_hours}h")
            print(f"  Scope: {cfg.evaluation.scope} | Consensus: {cfg.evaluation.consensus_threshold}/7")
            print(f"  Directions: {cfg.evaluation.directions}")
            print(f"  Min regime: {cfg.evaluation.min_regime}")
            print(f"  Max positions: {cfg.risk.max_positions}")
            print(f"  Position size: {cfg.risk.position_size_pct}%")
            print(f"  Stop loss: {cfg.risk.stop_loss_pct}%")
            print(f"  Reserve: {cfg.risk.reserve_pct}%")
            print(f"  Max daily loss: {cfg.risk.max_daily_loss_pct}%")
            print(f"  Max hold: {cfg.risk.max_hold_hours}h")
            print(f"  Entry-end action: {cfg.risk.entry_end_action}")
            print(f"  Trailing stop: {cfg.exits.trailing_stop} "
                  f"(+{cfg.exits.trailing_activation_pct}% → trail {cfg.exits.trailing_distance_pct}%)")
            print(f"  Unlock score: {cfg.unlock.score_minimum}")
        except Exception as e:
            print(f"Error: {e}")
            _sys.exit(1)

    else:
        print("Usage:")
        print("  python strategy_loader.py list")
        print("  python strategy_loader.py validate [name ...]")
        print("  python strategy_loader.py show <name>")

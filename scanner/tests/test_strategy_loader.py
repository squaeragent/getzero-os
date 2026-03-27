"""
Strategy Loader tests.

Tests:
  - All 9 strategy YAML files load and validate cleanly
  - Required fields are present and correctly typed
  - Missing fields raise ValueError with a clear message
  - Malformed values (bad tier, out-of-range consensus) raise ValueError
  - list_strategies() returns exactly 9 strategies
  - load_all_strategies() returns all 9 without exceptions
  - Helper methods on StrategyConfig work correctly
  - Watch strategy correctly identifies itself as watch-only
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.strategy_loader import (
    load_strategy,
    list_strategies,
    load_all_strategies,
    STRATEGIES_DIR,
)

# ─── ALL 9 STRATEGIES EXIST AND LOAD ──────────────────────────────────────────

EXPECTED_STRATEGIES = {
    "momentum", "defense", "watch", "scout",
    "funding", "degen", "sniper", "fade", "apex",
}


def test_all_9_strategies_listed():
    """list_strategies() returns exactly the 9 expected strategies."""
    found = set(list_strategies())
    assert found == EXPECTED_STRATEGIES, (
        f"Missing: {EXPECTED_STRATEGIES - found} | Extra: {found - EXPECTED_STRATEGIES}"
    )


def test_all_9_strategies_load():
    """Every strategy loads without raising exceptions."""
    configs = load_all_strategies()
    missing = EXPECTED_STRATEGIES - set(configs.keys())
    assert not missing, f"Failed to load: {missing}"
    assert len(configs) == 9


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_strategy_loads(name):
    """Each strategy loads individually and has correct name."""
    cfg = load_strategy(name)
    assert cfg.name == name


# ─── REQUIRED FIELDS PRESENT ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_required_risk_fields(name):
    """Every strategy has all required risk fields with valid types."""
    cfg = load_strategy(name)
    r = cfg.risk
    assert isinstance(r.max_positions, int)
    assert isinstance(r.position_size_pct, float)
    assert isinstance(r.stop_loss_pct, float)
    assert isinstance(r.reserve_pct, float)
    assert isinstance(r.max_daily_loss_pct, float)
    assert isinstance(r.max_hold_hours, int)
    assert r.entry_end_action in ("hold", "close")


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_required_evaluation_fields(name):
    """Every strategy has valid evaluation config."""
    cfg = load_strategy(name)
    ev = cfg.evaluation
    assert ev.scope.startswith("top_"), f"{name}: scope should start with 'top_'"
    assert 1 <= ev.consensus_threshold <= 7, f"{name}: threshold out of range"
    assert len(ev.directions) >= 1
    assert len(ev.min_regime) >= 1


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_required_exits_fields(name):
    """Every strategy has valid exits config."""
    cfg = load_strategy(name)
    ex = cfg.exits
    assert isinstance(ex.trailing_stop, bool)
    assert isinstance(ex.trailing_activation_pct, float)
    assert isinstance(ex.trailing_distance_pct, float)
    assert isinstance(ex.regime_shift_exit, bool)
    assert isinstance(ex.time_exit, bool)


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_session_duration_positive(name):
    """Session duration must be positive."""
    cfg = load_strategy(name)
    assert cfg.session.duration_hours > 0, f"{name}: duration must be > 0"


@pytest.mark.parametrize("name", sorted(EXPECTED_STRATEGIES))
def test_max_hold_hours_positive(name):
    """max_hold_hours must be positive (watch has max_positions=0 but still needs hold time)."""
    cfg = load_strategy(name)
    # Watch is the exception — it has 0 positions but still has a session duration
    if cfg.name == "watch":
        assert cfg.risk.max_positions == 0
    else:
        assert cfg.risk.max_hold_hours > 0, f"{name}: max_hold_hours must be > 0"


# ─── TIER VALUES ──────────────────────────────────────────────────────────────

def test_free_strategies():
    """Momentum, Defense, Watch are free tier."""
    for name in ("momentum", "defense", "watch"):
        cfg = load_strategy(name)
        assert cfg.tier == "free", f"{name}: expected tier=free, got {cfg.tier}"
        assert cfg.unlock.score_minimum == 0, f"{name}: free should have score_minimum=0"


def test_pro_strategies():
    """Scout, Funding, Degen are pro tier."""
    for name in ("scout", "funding", "degen"):
        cfg = load_strategy(name)
        assert cfg.tier == "pro", f"{name}: expected tier=pro, got {cfg.tier}"


def test_scale_strategies():
    """Sniper, Fade, Apex are scale tier."""
    for name in ("sniper", "fade", "apex"):
        cfg = load_strategy(name)
        assert cfg.tier == "scale", f"{name}: expected tier=scale, got {cfg.tier}"


# ─── UNLOCK SCORES ────────────────────────────────────────────────────────────

def test_unlock_scores():
    """Verify unlock score thresholds match spec."""
    expected = {
        "momentum": 0.0,
        "defense":  0.0,
        "watch":    0.0,
        "scout":    4.0,
        "funding":  5.0,
        "degen":    6.0,
        "sniper":   5.0,
        "fade":     6.0,
        "apex":     7.0,
    }
    for name, score in expected.items():
        cfg = load_strategy(name)
        assert cfg.unlock.score_minimum == score, (
            f"{name}: expected unlock score {score}, got {cfg.unlock.score_minimum}"
        )


# ─── SPEC-SPECIFIC VALUES ─────────────────────────────────────────────────────

def test_momentum_spec_values():
    """Momentum strategy exactly matches the spec YAML."""
    cfg = load_strategy("momentum")
    assert cfg.session.duration_hours == 48
    assert cfg.evaluation.scope == "top_50"
    assert cfg.evaluation.consensus_threshold == 5
    assert "long" in cfg.evaluation.directions
    assert "short" in cfg.evaluation.directions
    assert "trending" in cfg.evaluation.min_regime
    assert "stable" in cfg.evaluation.min_regime
    assert cfg.risk.max_positions == 5
    assert cfg.risk.position_size_pct == 10.0
    assert cfg.risk.stop_loss_pct == 3.0
    assert cfg.risk.reserve_pct == 20.0
    assert cfg.risk.max_daily_loss_pct == 5.0
    assert cfg.risk.max_hold_hours == 48
    assert cfg.risk.entry_end_action == "hold"
    assert cfg.exits.trailing_stop is True
    assert cfg.exits.trailing_activation_pct == 1.5
    assert cfg.exits.trailing_distance_pct == 1.0
    assert cfg.exits.regime_shift_exit is True
    assert cfg.exits.time_exit is True


def test_sniper_7_of_7_consensus():
    """Sniper requires 7/7 — maximum selectivity."""
    cfg = load_strategy("sniper")
    assert cfg.evaluation.consensus_threshold == 7
    assert cfg.evaluation.scope == "top_20"
    assert cfg.tier == "scale"


def test_watch_is_observe_only():
    """Watch strategy has max_positions=0 — no trades."""
    cfg = load_strategy("watch")
    assert cfg.risk.max_positions == 0
    assert cfg.risk.reserve_pct == 100.0
    assert cfg.is_watch_only()


def test_defense_top_20():
    """Defense scans only top_20 coins."""
    cfg = load_strategy("defense")
    assert cfg.evaluation.scope == "top_20"
    assert cfg.evaluation.consensus_threshold == 6


def test_scout_top_200():
    """Scout scans the widest universe — top_200."""
    cfg = load_strategy("scout")
    assert cfg.evaluation.scope == "top_200"


def test_apex_extreme_risk():
    """Apex has the most aggressive risk params — within 80% hard cap."""
    cfg = load_strategy("apex")
    assert cfg.risk.max_positions >= 3
    assert cfg.risk.position_size_pct >= 15
    assert cfg.risk.max_daily_loss_pct >= 12
    assert cfg.unlock.score_minimum == 7.0
    # Verify total allocation stays within 80% hard cap
    total = cfg.risk.max_positions * cfg.risk.position_size_pct + cfg.risk.reserve_pct
    assert total <= 80, f"Apex over-allocated: {total}% > 80% hard cap"


def test_funding_entry_end_close():
    """Funding strategy closes on entry-end — signal-driven, not momentum."""
    cfg = load_strategy("funding")
    assert cfg.risk.entry_end_action == "close"


def test_fade_reverting_regime():
    """Fade strategy targets reverting market regime."""
    cfg = load_strategy("fade")
    assert "reverting" in cfg.evaluation.min_regime


def test_degen_24h_session():
    """Degen is the shortest session — 24h sprint."""
    cfg = load_strategy("degen")
    assert cfg.session.duration_hours == 24
    assert cfg.risk.max_hold_hours == 24


# ─── HELPER METHODS ───────────────────────────────────────────────────────────

class TestHelperMethods:
    """StrategyConfig helper methods work correctly."""

    def test_allows_direction(self):
        cfg = load_strategy("momentum")
        assert cfg.allows_direction("long")
        assert cfg.allows_direction("LONG")
        assert cfg.allows_direction("short")
        assert cfg.allows_direction("SHORT")

    def test_allows_regime(self):
        cfg = load_strategy("momentum")
        assert cfg.allows_regime("trending")
        assert cfg.allows_regime("stable")
        assert not cfg.allows_regime("chaotic")
        assert not cfg.allows_regime("reverting")

    def test_reserve_usd(self):
        cfg = load_strategy("momentum")
        # 20% of $100 = $20
        assert cfg.reserve_usd(100.0) == pytest.approx(20.0)

    def test_max_position_usd(self):
        cfg = load_strategy("momentum")
        # 10% of $100 = $10
        assert cfg.max_position_usd(100.0) == pytest.approx(10.0)

    def test_daily_loss_limit_usd(self):
        cfg = load_strategy("momentum")
        # 5% of $100 = $5
        assert cfg.daily_loss_limit_usd(100.0) == pytest.approx(5.0)

    def test_watch_is_watch_only(self):
        cfg = load_strategy("watch")
        assert cfg.is_watch_only()

    def test_momentum_not_watch_only(self):
        cfg = load_strategy("momentum")
        assert not cfg.is_watch_only()


# ─── VALIDATION ERRORS ────────────────────────────────────────────────────────

class TestValidationErrors:
    """Malformed YAML configs raise ValueError with clear messages."""

    def _write_yaml(self, tmp_path: Path, name: str, content: str) -> Path:
        d = tmp_path / "strategies"
        d.mkdir(exist_ok=True)
        (d / f"{name}.yaml").write_text(content)
        return d

    def test_missing_top_level_key(self, tmp_path):
        """Missing a required top-level key raises ValueError."""
        d = self._write_yaml(tmp_path, "bad", """
name: bad
display: "Bad"
# missing: session, evaluation, risk, exits, unlock, tier
""")
        with pytest.raises(ValueError, match="Missing top-level keys"):
            load_strategy("bad", d)

    def test_bad_tier(self, tmp_path):
        """Invalid tier value raises ValueError."""
        d = self._write_yaml(tmp_path, "bad_tier", """
name: bad_tier
display: "Bad Tier"
tier: ultra
session:
  duration_hours: 48
evaluation:
  scope: top_50
  consensus_threshold: 5
  directions: [long]
  min_regime: [trending]
risk:
  max_positions: 3
  position_size_pct: 10
  stop_loss_pct: 3
  reserve_pct: 20
  max_daily_loss_pct: 5
  max_hold_hours: 48
  entry_end_action: hold
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
""")
        with pytest.raises(ValueError, match="tier"):
            load_strategy("bad_tier", d)

    def test_consensus_out_of_range(self, tmp_path):
        """Consensus threshold > 7 raises ValueError."""
        d = self._write_yaml(tmp_path, "bad_consensus", """
name: bad_consensus
display: "Bad Consensus"
tier: free
session:
  duration_hours: 48
evaluation:
  scope: top_50
  consensus_threshold: 9
  directions: [long]
  min_regime: [trending]
risk:
  max_positions: 3
  position_size_pct: 10
  stop_loss_pct: 3
  reserve_pct: 20
  max_daily_loss_pct: 5
  max_hold_hours: 48
  entry_end_action: hold
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
""")
        with pytest.raises(ValueError, match="consensus_threshold"):
            load_strategy("bad_consensus", d)

    def test_invalid_entry_end_action(self, tmp_path):
        """Invalid entry_end_action raises ValueError."""
        d = self._write_yaml(tmp_path, "bad_action", """
name: bad_action
display: "Bad Action"
tier: free
session:
  duration_hours: 48
evaluation:
  scope: top_50
  consensus_threshold: 5
  directions: [long]
  min_regime: [trending]
risk:
  max_positions: 3
  position_size_pct: 10
  stop_loss_pct: 3
  reserve_pct: 20
  max_daily_loss_pct: 5
  max_hold_hours: 48
  entry_end_action: wait
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
""")
        with pytest.raises(ValueError, match="entry_end_action"):
            load_strategy("bad_action", d)

    def test_invalid_scope(self, tmp_path):
        """Invalid scope raises ValueError."""
        d = self._write_yaml(tmp_path, "bad_scope", """
name: bad_scope
display: "Bad Scope"
tier: free
session:
  duration_hours: 48
evaluation:
  scope: top_999
  consensus_threshold: 5
  directions: [long]
  min_regime: [trending]
risk:
  max_positions: 3
  position_size_pct: 10
  stop_loss_pct: 3
  reserve_pct: 20
  max_daily_loss_pct: 5
  max_hold_hours: 48
  entry_end_action: hold
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
""")
        with pytest.raises(ValueError, match="scope"):
            load_strategy("bad_scope", d)

    def test_missing_risk_field(self, tmp_path):
        """Missing a risk sub-key raises ValueError."""
        d = self._write_yaml(tmp_path, "bad_risk", """
name: bad_risk
display: "Bad Risk"
tier: free
session:
  duration_hours: 48
evaluation:
  scope: top_50
  consensus_threshold: 5
  directions: [long]
  min_regime: [trending]
risk:
  max_positions: 3
  # missing: position_size_pct, stop_loss_pct, etc.
exits:
  trailing_stop: true
  trailing_activation_pct: 1.5
  trailing_distance_pct: 1.0
  regime_shift_exit: true
  time_exit: true
unlock:
  score_minimum: 0
""")
        with pytest.raises(ValueError, match="risk"):
            load_strategy("bad_risk", d)

    def test_not_found(self):
        """FileNotFoundError when strategy does not exist."""
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            load_strategy("nonexistent")

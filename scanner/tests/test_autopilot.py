"""Tests for auto-pilot strategy selection — scoring, regime matching, history, plan gating, card."""

import json
import struct
import pytest
from unittest.mock import MagicMock, patch

from scanner.v6.autopilot import (
    AutoPilot,
    AutoPilotDecision,
    REGIME_STRATEGY_MAP,
    FEAR_GREED_MODIFIERS,
    VOLATILITY_MODIFIERS,
    ALL_STRATEGIES,
)


# ── Test helpers ─────────────────────────────────────────────────────────────

def _make_coin(coin, direction="SHORT", consensus=5, regime="strong_trend", funding_passed=True):
    """Helper to build a heat coin entry."""
    return {
        "coin": coin,
        "direction": direction,
        "consensus": consensus,
        "conviction": consensus / 7,
        "regime": regime,
        "price": 100.0,
        "layers": [
            {"layer": "regime", "passed": True, "value": regime},
            {"layer": "technical", "passed": consensus >= 3, "value": {}},
            {"layer": "funding", "passed": funding_passed, "value": 0.0001},
            {"layer": "book", "passed": consensus >= 4, "value": {}},
            {"layer": "OI", "passed": consensus >= 2, "value": 1000},
            {"layer": "macro", "passed": consensus >= 5, "value": 30},
            {"layer": "collective", "passed": consensus >= 6, "value": {}},
        ],
    }


def _heat(coins):
    return {"coins": coins, "count": len(coins), "timestamp": "2026-03-27T12:00:00Z"}


def _brief(fear_greed=50, approaching=None):
    return {
        "fear_greed": fear_greed,
        "positions": [],
        "open_positions": 0,
        "session": {"active": False},
        "approaching": approaching or [],
    }


def _make_history(strategy, wins, losses, count=5):
    """Generate session history entries for a strategy."""
    entries = []
    for i in range(count):
        w = 1 if i < wins else 0
        l = 1 if i >= wins and i < wins + losses else 0
        entries.append({
            "strategy": strategy,
            "wins": w,
            "losses": l,
            "trade_count": w + l,
            "total_pnl_usd": 10.0 if w else -5.0,
        })
    return entries


def _mock_api(regime_data=None, history=None, plan="scale"):
    """Create a mock API for AutoPilot."""
    api = MagicMock()

    # Default regime: SHORT market, extreme fear, high volatility
    if regime_data is None:
        coins = [_make_coin(f"C{i}", "SHORT") for i in range(14)]
        coins += [_make_coin(f"N{i}", "NONE") for i in range(6)]
        regime_data = {"heat": _heat(coins), "brief": _brief(fear_greed=15)}

    api.get_heat.return_value = regime_data["heat"]
    api.get_brief.return_value = regime_data["brief"]
    api.session_history.return_value = {"sessions": history or [], "count": len(history or [])}

    return api


@pytest.fixture
def short_regime():
    """SHORT market, extreme fear, high volatility."""
    coins = [_make_coin(f"C{i}", "SHORT", regime="strong_trend") for i in range(14)]
    coins += [_make_coin(f"N{i}", "NONE") for i in range(6)]
    return {"heat": _heat(coins), "brief": _brief(fear_greed=15)}


@pytest.fixture
def quiet_regime():
    """QUIET market, neutral sentiment."""
    coins = [_make_coin(f"S{i}", "SHORT") for i in range(2)]
    coins += [_make_coin(f"N{i}", "NONE") for i in range(38)]
    return {"heat": _heat(coins), "brief": _brief(fear_greed=50)}


@pytest.fixture
def mixed_regime():
    """MIXED market."""
    coins = [_make_coin(f"S{i}", "SHORT") for i in range(6)]
    coins += [_make_coin(f"L{i}", "LONG") for i in range(5)]
    coins += [_make_coin(f"N{i}", "NONE") for i in range(9)]
    return {"heat": _heat(coins), "brief": _brief(fear_greed=50)}


@pytest.fixture
def extreme_greed_regime():
    """LONG market, extreme greed."""
    coins = [_make_coin(f"L{i}", "LONG") for i in range(15)]
    coins += [_make_coin(f"N{i}", "NONE") for i in range(5)]
    return {"heat": _heat(coins), "brief": _brief(fear_greed=90)}


@pytest.fixture
def high_vol_regime():
    """HIGH volatility market."""
    coins = [_make_coin(f"C{i}", "SHORT", regime="chaotic_trend") for i in range(6)]
    coins += [_make_coin(f"N{i}", "NONE", regime="random_quiet") for i in range(14)]
    return {"heat": _heat(coins), "brief": _brief(fear_greed=50)}


# ── Regime-strategy matching ────────────────────────────────────────────────

class TestRegimeMatching:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_short_regime_prefers_momentum(self, _mock_plan, short_regime):
        """SHORT regime should prefer momentum."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        # momentum should be top pick for SHORT (regime +30 + extreme_fear +10)
        assert decision.strategy == "momentum"

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_quiet_regime_prefers_defense(self, _mock_plan, quiet_regime):
        """QUIET regime should prefer defense."""
        api = _mock_api(quiet_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert decision.strategy == "defense"

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_mixed_regime_prefers_defense(self, _mock_plan, mixed_regime):
        """MIXED regime should prefer defense."""
        api = _mock_api(mixed_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert decision.strategy == "defense"


# ── Fear & greed modifiers ──────────────────────────────────────────────────

class TestFearGreedModifiers:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_extreme_fear_short_boosts_degen(self, _mock_plan, short_regime):
        """Extreme fear + SHORT → degen gets +20 bonus."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        score_degen = pilot._score_strategy("degen", regime, [], "scale")
        # degen: base 50 + regime +20 (2nd in SHORT) + extreme_fear +20 + LOW vol -20 = 70
        assert score_degen >= 70

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_extreme_greed_boosts_defense(self, _mock_plan, extreme_greed_regime):
        """Extreme greed should boost defense."""
        api = _mock_api(extreme_greed_regime)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        score_defense = pilot._score_strategy("defense", regime, [], "scale")
        # defense: extreme_greed +25 = 75
        assert score_defense >= 70


# ── Volatility modifiers ────────────────────────────────────────────────────

class TestVolatilityModifiers:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_high_vol_boosts_sniper(self, _mock_plan, high_vol_regime):
        """HIGH volatility should boost sniper."""
        api = _mock_api(high_vol_regime)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        score = pilot._score_strategy("sniper", regime, [], "scale")
        # sniper: HIGH vol +15 = 65
        assert score >= 60

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_extreme_vol_boosts_defense(self, _mock_plan):
        """EXTREME volatility should strongly boost defense."""
        coins = [_make_coin(f"C{i}", "SHORT", regime="chaotic_trend") for i in range(10)]
        coins += [_make_coin(f"N{i}", "NONE", regime="random_quiet") for i in range(10)]
        regime_data = {"heat": _heat(coins), "brief": _brief(fear_greed=50)}
        api = _mock_api(regime_data)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        score = pilot._score_strategy("defense", regime, [], "scale")
        # defense: EXTREME vol +30 = 80
        assert score >= 75


# ── Operator history ────────────────────────────────────────────────────────

class TestOperatorHistory:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_high_wr_boosts(self, _mock_plan, short_regime):
        """WR > 65% should give +20 bonus."""
        history = _make_history("momentum", wins=4, losses=1, count=5)
        api = _mock_api(short_regime, history=history)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        bonus = pilot._history_bonus("momentum", history)
        assert bonus == 20

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_low_wr_penalizes(self, _mock_plan, short_regime):
        """WR < 35% should give -20 penalty."""
        history = _make_history("momentum", wins=1, losses=4, count=5)
        api = _mock_api(short_regime, history=history)
        pilot = AutoPilot(api)
        bonus = pilot._history_bonus("momentum", history)
        assert bonus == -20

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_empty_history_no_bonus(self, _mock_plan, short_regime):
        """Empty history should give 0 bonus."""
        api = _mock_api(short_regime, history=[])
        pilot = AutoPilot(api)
        bonus = pilot._history_bonus("momentum", [])
        assert bonus == 0


# ── Plan gating ─────────────────────────────────────────────────────────────

class TestPlanGating:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="free")
    def test_free_plan_cannot_select_pro(self, _mock_plan, short_regime):
        """Free plan operator shouldn't get pro/scale strategies."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        # Free plan: only momentum, defense, watch
        assert decision.strategy in ("momentum", "defense", "watch")

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="free")
    def test_free_plan_scores_pro_as_zero(self, _mock_plan, short_regime):
        """Pro strategies should score 0 on free plan."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        regime = pilot._get_regime("op_default")
        for strat in ["degen", "scout", "funding", "sniper", "fade", "apex"]:
            score = pilot._score_strategy(strat, regime, [], "free")
            assert score == 0, f"{strat} should be 0 on free plan"


# ── Decision output ─────────────────────────────────────────────────────────

class TestDecisionOutput:
    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_alternatives_has_entries(self, _mock_plan, short_regime):
        """Decision should have at least 2 alternatives."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert len(decision.alternatives) >= 2

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_confidence_between_0_and_1(self, _mock_plan, short_regime):
        """Confidence should be between 0.0 and 1.0."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert 0.0 <= decision.confidence <= 1.0

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_reason_is_nonempty(self, _mock_plan, short_regime):
        """Reason should be a non-empty string."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_to_dict_serializable(self, _mock_plan, short_regime):
        """to_dict() should produce JSON-serializable output."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        d = decision.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        assert "strategy" in d
        assert "confidence" in d
        assert "alternatives" in d

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_regime_field_populated(self, _mock_plan, short_regime):
        """Decision.regime should reflect current regime direction."""
        api = _mock_api(short_regime)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert decision.regime in ("SHORT", "LONG", "MIXED", "QUIET")

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_operator_wr_with_history(self, _mock_plan, short_regime):
        """Operator WR should be populated when history exists."""
        history = _make_history("momentum", wins=4, losses=1, count=5)
        api = _mock_api(short_regime, history=history)
        pilot = AutoPilot(api)
        decision = pilot.decide()
        # momentum is likely the pick; check WR is populated
        if decision.strategy == "momentum":
            assert decision.operator_wr is not None
            assert decision.operator_wr == 80.0  # 4/5

    @patch("scanner.v6.autopilot.AutoPilot._get_plan", return_value="scale")
    def test_operator_wr_none_without_history(self, _mock_plan, short_regime):
        """Operator WR should be None when no history."""
        api = _mock_api(short_regime, history=[])
        pilot = AutoPilot(api)
        decision = pilot.decide()
        assert decision.operator_wr is None


# ── Card rendering ──────────────────────────────────────────────────────────

def _is_png(data: bytes) -> bool:
    return data[:8] == b'\x89PNG\r\n\x1a\n'


def _png_dimensions(data: bytes) -> tuple:
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    return w, h


AUTOPILOT_DATA = {
    "strategy": "momentum",
    "confidence": 0.85,
    "reason": "SHORT regime favors momentum. extreme fear boosts momentum. your WR: 72%.",
    "regime": "SHORT",
    "operator_wr": 72.0,
    "backtest_pnl": None,
    "alternatives": [
        {"strategy": "degen", "score": 80, "reason": "SHORT regime favors degen.", "operator_wr": 60.0},
        {"strategy": "sniper", "score": 65, "reason": "HIGH volatility favors sniper.", "operator_wr": None},
        {"strategy": "defense", "score": 50, "reason": "balanced pick.", "operator_wr": 55.0},
    ],
}


@pytest.fixture(scope="module")
def renderer():
    from scanner.v6.cards.renderer import CardRenderer
    return CardRenderer()


class TestAutopilotCard:
    def test_renders(self, renderer):
        png = renderer.render("autopilot_card", AUTOPILOT_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("autopilot_card", AUTOPILOT_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_low_confidence(self, renderer):
        data = {**AUTOPILOT_DATA, "confidence": 0.3, "strategy": "defense"}
        png = renderer.render("autopilot_card", data)
        assert _is_png(png)

    def test_no_alternatives(self, renderer):
        data = {**AUTOPILOT_DATA, "alternatives": []}
        png = renderer.render("autopilot_card", data)
        assert _is_png(png)

    def test_no_wr(self, renderer):
        data = {**AUTOPILOT_DATA, "operator_wr": None}
        png = renderer.render("autopilot_card", data)
        assert _is_png(png)


class TestAutopilotEndpoints:
    """Test that autopilot endpoints are registered (import-level check)."""

    def test_card_api_has_autopilot_route(self):
        from scanner.v6.cards.card_api import router
        paths = [r.path for r in router.routes]
        assert any("autopilot" in p for p in paths)

    def test_mcp_has_auto_select_tool(self):
        from scanner.v6 import mcp_server
        assert hasattr(mcp_server, "zero_auto_select")
        assert callable(mcp_server.zero_auto_select)

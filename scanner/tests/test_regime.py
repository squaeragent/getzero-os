"""Tests for regime awareness — RegimeState, detect_shift, card rendering, endpoints."""

import json
import struct
import pytest

from scanner.v6.regime import RegimeState, detect_shift


# ── Test data ────────────────────────────────────────────────────────────────

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


# ── RegimeState.from_heat ────────────────────────────────────────────────────

class TestRegimeStateFromHeat:
    def test_short_dominant(self):
        """14 of 20 coins SHORT = >60% → SHORT dominant."""
        coins = [_make_coin(f"C{i}", "SHORT") for i in range(14)]
        coins += [_make_coin(f"N{i}", "NONE") for i in range(6)]
        state = RegimeState.from_heat(_heat(coins), _brief(fear_greed=15))
        assert state.dominant_direction == "SHORT"
        assert state.trending_short == 14
        assert state.trending_long == 0
        assert state.neutral == 6
        assert state.total == 20
        assert state.fear_greed == 15
        assert state.fear_greed_label == "EXTREME FEAR"
        assert "SHORT MARKET" in state.regime_label

    def test_long_dominant(self):
        """15 of 20 coins LONG → LONG dominant."""
        coins = [_make_coin(f"C{i}", "LONG") for i in range(15)]
        coins += [_make_coin(f"N{i}", "NONE") for i in range(5)]
        state = RegimeState.from_heat(_heat(coins), _brief(fear_greed=75))
        assert state.dominant_direction == "LONG"
        assert state.trending_long == 15
        assert state.fear_greed_label == "GREED"
        assert "LONG MARKET" in state.regime_label

    def test_mixed_direction(self):
        """6 short, 5 long, 9 neutral → MIXED."""
        coins = [_make_coin(f"S{i}", "SHORT") for i in range(6)]
        coins += [_make_coin(f"L{i}", "LONG") for i in range(5)]
        coins += [_make_coin(f"N{i}", "NONE") for i in range(9)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.dominant_direction == "MIXED"
        assert state.trending_short == 6
        assert state.trending_long == 5
        assert state.neutral == 9
        assert "MIXED" in state.regime_label

    def test_quiet_market(self):
        """<20% with direction → QUIET."""
        coins = [_make_coin(f"S{i}", "SHORT") for i in range(2)]
        coins += [_make_coin(f"N{i}", "NONE") for i in range(38)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.dominant_direction == "QUIET"
        assert "QUIET" in state.regime_label
        assert "patience" in state.regime_label

    def test_empty_coins(self):
        state = RegimeState.from_heat(_heat([]), _brief())
        assert state.dominant_direction == "QUIET"
        assert state.total == 0

    def test_fear_greed_boundaries(self):
        """Test all 5 F&G labels."""
        for val, label in [
            (10, "EXTREME FEAR"), (30, "FEAR"), (50, "NEUTRAL"),
            (70, "GREED"), (90, "EXTREME GREED"),
        ]:
            coins = [_make_coin("BTC", "NONE")]
            state = RegimeState.from_heat(_heat(coins), _brief(fear_greed=val))
            assert state.fear_greed_label == label, f"Expected {label} for val={val}"

    def test_funding_bias_shorts_paid(self):
        """Multiple short coins with passing funding → SHORTS PAID."""
        coins = [_make_coin(f"S{i}", "SHORT", funding_passed=True) for i in range(5)]
        coins += [_make_coin(f"L{i}", "LONG", funding_passed=False) for i in range(3)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.funding_bias == "SHORTS PAID"
        assert state.funding_paid_count == 5

    def test_funding_bias_longs_paid(self):
        coins = [_make_coin(f"L{i}", "LONG", funding_passed=True) for i in range(4)]
        coins += [_make_coin(f"S{i}", "SHORT", funding_passed=False) for i in range(2)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.funding_bias == "LONGS PAID"
        assert state.funding_paid_count == 4

    def test_funding_bias_neutral(self):
        coins = [_make_coin(f"S{i}", "SHORT", funding_passed=True) for i in range(1)]
        coins += [_make_coin(f"L{i}", "LONG", funding_passed=True) for i in range(1)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.funding_bias == "NEUTRAL"

    def test_volatility_extreme(self):
        """Many chaotic regimes → EXTREME."""
        coins = [_make_coin(f"C{i}", regime="chaotic_trend") for i in range(5)]
        coins += [_make_coin(f"N{i}", regime="random_quiet") for i in range(5)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.volatility == "EXTREME"

    def test_volatility_high(self):
        coins = [_make_coin(f"C{i}", regime="chaotic_flat") for i in range(3)]
        coins += [_make_coin(f"N{i}", regime="stable") for i in range(7)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.volatility == "HIGH"

    def test_volatility_low(self):
        coins = [_make_coin(f"N{i}", regime="random_quiet") for i in range(10)]
        state = RegimeState.from_heat(_heat(coins), _brief())
        assert state.volatility == "LOW"

    def test_approaching_count(self):
        approaching = [{"coin": "ETH"}, {"coin": "SOL"}]
        state = RegimeState.from_heat(_heat([]), _brief(approaching=approaching))
        assert state.approaching_count == 2

    def test_to_dict(self):
        coins = [_make_coin("BTC", "SHORT")]
        state = RegimeState.from_heat(_heat(coins), _brief())
        d = state.to_dict()
        assert isinstance(d, dict)
        assert "dominant_direction" in d
        assert "regime_label" in d
        assert "fear_greed" in d


# ── detect_shift ─────────────────────────────────────────────────────────────

class TestDetectShift:
    def _regime(self, dominant="MIXED", short=5, long=5, neutral=30,
                fg=50, funding="NEUTRAL"):
        return RegimeState(
            dominant_direction=dominant,
            trending_short=short, trending_long=long,
            neutral=neutral, total=short + long + neutral,
            approaching_count=0, fear_greed=fg,
            fear_greed_label="NEUTRAL", funding_bias=funding,
            funding_paid_count=0, volatility="NORMAL",
            regime_label="test",
        )

    def test_direction_change(self):
        prev = self._regime(dominant="MIXED")
        curr = self._regime(dominant="SHORT")
        shift = detect_shift(prev, curr)
        assert shift is not None
        assert shift["from_direction"] == "MIXED"
        assert shift["to_direction"] == "SHORT"

    def test_coin_shift_3_plus(self):
        prev = self._regime(short=5, long=5, neutral=30)
        curr = self._regime(short=9, long=5, neutral=26)
        shift = detect_shift(prev, curr)
        assert shift is not None
        assert "flipped short" in shift["summary"]

    def test_minor_change_no_shift(self):
        prev = self._regime(short=5, long=5, neutral=30)
        curr = self._regime(short=6, long=5, neutral=29)
        shift = detect_shift(prev, curr)
        assert shift is None

    def test_fear_greed_boundary_cross(self):
        prev = self._regime(fg=38)
        curr = self._regime(fg=42)
        shift = detect_shift(prev, curr)
        assert shift is not None
        assert "fear/greed" in shift["summary"]

    def test_fear_greed_same_zone_no_shift(self):
        prev = self._regime(fg=32)
        curr = self._regime(fg=38)
        shift = detect_shift(prev, curr)
        assert shift is None

    def test_funding_flip(self):
        prev = self._regime(funding="SHORTS PAID")
        curr = self._regime(funding="LONGS PAID")
        shift = detect_shift(prev, curr)
        assert shift is not None
        assert "funding" in shift["summary"]

    def test_funding_to_neutral_no_flip(self):
        prev = self._regime(funding="SHORTS PAID")
        curr = self._regime(funding="NEUTRAL")
        shift = detect_shift(prev, curr)
        # Funding to neutral is not a flip (requires both non-neutral)
        assert shift is None

    def test_no_change(self):
        r = self._regime()
        shift = detect_shift(r, r)
        assert shift is None

    def test_minimum_3_coin_threshold(self):
        """2-coin shift should not trigger."""
        prev = self._regime(short=5, long=5, neutral=30)
        curr = self._regime(short=7, long=5, neutral=28)
        shift = detect_shift(prev, curr)
        assert shift is None

    def test_multiple_shifts_combined(self):
        prev = self._regime(dominant="MIXED", short=5, long=5, fg=35, funding="SHORTS PAID")
        curr = self._regime(dominant="SHORT", short=10, long=2, fg=18, funding="LONGS PAID")
        shift = detect_shift(prev, curr)
        assert shift is not None
        assert len(shift["shifts"]) >= 2


# ── Card rendering ───────────────────────────────────────────────────────────

def _is_png(data: bytes) -> bool:
    return data[:8] == b'\x89PNG\r\n\x1a\n'


def _png_dimensions(data: bytes) -> tuple:
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    return w, h


REGIME_DATA = {
    "dominant_direction": "SHORT",
    "trending_short": 14,
    "trending_long": 6,
    "neutral": 20,
    "total": 40,
    "approaching_count": 3,
    "fear_greed": 15,
    "fear_greed_label": "EXTREME FEAR",
    "funding_bias": "SHORTS PAID",
    "funding_paid_count": 8,
    "volatility": "HIGH",
    "regime_label": "SHORT MARKET. 14 of 40 coins trending short. extreme fear. shorts paid.",
}


@pytest.fixture(scope="module")
def renderer():
    from scanner.v6.cards.renderer import CardRenderer
    return CardRenderer()


class TestRegimeCard:
    def test_renders(self, renderer):
        png = renderer.render("regime_card", REGIME_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("regime_card", REGIME_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_long_variant(self, renderer):
        data = {**REGIME_DATA, "dominant_direction": "LONG", "trending_long": 15,
                "trending_short": 3, "fear_greed": 80, "fear_greed_label": "GREED",
                "funding_bias": "LONGS PAID", "volatility": "LOW",
                "regime_label": "LONG MARKET. 15 of 40 coins trending long. greed. longs paid."}
        png = renderer.render("regime_card", data)
        assert _is_png(png)

    def test_mixed_variant(self, renderer):
        data = {**REGIME_DATA, "dominant_direction": "MIXED", "trending_short": 8,
                "trending_long": 7, "neutral": 25, "fear_greed": 50,
                "fear_greed_label": "NEUTRAL", "funding_bias": "NEUTRAL",
                "volatility": "NORMAL",
                "regime_label": "MIXED. no clear direction. 8 short, 7 long, 25 neutral. normal volatility."}
        png = renderer.render("regime_card", data)
        assert _is_png(png)

    def test_quiet_variant(self, renderer):
        data = {**REGIME_DATA, "dominant_direction": "QUIET", "trending_short": 1,
                "trending_long": 1, "neutral": 38, "fear_greed": 45,
                "fear_greed_label": "NEUTRAL", "funding_bias": "NEUTRAL",
                "volatility": "LOW",
                "regime_label": "QUIET. 2 of 40 coins have conviction. low volatility. patience."}
        png = renderer.render("regime_card", data)
        assert _is_png(png)

    def test_extreme_volatility(self, renderer):
        data = {**REGIME_DATA, "volatility": "EXTREME"}
        png = renderer.render("regime_card", data)
        assert _is_png(png)

    def test_zero_coins(self, renderer):
        data = {**REGIME_DATA, "total": 0, "trending_short": 0, "trending_long": 0, "neutral": 0}
        png = renderer.render("regime_card", data)
        assert _is_png(png)


class TestRegimeSamplePNG:
    def test_regime_png(self, renderer):
        path = renderer.render_to_file("regime_card", REGIME_DATA, "/tmp/test_regime.png")
        assert path == "/tmp/test_regime.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())


# ── Endpoint tests (card_api) ───────────────────────────────────────────────

class TestRegimeEndpoints:
    """Test that regime endpoints are registered (import-level check)."""

    def test_card_api_has_regime_route(self):
        from scanner.v6.cards.card_api import router
        paths = [r.path for r in router.routes]
        assert any("regime" in p for p in paths)

    def test_regime_state_serializable(self):
        """RegimeState.to_dict() produces JSON-serializable output."""
        coins = [_make_coin("BTC", "SHORT")]
        state = RegimeState.from_heat(_heat(coins), _brief())
        d = state.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

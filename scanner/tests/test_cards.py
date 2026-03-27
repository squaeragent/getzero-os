"""Tests for the visual card engine (renderer + templates)."""

import struct
import pytest
from scanner.v6.cards.renderer import CardRenderer


EVAL_DATA = {
    "coin": "SOL",
    "price": 82.95,
    "consensus": 3,
    "conviction": 0.5,
    "direction": "SHORT",
    "regime": "strong_trend",
    "layers": [
        {"layer": "regime", "passed": True, "value": "strong_trend"},
        {"layer": "technical", "passed": False, "value": {"agree": 1, "total": 4, "rsi": 32.16}},
        {"layer": "funding", "passed": True, "value": -5.04e-05},
        {"layer": "book", "passed": False, "value": {"bid_ratio": 0.6}},
        {"layer": "OI", "passed": True, "value": 123456},
        {"layer": "macro", "passed": False, "value": 13},
        {"layer": "collective", "passed": True, "value": None},
    ],
}

HEAT_DATA = {
    "count": 50,
    "coins": [
        {"coin": "SOL", "consensus": 5, "conviction": 0.833, "direction": "SHORT", "price": 82.95, "regime": "strong_trend"},
        {"coin": "BTC", "consensus": 4, "conviction": 0.714, "direction": "LONG", "price": 66500, "regime": "ranging"},
        {"coin": "ETH", "consensus": 3, "conviction": 0.500, "direction": "SHORT", "price": 3200, "regime": "weak_trend"},
        {"coin": "APT", "consensus": 6, "conviction": 0.900, "direction": "SHORT", "price": 8.50, "regime": "strong_trend"},
    ],
}

BRIEF_DATA = {
    "fear_greed": 13,
    "open_positions": 5,
    "positions": [
        {"coin": "APT", "direction": "SHORT", "entry_price": 1.0263, "size_usd": 20.01},
        {"coin": "SOL", "direction": "SHORT", "entry_price": 82.50, "size_usd": 15.00},
        {"coin": "BTC", "direction": "LONG", "entry_price": 66000, "size_usd": 50.00},
    ],
    "session": {"active": False},
}

APPROACHING_DATA = {
    "approaching": [
        {
            "coin": "SEI", "consensus": 3, "threshold": 5, "distance": 2,
            "direction": "SHORT",
            "passing_layers": ["regime", "funding", "OI"],
            "failing_layers": ["technical", "book", "macro"],
            "bottleneck": "technical",
        },
        {
            "coin": "DOGE", "consensus": 4, "threshold": 5, "distance": 1,
            "direction": "LONG",
            "passing_layers": ["regime", "funding", "OI", "technical"],
            "failing_layers": ["book"],
            "bottleneck": "book",
        },
    ],
}

RESULT_DATA = {
    "strategy": "momentum",
    "duration_hours": 48,
    "paper": True,
    "trades": 3,
    "win_rate": 66.7,
    "total_pnl": 4.20,
    "max_drawdown": -1.50,
    "eval_count": 2880,
    "reject_count": 2877,
}


def _is_png(data: bytes) -> bool:
    """Check PNG magic bytes."""
    return data[:8] == b'\x89PNG\r\n\x1a\n'


def _png_dimensions(data: bytes) -> tuple:
    """Extract width, height from PNG IHDR chunk."""
    # IHDR starts at byte 16: 4 bytes width, 4 bytes height (big-endian)
    w = struct.unpack(">I", data[16:20])[0]
    h = struct.unpack(">I", data[20:24])[0]
    return w, h


@pytest.fixture(scope="module")
def renderer():
    return CardRenderer()


class TestEvalCard:
    def test_renders(self, renderer):
        png = renderer.render("eval_card", EVAL_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("eval_card", EVAL_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_minimal_data(self, renderer):
        png = renderer.render("eval_card", {"coin": "X", "price": 0, "consensus": 0,
                                             "conviction": 0, "direction": "NONE",
                                             "regime": "---", "layers": []})
        assert _is_png(png)


class TestHeatCard:
    def test_renders(self, renderer):
        png = renderer.render("heat_card", HEAT_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("heat_card", HEAT_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty_coins(self, renderer):
        png = renderer.render("heat_card", {"count": 0, "coins": []})
        assert _is_png(png)


class TestBriefCard:
    def test_renders(self, renderer):
        png = renderer.render("brief_card", BRIEF_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("brief_card", BRIEF_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_no_positions(self, renderer):
        png = renderer.render("brief_card", {"fear_greed": 50, "open_positions": 0,
                                              "positions": [], "session": {"active": False}})
        assert _is_png(png)


class TestApproachingCard:
    def test_renders(self, renderer):
        png = renderer.render("approaching_card", APPROACHING_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("approaching_card", APPROACHING_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty(self, renderer):
        png = renderer.render("approaching_card", {"approaching": []})
        assert _is_png(png)


class TestResultCard:
    def test_renders(self, renderer):
        png = renderer.render("result_card", RESULT_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("result_card", RESULT_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_live_badge(self, renderer):
        data = {**RESULT_DATA, "paper": False}
        png = renderer.render("result_card", data)
        assert _is_png(png)

    def test_negative_pnl(self, renderer):
        data = {**RESULT_DATA, "total_pnl": -2.50}
        png = renderer.render("result_card", data)
        assert _is_png(png)


class TestRenderToFile:
    def test_saves_file(self, renderer, tmp_path):
        out = str(tmp_path / "test.png")
        path = renderer.render_to_file("eval_card", EVAL_DATA, out)
        assert path == out
        with open(out, "rb") as f:
            assert _is_png(f.read())


class TestCustomDimensions:
    def test_wide(self, renderer):
        png = renderer.render("eval_card", EVAL_DATA, width=1200, height=600)
        w, h = _png_dimensions(png)
        assert w == 1200
        assert h == 600

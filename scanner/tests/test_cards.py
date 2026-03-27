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


# ── S16 Chart templates ─────────────────────────────────────────────

EQUITY_DATA = {
    "strategy": "momentum",
    "duration_hours": 24,
    "points": [
        {"ts": "00:00", "pnl": 0.0},
        {"ts": "02:00", "pnl": 1.20, "event": "entry"},
        {"ts": "04:00", "pnl": 2.50},
        {"ts": "06:00", "pnl": 1.80},
        {"ts": "08:00", "pnl": 3.10, "event": "exit"},
        {"ts": "10:00", "pnl": 3.00},
        {"ts": "12:00", "pnl": -0.50},
        {"ts": "14:00", "pnl": -1.20, "event": "entry"},
        {"ts": "16:00", "pnl": 0.80},
        {"ts": "18:00", "pnl": 2.40, "event": "exit"},
        {"ts": "20:00", "pnl": 2.20},
        {"ts": "22:00", "pnl": 3.50},
    ],
}

RADAR_DATA = {
    "coin": "SOL",
    "consensus": 5,
    "layers": [
        {"layer": "regime", "passed": True},
        {"layer": "technical", "passed": False},
        {"layer": "funding", "passed": True},
        {"layer": "book", "passed": True},
        {"layer": "OI", "passed": True},
        {"layer": "macro", "passed": False},
        {"layer": "collective", "passed": True},
    ],
}

GAUGE_DATA = {
    "value": 13,
    "label": "Fear & Greed",
}

FUNNEL_DATA = {
    "strategy": "momentum",
    "duration_hours": 48,
    "eval_count": 2880,
    "reject_count": 2877,
    "trades": 3,
}


class TestEquityCard:
    def test_renders(self, renderer):
        png = renderer.render("equity_card", EQUITY_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("equity_card", EQUITY_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty_points(self, renderer):
        png = renderer.render("equity_card", {"strategy": "test", "duration_hours": 1, "points": []})
        assert _is_png(png)

    def test_single_point(self, renderer):
        png = renderer.render("equity_card", {"strategy": "x", "duration_hours": 1,
                                               "points": [{"ts": "0", "pnl": 5.0}]})
        assert _is_png(png)

    def test_negative_pnl(self, renderer):
        pts = [{"ts": str(i), "pnl": -float(i)} for i in range(5)]
        png = renderer.render("equity_card", {"strategy": "down", "duration_hours": 5, "points": pts})
        assert _is_png(png)


class TestRadarCard:
    def test_renders(self, renderer):
        png = renderer.render("radar_card", RADAR_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("radar_card", RADAR_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_all_passing(self, renderer):
        data = {**RADAR_DATA, "layers": [{"layer": f"L{i}", "passed": True} for i in range(7)]}
        png = renderer.render("radar_card", data)
        assert _is_png(png)

    def test_all_failing(self, renderer):
        data = {**RADAR_DATA, "consensus": 0,
                "layers": [{"layer": f"L{i}", "passed": False} for i in range(7)]}
        png = renderer.render("radar_card", data)
        assert _is_png(png)


class TestGaugeCard:
    def test_renders(self, renderer):
        png = renderer.render("gauge_card", GAUGE_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("gauge_card", GAUGE_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_extreme_fear(self, renderer):
        png = renderer.render("gauge_card", {"value": 5, "label": "Fear & Greed"})
        assert _is_png(png)

    def test_extreme_greed(self, renderer):
        png = renderer.render("gauge_card", {"value": 95, "label": "Fear & Greed"})
        assert _is_png(png)

    def test_neutral(self, renderer):
        png = renderer.render("gauge_card", {"value": 50, "label": "Fear & Greed"})
        assert _is_png(png)


class TestFunnelCard:
    def test_renders(self, renderer):
        png = renderer.render("funnel_card", FUNNEL_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("funnel_card", FUNNEL_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_zero_evals(self, renderer):
        png = renderer.render("funnel_card", {"strategy": "x", "duration_hours": 1,
                                               "eval_count": 0, "reject_count": 0, "trades": 0})
        assert _is_png(png)

    def test_high_trade_rate(self, renderer):
        png = renderer.render("funnel_card", {"strategy": "aggressive", "duration_hours": 24,
                                               "eval_count": 100, "reject_count": 50, "trades": 50})
        assert _is_png(png)


class TestSamplePNGs:
    """Generate sample PNGs to /tmp for visual inspection."""

    def test_equity_png(self, renderer):
        path = renderer.render_to_file("equity_card", EQUITY_DATA, "/tmp/test_equity.png")
        assert path == "/tmp/test_equity.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_radar_png(self, renderer):
        path = renderer.render_to_file("radar_card", RADAR_DATA, "/tmp/test_radar.png")
        assert path == "/tmp/test_radar.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_gauge_png(self, renderer):
        path = renderer.render_to_file("gauge_card", GAUGE_DATA, "/tmp/test_gauge.png")
        assert path == "/tmp/test_gauge.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_funnel_png(self, renderer):
        path = renderer.render_to_file("funnel_card", FUNNEL_DATA, "/tmp/test_funnel.png")
        assert path == "/tmp/test_funnel.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())


# ── S21 Backtest card templates ────────────────────────────────────

BACKTEST_SUMMARY_DATA = {
    "strategies": [
        {"name": "momentum", "total_pnl_pct": 12.5, "total_trades": 45, "win_rate": 62.2, "max_drawdown_pct": 4.8, "sharpe_ratio": 1.85},
        {"name": "mean_revert", "total_pnl_pct": -3.2, "total_trades": 30, "win_rate": 46.7, "max_drawdown_pct": 8.1, "sharpe_ratio": -0.42},
        {"name": "breakout", "total_pnl_pct": 7.8, "total_trades": 22, "win_rate": 59.1, "max_drawdown_pct": 3.5, "sharpe_ratio": 1.20},
        {"name": "degen", "total_pnl_pct": -15.3, "total_trades": 120, "win_rate": 38.3, "max_drawdown_pct": 22.0, "sharpe_ratio": -2.10},
        {"name": "sniper", "total_pnl_pct": 5.1, "total_trades": 8, "win_rate": 75.0, "max_drawdown_pct": 2.1, "sharpe_ratio": 2.30},
        {"name": "funding_arb", "total_pnl_pct": 1.2, "total_trades": 55, "win_rate": 52.7, "max_drawdown_pct": 1.8, "sharpe_ratio": 0.65},
        {"name": "scalp", "total_pnl_pct": -0.5, "total_trades": 200, "win_rate": 49.0, "max_drawdown_pct": 5.0, "sharpe_ratio": -0.10},
        {"name": "trend_follow", "total_pnl_pct": 9.3, "total_trades": 15, "win_rate": 66.7, "max_drawdown_pct": 6.2, "sharpe_ratio": 1.50},
        {"name": "grid", "total_pnl_pct": 3.4, "total_trades": 90, "win_rate": 55.6, "max_drawdown_pct": 3.0, "sharpe_ratio": 0.80},
    ],
    "days": 90,
    "start_date": "2025-12-28",
    "end_date": "2026-03-27",
}

BACKTEST_EQUITY_DATA = {
    "strategy": "momentum",
    "equity_curve": [
        {"ts": "2026-03-01", "equity": 100.0},
        {"ts": "2026-03-05", "equity": 102.5},
        {"ts": "2026-03-10", "equity": 101.0},
        {"ts": "2026-03-15", "equity": 105.2},
        {"ts": "2026-03-18", "equity": 103.8},
        {"ts": "2026-03-20", "equity": 107.1},
        {"ts": "2026-03-22", "equity": 108.5},
        {"ts": "2026-03-25", "equity": 106.0},
        {"ts": "2026-03-27", "equity": 112.5},
    ],
    "total_pnl_pct": 12.5,
    "max_drawdown_pct": 4.8,
    "total_trades": 45,
    "win_rate": 62.2,
    "days": 30,
}

BACKTEST_COMPARE_DATA = {
    "a": {
        "strategy": "momentum",
        "equity_curve": [
            {"ts": "2026-03-01", "equity": 100.0},
            {"ts": "2026-03-05", "equity": 103.0},
            {"ts": "2026-03-10", "equity": 101.5},
            {"ts": "2026-03-15", "equity": 106.0},
            {"ts": "2026-03-20", "equity": 108.0},
            {"ts": "2026-03-25", "equity": 105.5},
            {"ts": "2026-03-27", "equity": 112.5},
        ],
        "total_pnl_pct": 12.5,
        "win_rate": 62.2,
        "max_drawdown_pct": 4.8,
        "sharpe_ratio": 1.85,
    },
    "b": {
        "strategy": "degen",
        "equity_curve": [
            {"ts": "2026-03-01", "equity": 100.0},
            {"ts": "2026-03-05", "equity": 98.0},
            {"ts": "2026-03-10", "equity": 95.0},
            {"ts": "2026-03-15", "equity": 92.0},
            {"ts": "2026-03-20", "equity": 88.0},
            {"ts": "2026-03-25", "equity": 86.0},
            {"ts": "2026-03-27", "equity": 84.7},
        ],
        "total_pnl_pct": -15.3,
        "win_rate": 38.3,
        "max_drawdown_pct": 22.0,
        "sharpe_ratio": -2.10,
    },
    "days": 30,
}


class TestBacktestSummaryCard:
    def test_renders(self, renderer):
        png = renderer.render("backtest_summary_card", BACKTEST_SUMMARY_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("backtest_summary_card", BACKTEST_SUMMARY_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty_strategies(self, renderer):
        png = renderer.render("backtest_summary_card", {
            "strategies": [], "days": 30, "start_date": "---", "end_date": "---"})
        assert _is_png(png)

    def test_single_strategy(self, renderer):
        data = {**BACKTEST_SUMMARY_DATA, "strategies": BACKTEST_SUMMARY_DATA["strategies"][:1]}
        png = renderer.render("backtest_summary_card", data)
        assert _is_png(png)


class TestBacktestEquityCard:
    def test_renders(self, renderer):
        png = renderer.render("backtest_equity_card", BACKTEST_EQUITY_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("backtest_equity_card", BACKTEST_EQUITY_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty_curve(self, renderer):
        data = {**BACKTEST_EQUITY_DATA, "equity_curve": []}
        png = renderer.render("backtest_equity_card", data)
        assert _is_png(png)

    def test_negative_pnl(self, renderer):
        curve = [{"ts": str(i), "equity": 100 - i * 2} for i in range(10)]
        data = {**BACKTEST_EQUITY_DATA, "equity_curve": curve, "total_pnl_pct": -18.0}
        png = renderer.render("backtest_equity_card", data)
        assert _is_png(png)

    def test_single_point(self, renderer):
        data = {**BACKTEST_EQUITY_DATA, "equity_curve": [{"ts": "0", "equity": 100}]}
        png = renderer.render("backtest_equity_card", data)
        assert _is_png(png)


class TestBacktestCompareCard:
    def test_renders(self, renderer):
        png = renderer.render("backtest_compare_card", BACKTEST_COMPARE_DATA)
        assert _is_png(png)
        assert len(png) > 1000

    def test_dimensions(self, renderer):
        png = renderer.render("backtest_compare_card", BACKTEST_COMPARE_DATA)
        w, h = _png_dimensions(png)
        assert w == 800
        assert h == 400

    def test_empty_curves(self, renderer):
        data = {
            "a": {"strategy": "A", "equity_curve": [], "total_pnl_pct": 0, "win_rate": 0, "max_drawdown_pct": 0, "sharpe_ratio": 0},
            "b": {"strategy": "B", "equity_curve": [], "total_pnl_pct": 0, "win_rate": 0, "max_drawdown_pct": 0, "sharpe_ratio": 0},
            "days": 30,
        }
        png = renderer.render("backtest_compare_card", data)
        assert _is_png(png)

    def test_one_empty_curve(self, renderer):
        data = {**BACKTEST_COMPARE_DATA}
        data["b"] = {**data["b"], "equity_curve": []}
        png = renderer.render("backtest_compare_card", data)
        assert _is_png(png)


class TestBacktestSamplePNGs:
    """Generate backtest sample PNGs to /tmp for visual inspection."""

    def test_backtest_summary_png(self, renderer):
        path = renderer.render_to_file("backtest_summary_card", BACKTEST_SUMMARY_DATA, "/tmp/test_backtest_summary.png")
        assert path == "/tmp/test_backtest_summary.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_backtest_equity_png(self, renderer):
        path = renderer.render_to_file("backtest_equity_card", BACKTEST_EQUITY_DATA, "/tmp/test_backtest_equity.png")
        assert path == "/tmp/test_backtest_equity.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

    def test_backtest_compare_png(self, renderer):
        path = renderer.render_to_file("backtest_compare_card", BACKTEST_COMPARE_DATA, "/tmp/test_backtest_compare.png")
        assert path == "/tmp/test_backtest_compare.png"
        with open(path, "rb") as f:
            assert _is_png(f.read())

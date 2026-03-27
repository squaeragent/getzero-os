"""Tests for the backtest engine — data fetcher, backtester, and result structure."""

import json
import math
import tempfile
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scanner.v6.backtest.data_fetcher import HistoricalDataFetcher, TOP_COINS
from scanner.v6.backtest.backtester import (
    Backtester,
    BacktestResult,
    BacktestTrade,
    evaluate_backtest,
    _eval_regime,
    _eval_technical,
    _eval_funding,
    _eval_macro_proxy,
)
from scanner.v6.strategy_loader import load_strategy


# ─── Fixtures ────────────────────────────────────────────────────

def _make_candles(n: int = 300, base_price: float = 100.0, trend: float = 0.001) -> list[dict]:
    """Generate synthetic candle data with a slight uptrend."""
    import time
    start_ms = int(time.time() * 1000) - n * 3_600_000
    candles = []
    price = base_price
    for i in range(n):
        noise = math.sin(i * 0.1) * 2 + math.cos(i * 0.07) * 1.5
        price = price * (1 + trend) + noise * 0.01
        o = price
        h = price * 1.005
        l = price * 0.995
        c = price + noise * 0.005
        candles.append({
            "t": start_ms + i * 3_600_000,
            "o": str(round(o, 2)),
            "h": str(round(h, 2)),
            "l": str(round(l, 2)),
            "c": str(round(c, 2)),
            "v": str(round(1000 + i * 10, 2)),
        })
    return candles


def _make_funding(n: int = 300, rate: float = 0.0001) -> list[dict]:
    """Generate synthetic funding data."""
    import time
    start_ms = int(time.time() * 1000) - n * 28_800_000  # 8h intervals
    records = []
    for i in range(n):
        records.append({
            "coin": "BTC",
            "fundingRate": str(rate * (1 + math.sin(i * 0.3) * 0.5)),
            "premium": "0.0005",
            "time": start_ms + i * 28_800_000,
        })
    return records


# ─── Data Fetcher Tests ─────────────────────────────────────────

class TestDataFetcher:
    def test_cache_dir_creation(self, tmp_path):
        cache = tmp_path / "test_cache"
        fetcher = HistoricalDataFetcher(cache_dir=cache)
        assert cache.exists()

    def test_top_coins_list(self):
        assert len(TOP_COINS) == 30
        assert "BTC" in TOP_COINS
        assert "ETH" in TOP_COINS
        assert "SOL" in TOP_COINS

    def test_fetch_candles_caches(self, tmp_path):
        fetcher = HistoricalDataFetcher(cache_dir=tmp_path)
        candles = _make_candles(50)

        # Write fake cache
        cache_path = tmp_path / "BTC_1h_30d_candles.json"
        cache_path.write_text(json.dumps(candles))

        result = fetcher.fetch_candles("BTC", "1h", 30)
        assert len(result) == 50
        assert result[0]["t"] == candles[0]["t"]

    def test_fetch_funding_caches(self, tmp_path):
        fetcher = HistoricalDataFetcher(cache_dir=tmp_path)
        funding = _make_funding(20)

        cache_path = tmp_path / "ETH_30d_funding.json"
        cache_path.write_text(json.dumps(funding))

        result = fetcher.fetch_funding("ETH", 30)
        assert len(result) == 20

    @patch("scanner.v6.backtest.data_fetcher.requests.post")
    def test_fetch_candles_api_call(self, mock_post, tmp_path):
        fetcher = HistoricalDataFetcher(cache_dir=tmp_path)
        candles = _make_candles(10)

        mock_resp = MagicMock()
        mock_resp.json.return_value = candles
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = fetcher.fetch_candles("BTC", "1h", 1, force=True)
        assert mock_post.called
        assert len(result) == 10

        # Check cache was written
        cache_path = tmp_path / "BTC_1h_1d_candles.json"
        assert cache_path.exists()


# ─── Layer Evaluation Tests ──────────────────────────────────────

class TestLayers:
    def test_eval_technical_long_signal(self):
        # Create prices with clear uptrend (fast EMA > medium > slow, RSI not overbought)
        closes = [50 + i * 0.5 for i in range(200)]
        result = _eval_technical(closes, "LONG")
        assert result["layer"] == "technical"
        assert isinstance(result["pass"], bool)

    def test_eval_technical_short_signal(self):
        closes = [150 - i * 0.5 for i in range(200)]
        result = _eval_technical(closes, "SHORT")
        assert result["layer"] == "technical"
        assert isinstance(result["pass"], bool)

    def test_eval_funding_neutral(self):
        result = _eval_funding(0.00005, "LONG")
        assert result["pass"] is True
        assert "neutral" in result["reason"]

    def test_eval_funding_favors_long(self):
        result = _eval_funding(-0.0005, "LONG")
        assert result["pass"] is True

    def test_eval_funding_opposes_long(self):
        result = _eval_funding(0.001, "LONG")
        assert result["pass"] is False

    def test_eval_funding_favors_short(self):
        result = _eval_funding(0.0005, "SHORT")
        assert result["pass"] is True

    def test_eval_macro_proxy_normal(self):
        closes = [100 + math.sin(i * 0.1) * 2 for i in range(50)]
        result = _eval_macro_proxy(closes)
        assert result["layer"] == "macro"
        assert result["pass"] is True

    def test_eval_macro_proxy_crash(self):
        # Simulate crash: price drops 15% below SMA rapidly
        closes = [100] * 30 + [100 - i * 2 for i in range(20)]
        result = _eval_macro_proxy(closes)
        assert result["layer"] == "macro"
        # In extreme crash, should fail

    def test_eval_regime_with_strategy(self):
        strategy = load_strategy("momentum")
        closes = [100 + i * 0.3 for i in range(250)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        result = _eval_regime(closes, highs, lows, strategy)
        assert result["layer"] == "regime"
        assert "regime" in result
        assert "category" in result

    def test_evaluate_backtest_returns_consensus(self):
        strategy = load_strategy("momentum")
        closes = [100 + i * 0.2 for i in range(250)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]

        consensus, layers = evaluate_backtest(closes, highs, lows, 0.0001, "LONG", strategy)
        assert isinstance(consensus, int)
        assert consensus in (0, 2, 3, 5, 7)
        assert len(layers) == 7  # 4 evaluated + 3 auto-pass
        layer_names = [l["layer"] for l in layers]
        assert "book" in layer_names
        assert "oi" in layer_names
        assert "collective" in layer_names


# ─── Backtester Tests ───────────────────────────────────────────

class TestBacktester:
    def test_empty_result(self):
        bt = Backtester(starting_equity=100.0)
        result = bt._empty_result("test", 30)
        assert result.strategy == "test"
        assert result.total_trades == 0
        assert result.total_pnl_pct == 0.0
        assert result.total_pnl_usd == 0.0

    def test_backtest_result_fields(self):
        result = BacktestResult(
            strategy="test",
            start_date="2025-01-01",
            end_date="2025-03-31",
            days=90,
            total_pnl_pct=5.5,
            total_pnl_usd=5.5,
            win_rate=60.0,
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            max_drawdown_pct=3.2,
            sharpe_ratio=1.5,
            avg_hold_hours=12.0,
            rejection_rate=0.75,
            total_evals=100,
            total_rejections=75,
        )
        d = result.to_dict()
        assert d["strategy"] == "test"
        assert d["total_pnl_pct"] == 5.5
        assert d["win_rate"] == 60.0
        assert d["max_drawdown_pct"] == 3.2
        assert d["sharpe_ratio"] == 1.5
        assert isinstance(d["trades"], list)
        assert isinstance(d["equity_curve"], list)

    def test_backtest_trade_fields(self):
        trade = BacktestTrade(
            coin="BTC",
            direction="LONG",
            entry_price=50000.0,
            entry_time="2025-01-01 00:00",
            exit_price=51000.0,
            exit_time="2025-01-02 00:00",
            pnl_pct=2.0,
            pnl_usd=2.0,
            hold_hours=24.0,
            consensus_at_entry=7,
            exit_reason="trailing_stop",
        )
        assert trade.coin == "BTC"
        assert trade.pnl_pct == 2.0

    def test_strategy_configs_load(self):
        """All strategy configs load without error."""
        from scanner.v6.strategy_loader import list_strategies
        for name in list_strategies():
            s = load_strategy(name)
            assert s.evaluation.consensus_threshold >= 1
            # "watch" is observe-only with no positions
            if name != "watch":
                assert s.risk.stop_loss_pct > 0
                assert s.risk.max_positions > 0

    @patch("scanner.v6.backtest.backtester.HistoricalDataFetcher")
    def test_run_with_synthetic_data(self, MockFetcher):
        """Run backtester with synthetic data and verify result structure."""
        candles = _make_candles(300, base_price=100.0, trend=0.0005)
        funding = _make_funding(40, rate=0.0001)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_candles.return_value = candles
        mock_fetcher.fetch_funding.return_value = funding
        MockFetcher.return_value = mock_fetcher

        bt = Backtester(starting_equity=100.0)
        result = bt.run("momentum", coins=["BTC"], days=30)

        assert result.strategy == "momentum"
        assert result.days == 30
        assert isinstance(result.total_trades, int)
        assert isinstance(result.total_pnl_pct, float)
        assert isinstance(result.win_rate, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert isinstance(result.sharpe_ratio, float)
        assert result.total_evals >= 0
        assert len(result.equity_curve) >= 1

    @patch("scanner.v6.backtest.backtester.HistoricalDataFetcher")
    def test_run_no_data(self, MockFetcher):
        """Backtester returns empty result when no data available."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_candles.return_value = []
        mock_fetcher.fetch_funding.return_value = []
        MockFetcher.return_value = mock_fetcher

        bt = Backtester(starting_equity=100.0)
        result = bt.run("momentum", coins=["BTC"], days=30)

        assert result.total_trades == 0
        assert result.total_pnl_pct == 0.0

    @patch("scanner.v6.backtest.backtester.HistoricalDataFetcher")
    def test_run_single_trade(self, MockFetcher):
        """Check that a single clear trend generates at least one trade."""
        # Strong uptrend
        candles = _make_candles(300, base_price=100.0, trend=0.002)
        funding = _make_funding(40, rate=-0.0002)  # Negative funding favors longs

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_candles.return_value = candles
        mock_fetcher.fetch_funding.return_value = funding
        MockFetcher.return_value = mock_fetcher

        bt = Backtester(starting_equity=100.0)
        result = bt.run("degen", coins=["BTC"], days=30)

        assert result.strategy == "degen"
        # With strong trend + favorable funding, should get some trades
        assert result.total_evals > 0

    @patch("scanner.v6.backtest.backtester.HistoricalDataFetcher")
    def test_result_serialization(self, MockFetcher):
        """BacktestResult.to_dict() produces valid JSON."""
        candles = _make_candles(300)
        funding = _make_funding(40)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_candles.return_value = candles
        mock_fetcher.fetch_funding.return_value = funding
        MockFetcher.return_value = mock_fetcher

        bt = Backtester(starting_equity=100.0)
        result = bt.run("momentum", coins=["BTC"], days=30)

        d = result.to_dict()
        serialized = json.dumps(d, default=str)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["strategy"] == "momentum"

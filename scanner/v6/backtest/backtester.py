"""Core backtest replay engine — runs historical data through simplified 7-layer evaluation."""

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.strategy_loader import load_strategy, StrategyConfig
from scanner.v6.smart_indicators import (
    compute_rsi,
    compute_macd,
    compute_ema_cross,
    compute_bollinger,
    compute_atr,
    compute_funding,
    compute_hurst,
    RegimeClassifier,
    _sma,
)
from scanner.v6.backtest.data_fetcher import HistoricalDataFetcher, CACHE_DIR


# ─── Data classes ────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    coin: str
    direction: str  # LONG or SHORT
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    pnl_pct: float
    pnl_usd: float
    hold_hours: float
    consensus_at_entry: int
    layers_at_entry: list[dict] = field(default_factory=list)
    exit_reason: str = ""  # stop_loss, take_profit, session_end, trailing_stop


@dataclass
class BacktestResult:
    strategy: str
    start_date: str
    end_date: str
    days: int
    total_pnl_pct: float
    total_pnl_usd: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_hold_hours: float
    rejection_rate: float
    total_evals: int
    total_rejections: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ─── Simplified layer evaluators ─────────────────────────────────

_regime_classifier = RegimeClassifier()

LOOKBACK = 200  # bars needed for indicators


def _eval_regime(closes: list[float], highs: list[float], lows: list[float], strategy: StrategyConfig) -> dict:
    """Regime layer — classify market regime from price data."""
    if len(closes) < LOOKBACK:
        return {"layer": "regime", "pass": True, "regime": "insufficient_data", "reason": "not enough data"}

    hurst = compute_hurst(closes[-LOOKBACK:])
    atr_data = compute_atr(highs[-15:], lows[-15:], closes[-15:])
    atr_pct = atr_data.get("percent", 0.0)

    regime = _regime_classifier.classify(hurst, hurst, atr_pct)  # dfa≈hurst approx

    # Map to strategy min_regime categories
    regime_map = {
        "strong_trend": "trending", "moderate_trend": "trending", "weak_trend": "trending",
        "chaotic_trend": "trending",
        "random_quiet": "stable", "random_volatile": "stable",
        "weak_revert": "reverting", "moderate_revert": "reverting", "strong_revert": "reverting",
        "chaotic_flat": "reverting",
        "divergent": "stable", "transition": "stable", "insufficient_data": "stable",
    }
    category = regime_map.get(regime, "stable")
    allowed = strategy.evaluation.min_regime
    passed = category in allowed

    return {"layer": "regime", "pass": passed, "regime": regime, "category": category, "reason": f"{regime} -> {category}"}


def _eval_technical(closes: list[float], direction: str) -> dict:
    """Technical layer — RSI + MACD + EMA cross must agree on direction."""
    rsi = compute_rsi(closes)
    macd = compute_macd(closes)
    ema = compute_ema_cross(closes)

    signals = [rsi["signal"], macd["signal"], ema["signal"]]
    dir_lower = direction.lower()

    agrees = sum(1 for s in signals if s == dir_lower)
    neutrals = sum(1 for s in signals if s == "neutral")

    # Pass if majority agree or neutral (not opposing)
    opposes = 3 - agrees - neutrals
    passed = agrees >= 2 or (agrees >= 1 and opposes == 0)

    return {
        "layer": "technical",
        "pass": passed,
        "rsi": rsi["signal"],
        "macd": macd["signal"],
        "ema": ema["signal"],
        "agrees": agrees,
        "direction": direction,
    }


def _eval_funding(funding_rate: float, direction: str) -> dict:
    """Funding layer — check if funding rate favors the direction."""
    # Positive funding = longs pay shorts → favors SHORT
    # Negative funding = shorts pay longs → favors LONG
    # Near zero = neutral → pass either way
    threshold = 0.0001  # 0.01% — typical threshold

    if abs(funding_rate) < threshold:
        return {"layer": "funding", "pass": True, "rate": funding_rate, "reason": "neutral funding"}

    if direction == "LONG" and funding_rate < -threshold:
        return {"layer": "funding", "pass": True, "rate": funding_rate, "reason": "negative funding favors long"}
    if direction == "SHORT" and funding_rate > threshold:
        return {"layer": "funding", "pass": True, "rate": funding_rate, "reason": "positive funding favors short"}

    # Mild opposing funding still passes (within 2x threshold)
    if abs(funding_rate) < threshold * 3:
        return {"layer": "funding", "pass": True, "rate": funding_rate, "reason": "mild opposing funding"}

    return {"layer": "funding", "pass": False, "rate": funding_rate, "reason": "funding opposes direction"}


def _eval_macro_proxy(closes: list[float]) -> dict:
    """Macro layer proxy — use BTC-like price momentum as fear/greed proxy."""
    if len(closes) < 30:
        return {"layer": "macro", "pass": True, "reason": "insufficient data, auto-pass"}

    # Simple momentum proxy: is price above 20-day SMA?
    sma20 = _sma(closes, 20)
    current = closes[-1]
    pct_above = (current - sma20) / sma20 * 100 if sma20 else 0

    # Extreme fear (price way below SMA) = risky but still pass
    # We mainly fail if market is in freefall (> 10% below SMA with declining momentum)
    sma5 = _sma(closes, 5)
    short_trend = (sma5 - sma20) / sma20 * 100 if sma20 else 0

    # Only fail in extreme panic: price > 8% below SMA AND accelerating down
    if pct_above < -8 and short_trend < -5:
        return {"layer": "macro", "pass": False, "pct_above_sma20": pct_above, "reason": "extreme fear proxy"}

    return {"layer": "macro", "pass": True, "pct_above_sma20": pct_above, "reason": "macro OK"}


def evaluate_backtest(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    funding_rate: float,
    direction: str,
    strategy: StrategyConfig,
) -> tuple[int, list[dict]]:
    """Run simplified 4-layer evaluation. Returns (consensus, layer_results).

    Evaluated layers: regime, technical, funding, macro-proxy.
    Auto-pass layers: book, OI, collective (no historical data).

    Consensus mapping:
      4/4 evaluated pass = 7/7 equivalent
      3/4 = 5/7
      2/4 = 3/7
      1/4 = 2/7
      0/4 = 0/7
    """
    layers = [
        _eval_regime(closes, highs, lows, strategy),
        _eval_technical(closes, direction),
        _eval_funding(funding_rate, direction),
        _eval_macro_proxy(closes),
    ]

    evaluated_pass = sum(1 for l in layers if l["pass"])

    # Map to 7-layer equivalent
    consensus_map = {4: 7, 3: 5, 2: 3, 1: 2, 0: 0}
    consensus = consensus_map[evaluated_pass]

    # Add auto-pass layers for completeness
    for name in ("book", "oi", "collective"):
        layers.append({"layer": name, "pass": True, "reason": "auto-pass (no historical data)"})

    return consensus, layers


# ─── Position tracker ────────────────────────────────────────────

@dataclass
class _OpenPosition:
    coin: str
    direction: str
    entry_price: float
    entry_time: str
    entry_ts: int
    size_usd: float
    consensus: int
    layers: list[dict]
    highest_price: float = 0.0
    lowest_price: float = 999_999_999.0
    trailing_activated: bool = False


# ─── Backtester ──────────────────────────────────────────────────

class Backtester:
    def __init__(self, starting_equity: float = 100.0):
        self.starting_equity = starting_equity

    def run(
        self,
        strategy_name: str,
        coins: list[str] | None = None,
        days: int = 90,
        interval: str = "1h",
    ) -> BacktestResult:
        strategy = load_strategy(strategy_name)
        fetcher = HistoricalDataFetcher()
        coins = coins or ["BTC", "ETH", "SOL"]

        # Load data
        coin_candles: dict[str, list[dict]] = {}
        coin_funding: dict[str, list[dict]] = {}
        for coin in coins:
            candles = fetcher.fetch_candles(coin, interval, days)
            funding = fetcher.fetch_funding(coin, days)
            if candles:
                coin_candles[coin] = candles
                coin_funding[coin] = funding

        if not coin_candles:
            return self._empty_result(strategy_name, days)

        # Build unified timeline of hourly timestamps
        all_ts: set[int] = set()
        for candles in coin_candles.values():
            for c in candles:
                all_ts.add(int(c["t"]) if isinstance(c["t"], str) else c["t"])
        timeline = sorted(all_ts)

        if not timeline:
            return self._empty_result(strategy_name, days)

        # Index candles and funding by timestamp for fast lookup
        candle_idx: dict[str, dict[int, dict]] = {}
        for coin, candles in coin_candles.items():
            candle_idx[coin] = {(int(c["t"]) if isinstance(c["t"], str) else c["t"]): c for c in candles}

        funding_idx: dict[str, dict[int, float]] = {}
        for coin, records in coin_funding.items():
            idx: dict[int, float] = {}
            for r in records:
                t = int(r["time"]) if isinstance(r["time"], str) else r["time"]
                rate = float(r.get("fundingRate", 0))
                idx[t] = rate
            funding_idx[coin] = idx

        # State
        equity = self.starting_equity
        peak_equity = equity
        max_drawdown = 0.0
        positions: list[_OpenPosition] = []
        trades: list[BacktestTrade] = []
        equity_curve: list[dict] = []
        total_evals = 0
        total_rejections = 0
        daily_pnl = 0.0
        last_day = ""

        # History buffers per coin
        history: dict[str, dict] = {coin: {"closes": [], "highs": [], "lows": []} for coin in coins}

        for ts in timeline:
            ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            current_day = ts_str[:10]

            # Reset daily PnL
            if current_day != last_day:
                daily_pnl = 0.0
                last_day = current_day

            # Update history for each coin
            for coin in coins:
                candle = candle_idx.get(coin, {}).get(ts)
                if candle:
                    history[coin]["closes"].append(float(candle["c"]))
                    history[coin]["highs"].append(float(candle["h"]))
                    history[coin]["lows"].append(float(candle["l"]))

            # Check existing positions for exits
            closed_indices: list[int] = []
            for i, pos in enumerate(positions):
                candle = candle_idx.get(pos.coin, {}).get(ts)
                if not candle:
                    continue

                high = float(candle["h"])
                low = float(candle["l"])
                close = float(candle["c"])
                hours_held = (ts - pos.entry_ts) / 3_600_000

                pos.highest_price = max(pos.highest_price, high)
                pos.lowest_price = min(pos.lowest_price, low)

                exit_price = None
                exit_reason = ""

                # Stop loss
                if pos.direction == "LONG":
                    sl_price = pos.entry_price * (1 - strategy.risk.stop_loss_pct / 100)
                    if low <= sl_price:
                        exit_price = sl_price
                        exit_reason = "stop_loss"
                else:
                    sl_price = pos.entry_price * (1 + strategy.risk.stop_loss_pct / 100)
                    if high >= sl_price:
                        exit_price = sl_price
                        exit_reason = "stop_loss"

                # Trailing stop
                if not exit_price and strategy.exits.trailing_stop:
                    act_pct = strategy.exits.trailing_activation_pct / 100
                    trail_pct = strategy.exits.trailing_distance_pct / 100

                    if pos.direction == "LONG":
                        gain = (pos.highest_price - pos.entry_price) / pos.entry_price
                        if gain >= act_pct:
                            pos.trailing_activated = True
                        if pos.trailing_activated:
                            trail_price = pos.highest_price * (1 - trail_pct)
                            if low <= trail_price:
                                exit_price = trail_price
                                exit_reason = "trailing_stop"
                    else:
                        gain = (pos.entry_price - pos.lowest_price) / pos.entry_price
                        if gain >= act_pct:
                            pos.trailing_activated = True
                        if pos.trailing_activated:
                            trail_price = pos.lowest_price * (1 + trail_pct)
                            if high >= trail_price:
                                exit_price = trail_price
                                exit_reason = "trailing_stop"

                # Max hold time
                if not exit_price and hours_held >= strategy.risk.max_hold_hours:
                    exit_price = close
                    exit_reason = "session_end"

                # Daily loss limit
                if not exit_price and daily_pnl <= -(strategy.risk.max_daily_loss_pct / 100 * self.starting_equity):
                    exit_price = close
                    exit_reason = "daily_loss_limit"

                if exit_price:
                    if pos.direction == "LONG":
                        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
                    else:
                        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

                    pnl_usd = pos.size_usd * pnl_pct / 100
                    equity += pnl_usd
                    daily_pnl += pnl_usd
                    peak_equity = max(peak_equity, equity)
                    dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
                    max_drawdown = max(max_drawdown, dd)

                    trades.append(BacktestTrade(
                        coin=pos.coin,
                        direction=pos.direction,
                        entry_price=pos.entry_price,
                        entry_time=pos.entry_time,
                        exit_price=exit_price,
                        exit_time=ts_str,
                        pnl_pct=round(pnl_pct, 4),
                        pnl_usd=round(pnl_usd, 4),
                        hold_hours=round(hours_held, 1),
                        consensus_at_entry=pos.consensus,
                        layers_at_entry=pos.layers,
                        exit_reason=exit_reason,
                    ))
                    closed_indices.append(i)

            # Remove closed positions (reverse order)
            for i in sorted(closed_indices, reverse=True):
                positions.pop(i)

            # Try to open new positions
            if len(positions) < strategy.risk.max_positions:
                # Check reserve
                invested = sum(p.size_usd for p in positions)
                available = equity - invested
                reserve = equity * strategy.risk.reserve_pct / 100
                can_invest = available - reserve

                if can_invest > 0:
                    for coin in coins:
                        if len(positions) >= strategy.risk.max_positions:
                            break
                        # Skip if already in position for this coin
                        if any(p.coin == coin for p in positions):
                            continue

                        h = history[coin]
                        if len(h["closes"]) < LOOKBACK:
                            continue

                        candle = candle_idx.get(coin, {}).get(ts)
                        if not candle:
                            continue

                        current_price = float(candle["c"])

                        # Get current funding rate (find nearest)
                        fr = 0.0
                        fi = funding_idx.get(coin, {})
                        if fi:
                            nearest_t = min(fi.keys(), key=lambda t2: abs(t2 - ts), default=None)
                            if nearest_t and abs(nearest_t - ts) < 28_800_000:  # within 8h
                                fr = fi[nearest_t]

                        for direction in strategy.evaluation.directions:
                            dir_upper = direction.upper()
                            total_evals += 1

                            consensus, layers = evaluate_backtest(
                                h["closes"], h["highs"], h["lows"],
                                fr, dir_upper, strategy,
                            )

                            if consensus < strategy.evaluation.consensus_threshold:
                                total_rejections += 1
                                continue

                            # Entry
                            size = min(
                                equity * strategy.risk.position_size_pct / 100,
                                can_invest,
                            )
                            if size < 1.0:
                                continue

                            positions.append(_OpenPosition(
                                coin=coin,
                                direction=dir_upper,
                                entry_price=current_price,
                                entry_time=ts_str,
                                entry_ts=ts,
                                size_usd=size,
                                consensus=consensus,
                                layers=layers,
                                highest_price=current_price,
                                lowest_price=current_price,
                            ))
                            can_invest -= size
                            break  # one entry per coin per bar

            # Record equity curve point (every 24 bars = 1 day for hourly)
            if len(equity_curve) == 0 or (len(timeline) and timeline.index(ts) % 24 == 0 if ts in timeline else False):
                equity_curve.append({"ts": ts_str, "equity": round(equity, 2), "pnl": round(equity - self.starting_equity, 2)})

        # Force close remaining positions at last price
        for pos in positions:
            coin_closes = history[pos.coin]["closes"]
            if coin_closes:
                exit_price = coin_closes[-1]
                hours_held = (timeline[-1] - pos.entry_ts) / 3_600_000 if timeline else 0

                if pos.direction == "LONG":
                    pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

                pnl_usd = pos.size_usd * pnl_pct / 100
                equity += pnl_usd

                last_ts_str = datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if timeline else ""
                trades.append(BacktestTrade(
                    coin=pos.coin,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    entry_time=pos.entry_time,
                    exit_price=exit_price,
                    exit_time=last_ts_str,
                    pnl_pct=round(pnl_pct, 4),
                    pnl_usd=round(pnl_usd, 4),
                    hold_hours=round(hours_held, 1),
                    consensus_at_entry=pos.consensus,
                    layers_at_entry=pos.layers,
                    exit_reason="backtest_end",
                ))

        # Final equity curve point
        if timeline:
            last_ts_str = datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            equity_curve.append({"ts": last_ts_str, "equity": round(equity, 2), "pnl": round(equity - self.starting_equity, 2)})

        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        max_drawdown = max(max_drawdown, dd)

        # Compute metrics
        winning = [t for t in trades if t.pnl_pct > 0]
        losing = [t for t in trades if t.pnl_pct <= 0]
        total = len(trades)
        win_rate = len(winning) / total * 100 if total else 0
        avg_hold = sum(t.hold_hours for t in trades) / total if total else 0
        total_pnl_pct = (equity - self.starting_equity) / self.starting_equity * 100
        rejection_rate = total_rejections / total_evals if total_evals else 0

        # Sharpe ratio (annualized from daily returns)
        daily_returns = []
        for i in range(1, len(equity_curve)):
            prev_eq = equity_curve[i - 1]["equity"]
            curr_eq = equity_curve[i]["equity"]
            if prev_eq > 0:
                daily_returns.append((curr_eq - prev_eq) / prev_eq)
        if daily_returns and len(daily_returns) > 1:
            mean_r = sum(daily_returns) / len(daily_returns)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns))
            sharpe = (mean_r / std_r) * math.sqrt(365) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        start_date = datetime.fromtimestamp(timeline[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if timeline else ""
        end_date = datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if timeline else ""

        return BacktestResult(
            strategy=strategy_name,
            start_date=start_date,
            end_date=end_date,
            days=days,
            total_pnl_pct=round(total_pnl_pct, 2),
            total_pnl_usd=round(equity - self.starting_equity, 2),
            win_rate=round(win_rate, 1),
            total_trades=total,
            winning_trades=len(winning),
            losing_trades=len(losing),
            max_drawdown_pct=round(max_drawdown, 2),
            sharpe_ratio=round(sharpe, 2),
            avg_hold_hours=round(avg_hold, 1),
            rejection_rate=round(rejection_rate, 4),
            total_evals=total_evals,
            total_rejections=total_rejections,
            trades=trades,
            equity_curve=equity_curve,
        )

    def _empty_result(self, strategy_name: str, days: int) -> BacktestResult:
        return BacktestResult(
            strategy=strategy_name,
            start_date="",
            end_date="",
            days=days,
            total_pnl_pct=0.0,
            total_pnl_usd=0.0,
            win_rate=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            avg_hold_hours=0.0,
            rejection_rate=0.0,
            total_evals=0,
            total_rejections=0,
        )

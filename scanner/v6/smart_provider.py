# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

#!/usr/bin/env python3
"""
SmartProvider — ZERO's own signal intelligence.

Implements SignalProvider interface. Drop-in replacement for ENVY.
11 indicators + 13-regime classifier + regime-weighted voting.

Quality: 7/10 (vs ENVY 10/10, BasicProvider 3/10).
After learning engine activates (200+ trades): targeting 8/10.
"""

import json
import os
import time
import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.smart_indicators import (
    compute_rsi, compute_macd, compute_ema_cross, compute_bollinger,
    compute_atr, compute_obv, compute_funding, compute_volume_profile,
    compute_hurst, compute_dfa, RegimeClassifier,
)
from scanner.v6.smart_data import get_market_data, MarketData
from scanner.v6.signal_provider import SignalProvider

SHADOW_DIR = Path(__file__).parent / "cache" / "smart_shadow"

# Regime quality penalties
REGIME_PENALTY = {
    "chaotic_trend": 0.5,
    "chaotic_flat": 0.4,
    "divergent": 0.3,
    "transition": 0.2,
    "random_volatile": 0.6,
    "insufficient_data": 0.3,
}


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [SMART] {msg}", flush=True)


class SmartProvider(SignalProvider):
    """ZERO's own signal engine. No external dependencies."""

    def __init__(self):
        self.regime_classifier = RegimeClassifier()
        self._prev_hurst: dict[str, float] = {}
        self._eval_count = 0
        self._learned_weights = None
        self._try_load_learned_weights()

    def _try_load_learned_weights(self):
        """Load learned weights from local cache, then try collective sync."""
        try:
            weights_file = Path(__file__).parent / "cache" / "smart_weights.json"
            if weights_file.exists():
                data = json.loads(weights_file.read_text())
                if data.get("weights") and data.get("trades_count", 0) >= 200:
                    self._learned_weights = data["weights"]
                    _log(f"Loaded learned weights from {data.get('trades_count', 0)} trades")
                    return
        except Exception:
            pass

        # Try collective sync (network learned weights)
        try:
            from collective import get_learned_weights
            result = get_learned_weights()
            if result.get("source") in ("collective", "thesis") and result.get("weights"):
                self._learned_weights = result["weights"]
                _log(f"Loaded weights from collective (source={result['source']})")
        except Exception:
            pass

    def evaluate_coin(self, coin: str, market_data: MarketData = None) -> dict:
        """Core evaluation engine. Computes all indicators, classifies regime, votes."""
        self._eval_count += 1

        # Fetch data if not provided
        if market_data is None:
            market_data = get_market_data(coin)

        if not market_data.closes or len(market_data.closes) < 20:
            return self._neutral_result(coin, "insufficient_data")

        closes = market_data.closes
        highs = market_data.highs
        lows = market_data.lows
        volumes = market_data.volumes

        # ── Compute all 11 indicators ──
        indicators = {
            "rsi": compute_rsi(closes),
            "macd": compute_macd(closes),
            "ema": compute_ema_cross(closes),
            "bollinger": compute_bollinger(closes),
            "atr": compute_atr(highs, lows, closes),
            "obv": compute_obv(closes, volumes),
            "funding": compute_funding(
                market_data.funding_current,
                market_data.funding_predicted,
                market_data.funding_history,
            ),
            "volume": compute_volume_profile(closes, volumes),
        }

        # Regime detection (use 4h candles with window=100 — stable without saturation)
        regime_closes = market_data.closes_4h if len(market_data.closes_4h) >= 50 else closes
        h = compute_hurst(regime_closes, window=100)
        d = compute_dfa(regime_closes, window=100)
        atr_pct = indicators["atr"]["percent"]
        h_prev = self._prev_hurst.get(coin)
        self._prev_hurst[coin] = h

        regime = self.regime_classifier.classify(h, d, atr_pct, h_prev)

        # Get weights (learned → blended with personal → hardcoded fallback)
        if self._learned_weights and regime in self._learned_weights:
            weights = self._learned_weights[regime]
        else:
            weights = self.regime_classifier.get_signal_weights(regime)

        # ── Weighted vote across directional indicators ──
        long_score = 0.0
        short_score = 0.0
        total_weight = 0.0

        directional = ["rsi", "macd", "ema", "bollinger", "obv", "funding"]
        for name in directional:
            ind = indicators.get(name, {})
            weight = weights.get(name, 0.5)
            strength = ind.get("strength", 0.5)
            signal = ind.get("signal", "neutral")

            effective = weight * strength
            total_weight += effective

            if signal == "long":
                long_score += effective
            elif signal == "short":
                short_score += effective

        # Normalize
        if total_weight < 0.01:
            return self._neutral_result(coin, regime, h, d, atr_pct, indicators)

        long_pct = long_score / total_weight
        short_pct = short_score / total_weight

        # Direction: needs >60% agreement
        if long_pct > 0.60:
            direction = "LONG"
            raw_quality = long_pct
        elif short_pct > 0.60:
            direction = "SHORT"
            raw_quality = short_pct
        else:
            direction = "NEUTRAL"
            raw_quality = max(long_pct, short_pct)

        # Regime penalty
        penalty = REGIME_PENALTY.get(regime, 1.0)
        quality = raw_quality * penalty

        # Scale to 0-10 (SmartProvider max is 7, or 8 with learned weights)
        max_quality = 8 if self._learned_weights else 7
        quality_10 = min(max_quality, round(quality * max_quality))

        # Build reasons
        reasons = []
        for name in directional:
            ind = indicators[name]
            if ind.get("signal") == direction.lower():
                reason_map = {
                    "rsi": f"RSI_{ind.get('value', 0):.0f}",
                    "macd": "MACD_BULL" if direction == "LONG" else "MACD_BEAR",
                    "ema": "EMA_BULL" if direction == "LONG" else "EMA_BEAR",
                    "bollinger": f"BB_{ind.get('percent_b', 0):.2f}",
                    "obv": "OBV_CONFIRM",
                    "funding": "FUND_CONTRARIAN",
                }
                reasons.append(reason_map.get(name, name.upper()))

        funding_data = indicators["funding"]
        result = {
            "coin": coin,
            "signal": direction,
            "direction": direction,
            "confidence": round(quality, 4),
            "quality": quality_10,
            "regime": regime,
            "hurst": round(h, 4),
            "dfa": round(d, 4),
            "atr_pct": round(atr_pct, 6),
            "funding_rate": funding_data.get("current", 0),
            "funding_annualized": funding_data.get("annualized", 0),
            "source": "smart_local",
            "regime_context": "",
            "indicator_votes": {name: indicators[name].get("signal", "neutral") for name in directional},
            "regime_weights": weights,
            "reasons": reasons,
            "indicators": {
                "RSI_14": indicators["rsi"].get("value", 50),
                "MACD_HIST": indicators["macd"].get("histogram", 0),
                "EMA_9": indicators["ema"].get("fast", 0),
                "EMA_21": indicators["ema"].get("medium", 0),
                "EMA_50": indicators["ema"].get("slow", 0),
                "BB_PCT": indicators["bollinger"].get("percent_b", 0.5),
                "BB_BANDWIDTH": indicators["bollinger"].get("bandwidth", 0),
                "ATR": indicators["atr"].get("value", 0),
                "ATR_PCT": atr_pct,
                "OBV": indicators["obv"].get("value", 0),
                "FUNDING": funding_data.get("current", 0),
                "FUNDING_ANN": funding_data.get("annualized", 0),
                "VOL_RATIO": indicators["volume"].get("ratio", 1),
                "HURST": h,
                "DFA": d,
                "CLOSE_PRICE": closes[-1],
                "BOOK_DEPTH_USD": market_data.book_depth_usd,
                "SPREAD_BPS": market_data.spread_bps,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Shadow logging
        self._shadow_log(result)

        return result

    def _neutral_result(self, coin, regime="insufficient_data", h=0.5, d=0.5, atr_pct=0, indicators=None):
        return {
            "coin": coin, "signal": "NEUTRAL", "direction": "NEUTRAL",
            "confidence": 0, "quality": 0, "regime": regime,
            "hurst": round(h, 4), "dfa": round(d, 4), "atr_pct": round(atr_pct, 6),
            "funding_rate": 0, "funding_annualized": 0,
            "source": "smart_local",
            "indicator_votes": {}, "regime_weights": {}, "reasons": [],
            "indicators": {}, "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _shadow_log(self, result: dict):
        """Log evaluation to shadow file for validation."""
        try:
            SHADOW_DIR.mkdir(parents=True, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = SHADOW_DIR / f"{today}.jsonl"
            entry = {
                "coin": result["coin"],
                "signal": result["signal"],
                "quality": result["quality"],
                "regime": result["regime"],
                "hurst": result["hurst"],
                "dfa": result["dfa"],
                "ts": result["timestamp"],
            }
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # ── SignalProvider Interface ──────────────────────────────

    def check_signals(self, coin: str, expressions: list = None) -> dict:
        """Check signals — matches SignalProvider interface."""
        return self.evaluate_coin(coin)

    def assemble_strategy(self, coin: str) -> dict:
        """Convert SmartProvider output to strategy format."""
        result = self.evaluate_coin(coin)

        if result["signal"] == "NEUTRAL" or result["quality"] < 1:
            return {"coin": coin, "signals": []}

        direction = result["signal"]
        quality = result["quality"]
        atr_pct = result["atr_pct"]

        stop_pct = max(atr_pct * 2, 0.03)  # 2× ATR, minimum 3%

        signal_entry = {
            "name": f"SMART_{coin}_{direction}",
            "direction": direction,
            "expression": f"SMART_REGIME={result['regime']}",
            "exit_expression": "",
            "max_hold_hours": 24,
            "sharpe": round(quality * 0.7, 2),
            "win_rate": round(45 + quality * 5, 1),
            "composite_score": round(quality * 0.7, 2),
            "stop_loss_pct": round(stop_pct, 4),
            "priority": 1,
            "source": "smart_local",
            "regime": result["regime"],
            "hurst": result["hurst"],
            "dfa": result["dfa"],
            "funding_rate": result["funding_rate"],
            "book_depth": result["indicators"].get("BOOK_DEPTH_USD", 0),
            "signal_sharpe": round(quality * 0.7, 2),
            "quality": result.get("regime", "unknown"),
        }

        return {
            "coin": coin,
            "signals": [signal_entry],
            "best_sharpe": signal_entry["sharpe"],
            "signal_count": 1,
        }

    def optimize_portfolio(self, coins: list[str]) -> dict:
        """Quality-weighted portfolio allocation."""
        evaluations = {}
        for coin in coins:
            try:
                r = self.evaluate_coin(coin)
                if r["signal"] != "NEUTRAL" and r["quality"] > 0:
                    evaluations[coin] = r["quality"]
            except Exception:
                pass

        if not evaluations:
            return {}

        total_q = sum(evaluations.values())
        if total_q == 0:
            return {coin: round(1 / len(evaluations), 4) for coin in evaluations}

        return {coin: round(q / total_q, 4) for coin, q in evaluations.items()}

    def evaluate_universe(self, coins: list[str]) -> list[dict]:
        """Evaluate all coins in parallel."""
        results = []

        def _eval(coin):
            try:
                return self.evaluate_coin(coin)
            except Exception as e:
                _log(f"eval {coin} failed: {e}")
                return self._neutral_result(coin)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_eval, coins))

        return results

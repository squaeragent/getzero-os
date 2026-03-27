#!/usr/bin/env python3
"""
Monitor — ZERO's signal intelligence EYES.

Session 9: The Monitor evaluates markets through 7 layers and emits typed signals.
It is STATELESS regarding positions — it only evaluates markets and emits signals.
Controller receives signals and executes trades.

Architecture:
    SmartProvider (existing) → Monitor (NEW) → Controller (done)

7 Layers:
    1. regime    — SmartProvider regime_classifier
    2. technical — RSI + MACD + EMA + BB majority vote
    3. funding   — funding indicator favorable for direction
    4. book      — L2 depth ratio from DataCache batch
    5. OI        — open interest confirming direction
    6. macro     — Fear & Greed contrarian signal
    7. collective — network consensus >60% agreement

Signal State Machine (per coin):
    States: inactive | entry | entry_end
    inactive + consensus >= threshold → ENTRY, state=entry
    entry + consensus >= threshold    → stay (dedup)
    entry + consensus < threshold     → ENTRY_END, state=entry_end
    entry + exit condition            → EXIT, state=inactive
    entry_end + consensus >= threshold → ENTRY (re-entry), state=entry
    entry_end + exit condition         → EXIT, state=inactive

Exit conditions (signal-based):
    - RSI overbought (>70 longs, <30 shorts)
    - Regime shift to excluded regime

Usage:
    python -m scanner.v6.monitor                  # one-shot
    python -m scanner.v6.monitor --loop            # continuous 60s cycles
    python -m scanner.v6.monitor --strategy degen  # use specific strategy
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.smart_provider import SmartProvider
from scanner.v6.strategy_loader import load_strategy, load_all_strategies, StrategyConfig

# ── Bus / file paths ───────────────────────────────────────────────────────────
V6_DIR = Path(__file__).parent
BUS_DIR = V6_DIR / "bus"
SIGNALS_FILE     = BUS_DIR / "signals.json"
NEAR_MISS_FILE   = BUS_DIR / "near_misses.jsonl"
DECISIONS_FILE   = BUS_DIR / "decisions.jsonl"
HEARTBEAT_FILE   = BUS_DIR / "heartbeat.json"

# HL REST
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Fear & Greed API
FG_URL = "https://api.alternative.me/fng/?limit=1"

# Data staleness limits
PRICE_STALE_MS       = 120_000   # 120s — skip cycle if prices stale
SOURCE_STALE_MS      = 30_000    # 30s — flag in evaluation if any source stale
FEAR_GREED_TTL_S     = 300       # 5 min between F&G fetches


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [MONITOR] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_atomic(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def _hl_post(payload: dict, timeout: int = 15) -> Any:
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class LayerResult:
    layer: str           # "regime", "technical", "funding", "book", "OI", "macro", "collective"
    passed: bool
    value: Any
    detail: str
    data_available: bool = True


@dataclass
class EvaluationResult:
    coin: str
    timestamp: str
    layers: list[LayerResult]
    consensus: int        # 0-7
    conviction: float     # 0.0-1.0
    direction: str        # "LONG", "SHORT", "NONE"
    regime: str
    price: float
    data_age_ms: int
    data_complete: bool


@dataclass
class ApproachingSignal:
    """Signal emitted when a coin is close to passing consensus threshold."""
    coin: str
    consensus: int
    threshold: int
    distance: int
    passing_layers: list
    failing_layers: list
    bottleneck: str
    bottleneck_detail: str
    direction: str
    price: float
    timestamp: str
    urgency: str  # "high" or "low"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class CycleMetrics:
    """Execution metrics for a single monitor cycle."""
    cycle_number: int
    timestamp: str
    cycle_duration_ms: int
    data_fetch_duration_ms: int
    evaluation_duration_ms: int
    signal_emission_duration_ms: int
    data_freshness_max_ms: int
    data_sources_available: int
    data_sources_stale: int
    coins_evaluated: int
    coins_passed: int
    coins_rejected: int
    coins_approaching: int
    signals_emitted: int
    memory_mb: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Signal:
    type: str            # "ENTRY", "ENTRY_END", "EXIT"
    coin: str
    direction: str
    timestamp: str
    price: float
    consensus: int = 0
    conviction: float = 0.0
    layers: list = field(default_factory=list)
    regime: str = ""
    layers_remaining: int = 0
    layers_lost: list = field(default_factory=list)
    reason: str = ""
    would_pass_strategies: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "coin": self.coin,
            "direction": self.direction,
            "timestamp": self.timestamp,
            "price": self.price,
            "consensus": self.consensus,
            "conviction": round(self.conviction, 4),
            "layers": self.layers,
            "regime": self.regime,
            "layers_remaining": self.layers_remaining,
            "layers_lost": self.layers_lost,
            "reason": self.reason,
            "would_pass_strategies": self.would_pass_strategies,
        }


# ─── DataCache ───────────────────────────────────────────────────────────────

class DataCache:
    """
    Batch data cache for all prices, funding, OI, and fear & greed.

    Fetches ALL prices in one allMids call.
    Fetches ALL funding + OI via predictedFundings or metaAndAssetCtxs.
    Tracks data_age_ms per source.
    """

    def __init__(self):
        # Price: {coin: float}
        self.prices: dict[str, float] = {}
        self.prices_ts: float = 0.0

        # Funding: {coin: float}
        self.funding: dict[str, float] = {}
        self.funding_ts: float = 0.0

        # OI: {coin: float}
        self.oi: dict[str, float] = {}
        self.oi_ts: float = 0.0

        # Fear & greed
        self.fear_greed: int = 50
        self.fear_greed_ts: float = 0.0

        # Book data (fetched on-demand, cached briefly)
        self._book_cache: dict[str, tuple[float, float]] = {}  # coin -> (bid_depth, ask_depth)
        self._book_ts: dict[str, float] = {}

        # B5: API call tracking
        self.api_call_count: int = 0
        self.api_calls_by_type: dict[str, int] = {}

    def refresh(self) -> bool:
        """
        Batch-refresh prices + funding + OI.
        Returns False if prices fail (caller should skip cycle).
        """
        # 1. All mids
        try:
            self._track_api_call("allMids")
            mids = _hl_post({"type": "allMids"})
            if isinstance(mids, dict):
                self.prices = {k: float(v) for k, v in mids.items()}
                self.prices_ts = time.time()
        except Exception as e:
            _log(f"allMids failed: {e}")
            # Keep old prices — caller will check staleness

        # 2. Funding + OI via metaAndAssetCtxs
        try:
            self._track_api_call("metaAndAssetCtxs")
            meta = _hl_post({"type": "metaAndAssetCtxs"})
            if isinstance(meta, list) and len(meta) >= 2:
                universe = meta[0].get("universe", [])
                contexts = meta[1] if isinstance(meta[1], list) else []
                fm, om = {}, {}
                for i, cm in enumerate(universe):
                    if i < len(contexts):
                        ctx = contexts[i]
                        name = cm.get("name", "")
                        fm[name] = float(ctx.get("funding", 0))
                        om[name] = float(ctx.get("openInterest", 0))
                self.funding = fm
                self.oi = om
                self.funding_ts = time.time()
                self.oi_ts = time.time()
        except Exception as e:
            _log(f"metaAndAssetCtxs failed: {e}")

        # 3. Fear & Greed (every 5 min)
        if time.time() - self.fear_greed_ts > FEAR_GREED_TTL_S:
            self._refresh_fear_greed()

        return bool(self.prices)

    def _track_api_call(self, call_type: str):
        self.api_call_count += 1
        self.api_calls_by_type[call_type] = self.api_calls_by_type.get(call_type, 0) + 1

    def _refresh_fear_greed(self):
        """Fetch fear & greed from alternative.me. Fallback to 50."""
        self._track_api_call("fearGreed")
        try:
            req = urllib.request.Request(FG_URL, headers={"User-Agent": "zeroos/1.0"})
            raw = json.loads(urllib.request.urlopen(req, timeout=10).read())
            value = int(raw["data"][0]["value"])
            self.fear_greed = value
            self.fear_greed_ts = time.time()
        except Exception:
            # Keep last value (or 50 if never fetched)
            if self.fear_greed_ts == 0:
                self.fear_greed = 50
                self.fear_greed_ts = time.time()  # don't retry every cycle

    def price_age_ms(self) -> int:
        if self.prices_ts == 0:
            return 999_999
        return int((time.time() - self.prices_ts) * 1000)

    def is_price_stale(self) -> bool:
        return self.price_age_ms() > PRICE_STALE_MS

    def is_source_stale(self, source: str) -> bool:
        """Check if a data source is stale (> 30s)."""
        ts_map = {
            "prices": self.prices_ts,
            "funding": self.funding_ts,
            "oi": self.oi_ts,
        }
        ts = ts_map.get(source, 0)
        if ts == 0:
            return True
        return (time.time() - ts) > (SOURCE_STALE_MS / 1000)

    def get_price(self, coin: str) -> float | None:
        return self.prices.get(coin)

    def get_funding(self, coin: str) -> float:
        return self.funding.get(coin, 0.0)

    def get_oi(self, coin: str) -> float:
        return self.oi.get(coin, 0.0)

    def get_book(self, coin: str) -> tuple[float, float]:
        """
        Return (bid_depth_usd, ask_depth_usd).
        Fetched on-demand (not in batch) since L2 is per-coin.
        """
        cached_ts = self._book_ts.get(coin, 0)
        if time.time() - cached_ts < 30:
            return self._book_cache.get(coin, (0.0, 0.0))
        try:
            self._track_api_call("l2Book")
            data = _hl_post({"type": "l2Book", "coin": coin, "nSigFigs": 5})
            levels = data.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            bid_depth = sum(float(b.get("sz", 0)) * float(b.get("px", 0)) for b in bids)
            ask_depth = sum(float(a.get("sz", 0)) * float(a.get("px", 0)) for a in asks)
            self._book_cache[coin] = (bid_depth, ask_depth)
            self._book_ts[coin] = time.time()
            return bid_depth, ask_depth
        except Exception:
            return 0.0, 0.0

    def data_complete(self) -> bool:
        """True if all sources have data."""
        return bool(self.prices) and bool(self.funding) and bool(self.oi)

    def any_source_stale(self) -> bool:
        return any(self.is_source_stale(s) for s in ("prices", "funding", "oi"))


# ─── NearMissDetector ────────────────────────────────────────────────────────

class NearMissDetector:
    """Cross-check evaluation results against ALL strategies to find near-misses."""

    def __init__(self, bus_dir: Path | None = None):
        self._all_strategies: dict[str, StrategyConfig] = {}
        self._bus_dir = bus_dir or BUS_DIR
        self._near_miss_file = self._bus_dir / "near_misses.jsonl"
        self._load_strategies()

    def _load_strategies(self):
        try:
            self._all_strategies = load_all_strategies()
        except Exception as e:
            _log(f"Could not load all strategies: {e}")
            self._all_strategies = {}

    def check(self, coin: str, result: EvaluationResult, active_strategy: StrategyConfig) -> list[dict]:
        """
        Cross-check result against all strategies.
        Return list of near-miss dicts for strategies that WOULD pass but active doesn't.
        """
        near_misses = []

        # Does the coin pass the active strategy?
        passes_active = result.consensus >= active_strategy.evaluation.consensus_threshold

        for strat_name, strat in self._all_strategies.items():
            if strat_name == active_strategy.name:
                continue

            # Check if this result would pass the other strategy
            passes_other = (
                result.consensus >= strat.evaluation.consensus_threshold
                and result.direction != "NONE"
                and result.direction.lower() in [d.lower() for d in strat.evaluation.directions]
                and strat.allows_regime(result.regime)
            )

            if passes_other and not passes_active:
                nm = {
                    "coin": coin,
                    "direction": result.direction,
                    "consensus": result.consensus,
                    "conviction": round(result.conviction, 4),
                    "regime": result.regime,
                    "active_strategy": active_strategy.name,
                    "near_miss_strategy": strat_name,
                    "active_threshold": active_strategy.evaluation.consensus_threshold,
                    "near_miss_threshold": strat.evaluation.consensus_threshold,
                    "layers_passed": [lr.layer for lr in result.layers if lr.passed],
                    "timestamp": result.timestamp,
                }
                near_misses.append(nm)

        return near_misses

    def write(self, near_misses: list[dict]):
        """Append near misses to the JSONL file."""
        if not near_misses:
            return
        self._near_miss_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._near_miss_file, "a") as f:
            for nm in near_misses:
                f.write(json.dumps(nm, default=str) + "\n")


# ─── Monitor ─────────────────────────────────────────────────────────────────

class Monitor:
    """
    The EYES of ZERO's trading engine.

    Evaluates markets through 7 layers and emits typed signals.
    Stateless regarding positions — it only evaluates markets.
    """

    def __init__(
        self,
        strategy_name: str = "momentum",
        bus_dir: Path | None = None,
    ):
        self.strategy = load_strategy(strategy_name)
        self.smart_provider = SmartProvider()
        self.cache = DataCache()
        self.coin_states: dict[str, str] = {}     # coin -> "inactive"|"entry"|"entry_end"
        self.prev_results: dict[str, EvaluationResult] = {}
        self.cycle_count = 0
        self._bus_dir = bus_dir or BUS_DIR
        self._signals_file = self._bus_dir / "signals.json"
        self._near_miss_file = self._bus_dir / "near_misses.jsonl"
        self._decisions_file = self._bus_dir / "decisions.jsonl"
        self._heartbeat_file = self._bus_dir / "heartbeat.json"
        self._near_miss_detector = NearMissDetector(self._bus_dir)
        # B1: Approaching detection — dedup by tracking previous consensus per coin
        self.approaching_states: dict[str, int] = {}
        # B4: Metrics log
        self._metrics_file = self._bus_dir / "metrics.jsonl"
        self.last_cycle_metrics: CycleMetrics | None = None

    def run_cycle(self) -> dict:
        """
        Run one evaluation cycle.

        1. Refresh cache (batch fetch)
        2. Get coins in scope
        3. Evaluate each coin through 7 layers
        4. Compare with previous, emit signals
        4b. Check approaching detection (B1)
        5. Cross-check near misses
        6. Log to decisions.jsonl
        7. Compute cycle metrics (B4)
        8. Return cycle summary
        """
        import resource

        self.cycle_count += 1
        cycle_start = time.time()
        summary = {
            "cycle": self.cycle_count,
            "timestamp": _now_iso(),
            "coins_evaluated": 0,
            "signals_emitted": 0,
            "near_misses": 0,
            "approaching": 0,
            "skipped": False,
            "skip_reason": "",
        }

        # 1. Refresh cache
        t_fetch_start = time.time()
        ok = self.cache.refresh()
        t_fetch_end = time.time()
        if not ok or self.cache.is_price_stale():
            age = self.cache.price_age_ms()
            _log(f"[CYCLE {self.cycle_count}] SKIP — price data stale ({age}ms)")
            summary["skipped"] = True
            summary["skip_reason"] = f"price_stale_{age}ms"
            self._write_heartbeat(summary)
            return summary

        # 2. Coins in scope
        coins = self._get_coins()

        # 3+4. Evaluate + signals
        t_eval_start = time.time()
        all_signals: list[Signal] = []
        all_near_misses: list[dict] = []
        all_approaching: list[ApproachingSignal] = []
        results: list[EvaluationResult] = []
        coins_passed = 0

        threshold = self.strategy.evaluation.consensus_threshold

        for coin in coins:
            try:
                result = self.evaluate_coin(coin)
                results.append(result)

                # Emit signals (state machine)
                signals = self.check_signals(coin, result)
                all_signals.extend(signals)

                # Track passed coins
                if result.consensus >= threshold and result.direction != "NONE":
                    coins_passed += 1

                # B1: Approaching detection
                approaching = self._check_approaching(coin, result)
                if approaching:
                    all_approaching.append(approaching)

                # Near miss detection
                near_misses = self.check_near_misses(coin, result)
                all_near_misses.extend(near_misses)

                # Log decision
                self._log_decision(coin, result, signals)

                self.prev_results[coin] = result

            except Exception as e:
                _log(f"  WARN: eval {coin} failed: {e}")

        t_eval_end = time.time()

        # 5. Write near misses
        if all_near_misses:
            self._near_miss_detector.write(all_near_misses)

        # 6. Write signals to bus
        t_signal_start = time.time()
        self._write_signals(all_signals)

        # B1: Write approaching signals to bus + decisions log
        if all_approaching:
            self._write_approaching(all_approaching)
        t_signal_end = time.time()

        # 7. Heartbeat + summary
        cycle_end = time.time()
        summary["coins_evaluated"] = len(results)
        summary["signals_emitted"] = len(all_signals)
        summary["near_misses"] = len(all_near_misses)
        summary["approaching"] = len(all_approaching)
        summary["cycle_ms"] = int((cycle_end - cycle_start) * 1000)

        self._write_heartbeat(summary)

        # B4: Compute and log cycle metrics
        data_sources_available = sum(1 for s in ("prices", "funding", "oi") if not self.cache.is_source_stale(s))
        data_sources_stale = 3 - data_sources_available
        try:
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        except Exception:
            mem_mb = 0.0

        metrics = CycleMetrics(
            cycle_number=self.cycle_count,
            timestamp=_now_iso(),
            cycle_duration_ms=int((cycle_end - cycle_start) * 1000),
            data_fetch_duration_ms=int((t_fetch_end - t_fetch_start) * 1000),
            evaluation_duration_ms=int((t_eval_end - t_eval_start) * 1000),
            signal_emission_duration_ms=int((t_signal_end - t_signal_start) * 1000),
            data_freshness_max_ms=self.cache.price_age_ms(),
            data_sources_available=data_sources_available,
            data_sources_stale=data_sources_stale,
            coins_evaluated=len(results),
            coins_passed=coins_passed,
            coins_rejected=len(results) - coins_passed,
            coins_approaching=len(all_approaching),
            signals_emitted=len(all_signals),
            memory_mb=round(mem_mb, 2),
        )
        self.last_cycle_metrics = metrics
        self._log_metrics(metrics)

        _log(
            f"[CYCLE {self.cycle_count}] "
            f"coins={len(results)} signals={len(all_signals)} "
            f"approaching={len(all_approaching)} "
            f"near_miss={len(all_near_misses)} "
            f"ms={summary['cycle_ms']}"
        )

        return summary

    def evaluate_coin(self, coin: str, direction_hint: str = "") -> EvaluationResult:
        """
        Evaluate a single coin through all 7 layers.
        Uses SmartProvider internally, wraps output in LayerResult dataclasses.
        """
        ts = _now_iso()
        price = self.cache.get_price(coin) or 0.0
        data_age_ms = self.cache.price_age_ms()
        data_complete = self.cache.data_complete() and not self.cache.any_source_stale()

        # Run SmartProvider
        sp_result: dict = {}
        try:
            sp_result = self.smart_provider.evaluate_coin(coin)
        except Exception as e:
            _log(f"SmartProvider.evaluate_coin({coin}) failed: {e}")
            sp_result = {
                "signal": "NEUTRAL", "direction": "NEUTRAL",
                "confidence": 0, "quality": 0, "regime": "insufficient_data",
                "indicator_votes": {}, "indicators": {}, "funding_rate": 0,
            }

        regime = sp_result.get("regime", "insufficient_data")
        sp_direction = sp_result.get("direction", "NEUTRAL")
        indicator_votes = sp_result.get("indicator_votes", {})
        indicators = sp_result.get("indicators", {})

        # Determine target direction
        if direction_hint:
            direction = direction_hint.upper()
        elif sp_direction != "NEUTRAL":
            direction = sp_direction
        else:
            direction = "NONE"

        # ── Build 7 layers ────────────────────────────────────────────────────

        layers: list[LayerResult] = []

        # Layer 1: Regime
        regime_passed = self.strategy.allows_regime(regime)
        layers.append(LayerResult(
            layer="regime",
            passed=regime_passed,
            value=regime,
            detail=f"regime={regime} allowed={self.strategy.evaluation.min_regime}",
        ))

        # Layer 2: Technical (RSI + MACD + EMA + BB majority agree on direction)
        tech_layer = self._eval_technical_layer(indicator_votes, indicators, direction)
        layers.append(tech_layer)

        # Layer 3: Funding
        funding_layer = self._eval_funding_layer(sp_result, direction)
        layers.append(funding_layer)

        # Layer 4: Book depth
        book_layer = self._eval_book_layer(coin, direction)
        layers.append(book_layer)

        # Layer 5: OI
        oi_layer = self._eval_oi_layer(coin, direction)
        layers.append(oi_layer)

        # Layer 6: Macro (Fear & Greed contrarian)
        macro_layer = self._eval_macro_layer(direction)
        layers.append(macro_layer)

        # Layer 7: Collective consensus
        collective_layer = self._eval_collective_layer(coin, direction)
        layers.append(collective_layer)

        # ── Consensus count (layers that are available AND passed) ────────────
        available_layers = [lr for lr in layers if lr.data_available]
        passed_layers = [lr for lr in available_layers if lr.passed]
        consensus = len(passed_layers)

        # Conviction: % of available layers that passed
        conviction = (consensus / len(available_layers)) if available_layers else 0.0

        # Final direction: use SmartProvider direction if we have conviction, else NONE
        if direction == "NONE" or consensus == 0:
            final_direction = "NONE"
        else:
            # Respect strategy direction filters
            if self.strategy.allows_direction(direction):
                final_direction = direction
            else:
                final_direction = "NONE"

        return EvaluationResult(
            coin=coin,
            timestamp=ts,
            layers=layers,
            consensus=consensus,
            conviction=round(conviction, 4),
            direction=final_direction,
            regime=regime,
            price=price,
            data_age_ms=data_age_ms,
            data_complete=data_complete,
        )

    def _eval_technical_layer(
        self,
        indicator_votes: dict,
        indicators: dict,
        direction: str,
    ) -> LayerResult:
        """Technical layer: RSI, MACD, EMA, BB majority agree on direction."""
        technical_indicators = ["rsi", "macd", "ema", "bollinger"]
        agree = 0
        total = 0
        detail_parts = []

        for ind_name in technical_indicators:
            vote = indicator_votes.get(ind_name, "neutral")
            total += 1
            if direction != "NONE" and vote == direction.lower():
                agree += 1
                detail_parts.append(f"{ind_name}=✓")
            else:
                detail_parts.append(f"{ind_name}={vote}")

        # Also check RSI value directly
        rsi_val = indicators.get("RSI_14", 50.0)
        majority = agree > total / 2 if total > 0 else False

        return LayerResult(
            layer="technical",
            passed=majority,
            value={"agree": agree, "total": total, "rsi": rsi_val},
            detail=f"technical: {'/'.join(detail_parts)} agree={agree}/{total}",
        )

    def _eval_funding_layer(self, sp_result: dict, direction: str) -> LayerResult:
        """Funding layer: funding favorable for the trade direction."""
        funding_rate = sp_result.get("funding_rate", 0.0)
        funding_votes = sp_result.get("indicator_votes", {})
        funding_signal = funding_votes.get("funding", "neutral")

        # Favorable: contrarian signal agrees with direction
        # positive funding → longs pay shorts → favorable for SHORT
        # negative funding → shorts pay longs → favorable for LONG
        if direction == "LONG":
            favorable = funding_rate <= 0.0001 or funding_signal == "long"
        elif direction == "SHORT":
            favorable = funding_rate >= -0.0001 or funding_signal == "short"
        else:
            favorable = False

        return LayerResult(
            layer="funding",
            passed=favorable,
            value=round(funding_rate, 8),
            detail=f"funding_rate={funding_rate:.6f} signal={funding_signal} direction={direction}",
        )

    def _eval_book_layer(self, coin: str, direction: str) -> LayerResult:
        """Book layer: L2 depth ratio favorable for direction."""
        bid_depth, ask_depth = self.cache.get_book(coin)
        total_depth = bid_depth + ask_depth

        if total_depth < 1000:
            # Low liquidity — mark unavailable
            return LayerResult(
                layer="book",
                passed=False,
                value={"bid": bid_depth, "ask": ask_depth},
                detail="book: insufficient liquidity",
                data_available=False,
            )

        bid_ratio = bid_depth / total_depth

        # LONG: more bids than asks (bid_ratio > 0.5)
        # SHORT: more asks than bids (bid_ratio < 0.5)
        if direction == "LONG":
            passed = bid_ratio > 0.5
        elif direction == "SHORT":
            passed = bid_ratio < 0.5
        else:
            passed = False

        return LayerResult(
            layer="book",
            passed=passed,
            value={"bid_ratio": round(bid_ratio, 4), "total_depth_usd": round(total_depth, 0)},
            detail=f"book: bid_ratio={bid_ratio:.3f} direction={direction}",
        )

    def _eval_oi_layer(self, coin: str, direction: str) -> LayerResult:
        """OI layer: open interest confirming direction."""
        oi = self.cache.get_oi(coin)
        prev_result = self.prev_results.get(coin)

        if oi == 0:
            return LayerResult(
                layer="OI",
                passed=False,
                value=0,
                detail="OI: no data",
                data_available=False,
            )

        # Compare OI to previous cycle to detect direction
        # If no previous data, just check OI is non-zero (confirming activity)
        # But if direction is NONE, OI can't confirm anything
        passed = oi > 0 and direction != "NONE"

        detail = f"OI={oi:.0f}"
        if prev_result is not None:
            # Find previous OI from prev result layers
            prev_oi_layer = next((lr for lr in prev_result.layers if lr.layer == "OI"), None)
            if prev_oi_layer and isinstance(prev_oi_layer.value, (int, float)) and prev_oi_layer.value > 0:
                prev_oi = float(prev_oi_layer.value)
                oi_change = (oi - prev_oi) / prev_oi if prev_oi else 0
                # Rising OI confirms direction
                if direction == "LONG":
                    passed = oi_change >= 0
                elif direction == "SHORT":
                    passed = oi_change >= 0  # rising OI on shorts also confirms
                detail = f"OI={oi:.0f} prev={prev_oi:.0f} chg={oi_change:+.2%}"

        return LayerResult(
            layer="OI",
            passed=passed,
            value=oi,
            detail=detail,
        )

    def _eval_macro_layer(self, direction: str) -> LayerResult:
        """Macro layer: Fear & Greed contrarian signal supports direction."""
        fg = self.cache.fear_greed

        # Contrarian: extreme fear → bullish (LONG), extreme greed → bearish (SHORT)
        # Fear (0-40) → supports LONG entry
        # Greed (60-100) → supports SHORT entry
        # Neutral (40-60) → no strong signal
        if direction == "LONG":
            passed = fg <= 40
        elif direction == "SHORT":
            passed = fg >= 60
        else:
            passed = False

        if fg <= 25:
            sentiment = "extreme_fear"
        elif fg <= 40:
            sentiment = "fear"
        elif fg <= 60:
            sentiment = "neutral"
        elif fg <= 75:
            sentiment = "greed"
        else:
            sentiment = "extreme_greed"

        return LayerResult(
            layer="macro",
            passed=passed,
            value=fg,
            detail=f"fear_greed={fg} sentiment={sentiment} direction={direction}",
        )

    def _eval_collective_layer(self, coin: str, direction: str) -> LayerResult:
        """Collective layer: network consensus >60% agree on direction."""
        try:
            collective_file = self._bus_dir / "collective_signals.json"
            if not collective_file.exists():
                return LayerResult(
                    layer="collective",
                    passed=False,
                    value=None,
                    detail="collective: no data file",
                    data_available=False,
                )

            data = json.loads(collective_file.read_text())
            coin_data = data.get("coins", {}).get(coin, {})

            if not coin_data:
                return LayerResult(
                    layer="collective",
                    passed=False,
                    value=None,
                    detail=f"collective: no data for {coin}",
                    data_available=False,
                )

            # Check consensus direction and pct agreement
            consensus_dir = coin_data.get("direction", "NEUTRAL").upper()
            agreement_pct = float(coin_data.get("agreement_pct", 0))

            if agreement_pct == 0:
                # Try to compute from votes
                long_votes = coin_data.get("long_votes", 0)
                short_votes = coin_data.get("short_votes", 0)
                total_votes = long_votes + short_votes
                if total_votes > 0:
                    if direction == "LONG":
                        agreement_pct = long_votes / total_votes
                    elif direction == "SHORT":
                        agreement_pct = short_votes / total_votes
                    else:
                        agreement_pct = 0

            passed = (
                agreement_pct > 0.60
                and consensus_dir == direction
            )

            return LayerResult(
                layer="collective",
                passed=passed,
                value={"agreement_pct": round(agreement_pct, 3), "direction": consensus_dir},
                detail=f"collective: direction={consensus_dir} agreement={agreement_pct:.1%}",
            )
        except Exception as e:
            return LayerResult(
                layer="collective",
                passed=False,
                value=None,
                detail=f"collective: error={e}",
                data_available=False,
            )

    def check_signals(self, coin: str, result: EvaluationResult) -> list[Signal]:
        """
        Apply the state machine to emit signals for a coin.

        States: inactive | entry | entry_end
        """
        signals: list[Signal] = []
        threshold = self.strategy.evaluation.consensus_threshold
        state = self.coin_states.get(coin, "inactive")
        prev = self.prev_results.get(coin)

        consensus = result.consensus
        direction = result.direction
        regime = result.regime

        # Check exit conditions
        exit_triggered, exit_reason = self._check_exit_conditions(result, prev)

        passed = (
            consensus >= threshold
            and direction != "NONE"
            and self.strategy.allows_regime(regime)
        )

        # ── State machine ─────────────────────────────────────────────────────

        if state == "inactive":
            if passed:
                sig = Signal(
                    type="ENTRY",
                    coin=coin,
                    direction=direction,
                    timestamp=result.timestamp,
                    price=result.price,
                    consensus=consensus,
                    conviction=result.conviction,
                    layers=[lr.layer for lr in result.layers if lr.passed],
                    regime=regime,
                    reason="consensus_threshold_met",
                    would_pass_strategies=self._get_passing_strategies(result),
                )
                signals.append(sig)
                self.coin_states[coin] = "entry"

        elif state == "entry":
            if exit_triggered:
                # Build layers_lost vs layers_remaining
                layers_now = {lr.layer for lr in result.layers if lr.passed}
                layers_prev = set()
                if prev:
                    layers_prev = {lr.layer for lr in prev.layers if lr.passed}

                sig = Signal(
                    type="EXIT",
                    coin=coin,
                    direction=direction,
                    timestamp=result.timestamp,
                    price=result.price,
                    consensus=consensus,
                    conviction=result.conviction,
                    layers=[lr.layer for lr in result.layers if lr.passed],
                    regime=regime,
                    reason=exit_reason,
                    layers_remaining=len(layers_now),
                    layers_lost=list(layers_prev - layers_now),
                )
                signals.append(sig)
                self.coin_states[coin] = "inactive"

            elif not passed:
                # Consensus fell below threshold
                layers_now = {lr.layer for lr in result.layers if lr.passed}
                layers_prev = set()
                if prev:
                    layers_prev = {lr.layer for lr in prev.layers if lr.passed}

                sig = Signal(
                    type="ENTRY_END",
                    coin=coin,
                    direction=direction,
                    timestamp=result.timestamp,
                    price=result.price,
                    consensus=consensus,
                    conviction=result.conviction,
                    layers=[lr.layer for lr in result.layers if lr.passed],
                    regime=regime,
                    reason="consensus_below_threshold",
                    layers_remaining=consensus,
                    layers_lost=list(layers_prev - layers_now),
                )
                signals.append(sig)
                self.coin_states[coin] = "entry_end"

            # else: still passed → stay (dedup, no re-emit)

        elif state == "entry_end":
            if exit_triggered:
                sig = Signal(
                    type="EXIT",
                    coin=coin,
                    direction=direction,
                    timestamp=result.timestamp,
                    price=result.price,
                    consensus=consensus,
                    conviction=result.conviction,
                    layers=[lr.layer for lr in result.layers if lr.passed],
                    regime=regime,
                    reason=exit_reason,
                )
                signals.append(sig)
                self.coin_states[coin] = "inactive"

            elif passed:
                # Re-entry
                sig = Signal(
                    type="ENTRY",
                    coin=coin,
                    direction=direction,
                    timestamp=result.timestamp,
                    price=result.price,
                    consensus=consensus,
                    conviction=result.conviction,
                    layers=[lr.layer for lr in result.layers if lr.passed],
                    regime=regime,
                    reason="re_entry",
                    would_pass_strategies=self._get_passing_strategies(result),
                )
                signals.append(sig)
                self.coin_states[coin] = "entry"

        return signals

    def _check_exit_conditions(
        self, result: EvaluationResult, prev: EvaluationResult | None
    ) -> tuple[bool, str]:
        """
        Check signal-based exit conditions.

        Returns (triggered, reason).

        Exit conditions:
        - RSI overbought (>70 for longs, <30 for shorts)
        - Regime shift to excluded regime
        """
        direction = result.direction

        # Check RSI overbought/oversold
        for lr in result.layers:
            if lr.layer == "technical" and isinstance(lr.value, dict):
                rsi = lr.value.get("rsi", 50)
                if direction == "LONG" and rsi > 70:
                    return True, f"rsi_overbought_{rsi:.0f}"
                if direction == "SHORT" and rsi < 30:
                    return True, f"rsi_oversold_{rsi:.0f}"

        # Check regime shift to excluded regime
        if not self.strategy.allows_regime(result.regime):
            return True, f"regime_shift_{result.regime}"

        return False, ""

    def _get_passing_strategies(self, result: EvaluationResult) -> list[str]:
        """Return list of strategy names that this result would pass."""
        passing = []
        try:
            all_strategies = load_all_strategies()
            for name, strat in all_strategies.items():
                if (
                    result.consensus >= strat.evaluation.consensus_threshold
                    and result.direction != "NONE"
                    and strat.allows_direction(result.direction)
                    and strat.allows_regime(result.regime)
                ):
                    passing.append(name)
        except Exception:
            pass
        return passing

    def check_near_misses(self, coin: str, result: EvaluationResult) -> list[dict]:
        """Cross-check against all strategies to find near-misses."""
        return self._near_miss_detector.check(coin, result, self.strategy)

    # ── B1: Approaching Detection ─────────────────────────────────────────────

    def _check_approaching(self, coin: str, result: EvaluationResult) -> ApproachingSignal | None:
        """Detect coins approaching consensus threshold. Returns signal or None."""
        threshold = self.strategy.evaluation.consensus_threshold
        consensus = result.consensus
        distance = threshold - consensus

        # Only emit if within 2 of threshold and below it
        if distance < 1 or distance > 2:
            # If at or above threshold, clear approaching state
            if distance <= 0:
                self.approaching_states.pop(coin, None)
            return None

        # Dedup: only emit if consensus changed
        prev_consensus = self.approaching_states.get(coin)
        if prev_consensus == consensus:
            return None

        # Detect cooling: consensus dropped from closer to threshold
        if prev_consensus is not None and consensus < prev_consensus:
            urgency = "cooling"
        elif distance == 1:
            urgency = "high"
        else:
            urgency = "low"

        self.approaching_states[coin] = consensus

        # Bottleneck: find closest-to-passing failing layer
        passing = [lr.layer for lr in result.layers if lr.passed]
        failing = [lr for lr in result.layers if not lr.passed and lr.data_available]
        bottleneck = failing[0].layer if failing else ""
        bottleneck_detail = failing[0].detail if failing else ""

        return ApproachingSignal(
            coin=coin,
            consensus=consensus,
            threshold=threshold,
            distance=distance,
            passing_layers=passing,
            failing_layers=[lr.layer for lr in failing],
            bottleneck=bottleneck,
            bottleneck_detail=bottleneck_detail,
            direction=result.direction,
            price=result.price,
            timestamp=result.timestamp,
            urgency=urgency,
        )

    def _write_approaching(self, signals: list[ApproachingSignal]):
        """Write approaching signals to bus and decisions log."""
        # Write to bus/approaching.json for MCP access
        payload = {
            "updated_at": _now_iso(),
            "approaching": [s.to_dict() for s in signals],
        }
        _save_atomic(self._bus_dir / "approaching.json", payload)

        # Log to decisions.jsonl and emit events
        for sig in signals:
            record = {
                "type": "APPROACHING",
                "coin": sig.coin,
                "consensus": sig.consensus,
                "threshold": sig.threshold,
                "distance": sig.distance,
                "urgency": sig.urgency,
                "bottleneck": sig.bottleneck,
                "direction": sig.direction,
                "price": sig.price,
                "timestamp": sig.timestamp,
            }
            self._decisions_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._decisions_file, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

    # ── B4: Metrics Logging ──────────────────────────────────────────────────

    def _log_metrics(self, metrics: CycleMetrics):
        """Append cycle metrics to bus/metrics.jsonl."""
        self._metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._metrics_file, "a") as f:
            f.write(json.dumps(metrics.to_dict(), default=str) + "\n")

    def _get_coins(self) -> list[str]:
        """Get coins in scope from strategy config."""
        scope = self.strategy.evaluation.scope  # e.g. "top_50"
        n = int(scope.replace("top_", ""))

        # Try to load from strategies.json
        strategies_file = V6_DIR / "data" / "strategies.json"
        if strategies_file.exists():
            try:
                data = json.loads(strategies_file.read_text())
                coins = data.get("active_coins", [])
                if coins:
                    return coins[:n]
            except Exception:
                pass

        # Fallback: hardcoded top coins
        all_coins = [
            "BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "SUI",
            "ADA", "NEAR", "OP", "BNB", "AAVE", "SEI", "TIA", "INJ",
            "DOT", "UNI", "LTC", "BCH", "WLD", "ONDO", "JUP", "TON",
            "ARB", "ATOM", "FIL", "ICP", "APT", "ALGO", "HBAR", "XLM",
            "VET", "SAND", "MANA", "AXS", "ENJ", "CHZ", "GALA", "IMX",
            "LDO", "CRV", "BAL", "SNX", "YFI", "COMP", "MKR", "SUSHI",
            "1INCH", "ZRX",
        ]
        return all_coins[:n]

    def _write_signals(self, signals: list[Signal]):
        """Write all active signals to bus/signals.json."""
        payload = {
            "updated_at": _now_iso(),
            "signals": [s.to_dict() for s in signals],
        }
        _save_atomic(self._signals_file, payload)

    def _log_decision(self, coin: str, result: EvaluationResult, signals: list[Signal]):
        """Append decision record to decisions.jsonl."""
        record = {
            "coin": coin,
            "timestamp": result.timestamp,
            "direction": result.direction,
            "consensus": result.consensus,
            "conviction": result.conviction,
            "regime": result.regime,
            "price": result.price,
            "state": self.coin_states.get(coin, "inactive"),
            "signals": [s.type for s in signals],
            "layers": {lr.layer: lr.passed for lr in result.layers},
            "data_complete": result.data_complete,
        }
        self._decisions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._decisions_file, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _write_heartbeat(self, summary: dict):
        """Update heartbeat.json with monitor cycle info."""
        try:
            hb = {}
            if self._heartbeat_file.exists():
                try:
                    hb = json.loads(self._heartbeat_file.read_text())
                except Exception:
                    pass
            hb["monitor"] = _now_iso()
            hb["monitor_cycle"] = self.cycle_count
            hb["monitor_summary"] = summary
            _save_atomic(self._heartbeat_file, hb)
        except Exception as e:
            _log(f"Heartbeat write failed: {e}")


# ─── Main loop ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ZERO Monitor — 7-layer market evaluation")
    parser.add_argument("--strategy", default="momentum", help="Strategy name")
    parser.add_argument("--loop", action="store_true", help="Continuous 60s cycle loop")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit (default)")
    args = parser.parse_args()

    _log(f"Starting Monitor with strategy={args.strategy}")

    monitor = Monitor(strategy_name=args.strategy)

    if args.loop:
        while True:
            cycle_start = time.time()
            try:
                summary = monitor.run_cycle()
                _log(f"Cycle complete: {summary}")
            except Exception as e:
                _log(f"Cycle error: {e}")

            # Sleep until next 60s mark
            elapsed = time.time() - cycle_start
            sleep_time = max(0, 60 - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    else:
        summary = monitor.run_cycle()
        _log(f"Done: {summary}")


if __name__ == "__main__":
    main()

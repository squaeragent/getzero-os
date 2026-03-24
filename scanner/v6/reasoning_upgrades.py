# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
reasoning_upgrades.py — Five upgrades to the reasoning engine.

1. ConvictionSizer — size scales with confidence
2. ExitIntelligence — trailing + regime exit + profit target
3. RegimeMemory — per-coin regime history
4. PersonalLearner — personal indicator weights
5. CorrelationEngine — portfolio-level risk

Each upgrade raises P&L. Under performance fee model,
better reasoning = more revenue for both operator and zero.
"""

import json
import math
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [reasoning] [{ts}] {msg}", flush=True)


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation: a + (b - a) * t"""
    return a + (b - a) * max(0, min(1, t))


# ─── UPGRADE 1: CONVICTION SIZING ────────────────────────────────────────────

class ConvictionSizer:
    """Position size scales with conviction.
    Higher consensus + stronger regime + favorable funding = bigger position.
    """
    def __init__(self, base_pct=0.15, min_pct=0.08, max_pct=0.25):
        self.base = base_pct
        self.min = min_pct
        self.max = max_pct

    def calculate_size(self, equity: float, signal: dict) -> float:
        """
        signal keys:
          quality: 0-8 (SmartProvider quality score)
          confidence: 0-1 (consensus_pct from voting)
          hurst: 0-1
          dfa: 0-1
          funding_rate: float (annualized)
          book_depth: float (USD)
        """
        # 1. Consensus conviction: 60% threshold → 100%
        confidence = signal.get("confidence", 0.5)
        consensus_factor = max(0, min(1, (confidence - 0.60) / 0.40))

        # 2. Regime confidence: H and DFA alignment
        hurst = signal.get("hurst", 0.5)
        dfa = signal.get("dfa", 0.5)
        # Both far from 0.5 in same direction = high confidence
        h_strength = abs(hurst - 0.5) * 2  # 0-1
        d_strength = abs(dfa - 0.5) * 2    # 0-1
        h_trending = hurst > 0.5
        d_trending = dfa > 0.5
        alignment = h_strength * d_strength if h_trending == d_trending else h_strength * d_strength * 0.3
        regime_factor = min(1, alignment * 2)

        # 3. Funding favorability: negative funding when long = favorable
        funding = signal.get("funding_rate", 0)
        direction = signal.get("direction", "LONG")
        if direction == "LONG":
            funding_favor = -funding * 100  # negative funding = good for longs
        else:
            funding_favor = funding * 100   # positive funding = good for shorts
        funding_factor = 0.5 + max(-0.2, min(0.2, funding_favor))

        # 4. Composite conviction
        conviction = (
            consensus_factor * 0.50 +
            regime_factor * 0.30 +
            funding_factor * 0.20
        )

        # 5. Map conviction to size
        size_pct = self.min + (conviction * (self.max - self.min))

        # 6. Dollar size
        return round(equity * size_pct, 2)


# Global sizer instance
_sizer = ConvictionSizer()


def conviction_size(equity: float, signal: dict) -> float:
    """Drop-in replacement for flat sizing."""
    return _sizer.calculate_size(equity, signal)


# ─── UPGRADE 2: EXIT INTELLIGENCE ────────────────────────────────────────────

class ExitIntelligence:
    """Three exit mechanisms running simultaneously.
    Whichever triggers first, wins.
    Priority: regime_exit > trailing_stop > profit_target.
    """

    def check_exit(self, position: dict, current_price: float,
                   atr: float, current_regime: str, hurst: float = 0.5,
                   historical_mfe_pct: float | None = None) -> dict | None:
        """
        Returns exit signal dict or None.
        position keys: entry_price, direction, current_stop, entry_regime, regime_change_count
        """
        entry_price = position.get("entry_price", 0)
        direction = position.get("direction", "LONG")

        if entry_price <= 0 or current_price <= 0:
            return None

        # Unrealized P&L
        if direction == "LONG":
            unrealized_pct = (current_price - entry_price) / entry_price
        else:
            unrealized_pct = (entry_price - current_price) / entry_price

        # ── Trailing stop ──
        trailing = self._check_trailing(position, current_price, atr, unrealized_pct, direction)

        # ── Regime exit ──
        regime_exit = self._check_regime_exit(position, current_regime, hurst)

        # ── Profit target ──
        profit_target = self._check_profit_target(unrealized_pct, historical_mfe_pct)

        # Return highest priority trigger
        signals = [s for s in [regime_exit, trailing, profit_target] if s is not None]
        if not signals:
            return None
        return min(signals, key=lambda s: s.get("priority", 99))

    def _check_trailing(self, position: dict, current_price: float,
                        atr: float, unrealized_pct: float, direction: str) -> dict | None:
        """Trailing stop that tightens as profit grows."""
        if atr <= 0:
            return None

        atr_pct = atr / current_price if current_price > 0 else 0.01
        profit_in_atrs = unrealized_pct / atr_pct if atr_pct > 0 else 0

        # Trail distance narrows with profit
        if profit_in_atrs <= 0:
            trail_mult = 2.0
        elif profit_in_atrs < 1:
            trail_mult = lerp(2.0, 1.5, profit_in_atrs)
        elif profit_in_atrs < 2:
            trail_mult = lerp(1.5, 1.0, profit_in_atrs - 1)
        else:
            trail_mult = lerp(1.0, 0.75, min(profit_in_atrs - 2, 1))

        trail_distance = trail_mult * atr
        current_stop = position.get("current_stop", 0)

        if direction == "LONG":
            new_stop = current_price - trail_distance
            if current_stop > 0:
                new_stop = max(new_stop, current_stop)  # never widen
            if current_price <= new_stop and unrealized_pct > 0.005:
                return {"reason": "trailing_stop", "priority": 2,
                        "detail": f"trailing stop tightened to ${new_stop:.4f}"}
        else:
            new_stop = current_price + trail_distance
            if current_stop > 0:
                new_stop = min(new_stop, current_stop)
            if current_price >= new_stop and unrealized_pct > 0.005:
                return {"reason": "trailing_stop", "priority": 2,
                        "detail": f"trailing stop tightened to ${new_stop:.4f}"}

        # Update stop in position dict (caller reads this)
        position["_new_stop"] = new_stop
        return None

    def _check_regime_exit(self, position: dict, current_regime: str,
                           hurst: float) -> dict | None:
        """Exit when entry regime changes (with 2-cycle grace)."""
        entry_regime = position.get("entry_regime", "")
        if not entry_regime or not current_regime:
            return None

        if current_regime != entry_regime:
            change_count = position.get("regime_change_count", 0) + 1
            position["regime_change_count"] = change_count
            if change_count >= 2:
                return {"reason": "regime_change", "priority": 1,
                        "detail": f"{entry_regime} → {current_regime}"}

        # Regime weakening: trend dying
        if entry_regime in ("strong_trend", "moderate_trend") and hurst < 0.55:
            return {"reason": "regime_weakening", "priority": 1,
                    "detail": f"hurst dropped to {hurst:.2f} (trend dying)"}

        return None

    def _check_profit_target(self, unrealized_pct: float,
                             historical_mfe_pct: float | None) -> dict | None:
        """Exit near historical MFE for this regime+coin."""
        if historical_mfe_pct is None or historical_mfe_pct <= 0:
            return None

        if unrealized_pct > historical_mfe_pct * 0.80:
            return {"reason": "profit_target", "priority": 3,
                    "detail": f"reached {unrealized_pct*100:.1f}% of {historical_mfe_pct*100:.1f}% avg MFE"}

        return None

    def compute_new_stop(self, position: dict, current_price: float, atr: float) -> float | None:
        """Compute trailing stop price without triggering exit. Returns new stop or None."""
        if atr <= 0 or current_price <= 0:
            return None

        entry_price = position.get("entry_price", 0)
        direction = position.get("direction", "LONG")
        if entry_price <= 0:
            return None

        if direction == "LONG":
            unrealized_pct = (current_price - entry_price) / entry_price
        else:
            unrealized_pct = (entry_price - current_price) / entry_price

        atr_pct = atr / current_price
        profit_in_atrs = unrealized_pct / atr_pct if atr_pct > 0 else 0

        if profit_in_atrs <= 0:
            trail_mult = 2.0
        elif profit_in_atrs < 1:
            trail_mult = lerp(2.0, 1.5, profit_in_atrs)
        elif profit_in_atrs < 2:
            trail_mult = lerp(1.5, 1.0, profit_in_atrs - 1)
        else:
            trail_mult = lerp(1.0, 0.75, min(profit_in_atrs - 2, 1))

        trail_distance = trail_mult * atr
        current_stop = position.get("current_stop", 0)

        if direction == "LONG":
            new_stop = current_price - trail_distance
            if current_stop > 0:
                new_stop = max(new_stop, current_stop)
        else:
            new_stop = current_price + trail_distance
            if current_stop > 0:
                new_stop = min(new_stop, current_stop)

        return round(new_stop, 6)


# Global exit intelligence
_exit_intel = ExitIntelligence()


# ─── UPGRADE 3: REGIME MEMORY ────────────────────────────────────────────────

class RegimeMemory:
    """Per-coin regime history. What happened LAST TIME this regime occurred?"""

    def __init__(self):
        self._periods: dict[str, list[dict]] = {}
        self._state_dir = Path.home() / ".zeroos" / "state" / "regime_memory"
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def record_period(self, coin: str, regime: str, duration_hours: float,
                      trade_count: int, wins: int, total_pnl_pct: float,
                      transition_to: str = "unknown"):
        """Record a completed regime period."""
        periods = self._load_coin(coin)
        periods.append({
            "regime": regime,
            "duration_hours": round(duration_hours, 1),
            "trade_count": trade_count,
            "wins": wins,
            "total_pnl_pct": round(total_pnl_pct, 4),
            "transition_to": transition_to,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 50 periods per coin
        if len(periods) > 50:
            periods = periods[-50:]
        self._save_coin(coin, periods)

    def get_context(self, coin: str, current_regime: str) -> dict:
        """What does memory say about this coin in this regime?"""
        periods = self._load_coin(coin)
        matching = [p for p in periods if p.get("regime") == current_regime]

        if len(matching) < 2:
            return {
                "has_history": False,
                "message": f"first or second {current_regime} period for {coin}. no pattern yet.",
                "quality_multiplier": 1.0,
            }

        avg_dur = sum(p["duration_hours"] for p in matching) / len(matching)
        avg_pnl = sum(p["total_pnl_pct"] for p in matching) / len(matching)
        total_trades = sum(p.get("trade_count", 0) for p in matching)
        total_wins = sum(p.get("wins", 0) for p in matching)
        win_rate = total_wins / max(total_trades, 1)
        last = matching[-1]

        # Quality multiplier based on history
        if avg_pnl < -0.005:  # historically loses > 0.5%
            quality_mult = 0.5
        elif win_rate > 0.6:
            quality_mult = 1.1
        else:
            quality_mult = 1.0

        msg = (
            f"{coin} has been {current_regime} {len(matching)} times in 90 days. "
            f"avg duration: {avg_dur:.1f}h. avg P&L: {avg_pnl*100:+.1f}%. "
            f"win rate: {win_rate:.0%}. "
            f"last ended → {last.get('transition_to', '?')} after {last['duration_hours']:.1f}h."
        )
        if avg_pnl < -0.005:
            msg += f" ⚠ {coin} historically loses in {current_regime}."

        return {
            "has_history": True,
            "periods_count": len(matching),
            "avg_duration_hours": round(avg_dur, 1),
            "avg_pnl_pct": round(avg_pnl, 4),
            "win_rate": round(win_rate, 2),
            "quality_multiplier": quality_mult,
            "message": msg,
        }

    def _load_coin(self, coin: str) -> list[dict]:
        if coin in self._periods:
            return self._periods[coin]
        fpath = self._state_dir / f"{coin.lower()}.json"
        if fpath.exists():
            try:
                self._periods[coin] = json.loads(fpath.read_text())
            except Exception:
                self._periods[coin] = []
        else:
            self._periods[coin] = []
        return self._periods[coin]

    def _save_coin(self, coin: str, periods: list[dict]):
        self._periods[coin] = periods
        fpath = self._state_dir / f"{coin.lower()}.json"
        fpath.write_text(json.dumps(periods, indent=2, default=str))


# Global regime memory
_regime_memory = RegimeMemory()


# ─── UPGRADE 4: PERSONAL FEEDBACK LOOP ───────────────────────────────────────

class PersonalLearner:
    """Learns from THIS agent's trades. Produces personal indicator weights."""

    INDICATORS = ["rsi", "macd", "ema", "bollinger", "obv", "funding"]

    def __init__(self, agent_id: str = "default"):
        self.agent_id = agent_id
        self.personal_weights: dict[str, dict[str, float]] | None = None
        self._state_file = Path.home() / ".zeroos" / "state" / "personal_weights.json"
        self._load()

    def _load(self):
        if self._state_file.exists():
            try:
                self.personal_weights = json.loads(self._state_file.read_text())
            except Exception:
                self.personal_weights = None

    def _save(self):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.personal_weights:
            self._state_file.write_text(json.dumps(self.personal_weights, indent=2))

    def learn(self, trades: list[dict]):
        """Run after every 50 new trades. trades = enriched trade records."""
        if len(trades) < 50:
            return

        regime_groups: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            regime = t.get("entry_regime")
            if regime:
                regime_groups[regime].append(t)

        personal: dict[str, dict[str, float]] = {}

        for regime, regime_trades in regime_groups.items():
            if len(regime_trades) < 10:
                continue

            accuracies: dict[str, float] = {}
            for indicator in self.INDICATORS:
                correct = 0
                total = 0
                for trade in regime_trades:
                    votes = trade.get("indicator_votes", {})
                    vote = votes.get(indicator)
                    if vote in ("long", "short"):
                        total += 1
                        direction = trade.get("direction", "").lower()
                        profitable = (trade.get("pnl_pct") or 0) > 0
                        voted_with = vote == direction
                        if (voted_with and profitable) or (not voted_with and not profitable):
                            correct += 1
                if total >= 5:
                    accuracies[indicator] = round(correct / total, 3)

            if accuracies:
                personal[regime] = accuracies

        self.personal_weights = personal if personal else None
        self._save()
        _log(f"personal weights updated from {len(trades)} trades, {len(personal)} regimes")

    def blend_weights(self, collective_weights: dict, regime: str) -> dict:
        """Blend collective (60%) with personal (40%)."""
        collective = collective_weights.get(regime, {})
        personal = (self.personal_weights or {}).get(regime, {})

        blended = {}
        for indicator in self.INDICATORS:
            c = collective.get(indicator, 0.5)
            p = personal.get(indicator)
            if p is not None:
                blended[indicator] = round(c * 0.6 + p * 0.4, 3)
            else:
                blended[indicator] = c
        return blended


# Global personal learner
_personal_learner = PersonalLearner()


# ─── UPGRADE 5: CORRELATION ENGINE ───────────────────────────────────────────

class CorrelationEngine:
    """Real-time portfolio correlation management."""

    def __init__(self):
        self.matrix: dict[tuple[str, str], float] = {}
        self._last_update = 0

    def update_matrix(self, returns_data: dict[str, list[float]]):
        """Recompute from price returns. returns_data = {coin: [daily_returns]}."""
        coins = list(returns_data.keys())
        self.matrix = {}

        for i, coin_a in enumerate(coins):
            for j, coin_b in enumerate(coins):
                if i >= j:
                    continue
                ra = returns_data[coin_a]
                rb = returns_data[coin_b]
                n = min(len(ra), len(rb))
                if n < 5:
                    continue
                ra_n = ra[:n]
                rb_n = rb[:n]

                # Pearson correlation
                mean_a = sum(ra_n) / n
                mean_b = sum(rb_n) / n
                cov = sum((ra_n[k] - mean_a) * (rb_n[k] - mean_b) for k in range(n)) / n
                std_a = math.sqrt(sum((x - mean_a) ** 2 for x in ra_n) / n)
                std_b = math.sqrt(sum((x - mean_b) ** 2 for x in rb_n) / n)
                if std_a > 0 and std_b > 0:
                    corr = cov / (std_a * std_b)
                else:
                    corr = 0
                pair = tuple(sorted([coin_a, coin_b]))
                self.matrix[pair] = round(corr, 3)

        self._last_update = time.time()
        _log(f"correlation matrix updated: {len(self.matrix)} pairs")

    def check_entry(self, new_coin: str, new_direction: str,
                    open_positions: list[dict]) -> dict:
        """Check if entry would create too much concentrated exposure."""
        if not open_positions:
            return {"approved": True, "risk_level": "low", "warnings": [],
                    "size_adjustment": 1.0}

        warnings = []
        effective_exposure = 1.0

        for pos in open_positions:
            pair = tuple(sorted([new_coin, pos.get("coin", "")]))
            corr = self.matrix.get(pair, 0)
            same_dir = new_direction.upper() == pos.get("direction", "").upper()

            if same_dir and corr > 0.7:
                warnings.append(
                    f"{new_coin}↔{pos['coin']} corr={corr:.2f} both {new_direction}. concentrated."
                )
                effective_exposure += corr
            elif not same_dir and corr > 0.7:
                warnings.append(
                    f"{new_coin}↔{pos['coin']} corr={corr:.2f} opposite. partial hedge."
                )

        if effective_exposure > 2.5:
            return {"approved": False, "risk_level": "high", "warnings": warnings,
                    "size_adjustment": 0,
                    "message": f"exposure {effective_exposure:.1f}x. too concentrated."}

        if effective_exposure > 1.8:
            return {"approved": True, "risk_level": "elevated", "warnings": warnings,
                    "size_adjustment": 0.7,
                    "message": f"exposure {effective_exposure:.1f}x. reducing size 30%."}

        return {"approved": True, "risk_level": "low", "warnings": warnings,
                "size_adjustment": 1.0}

    def needs_update(self) -> bool:
        return time.time() - self._last_update > 3600


# Global correlation engine
_corr_engine = CorrelationEngine()


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def get_conviction_size(equity: float, signal: dict) -> float:
    """Upgrade 1: conviction-based sizing."""
    return conviction_size(equity, signal)


def check_exit(position: dict, current_price: float, atr: float,
               current_regime: str, hurst: float = 0.5,
               historical_mfe_pct: float | None = None) -> dict | None:
    """Upgrade 2: exit intelligence."""
    return _exit_intel.check_exit(position, current_price, atr,
                                  current_regime, hurst, historical_mfe_pct)


def get_trailing_stop(position: dict, current_price: float, atr: float) -> float | None:
    """Compute trailing stop price."""
    return _exit_intel.compute_new_stop(position, current_price, atr)


def get_regime_context(coin: str, regime: str) -> dict:
    """Upgrade 3: regime memory."""
    return _regime_memory.get_context(coin, regime)


def record_regime_period(coin: str, regime: str, duration_hours: float,
                         trade_count: int, wins: int, total_pnl_pct: float,
                         transition_to: str = "unknown"):
    """Record a completed regime period."""
    _regime_memory.record_period(coin, regime, duration_hours, trade_count,
                                 wins, total_pnl_pct, transition_to)


def learn_personal(trades: list[dict]):
    """Upgrade 4: personal feedback."""
    _personal_learner.learn(trades)


def blend_weights(collective_weights: dict, regime: str) -> dict:
    """Blend collective + personal weights."""
    return _personal_learner.blend_weights(collective_weights, regime)


def check_correlation(new_coin: str, new_direction: str,
                      open_positions: list[dict]) -> dict:
    """Upgrade 5: correlation check."""
    return _corr_engine.check_entry(new_coin, new_direction, open_positions)


def update_correlations(returns_data: dict[str, list[float]]):
    """Update correlation matrix."""
    _corr_engine.update_matrix(returns_data)


def needs_correlation_update() -> bool:
    """Check if correlation matrix needs refresh."""
    return _corr_engine.needs_update()

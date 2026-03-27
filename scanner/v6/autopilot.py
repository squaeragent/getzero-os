"""
Auto-pilot strategy selection.

Reads regime + operator history + backtest data -> picks the best strategy.
When a session ends and the operator chooses auto-pilot, the engine reads
the current regime, checks operator history, and deploys the best-fit
strategy automatically.

Usage:
    from scanner.v6.autopilot import AutoPilot
    pilot = AutoPilot(api)
    decision = pilot.decide()
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ── Strategy-Regime matching rules ───────────────────────────────────────────

REGIME_STRATEGY_MAP = {
    # regime_direction -> best strategies (ordered by fit)
    "SHORT": ["momentum", "degen", "sniper"],
    "LONG": ["momentum", "degen", "sniper"],
    "MIXED": ["defense", "scout", "fade"],
    "QUIET": ["defense", "watch", "funding"],
}

FEAR_GREED_MODIFIERS = {
    "extreme fear": {"degen": +20, "momentum": +10, "defense": -10},
    "fear": {"momentum": +10},
    "neutral": {},
    "greed": {"fade": +15, "defense": +10},
    "extreme greed": {"defense": +25, "fade": +20, "momentum": -15},
}

VOLATILITY_MODIFIERS = {
    "LOW": {"funding": +15, "defense": +10, "degen": -20},
    "NORMAL": {},
    "HIGH": {"sniper": +15, "degen": +10, "defense": +5},
    "EXTREME": {"defense": +30, "watch": +20, "degen": -10, "apex": -20},
}

# All scorable strategies
ALL_STRATEGIES = [
    "momentum", "defense", "watch", "degen", "scout",
    "funding", "sniper", "fade", "apex",
]


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class AutoPilotDecision:
    """Result of auto-pilot strategy selection."""
    strategy: str           # chosen strategy name
    confidence: float       # 0.0-1.0 how confident
    reason: str             # one-line explanation
    regime: str             # current regime used for decision
    operator_wr: Optional[float] = None   # operator's WR with this strategy
    backtest_pnl: Optional[float] = None  # backtest PnL for this strategy
    alternatives: list = field(default_factory=list)  # other strategies considered

    def to_dict(self) -> dict:
        return asdict(self)


# ── AutoPilot ────────────────────────────────────────────────────────────────

class AutoPilot:
    """Selects strategy based on regime + history + backtest."""

    def __init__(self, api):
        self.api = api

    def decide(self, operator_id: str = "op_default") -> AutoPilotDecision:
        """Pick the best strategy for current conditions."""
        # 1. Get current regime
        regime = self._get_regime(operator_id)

        # 2. Get operator session history
        history = self._get_history(operator_id)

        # 3. Get operator plan for gating
        plan = self._get_plan(operator_id)

        # 4. Score each strategy
        scores = {}
        for strategy in ALL_STRATEGIES:
            scores[strategy] = self._score_strategy(strategy, regime, history, plan)

        # 5. Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # Filter out gated strategies (score <= 0)
        ranked = [(s, sc) for s, sc in ranked if sc > 0]

        if not ranked:
            # Fallback: defense is always available on free
            ranked = [("defense", 50)]

        best_name, best_score = ranked[0]

        # Build alternatives (top 3 after best)
        alternatives = []
        for s, sc in ranked[1:4]:
            wr = self._strategy_wr(s, history)
            alternatives.append({
                "strategy": s,
                "score": sc,
                "reason": self._strategy_reason(s, regime, history),
                "operator_wr": wr,
            })

        # Confidence: map score 0-100 to 0.0-1.0
        confidence = round(min(best_score / 100.0, 1.0), 2)

        # Operator WR for the chosen strategy
        operator_wr = self._strategy_wr(best_name, history)

        return AutoPilotDecision(
            strategy=best_name,
            confidence=confidence,
            reason=self._strategy_reason(best_name, regime, history),
            regime=regime.get("dominant_direction", "UNKNOWN"),
            operator_wr=operator_wr,
            backtest_pnl=None,  # V2: integrate backtest data
            alternatives=alternatives,
        )

    # ── Scoring ──────────────────────────────────────────────────────────────

    def _score_strategy(
        self, strategy: str, regime: dict, history: list, plan: str,
    ) -> float:
        """Score a strategy 0-100 for current conditions."""
        score = 50  # base

        dominant = regime.get("dominant_direction", "MIXED")
        fg_label = regime.get("fear_greed_label", "NEUTRAL").lower()
        volatility = regime.get("volatility", "NORMAL")

        # 1. Regime match (+30 / +20 / +10)
        regime_picks = REGIME_STRATEGY_MAP.get(dominant, [])
        if strategy in regime_picks:
            rank = regime_picks.index(strategy)
            score += 30 - (rank * 10)  # first=+30, second=+20, third=+10

        # 2. Fear/greed modifier (+-25)
        fg_mods = FEAR_GREED_MODIFIERS.get(fg_label, {})
        score += fg_mods.get(strategy, 0)

        # 3. Volatility modifier (+-30)
        vol_mods = VOLATILITY_MODIFIERS.get(volatility, {})
        score += vol_mods.get(strategy, 0)

        # 4. Operator history bonus (+-20)
        score += self._history_bonus(strategy, history)

        # 5. Plan gating — eliminate unavailable strategies
        if not self._plan_allows(plan, strategy):
            return 0

        return max(0, min(100, score))

    def _history_bonus(self, strategy: str, history: list) -> float:
        """Bonus/penalty based on operator's track record with this strategy."""
        sessions = [h for h in history if h.get("strategy") == strategy]
        if len(sessions) < 5:
            return 0  # not enough data

        wins = sum(h.get("wins", 0) for h in sessions)
        losses = sum(h.get("losses", 0) for h in sessions)
        total = wins + losses
        if total == 0:
            return 0

        wr = wins / total * 100

        if wr > 65:
            return 20
        if wr > 55:
            return 10
        if wr < 35:
            return -20
        if wr < 45:
            return -15
        return 0

    def _strategy_wr(self, strategy: str, history: list) -> Optional[float]:
        """Calculate operator's win rate for a strategy. None if no data."""
        sessions = [h for h in history if h.get("strategy") == strategy]
        if not sessions:
            return None

        wins = sum(h.get("wins", 0) for h in sessions)
        losses = sum(h.get("losses", 0) for h in sessions)
        total = wins + losses
        if total == 0:
            return None

        return round(wins / total * 100, 1)

    def _strategy_reason(self, strategy: str, regime: dict, history: list) -> str:
        """One-line explanation for why this strategy was picked."""
        dominant = regime.get("dominant_direction", "MIXED")
        fg_label = regime.get("fear_greed_label", "NEUTRAL").lower()
        volatility = regime.get("volatility", "NORMAL")

        parts = []

        regime_picks = REGIME_STRATEGY_MAP.get(dominant, [])
        if strategy in regime_picks:
            parts.append(f"{dominant} regime favors {strategy}")

        fg_mods = FEAR_GREED_MODIFIERS.get(fg_label, {})
        if fg_mods.get(strategy, 0) > 0:
            parts.append(f"{fg_label} boosts {strategy}")
        elif fg_mods.get(strategy, 0) < 0:
            parts.append(f"{fg_label} reduces {strategy}")

        vol_mods = VOLATILITY_MODIFIERS.get(volatility, {})
        if vol_mods.get(strategy, 0) > 0:
            parts.append(f"{volatility} volatility favors {strategy}")

        wr = self._strategy_wr(strategy, history)
        if wr is not None and wr > 55:
            parts.append(f"your WR: {wr}%")

        if not parts:
            parts.append(f"balanced pick for current conditions")

        return ". ".join(parts) + "."

    # ── Data access ──────────────────────────────────────────────────────────

    def _get_regime(self, operator_id: str) -> dict:
        """Get current regime state."""
        from scanner.v6.regime import RegimeState

        heat_data = self.api.get_heat(operator_id)
        brief_data = self.api.get_brief(operator_id)
        regime = RegimeState.from_heat(heat_data, brief_data)
        return regime.to_dict()

    def _get_history(self, operator_id: str) -> list:
        """Get operator session history."""
        result = self.api.session_history(operator_id)
        return result.get("sessions", [])

    def _get_plan(self, operator_id: str) -> str:
        """Get operator's current plan."""
        from scanner.v6.operator import resolve_operator
        ctx = resolve_operator(operator_id)
        return ctx.plan

    def _plan_allows(self, plan: str, strategy: str) -> bool:
        """Check if operator's plan allows this strategy."""
        from scanner.v6.operator import plan_allows_strategy
        return plan_allows_strategy(plan, strategy)

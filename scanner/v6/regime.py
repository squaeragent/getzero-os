"""
ZERO Regime Awareness — global market regime aggregator.

Aggregates per-coin evaluations into a GLOBAL regime state.
Gives the operator a feel for what the market is doing — like feeling the road surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RegimeState:
    """Global market regime derived from individual coin evaluations."""

    dominant_direction: str  # "SHORT", "LONG", "MIXED", "QUIET"
    trending_short: int  # count of coins trending short
    trending_long: int  # count of coins trending long
    neutral: int  # count of coins with no direction
    total: int  # total coins scanned
    approaching_count: int  # coins near threshold
    fear_greed: int  # current fear & greed value
    fear_greed_label: str  # "EXTREME FEAR", "FEAR", "NEUTRAL", "GREED", "EXTREME GREED"
    funding_bias: str  # "SHORTS PAID", "LONGS PAID", "NEUTRAL"
    funding_paid_count: int  # coins where funding favors direction
    volatility: str  # "LOW", "NORMAL", "HIGH", "EXTREME"
    regime_label: str  # one-line summary

    def to_dict(self) -> dict:
        return {
            "dominant_direction": self.dominant_direction,
            "trending_short": self.trending_short,
            "trending_long": self.trending_long,
            "neutral": self.neutral,
            "total": self.total,
            "approaching_count": self.approaching_count,
            "fear_greed": self.fear_greed,
            "fear_greed_label": self.fear_greed_label,
            "funding_bias": self.funding_bias,
            "funding_paid_count": self.funding_paid_count,
            "volatility": self.volatility,
            "regime_label": self.regime_label,
        }

    @staticmethod
    def from_heat(heat_data: dict, brief_data: dict) -> "RegimeState":
        """Build regime state from heat scan + brief data."""
        coins = heat_data.get("coins", [])
        total = len(coins)

        # Count directions
        trending_short = 0
        trending_long = 0
        neutral = 0
        for c in coins:
            direction = c.get("direction", "NONE")
            if direction == "SHORT":
                trending_short += 1
            elif direction == "LONG":
                trending_long += 1
            else:
                neutral += 1

        # Dominant direction
        has_direction = trending_short + trending_long
        dominant_direction = _calc_dominant(trending_short, trending_long, total)

        # Approaching count from brief
        approaching_count = len(brief_data.get("approaching", []))

        # Fear & greed
        fg = brief_data.get("fear_greed", 50)
        if not isinstance(fg, (int, float)):
            fg = 50
        fg = int(fg)
        fg_label = _fg_label(fg)

        # Funding bias — check funding layer across coins
        funding_short_paid = 0
        funding_long_paid = 0
        for c in coins:
            layers = c.get("layers", [])
            direction = c.get("direction", "NONE")
            for layer in layers:
                if layer.get("layer") == "funding" and layer.get("passed"):
                    if direction == "SHORT":
                        funding_short_paid += 1
                    elif direction == "LONG":
                        funding_long_paid += 1
                    break
        funding_bias, funding_paid_count = _calc_funding_bias(
            funding_short_paid, funding_long_paid
        )

        # Volatility — derive from regime layer distribution
        volatility = _calc_volatility(coins)

        # Build label
        regime_label = _build_label(
            dominant_direction, trending_short, trending_long, neutral, total,
            fg_label, funding_bias, volatility,
        )

        return RegimeState(
            dominant_direction=dominant_direction,
            trending_short=trending_short,
            trending_long=trending_long,
            neutral=neutral,
            total=total,
            approaching_count=approaching_count,
            fear_greed=fg,
            fear_greed_label=fg_label,
            funding_bias=funding_bias,
            funding_paid_count=funding_paid_count,
            volatility=volatility,
            regime_label=regime_label,
        )


def detect_shift(previous: RegimeState, current: RegimeState) -> Optional[dict]:
    """Detect meaningful regime shift.

    Returns shift dict if:
    - dominant_direction changed
    - 3+ coins changed direction in same scan
    - fear_greed crossed a boundary (20, 40, 60, 80)
    - funding_bias flipped

    Returns None if no meaningful shift.
    Minimum threshold: 3+ coins must shift for it to count.
    """
    shifts = []

    # Direction change
    if previous.dominant_direction != current.dominant_direction:
        shifts.append(
            f"{previous.dominant_direction}\u2192{current.dominant_direction}"
        )

    # Count coin shifts (approximation: compare distribution deltas)
    short_delta = abs(current.trending_short - previous.trending_short)
    long_delta = abs(current.trending_long - previous.trending_long)
    max_delta = max(short_delta, long_delta)
    if max_delta >= 3:
        if current.trending_short > previous.trending_short:
            shifts.append(f"{short_delta} coins flipped short")
        elif current.trending_long > previous.trending_long:
            shifts.append(f"{long_delta} coins flipped long")

    # Fear & greed boundary crossing (20, 40, 60, 80)
    prev_zone = _fg_zone(previous.fear_greed)
    curr_zone = _fg_zone(current.fear_greed)
    if prev_zone != curr_zone:
        shifts.append(
            f"fear/greed {previous.fear_greed}\u2192{current.fear_greed}"
        )

    # Funding bias flip
    if (
        previous.funding_bias != current.funding_bias
        and previous.funding_bias != "NEUTRAL"
        and current.funding_bias != "NEUTRAL"
    ):
        shifts.append(
            f"funding {previous.funding_bias}\u2192{current.funding_bias}"
        )

    if not shifts:
        return None

    # Minimum threshold: need meaningful change (direction shift or 3+ coin shift)
    has_direction_change = previous.dominant_direction != current.dominant_direction
    has_coin_shift = max_delta >= 3
    has_fg_cross = prev_zone != curr_zone
    has_funding_flip = (
        previous.funding_bias != current.funding_bias
        and previous.funding_bias != "NEUTRAL"
        and current.funding_bias != "NEUTRAL"
    )

    if not (has_direction_change or has_coin_shift or has_fg_cross or has_funding_flip):
        return None

    return {
        "type": "regime_shift",
        "from_direction": previous.dominant_direction,
        "to_direction": current.dominant_direction,
        "shifts": shifts,
        "summary": " | ".join(shifts),
    }


# ── Private helpers ──────────────────────────────────────────────────────────


def _calc_dominant(short: int, long: int, total: int) -> str:
    """Determine dominant direction from counts."""
    if total == 0:
        return "QUIET"
    has_direction = short + long
    # If <20% have direction, QUIET
    if has_direction / total < 0.20:
        return "QUIET"
    # If >60% one direction (of those with direction), that wins
    if has_direction > 0:
        if short / total > 0.60:
            return "SHORT"
        if long / total > 0.60:
            return "LONG"
    return "MIXED"


def _fg_label(val: int) -> str:
    if val <= 20:
        return "EXTREME FEAR"
    if val <= 40:
        return "FEAR"
    if val <= 60:
        return "NEUTRAL"
    if val <= 80:
        return "GREED"
    return "EXTREME GREED"


def _fg_zone(val: int) -> int:
    """Map fear/greed value to zone index for boundary detection."""
    if val <= 20:
        return 0
    if val <= 40:
        return 1
    if val <= 60:
        return 2
    if val <= 80:
        return 3
    return 4


def _calc_funding_bias(short_paid: int, long_paid: int) -> tuple[str, int]:
    """Determine funding bias from layer results."""
    if short_paid > long_paid and short_paid >= 2:
        return "SHORTS PAID", short_paid
    if long_paid > short_paid and long_paid >= 2:
        return "LONGS PAID", long_paid
    return "NEUTRAL", 0


def _calc_volatility(coins: list) -> str:
    """Derive volatility from regime layer distribution."""
    if not coins:
        return "LOW"
    chaotic_count = 0
    trending_count = 0
    for c in coins:
        regime = c.get("regime", "")
        if "chaotic" in regime or "divergent" in regime:
            chaotic_count += 1
        elif "trend" in regime:
            trending_count += 1

    total = len(coins)
    chaotic_pct = chaotic_count / total if total else 0

    if chaotic_pct > 0.40:
        return "EXTREME"
    if chaotic_pct > 0.25:
        return "HIGH"
    if chaotic_pct > 0.10:
        return "NORMAL"
    return "LOW"


def _build_label(
    dominant: str, short: int, long: int, neutral: int, total: int,
    fg_label: str, funding_bias: str, volatility: str,
) -> str:
    """Build one-line regime summary label."""
    fg_lower = fg_label.lower()
    funding_lower = funding_bias.lower().replace("_", " ")
    vol_lower = volatility.lower()

    if dominant == "SHORT":
        return (
            f"SHORT MARKET. {short} of {total} coins trending short. "
            f"{fg_lower}. {funding_lower}."
        )
    elif dominant == "LONG":
        return (
            f"LONG MARKET. {long} of {total} coins trending long. "
            f"{fg_lower}. {funding_lower}."
        )
    elif dominant == "QUIET":
        has = short + long
        return (
            f"QUIET. {has} of {total} coins have conviction. "
            f"{vol_lower} volatility. patience."
        )
    else:  # MIXED
        return (
            f"MIXED. no clear direction. {short} short, {long} long, "
            f"{neutral} neutral. {vol_lower} volatility."
        )

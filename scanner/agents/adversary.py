#!/usr/bin/env python3
"""
ZERO OS — Agent 2B: Adversary (Cognitive Loop Phase 3)
Sits between Hypothesis Generator and Correlation Agent.
Tries to KILL every hypothesis. Only survivors proceed.

For each hypothesis, runs 6 attacks:
  1. Similar Failure Search   — find past losses in same setup
  2. Kill Condition Proximity — how close are kill conditions to being met?
  3. Portfolio Stress Test    — directional concentration + impact simulation
  4. Confidence vs Anti-Thesis — weak conviction or strong counter-evidence
  5. Regime Mismatch         — signal style vs current regime
  6. Funding Headwind        — are you paying to be wrong?

Survival score:
  score = 1.0 * product(1 - severity * weight)
  PROCEED (>=0.7), PROCEED_WITH_CAUTION (>=0.5), WEAK (>=0.3), KILLED (<0.3)

Inputs:
  scanner/bus/candidates.json         — hypotheses from Phase 2
  scanner/bus/world_state.json        — live world model
  scanner/data/live/closed.jsonl      — trade history
  scanner/data/live/positions.json    — current open positions

Outputs:
  scanner/bus/candidates.json         — updated with adversary fields, KILLED removed
  scanner/bus/adversary.json          — full adversary report
  scanner/memory/episodes/<id>.json   — updated with "adversary" key
  scanner/bus/heartbeat.json          — heartbeat "adversary" key

Usage:
  python3 scanner/agents/adversary.py           # single run
  python3 scanner/agents/adversary.py --loop    # continuous 300s cycle
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ─── EVOLVED WEIGHTS LOADER ───
def load_evolved_weights():
    """Load evolved attack weights from counterfactual learning."""
    weights_file = Path(__file__).parent.parent / "bus" / "evolved_weights.json"
    if not weights_file.exists():
        return {}
    try:
        with open(weights_file) as f:
            data = json.load(f)
        # Only use if we have enough data
        if data.get("data_points", 0) < 20:
            return {}
        return {k: v["evolved"] for k, v in data.get("weights", {}).items()}
    except Exception:
        return {}

# ── Upgrade 3 helper (session classification mirrors perception.py) ──
def _get_trading_session(utc_hour):
    if 0 <= utc_hour < 7:
        return "ASIA"
    elif 7 <= utc_hour < 13:
        return "EUROPE"
    elif 13 <= utc_hour < 20:
        return "US"
    else:
        return "LATE_US"

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"
MEMORY_DIR = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"
RULES_DIR = MEMORY_DIR / "rules"
ACTIVE_RULES_FILE = RULES_DIR / "active.json"

CANDIDATES_FILE = BUS_DIR / "candidates.json"
WORLD_STATE_FILE = BUS_DIR / "world_state.json"
TAAPI_SNAPSHOT_FILE = BUS_DIR / "taapi_snapshot.json"
ADVERSARY_FILE = BUS_DIR / "adversary.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
POSITIONS_FILE = LIVE_DIR / "positions.json"
CLOSED_FILE = LIVE_DIR / "closed.jsonl"
REGIME_PREDICTIONS_FILE = BUS_DIR / "regime_predictions.json"
GENEALOGY_FILE = BUS_DIR / "genealogy.json"

CYCLE_SECONDS = 300  # 5 minutes
TOTAL_EQUITY = 115.0  # approximate portfolio equity for stress tests

# Survival thresholds
THRESHOLD_PROCEED = 0.7
THRESHOLD_CAUTION = 0.5
THRESHOLD_WEAK = 0.3


# ─── LOGGING ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [ADVERSARY] {msg}")


# ─── FILE HELPERS ───
def load_json_safe(path, default=None):
    if default is None:
        default = {}
    if not Path(path).exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ─── DATA LOADERS ───
def load_closed_trades(max_lines=1000):
    """Load recent closed trades from closed.jsonl."""
    trades = []
    if not CLOSED_FILE.exists():
        return trades
    try:
        with open(CLOSED_FILE) as f:
            lines = f.readlines()
        for line in lines[-max_lines:]:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return trades


def load_positions():
    """Load current open positions."""
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def load_world_state():
    """Load unified world model — returns full data (coins + meta)."""
    return load_json_safe(WORLD_STATE_FILE, {})


def load_candidates():
    """Load current candidates from hypothesis generator."""
    data = load_json_safe(CANDIDATES_FILE, {})
    return data.get("timestamp", ""), data.get("candidates", [])


# ─── ATTACK 1: SIMILAR FAILURE SEARCH ───
def attack_similar_failure(hypothesis, closed_trades):
    """
    Find past trades with same coin+direction, signal, or regime that LOST.
    Returns attack dict with severity and detail.
    """
    coin = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "LONG")
    signal_name = hypothesis.get("signal", "")
    regime = hypothesis.get("regime", "")

    # Collect matching failures
    failures = []

    for trade in closed_trades:
        pnl = trade.get("pnl_usd", trade.get("pnl_dollars", 0))
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            pnl = 0.0

        if pnl >= 0:
            continue  # Only care about losers

        pnl_pct = trade.get("pnl_pct", 0)
        try:
            pnl_pct = float(pnl_pct)
        except (TypeError, ValueError):
            pnl_pct = 0.0

        match_score = 0
        match_reasons = []

        # Same coin + direction (strongest match)
        if trade.get("coin") == coin and trade.get("direction") == direction:
            match_score += 3
            match_reasons.append(f"{coin} {direction}")

        # Same signal
        if trade.get("signal") == signal_name and signal_name:
            match_score += 2
            match_reasons.append(f"signal {signal_name}")

        # Same regime
        if trade.get("regime") == regime and regime:
            match_score += 1
            match_reasons.append(f"regime {regime}")

        if match_score > 0:
            failures.append({
                "match_score": match_score,
                "match_reasons": match_reasons,
                "pnl_pct": pnl_pct,
                "coin": trade.get("coin", "?"),
                "direction": trade.get("direction", "?"),
                "signal": trade.get("signal", "?"),
            })

    if not failures:
        return {"attack": "similar_failure", "severity": 0.0, "weight": 1.5,
                "detail": "No similar losing trades found", "failures_found": 0}

    # Sort by relevance
    failures.sort(key=lambda x: x["match_score"], reverse=True)
    top_failures = failures[:10]

    count = len(top_failures)
    avg_loss = sum(f["pnl_pct"] for f in top_failures) / count

    if count < 3:
        severity = 0.0
        detail = f"{count} similar trade(s) lost, not enough to penalize"
    elif avg_loss < -3.0:
        severity = 0.6
        detail = f"{count} similar trades lost avg {avg_loss:.1f}%"
    elif avg_loss < -1.5:
        severity = 0.5
        detail = f"{count} similar trades lost avg {avg_loss:.1f}%"
    else:
        severity = 0.4
        detail = f"{count} similar trades lost avg {avg_loss:.1f}%"

    return {
        "attack": "similar_failure",
        "severity": round(severity, 3),
        "weight": 1.5,
        "detail": detail,
        "failures_found": count,
        "avg_loss_pct": round(avg_loss, 3),
    }


# ─── ATTACK 2: KILL CONDITION PROXIMITY ───
def attack_kill_condition_proximity(hypothesis, world_coins):
    """
    Check how close the current world state is to each kill condition.
    Returns attack dict with severity.
    """
    coin = hypothesis.get("coin", "")
    kill_conditions = hypothesis.get("kill_conditions", [])
    world_snap = hypothesis.get("world_snapshot", {})
    world_coin = world_coins.get(coin, {})

    if not kill_conditions:
        return {"attack": "kill_condition_proximity", "severity": 0.0, "weight": 1.0,
                "detail": "No kill conditions defined"}

    # Use world_snapshot from hypothesis (captured at generation time) + live world_coin
    regime = world_coin.get("regime", world_snap.get("regime", "unknown"))
    hurst_24h = world_snap.get("hurst_24h", 0.5)
    funding_direction = world_snap.get("funding_direction", "stable")
    spread_status = world_snap.get("spread_status", "NORMAL")
    liquidity_tradeable = world_snap.get("tradeable", True)
    tf_pattern = world_snap.get("timeframe_pattern", "NEUTRAL")
    funding_reversal = world_snap.get("funding_reversal", False)

    # Try to get live values from world_coin too (more current)
    live_ind = world_coin.get("indicators", {})
    live_hurst = live_ind.get("HURST_24H", hurst_24h)
    live_spread = world_coin.get("spread", {}).get("status", spread_status)
    live_regime = world_coin.get("regime", regime)
    live_tf = world_coin.get("timeframe", {}).get("pattern", tf_pattern)

    proximities = []

    for kill_cond in kill_conditions:
        kill_lower = kill_cond.lower()
        sev = 0.0
        detail_str = f"kill condition: '{kill_cond}'"

        # Regime-based kill conditions
        if "chaotic" in kill_lower and "regime" in kill_lower:
            if live_regime == "chaotic":
                sev = 1.0
                detail_str = f"kill condition '{kill_cond}' FULLY MET (regime={live_regime})"
            elif live_regime == "shift":
                sev = 0.5
                detail_str = f"kill condition '{kill_cond}' 50% met (currently shifting)"
            elif live_regime == "trending":
                sev = 0.1
                detail_str = f"kill condition '{kill_cond}' ~10% met (regime trending, stable)"

        elif "shift" in kill_lower and "regime" in kill_lower:
            if live_regime == "shift":
                sev = 0.6
                detail_str = f"kill condition '{kill_cond}' met (regime currently in shift)"
            elif live_regime == "chaotic":
                sev = 0.8
                detail_str = f"kill condition '{kill_cond}' surpassed (chaotic)"

        # Hurst-based kill conditions
        elif "hurst" in kill_lower and ("below" in kill_lower or "drop" in kill_lower or "<" in kill_lower):
            # Parse threshold value
            threshold = 0.5
            import re
            match = re.search(r'(\d+\.\d+)', kill_cond)
            if match:
                threshold = float(match.group(1))

            if live_hurst <= threshold:
                sev = 1.0
                detail_str = f"Hurst {live_hurst:.3f} already below kill threshold {threshold}"
            elif threshold > 0:
                # Proximity: how close is current Hurst to threshold?
                distance = live_hurst - threshold
                margin = 0.1  # 0.1 Hurst units = "close"
                if distance < 0.03:
                    sev = 0.7
                    detail_str = f"Hurst {live_hurst:.3f} very close to kill threshold {threshold} (gap={distance:.3f})"
                elif distance < margin:
                    sev = 0.5
                    detail_str = f"Hurst {live_hurst:.3f} approaching kill threshold {threshold} (gap={distance:.3f})"
                else:
                    sev = 0.1
                    detail_str = f"Hurst {live_hurst:.3f} — kill at {threshold} not near"

        # Spread-based kill conditions
        elif "mm_setup" in kill_lower or "spread" in kill_lower:
            if live_spread == "MM_SETUP":
                sev = 1.0
                detail_str = f"spread in MM_SETUP — kill condition FULLY MET"
            elif live_spread == "ELEVATED":
                sev = 0.4
                detail_str = f"spread ELEVATED — kill condition 40% met"
            else:
                sev = 0.0
                detail_str = f"spread {live_spread} — kill condition not near"

        # Liquidity-based kill conditions
        elif "liquidity" in kill_lower or "tradeable" in kill_lower:
            if not liquidity_tradeable:
                sev = 1.0
                detail_str = "liquidity below tradeable threshold — kill condition MET"
            else:
                liq_score = world_coin.get("liquidity", {}).get("score", 70)
                if liq_score < 40:
                    sev = 0.6
                    detail_str = f"liquidity score {liq_score:.0f} — approaching untradeable"
                elif liq_score < 55:
                    sev = 0.3
                    detail_str = f"liquidity score {liq_score:.0f} — moderately thin"
                else:
                    sev = 0.0
                    detail_str = f"liquidity score {liq_score:.0f} — adequate"

        # Funding reversal kill conditions
        elif "funding" in kill_lower and ("reverse" in kill_lower or "intensif" in kill_lower):
            if funding_reversal:
                sev = 0.8
                detail_str = f"funding reversal detected — kill condition nearly met"
            elif funding_direction == "intensifying":
                sev = 0.5
                detail_str = f"funding {funding_direction} — kill condition 50% met"
            else:
                sev = 0.0
                detail_str = f"funding {funding_direction} — kill condition not triggered"

        # Timeframe pattern flip kill conditions
        elif "confirmation_short" in kill_lower or "flip" in kill_lower or "timeframe" in kill_lower:
            direction = hypothesis.get("direction", "LONG")
            if direction == "LONG" and live_tf in ("CONFIRMATION_SHORT", "TRAP_LONG"):
                sev = 0.8
                detail_str = f"timeframe pattern {live_tf} conflicts with LONG — kill condition triggered"
            elif direction == "SHORT" and live_tf in ("CONFIRMATION_LONG", "TRAP_SHORT"):
                sev = 0.8
                detail_str = f"timeframe pattern {live_tf} conflicts with SHORT — kill condition triggered"
            else:
                sev = 0.0
                detail_str = f"timeframe pattern {live_tf} — kill condition not triggered"

        if sev > 0:
            proximities.append({"condition": kill_cond, "severity": sev, "detail": detail_str})

    if not proximities:
        return {"attack": "kill_condition_proximity", "severity": 0.0, "weight": 1.0,
                "detail": "Kill conditions not near current world state"}

    # Use max severity across all conditions
    max_prox = max(proximities, key=lambda x: x["severity"])
    all_details = "; ".join(p["detail"] for p in proximities if p["severity"] > 0.1)

    return {
        "attack": "kill_condition_proximity",
        "severity": round(max_prox["severity"], 3),
        "weight": 1.0,
        "detail": all_details[:200] if all_details else max_prox["detail"],
        "conditions_checked": len(kill_conditions),
        "conditions_near": len(proximities),
    }


# ─── ATTACK 3: PORTFOLIO STRESS TEST ───
def attack_portfolio_stress(hypothesis, positions):
    """
    Check directional concentration and simulate downside impact.
    Returns attack dict.
    """
    direction = hypothesis.get("direction", "LONG")

    if not positions:
        return {"attack": "portfolio_stress", "severity": 0.0, "weight": 1.0,
                "detail": "No open positions — no concentration risk"}

    # Count directional exposure
    long_exposure = sum(p.get("size_usd", 0) for p in positions if p.get("direction") == "LONG")
    short_exposure = sum(p.get("size_usd", 0) for p in positions if p.get("direction") == "SHORT")
    total_exposure = long_exposure + short_exposure

    # Estimate size of new position (use config defaults)
    new_position_size = 40.0  # conservative estimate

    # Simulate after adding new hypothesis
    if direction == "LONG":
        new_long = long_exposure + new_position_size
        new_total = new_long + short_exposure
        directional_pct = new_long / TOTAL_EQUITY * 100
        # Simulate: all LONGs drop 3%
        impact_3pct = new_long * 0.03
        detail = (
            f"adding LONG increases long exposure to ${new_long:.0f} "
            f"({directional_pct:.0f}% of equity), "
            f"3% drop = -${impact_3pct:.2f}"
        )
    else:
        new_short = short_exposure + new_position_size
        new_total = long_exposure + new_short
        directional_pct = new_short / TOTAL_EQUITY * 100
        impact_3pct = new_short * 0.03
        detail = (
            f"adding SHORT increases short exposure to ${new_short:.0f} "
            f"({directional_pct:.0f}% of equity), "
            f"3% adverse move = -${impact_3pct:.2f}"
        )

    # Threshold: 5% of equity = $5.75
    stress_threshold = TOTAL_EQUITY * 0.05
    same_dir_count = sum(1 for p in positions if p.get("direction") == direction)

    if impact_3pct > stress_threshold:
        severity = min(0.8, 0.5 + (impact_3pct - stress_threshold) / stress_threshold * 0.3)
    elif same_dir_count >= 3:
        severity = 0.4
        detail = f"{same_dir_count} existing {direction} positions + this one = high concentration. " + detail
    elif same_dir_count >= 2:
        severity = 0.25
    else:
        severity = 0.0

    return {
        "attack": "portfolio_stress",
        "severity": round(severity, 3),
        "weight": 1.0,
        "detail": detail,
        "same_direction_positions": same_dir_count,
        "stress_impact_usd": round(impact_3pct, 2),
        "directional_pct": round(directional_pct, 1),
    }


# ─── ATTACK 4: CONFIDENCE VS ANTI-THESIS ───
def attack_confidence_vs_anti_thesis(hypothesis):
    """
    Low confidence + strong counter-evidence → severity.
    Returns attack dict.
    """
    confidence = hypothesis.get("confidence", 0.5)
    evidence_for = hypothesis.get("evidence_for", [])
    evidence_against = hypothesis.get("evidence_against", [])
    anti_thesis = hypothesis.get("anti_thesis", "")

    severity = 0.0
    reasons = []

    # Weak confidence
    if confidence < 0.35:
        severity = max(severity, 0.5)
        reasons.append(f"confidence {confidence:.2f} is very weak")
    elif confidence < 0.4:
        severity = max(severity, 0.4)
        reasons.append(f"confidence {confidence:.2f} below threshold")

    # Evidence imbalance
    n_for = len(evidence_for)
    n_against = len(evidence_against)
    if n_against > n_for:
        sev_imbalance = min(0.4, 0.2 + (n_against - n_for) * 0.1)
        severity = max(severity, sev_imbalance)
        reasons.append(f"{n_against} evidence_against vs {n_for} evidence_for")

    # Anti-thesis keywords indicating exhaustion
    anti_lower = anti_thesis.lower()
    if "exhausting" in anti_lower or "exhaustion" in anti_lower:
        severity = max(severity, 0.3)
        reasons.append("anti_thesis mentions exhaustion")
    if "declining" in anti_lower:
        severity = max(severity, 0.3)
        reasons.append("anti_thesis mentions declining")
    if "collapse" in anti_lower or "collapsing" in anti_lower:
        severity = max(severity, 0.4)
        reasons.append("anti_thesis mentions collapse")

    detail = f"confidence {confidence:.2f}"
    if reasons:
        detail = "; ".join(reasons)

    return {
        "attack": "confidence_vs_antithesis",
        "severity": round(severity, 3),
        "weight": 1.0,
        "detail": detail,
        "confidence": confidence,
        "evidence_for_count": n_for,
        "evidence_against_count": n_against,
    }


# ─── ATTACK 5: REGIME MISMATCH ───
def attack_regime_mismatch(hypothesis, world_coins):
    """
    Signal style vs current regime — are they aligned?
    Returns attack dict.
    """
    coin = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "LONG")
    signal_name = hypothesis.get("signal", "").upper()
    world_snap = hypothesis.get("world_snapshot", {})

    # Get live regime (prefer world_coins, fallback to snapshot)
    world_coin = world_coins.get(coin, {})
    regime = world_coin.get("regime", world_snap.get("regime", "unknown"))

    # Classify signal style
    MOMENTUM_KEYWORDS = {"TREND", "MOMENTUM", "EMA", "BREAKOUT", "MACD", "CROSS", "CONVERGENCE", "TRIPLE"}
    REVERSAL_KEYWORDS = {"REVERSAL", "REVERT", "RSI", "BB", "BOUNCE", "OVERSOLD", "OVERBOUGHT", "DOJI", "SOCIAL", "EXHAUSTION"}

    momentum_score = sum(1 for kw in MOMENTUM_KEYWORDS if kw in signal_name)
    reversal_score = sum(1 for kw in REVERSAL_KEYWORDS if kw in signal_name)

    if momentum_score > reversal_score:
        signal_style = "momentum"
    elif reversal_score > momentum_score:
        signal_style = "reversal"
    else:
        signal_style = "neutral"

    severity = 0.0
    detail = f"{direction} signal (style={signal_style}) in {regime} regime"

    # Direct regime conflicts
    if regime == "chaotic":
        severity = 0.3
        detail = f"{direction} signal in chaotic regime — unpredictable"

    elif regime == "shift":
        if signal_style == "momentum":
            severity = 0.4
            detail = f"momentum signal in shifting regime — trend unreliable"
        else:
            severity = 0.2
            detail = f"signal in shifting regime — direction unclear"

    elif regime == "trending":
        if signal_style == "reversal":
            severity = 0.2
            detail = f"reversal signal in trending regime — fighting the trend"

    elif regime == "stable":
        if signal_style == "momentum":
            severity = 0.2
            detail = f"momentum signal in stable/low-vol regime — may not trigger"

    elif regime == "reverting":
        if signal_style == "momentum":
            severity = 0.25
            detail = f"momentum signal in reverting regime — mean-reversion environment"

    return {
        "attack": "regime_mismatch",
        "severity": round(severity, 3),
        "weight": 1.0,
        "detail": detail,
        "regime": regime,
        "signal_style": signal_style,
    }


# ─── ATTACK 6: FUNDING HEADWIND ───
def attack_funding_headwind(hypothesis, world_coins):
    """
    Is funding rate working against this position?
    Returns attack dict.
    """
    coin = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "LONG")
    world_snap = hypothesis.get("world_snapshot", {})
    world_coin = world_coins.get(coin, {})

    # Get funding data — prefer live world_coin
    funding = world_coin.get("funding", {})
    funding_rate = funding.get("rate", world_snap.get("funding_rate", 0))
    funding_velocity = funding.get("velocity_direction", world_snap.get("funding_direction", "stable"))
    funding_reversal = funding.get("reversal", world_snap.get("funding_reversal", False))

    try:
        funding_rate = float(funding_rate)
    except (TypeError, ValueError):
        funding_rate = 0.0

    severity = 0.0
    detail = f"{direction} with funding rate {funding_rate:.6f}"

    # LONG paying positive funding (being LONG costs money each 8h)
    if direction == "LONG" and funding_rate > 0.0001:  # > 0.01%
        # Cost per day for ~$50 position
        daily_cost = abs(funding_rate) * 3 * 50  # 3 funding periods per day
        severity = 0.3
        detail = (
            f"LONG with funding rate +{funding_rate:.5f}% "
            f"— paying ~${daily_cost:.2f}/day to hold $50 position"
        )
        if funding_velocity == "intensifying":
            severity = 0.5
            detail += " (funding intensifying against LONG)"

    # SHORT paying negative funding (being SHORT costs money)
    elif direction == "SHORT" and funding_rate < -0.0001:
        daily_cost = abs(funding_rate) * 3 * 50
        severity = 0.3
        detail = (
            f"SHORT with funding rate {funding_rate:.5f}% "
            f"— paying ~${daily_cost:.2f}/day to hold $50 position"
        )
        if funding_velocity == "intensifying":
            severity = 0.5
            detail += " (funding intensifying against SHORT)"

    # Funding reversal against direction
    elif funding_reversal:
        if direction == "LONG":
            severity = 0.3
            detail = f"funding reversal detected — could flip against LONG"
        else:
            severity = 0.3
            detail = f"funding reversal detected — could flip against SHORT"

    # Intensifying funding in wrong direction
    elif funding_velocity == "intensifying":
        if direction == "LONG" and funding_rate < -0.0001:
            severity = 0.2
            detail = f"funding intensifying negatively — LONG environment strengthening"
        elif direction == "SHORT" and funding_rate > 0.0001:
            severity = 0.2
            detail = f"funding intensifying positively — LONG bias strengthening"

    return {
        "attack": "funding_headwind",
        "severity": round(severity, 3),
        "weight": 1.0,
        "detail": detail,
        "funding_rate": funding_rate,
        "funding_velocity": funding_velocity,
        "funding_reversal": funding_reversal,
    }


# ─── ATTACK: FEAR & GREED FILTER (weight 1.3x) ───
def attack_fear_greed(hypothesis, macro_intel):
    """
    Penalize hypotheses that trade against the Fear & Greed index extremes.

    Logic:
      - F&G <= 20 ("Extreme Fear") + LONG  → severity 0.6 (market panic, don't go long)
      - F&G >= 80 ("Extreme Greed") + SHORT → severity 0.6 (market euphoria, don't short)
      - F&G <= 30 + LONG                   → severity 0.3 (moderate fear, caution for longs)
      - F&G >= 70 + SHORT                  → severity 0.3 (moderate greed, caution for shorts)

    Source: scanner/bus/macro_intel.json (populated by macro_intel agent)
    """
    if not macro_intel:
        return {
            "attack": "fear_greed",
            "severity": 0.0,
            "weight": 1.3,
            "detail": "macro_intel.json unavailable — skipping F&G check",
        }

    fg = macro_intel.get("fear_greed")
    fg_class = macro_intel.get("fear_greed_class", "")
    direction = hypothesis.get("direction", "")

    if fg is None:
        return {
            "attack": "fear_greed",
            "severity": 0.0,
            "weight": 1.3,
            "detail": "fear_greed value missing from macro_intel",
        }

    severity = 0.0
    detail = f"F&G={fg} ({fg_class}) direction={direction}"

    if direction == "LONG":
        if fg <= 20:
            severity = 0.6
            detail = f"Extreme Fear (F&G={fg}) + LONG — market panic, high reversal risk"
        elif fg <= 30:
            severity = 0.3
            detail = f"Fear (F&G={fg}) + LONG — cautious territory for new longs"
    elif direction == "SHORT":
        if fg >= 80:
            severity = 0.6
            detail = f"Extreme Greed (F&G={fg}) + SHORT — euphoria, shorts may get squeezed"
        elif fg >= 70:
            severity = 0.3
            detail = f"Greed (F&G={fg}) + SHORT — cautious territory for new shorts"

    return {
        "attack": "fear_greed",
        "severity": round(severity, 3),
        "weight": 1.3,
        "detail": detail,
        "fear_greed": fg,
        "fear_greed_class": fg_class,
    }


# ─── ATTACK: MACRO EVENT FILTER (weight 1.4x) ───
def attack_macro_event(hypothesis, macro_intel):
    """
    Penalize all new positions near scheduled macro events (FOMC, options expiry)
    and in high-volatility regimes (DVOL > 60).

    Logic:
      - macro_event_imminent == True → severity 0.4 for ALL (reduce trading near FOMC/expiry)
      - days_to_options_expiry <= 1  → severity 0.6 for ALL (expiry day = maximum chop)
      - signals.high_vol == True (DVOL > 60) + LONG → severity 0.3 (high vol favors shorts)

    Source: scanner/bus/macro_intel.json (populated by macro_intel agent)
    """
    if not macro_intel:
        return {
            "attack": "macro_event",
            "severity": 0.0,
            "weight": 1.4,
            "detail": "macro_intel.json unavailable — skipping macro event check",
        }

    direction = hypothesis.get("direction", "")
    macro_imminent = macro_intel.get("macro_event_imminent", False)
    days_to_expiry = macro_intel.get("days_to_options_expiry")
    signals = macro_intel.get("signals", {})
    high_vol = signals.get("high_vol", False)

    severity = 0.0
    details = []

    # Expiry day — maximum chop, penalize all directions hard
    if days_to_expiry is not None and days_to_expiry <= 1:
        severity = max(severity, 0.6)
        details.append(f"options expiry in {days_to_expiry}d — maximum chop environment")

    # FOMC or other macro event imminent — reduce all trading
    if macro_imminent and severity < 0.4:
        severity = max(severity, 0.4)
        details.append("macro event imminent (FOMC/CPI) — elevated uncertainty")

    # High vol (DVOL > 60) penalizes LONGs — high vol favors shorts/puts
    if high_vol and direction == "LONG":
        severity = max(severity, 0.3)
        details.append("DVOL > 60 (high vol regime) + LONG — vol environment favors shorts")

    detail = "; ".join(details) if details else "no macro events detected"

    return {
        "attack": "macro_event",
        "severity": round(severity, 3),
        "weight": 1.4,
        "detail": detail,
        "macro_event_imminent": macro_imminent,
        "days_to_options_expiry": days_to_expiry,
        "high_vol": high_vol,
    }


# ─── ATTACK 7 (Upgrade 1): MACRO REGIME ───
def attack_macro_regime(hypothesis, world_state_meta):
    """If market is RISK_OFF, penalize LONG hypotheses heavily."""
    macro     = world_state_meta.get("macro", {})
    state     = macro.get("state", "CHOPPY")
    direction = hypothesis.get("direction", "")
    fear      = macro.get("fear_score", 50)

    severity = 0.0
    details  = []

    if state == "RISK_OFF" and direction == "LONG":
        severity = 0.6
        details.append(f"RISK_OFF market + LONG = fighting the tide (fear={fear})")
    elif state == "RISK_OFF" and direction == "SHORT":
        severity = 0.0
        details.append("RISK_OFF favors SHORT")
    elif state == "CHOPPY":
        severity = 0.1
        details.append(f"CHOPPY market — reduced conviction (fear={fear})")

    btc_roc_4h = macro.get("btc_roc_4h", 0)
    btc_roc_24h = macro.get("btc_roc_24h", 0)

    # Block LONGs when BTC is declining on any timeframe
    if direction == "LONG":
        if btc_roc_24h < -2:
            severity = max(severity, 0.5)
            details.append(f"BTC down {btc_roc_24h:.1f}% in 24h — LONGs fighting gravity")
        if btc_roc_4h < -2:
            severity = max(severity, 0.6)
            details.append(f"BTC dumping {btc_roc_4h:.1f}% in 4h — alt longs are traps")

    # CHOPPY + LONG gets extra penalty
    if state == "CHOPPY" and direction == "LONG":
        severity = max(severity, 0.2)
        details.append(f"CHOPPY + LONG = coin flip territory (fear={fear})")

    return {
        "attack":   "macro_regime",
        "severity": round(severity, 3),
        "weight":   1.4,
        "detail":   "; ".join(details) if details else "macro neutral",
    }


# ─── ATTACK 8 (Upgrade 3): SESSION RISK ───
def attack_session_risk(hypothesis, world_state_meta):
    """Check if current session historically produces losses for this direction."""
    session   = world_state_meta.get("session", "UNKNOWN")
    direction = hypothesis.get("direction", "")

    obs_file = Path(__file__).parent.parent / "memory" / "observations.jsonl"
    if not obs_file.exists():
        return {"attack": "session_risk", "severity": 0, "weight": 1.0,
                "detail": "no session data yet"}

    session_trades = []
    with open(obs_file) as f:
        for line in f:
            try:
                obs = json.loads(line.strip())
                if obs.get("session") == session and obs.get("direction") == direction:
                    session_trades.append(obs)
            except Exception:
                pass

    if len(session_trades) < 10:
        return {"attack": "session_risk", "severity": 0, "weight": 1.0,
                "detail": f"only {len(session_trades)} trades in {session}+{direction}, need 10+"}

    wins = sum(1 for t in session_trades if t.get("outcome") == "win")
    wr   = wins / len(session_trades)

    severity = 0.0
    if wr < 0.3:
        severity = 0.5
    elif wr < 0.4:
        severity = 0.3

    return {
        "attack":   "session_risk",
        "severity": severity,
        "weight":   1.0,
        "detail":   f"{session} {direction}: {wins}/{len(session_trades)} WR ({wr:.0%}) over {len(session_trades)} trades",
    }


# ─── SURVIVAL SCORE ───
def compute_survival_score(attacks):
    """
    Multiply down from 1.0 using each attack's severity * weight.
    Clamp to [0.0, 1.0].
    """
    score = 1.0
    for attack in attacks:
        severity = attack.get("severity", 0.0)
        weight = attack.get("weight", 1.0)
        if severity > 0:
            score *= (1.0 - severity * weight)
    return round(max(0.0, min(1.0, score)), 4)


def score_to_verdict(survival_score):
    """Map survival score to verdict string."""
    if survival_score >= THRESHOLD_PROCEED:
        return "PROCEED", 1.0
    elif survival_score >= THRESHOLD_CAUTION:
        return "PROCEED_WITH_CAUTION", 0.7
    elif survival_score >= THRESHOLD_WEAK:
        return "WEAK", 0.4
    else:
        return "KILLED", 0.0


# ─── HEARTBEAT ───
def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = load_json_safe(HEARTBEAT_FILE, {})
    heartbeat["adversary"] = ts
    save_json(HEARTBEAT_FILE, heartbeat)


# ─── EPISODE UPDATE ───
def update_episode(hypothesis_id, adversary_result):
    """Append adversary result to existing episode file."""
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    episode_file = EPISODES_DIR / f"{hypothesis_id}.json"

    episode = {}
    if episode_file.exists():
        try:
            with open(episode_file) as f:
                episode = json.load(f)
        except (json.JSONDecodeError, OSError):
            episode = {}

    episode["adversary"] = adversary_result

    try:
        with open(episode_file, "w") as f:
            json.dump(episode, f, indent=2)
    except OSError as e:
        log(f"  [warn] Failed to update episode {hypothesis_id}: {e}")


# ─── MAIN ADVERSARY LOGIC ───
def load_active_rules_for_adversary():
    """Load active rules for adversary rule-based attacks."""
    if not ACTIVE_RULES_FILE.exists():
        return []
    try:
        with open(ACTIVE_RULES_FILE) as f:
            rules = json.load(f)
        return rules if isinstance(rules, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def attack_oi_divergence(hypothesis, world_coins):
    """
    Attack 7.5: OI divergence — detect when open interest contradicts position direction.
    OI rising + price falling = short buildup (bad for longs)
    OI rising + price rising = real trend (good for longs)
    OI falling = positions unwinding (caution for new entries)
    """
    coin = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "")
    coin_data = world_coins.get(coin, {})
    oi_data = coin_data.get("oi", {})

    if not oi_data or not oi_data.get("open_interest"):
        return {"attack": "oi_divergence", "severity": 0, "weight": 1.0,
                "detail": "No OI data available"}

    oi = oi_data.get("open_interest", 0)
    premium = oi_data.get("premium", 0)
    mark = oi_data.get("mark_price", 0)

    severity = 0.0
    details = []

    # Premium indicates mark vs index. Negative premium = mark below oracle = selling pressure
    if direction == "LONG" and premium < -0.001:
        severity = max(severity, 0.3)
        details.append(f"negative premium {premium:.4f} — selling pressure against LONG")
    elif direction == "SHORT" and premium > 0.001:
        severity = max(severity, 0.3)
        details.append(f"positive premium {premium:.4f} — buying pressure against SHORT")

    # Very high OI relative to daily volume suggests crowded trade
    volume = oi_data.get("volume_24h", 0)
    if volume > 0 and mark > 0:
        oi_usd = oi * mark
        oi_vol_ratio = oi_usd / volume if volume > 0 else 0
        if oi_vol_ratio > 2.0:
            severity = max(severity, 0.2)
            details.append(f"OI/volume ratio {oi_vol_ratio:.1f}x — crowded trade risk")

    detail = "; ".join(details) if details else "OI healthy"
    return {"attack": "oi_divergence", "severity": round(severity, 3), "weight": 1.0,
            "detail": detail}


def attack_timeframe_alignment(hypothesis, world_coins):
    """
    Block entries that conflict with cross-timeframe patterns.
    This replaces post-trade alignment exits — prevent bad trades, don't exit early.
    LONG + CONFIRMATION_SHORT/TRAP_LONG = severe penalty
    SHORT + CONFIRMATION_LONG/TRAP_SHORT = severe penalty
    """
    coin = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "")
    coin_data = world_coins.get(coin, {})
    tf = coin_data.get("timeframe", {})
    pattern = tf.get("pattern", "NEUTRAL")

    severity = 0.0
    details = []

    conflicts_long = {"CONFIRMATION_SHORT", "DIVERGENCE_BEAR", "TRAP_LONG"}
    conflicts_short = {"CONFIRMATION_LONG", "DIVERGENCE_BULL", "TRAP_SHORT"}

    if direction == "LONG" and pattern in conflicts_long:
        severity = 0.7 if pattern == "TRAP_LONG" else 0.5
        details.append(f"LONG vs {pattern} — timeframe says don't go long")
    elif direction == "SHORT" and pattern in conflicts_short:
        severity = 0.7 if pattern == "TRAP_SHORT" else 0.5
        details.append(f"SHORT vs {pattern} — timeframe says don't go short")

    return {"attack": "timeframe_alignment", "severity": round(severity, 3), "weight": 1.5,
            "detail": "; ".join(details) if details else "timeframe aligned"}


def attack_active_rules(hypothesis, active_rules):
    """
    Attack based on active rules from rule lifecycle.
    - If a 'kill' rule matches: severity 1.0
    - If 'reduce_confidence' rule matches: severity 0.4
    Returns attack dict.
    """
    if not active_rules:
        return {"attack": "rule_based", "severity": 0.0, "weight": 1.2,
                "detail": "No active rules", "matched_rules": []}

    coin = hypothesis.get("coin", "").lower()
    direction = hypothesis.get("direction", "").upper().lower()
    regime = hypothesis.get("regime", "").lower()
    pattern = str(hypothesis.get("signal", "")).lower()

    matched_kill = []
    matched_reduce = []

    for rule in active_rules:
        condition = rule.get("condition", "").lower()
        action = rule.get("action", "")

        # Match condition
        matched = False
        if f"regime == '{regime}'" in condition or f'regime == "{regime}"' in condition:
            matched = True
        elif f"direction == '{direction}'" in condition or f'direction == "{direction}"' in condition:
            matched = True
        elif f"pattern == '{pattern}'" in condition or f'pattern == "{pattern}"' in condition:
            matched = True

        if not matched:
            continue

        rule_id = rule.get("id", "?")
        if action == "kill":
            matched_kill.append(rule_id)
        elif action == "reduce_confidence":
            matched_reduce.append(rule_id)

    if matched_kill:
        return {
            "attack": "rule_based",
            "severity": 1.0,
            "weight": 1.2,
            "detail": f"Active KILL rules matched: {', '.join(matched_kill)}",
            "matched_rules": matched_kill,
        }
    elif matched_reduce:
        return {
            "attack": "rule_based",
            "severity": 0.4,
            "weight": 1.0,
            "detail": f"Active reduce rules matched: {', '.join(matched_reduce)}",
            "matched_rules": matched_reduce,
        }
    else:
        return {
            "attack": "rule_based",
            "severity": 0.0,
            "weight": 1.0,
            "detail": "No matching active rules",
            "matched_rules": [],
        }


# ─── ATTACK 11: CROSS-SOURCE DATA DISAGREEMENT ───
def load_taapi_snapshot() -> dict:
    """Load the latest TAAPI snapshot from bus file."""
    return load_json_safe(TAAPI_SNAPSHOT_FILE, {})


def _pct_delta(envy_val: float, taapi_val: float) -> float:
    """
    Compute percentage delta between two indicator values.
    Uses mean as denominator to avoid division-by-zero on near-zero values.
    Returns absolute percentage difference.
    """
    mean = (abs(envy_val) + abs(taapi_val)) / 2.0
    if mean < 1e-9:
        return 0.0
    return abs(envy_val - taapi_val) / mean * 100.0


# Indicators to compare between ENVY and TAAPI (must exist in both sources)
_DISAGREEMENT_INDICATORS = [
    "RSI_24H",
    "RSI_6H",
    "EMA_N_24H",
    "MACD_N_24H",
    "ADX_3H30M",
    "CMO_3H30M",
    "BB_POS_24H",
    "ROC_24H",
]


def attack_data_disagreement(hypothesis, world_coins, taapi_snapshot: dict) -> dict:
    """
    Attack 11: Cross-source data disagreement.

    Compares indicator values from ENVY (world_state.json) against TAAPI
    (taapi_snapshot.json) for the hypothesis coin. If multiple indicators
    show large divergence, the data sources disagree on market state — a
    signal that we cannot trust any single-source read.

    Severity rules:
      - Any indicator delta > 25% → severity 0.6 (regardless of count)
      - 5+ indicators delta > 10% → severity 0.8
      - 3+ indicators delta > 10% → severity 0.5
      - <3 indicators disagree    → severity 0.0
    """
    coin = hypothesis.get("coin", "")
    taapi_coins = taapi_snapshot.get("coins", {})

    # If no TAAPI snapshot exists or coin not in it, don't block
    if not taapi_coins:
        return {
            "attack": "data_disagreement",
            "severity": 0.0,
            "weight": 1.3,
            "detail": "TAAPI snapshot unavailable — skipping cross-source check",
            "disagreements": [],
        }

    taapi_coin = taapi_coins.get(coin, {})
    if not taapi_coin:
        return {
            "attack": "data_disagreement",
            "severity": 0.0,
            "weight": 1.3,
            "detail": f"No TAAPI data for {coin} — skipping cross-source check",
            "disagreements": [],
        }

    envy_coin = world_coins.get(coin, {})
    envy_indicators = envy_coin.get("indicators", {})

    disagreements = []
    checked = 0
    max_delta = 0.0

    for indicator in _DISAGREEMENT_INDICATORS:
        envy_val = envy_indicators.get(indicator)
        taapi_val = taapi_coin.get(indicator)

        # Skip if either source doesn't have this indicator
        if envy_val is None or taapi_val is None:
            continue

        try:
            envy_f = float(envy_val)
            taapi_f = float(taapi_val)
        except (TypeError, ValueError):
            continue

        checked += 1
        delta = _pct_delta(envy_f, taapi_f)
        max_delta = max(max_delta, delta)

        if delta > 10.0:
            disagreements.append({
                "indicator": indicator,
                "envy": round(envy_f, 6),
                "taapi": round(taapi_f, 6),
                "delta_pct": round(delta, 2),
            })

    # Can't do a meaningful check with too few indicators
    if checked < 2:
        return {
            "attack": "data_disagreement",
            "severity": 0.0,
            "weight": 1.3,
            "detail": f"Only {checked} comparable indicators available — cross-source check skipped",
            "disagreements": [],
        }

    n_disagree = len(disagreements)
    severity = 0.0
    reason_parts = []

    # Rule: any indicator delta > 25% → severity 0.6
    if max_delta > 25.0:
        severity = max(severity, 0.6)
        reason_parts.append(f"max delta {max_delta:.1f}% >25% threshold")

    # Rule: 5+ indicators disagree >10% → severity 0.8
    if n_disagree >= 5:
        severity = max(severity, 0.8)
        reason_parts.append(f"{n_disagree} indicators disagree >10%")
    # Rule: 3+ indicators disagree >10% → severity 0.5
    elif n_disagree >= 3:
        severity = max(severity, 0.5)
        reason_parts.append(f"{n_disagree} indicators disagree >10%")

    if not reason_parts:
        detail = (
            f"{n_disagree}/{checked} indicators disagree — below threshold"
            if n_disagree > 0
            else f"All {checked} indicators agree across ENVY and TAAPI"
        )
    else:
        detail = f"ENVY/TAAPI divergence: {'; '.join(reason_parts)}"

    return {
        "attack": "data_disagreement",
        "severity": round(severity, 3),
        "weight": 1.3,
        "detail": detail,
        "indicators_checked": checked,
        "indicators_disagreeing": n_disagree,
        "max_delta_pct": round(max_delta, 2),
        "disagreements": disagreements,
    }


# ─── SIGNAL FAMILY EXTRACTOR (mirrors genealogy.py) ───
def extract_signal_family(signal_name: str) -> str:
    """Extract the conceptual family from a full signal name."""
    if not signal_name:
        return signal_name
    parts = signal_name.split("_")
    family_parts = []
    for p in parts:
        if any(p.startswith(prefix) and (len(p) == 1 or p[1:].isdigit())
               for prefix in ("V", "EX", "Q", "MH")):
            break
        if p in ("LONG", "SHORT") and family_parts:
            break
        family_parts.append(p)
    return "_".join(family_parts) if family_parts else signal_name


# ─── REGIME PREDICTIONS LOADER ───
def load_regime_predictions() -> dict:
    """Load latest regime transition predictions."""
    if not REGIME_PREDICTIONS_FILE.exists():
        return {}
    try:
        with open(REGIME_PREDICTIONS_FILE) as f:
            data = json.load(f)
        return data.get("predictions", {})
    except (json.JSONDecodeError, OSError):
        return {}


# ─── GENEALOGY FAMILY STATS LOADER ───
def load_genealogy_family_stats() -> dict:
    """Load genealogy data for family track record attack."""
    if not GENEALOGY_FILE.exists():
        return {}
    try:
        with open(GENEALOGY_FILE) as f:
            data = json.load(f)
        return data.get("families", {})
    except (json.JSONDecodeError, OSError):
        return {}


# ─── ATTACK 9 (New): REGIME TRANSITION ───
def attack_regime_transition(hypothesis, regime_predictions: dict) -> dict:
    """
    If the coin is predicted to undergo regime transition, penalize new entries.
    Extra penalty if destabilizing + going LONG.
    """
    coin      = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "")
    pred      = regime_predictions.get(coin, {})
    prob      = pred.get("transition_probability", 0)
    dir_pred  = pred.get("predicted_direction", "")

    severity = 0.0
    if prob > 0.6:
        severity = 0.5   # high transition risk
    elif prob > 0.4:
        severity = 0.2   # moderate risk

    # Extra penalty: destabilizing + going LONG
    if dir_pred == "destabilizing" and direction == "LONG":
        severity = min(1.0, severity + 0.2)

    return {
        "attack":   "regime_transition",
        "severity": round(severity, 3),
        "weight":   1.3,
        "detail":   (
            f"transition prob {prob:.0%}, "
            f"direction: {dir_pred or 'unknown'}"
        ),
    }


# ─── ATTACK 10 (New): FAMILY TRACK RECORD ───
def attack_family_track_record(hypothesis, family_stats: dict) -> dict:
    """
    Penalize hypotheses from signal families with proven poor track records.
    Only fires when the family is mature (20+ instances) and 10+ trades.
    """
    signal    = hypothesis.get("signal", "")
    regime    = hypothesis.get("regime", "unknown")
    direction = hypothesis.get("direction", "?")

    family_key = f"{extract_signal_family(signal)}|{regime}|{direction}"
    family     = family_stats.get(family_key, {})

    if not family.get("mature") or family.get("traded", 0) < 10:
        return {
            "attack":  "family_track_record",
            "severity": 0,
            "weight":  1.0,
            "detail":  f"family immature ({family.get('total_instances', 0)} instances)",
        }

    wr = family.get("win_rate", 0.5)
    if wr is None:
        return {
            "attack":  "family_track_record",
            "severity": 0,
            "weight":  1.0,
            "detail":  f"family no win_rate data yet ({family.get('traded', 0)} traded)",
        }

    severity = 0.0
    if wr < 0.25:
        severity = 0.6   # proven loser family
    elif wr < 0.35:
        severity = 0.4
    elif wr < 0.45:
        severity = 0.2

    return {
        "attack":   "family_track_record",
        "severity": round(severity, 3),
        "weight":   1.3,
        "detail":   f"family WR {wr:.0%} over {family.get('traded', 0)} trades",
    }


# ─── ATTACK: PREMIUM DIVERGENCE ───
def attack_premium_divergence(hypothesis, world_coins) -> dict:
    """
    Attack: mark-oracle premium divergence.

    Positive premium means mark > oracle → longs are overleveraged.
    Going LONG into a strongly positive premium is risky (longs get squeezed).

    Negative premium means mark < oracle → shorts are overleveraged.
    Going SHORT into a strongly negative premium is risky.

    Threshold: abs(premium) > 0.002 triggers severity 0.5.
    Weight: 1.2x (premium is a reliable crowding indicator).
    """
    coin      = hypothesis.get("coin", "")
    direction = hypothesis.get("direction", "")
    coin_data = world_coins.get(coin, {})

    # Premium lives in oi sub-dict (injected by perception from hl_enrichment)
    oi_data = coin_data.get("oi", {})
    premium = safe_float_adv(oi_data.get("premium"))

    PREMIUM_THRESHOLD = 0.002  # 2x historical average signal level

    severity = 0.0
    details  = []

    if direction == "LONG" and premium > PREMIUM_THRESHOLD:
        severity = 0.5
        details.append(
            f"premium {premium:.5f} > {PREMIUM_THRESHOLD} — longs overleveraged, "
            f"risky to go LONG (mark above oracle by {premium*100:.3f}%)"
        )
    elif direction == "SHORT" and premium < -PREMIUM_THRESHOLD:
        severity = 0.5
        details.append(
            f"premium {premium:.5f} < -{PREMIUM_THRESHOLD} — shorts overleveraged, "
            f"risky to go SHORT (mark below oracle by {abs(premium)*100:.3f}%)"
        )

    return {
        "attack":   "premium_divergence",
        "severity": round(severity, 3),
        "weight":   1.2,
        "detail":   "; ".join(details) if details else f"premium {premium:.6f} — within normal range",
        "premium":  round(premium, 8),
    }


def safe_float_adv(val, default=0.0):
    """Safe float conversion for adversary module."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def run_adversary(hypotheses, closed_trades, positions, world_coins, world_state_meta=None):
    """
    Run all attacks on each hypothesis. Return (survivors, killed, results).
    """
    if world_state_meta is None:
        world_state_meta = {}

    results = []
    survivors = []
    killed_count = 0
    cautioned_count = 0
    proceeded_count = 0

    # Load evolved weights from counterfactual learning
    evolved_weights = load_evolved_weights()
    if evolved_weights:
        log(f"  Loaded evolved weights for {len(evolved_weights)} attacks from counterfactual learning")

    # Load active rules once for all hypotheses
    active_rules = load_active_rules_for_adversary()
    if active_rules:
        log(f"  Loaded {len(active_rules)} active rules for rule-based attacks")

    # Load macro intel for fear_greed + macro_event attacks
    macro_intel = load_json_safe(BUS_DIR / "macro_intel.json", {})

    # Load regime predictions + genealogy family stats (new attacks)
    regime_predictions = load_regime_predictions()
    family_stats       = load_genealogy_family_stats()
    if regime_predictions:
        log(f"  Loaded regime predictions for {len(regime_predictions)} coins")
    if family_stats:
        log(f"  Loaded genealogy family stats ({len(family_stats)} families)")

    # Load TAAPI snapshot for cross-source data disagreement attack
    taapi_snapshot = load_taapi_snapshot()
    taapi_ts = taapi_snapshot.get("timestamp", "")
    if taapi_snapshot.get("coins"):
        log(f"  Loaded TAAPI snapshot ({len(taapi_snapshot['coins'])} coins, ts={taapi_ts[:19]})")
    else:
        log("  No TAAPI snapshot available — data_disagreement attack will be passive")

    for hyp in hypotheses:
        coin = hyp.get("coin", "?")
        direction = hyp.get("direction", "?")
        hyp_id = hyp.get("hypothesis_id", f"hyp_{coin}_{direction}")

        log(f"  Attacking {hyp_id} ({coin} {direction})...")

        # Run all attacks (standard + rule-based + macro/session upgrades + new)
        attacks = [
            attack_similar_failure(hyp, closed_trades),
            attack_kill_condition_proximity(hyp, world_coins),
            attack_portfolio_stress(hyp, positions),
            attack_confidence_vs_anti_thesis(hyp),
            attack_regime_mismatch(hyp, world_coins),
            attack_funding_headwind(hyp, world_coins),
            attack_oi_divergence(hyp, world_coins),
            attack_timeframe_alignment(hyp, world_coins),
            attack_active_rules(hyp, active_rules),
            attack_macro_regime(hyp, world_state_meta),              # Upgrade 1
            attack_session_risk(hyp, world_state_meta),              # Upgrade 3
            attack_regime_transition(hyp, regime_predictions),       # New: predictive regime risk
            attack_family_track_record(hyp, family_stats),           # New: genealogy track record
            attack_data_disagreement(hyp, world_coins, taapi_snapshot),  # New: cross-source disagreement
            attack_fear_greed(hyp, macro_intel),                     # New: Fear & Greed filter
            attack_macro_event(hyp, macro_intel),                    # New: macro event filter
            attack_premium_divergence(hyp, world_coins),             # New: mark-oracle premium crowding
        ]

        # Apply evolved weights from counterfactual learning (overrides static weights)
        if evolved_weights:
            for attack in attacks:
                attack_name = attack.get("attack", "")
                if attack_name in evolved_weights:
                    attack["weight"] = evolved_weights[attack_name]

        # Only include attacks with non-zero severity in summary
        active_attacks = [a for a in attacks if a.get("severity", 0) > 0]

        # Compute survival
        survival_score = compute_survival_score(attacks)
        verdict, size_modifier = score_to_verdict(survival_score)

        result = {
            "hypothesis_id": hyp_id,
            "coin": coin,
            "direction": direction,
            "survival_score": survival_score,
            "verdict": verdict,
            "recommended_size_modifier": size_modifier,
            "attacks": attacks,
            "active_attacks": len(active_attacks),
        }

        results.append(result)

        # Update episode
        update_episode(hyp_id, result)

        if verdict == "KILLED":
            killed_count += 1
            log(f"    KILLED  score={survival_score:.3f} — {', '.join(a['detail'][:40] for a in active_attacks[:2])}")
        else:
            # Add adversary fields to hypothesis
            hyp["survival_score"] = survival_score
            hyp["adversary_verdict"] = verdict
            hyp["recommended_size_modifier"] = size_modifier
            hyp["attacks"] = attacks
            survivors.append(hyp)

            if verdict == "PROCEED_WITH_CAUTION":
                cautioned_count += 1
                log(f"    CAUTION score={survival_score:.3f}")
            elif verdict == "WEAK":
                cautioned_count += 1
                log(f"    WEAK    score={survival_score:.3f}")
            else:
                proceeded_count += 1
                log(f"    PROCEED score={survival_score:.3f}")

    return survivors, killed_count, cautioned_count, proceeded_count, results


# ─── RUN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()

    log("=" * 60)
    log(f"Adversary Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log("=" * 60)

    # Load all inputs
    cand_ts, hypotheses = load_candidates()
    if not hypotheses:
        log("No hypotheses found in candidates.json — skipping")
        write_heartbeat()
        return

    # Check if hypotheses are fresh (avoid re-processing old ones)
    adv_data = load_json_safe(ADVERSARY_FILE, {})
    last_processed_ts = adv_data.get("candidates_timestamp", "")
    if last_processed_ts and last_processed_ts == cand_ts:
        log(f"Already processed these hypotheses (ts={cand_ts}), skipping")
        write_heartbeat()
        return

    log(f"Processing {len(hypotheses)} hypotheses (ts={cand_ts})")

    closed_trades = load_closed_trades()
    positions = load_positions()
    world_state_full = load_world_state()
    world_coins = world_state_full.get("coins", {})
    world_state_meta = world_state_full.get("meta", {})

    log(f"  Closed trades: {len(closed_trades)}")
    log(f"  Open positions: {len(positions)}")
    log(f"  World state coins: {len(world_coins)}")
    log(f"  Macro: {world_state_meta.get('macro', {}).get('state', 'UNKNOWN')} | Session: {world_state_meta.get('session', 'UNKNOWN')}")

    # Run adversary
    survivors, killed, cautioned, proceeded, results = run_adversary(
        hypotheses, closed_trades, positions, world_coins, world_state_meta
    )

    # ── Write adversary.json ──
    adversary_output = {
        "timestamp": ts_iso,
        "candidates_timestamp": cand_ts,
        "hypotheses_received": len(hypotheses),
        "killed": killed,
        "cautioned": cautioned,
        "proceeded": proceeded,
        "survivors": len(survivors),
        "results": results,
    }
    save_json(ADVERSARY_FILE, adversary_output)
    log(f"Written to {ADVERSARY_FILE}")

    # ── Update candidates.json ──
    # Read fresh (don't rely on what we loaded since we mutated it)
    cand_data = load_json_safe(CANDIDATES_FILE, {})
    cand_data["candidates"] = survivors
    cand_data["adversary_timestamp"] = ts_iso
    cand_data["adversary_killed"] = killed
    save_json(CANDIDATES_FILE, cand_data)
    log(f"Updated {CANDIDATES_FILE}: {len(survivors)} survivors (killed {killed})")

    write_heartbeat()

    # ── Summary ──
    log(f"{'='*60}")
    log(f"Received:  {len(hypotheses)}")
    log(f"Killed:    {killed}")
    log(f"Cautioned: {cautioned}")
    log(f"Proceeded: {proceeded}")
    log(f"Survivors: {len(survivors)}")

    if survivors:
        log("Surviving hypotheses:")
        for h in survivors[:5]:
            log(
                f"  {h['adversary_verdict']:22s} score={h['survival_score']:.3f}  "
                f"{h['coin']:6s} {h['direction']:5s}  size_mod={h['recommended_size_modifier']}"
            )
    log("=" * 60)


def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Adversary starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

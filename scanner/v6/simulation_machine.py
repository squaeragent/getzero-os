# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
simulation_machine.py — 4 mechanical simulations for economy validation.

Pure math. No API calls. No external deps beyond stdlib.
Runs: python -m scanner.v6.simulation_machine
"""

import json
import math
import random
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

random.seed(42)

# ─── CONSTANTS ──────────────────────────────────────────────────────────────────

DAYS = 90
GENESIS_CREDITS = 10_000
PACK_PRICE_USD = 29
PACK_CREDITS = 10_000

SESSION_COSTS = {
    "sniper":   {"cost": 300, "used": 190, "refund": 110},
    "degen":    {"cost": 500, "used": 460, "refund":  40},
    "momentum": {"cost": 400, "used": 340, "refund":  60},
    "fade":     {"cost": 200, "used": 150, "refund":  50},
    "defense":  {"cost": 200, "used": 160, "refund":  40},
    "funding":  {"cost": 150, "used": 100, "refund":  50},
    "scout":    {"cost": 600, "used": 540, "refund":  60},
    "watch":    {"cost": 100, "used":  85, "refund":  15},
}

TRADES_PER_SESSION = {
    "sniper": 0.7, "degen": 10, "momentum": 4, "fade": 2,
    "defense": 0.5, "funding": 2, "scout": 20, "watch": 0,
}

STREAK_REWARDS = {7: 100, 14: 200, 30: 500, 60: 1000, 90: 2000}

EARN_BASE = {"scout": 10}  # default 5
SCORE_MULTIPLIER = [(9, 3.0), (8, 2.0), (7, 1.5), (6, 1.2)]

# Score system constants
BAYESIAN_PRIOR = 5.0
CONVERGENCE_TRADES = 100
DECAY_HALFLIFE_DAYS = 14


# ─── OPERATOR PROFILES ─────────────────────────────────────────────────────────

CREDIT_PROFILES = [
    {"name": "heavy_degen",     "count": 30,  "sessions_week": 5,
     "strats": {"degen": 0.6, "momentum": 0.3, "scout": 0.1},
     "score": 5.5, "early_quit": 0.20, "buy_below": 500},
    {"name": "steady_momentum", "count": 60,  "sessions_week": 3,
     "strats": {"momentum": 0.7, "defense": 0.2, "watch": 0.1},
     "score": 6.5, "early_quit": 0.10, "buy_below": 1000},
    {"name": "cautious_defender","count": 40, "sessions_week": 1.5,
     "strats": {"defense": 0.6, "watch": 0.2, "momentum": 0.2},
     "score": 7.0, "early_quit": 0.05, "buy_below": 500},
    {"name": "sniper_patient",  "count": 20,  "sessions_week": 2,
     "strats": {"sniper": 0.7, "momentum": 0.2, "fade": 0.1},
     "score": 7.5, "early_quit": 0.05, "buy_below": 300},
    {"name": "scout_farmer",    "count": 20,  "sessions_week": 2.5,
     "strats": {"scout": 0.8, "momentum": 0.2},
     "score": 6.0, "early_quit": 0.10, "buy_below": 600},
    {"name": "explorer",        "count": 20,  "sessions_week": 3,
     "strats": {"sniper": 0.125, "degen": 0.125, "momentum": 0.125,
                "fade": 0.125, "defense": 0.125, "funding": 0.125,
                "scout": 0.125, "watch": 0.125},
     "score": 6.0, "early_quit": 0.15, "buy_below": 800},
    {"name": "churner",         "count": 10,  "sessions_week": 4,
     "strats": {"degen": 0.5, "momentum": 0.5},
     "score": 4.5, "early_quit": 0.30, "buy_below": 0},
]

STREAK_PROFILES = [
    {"name": "daily_devotee",   "count": 40, "check_rate": 0.95},
    {"name": "weekday_warrior", "count": 60, "check_rate": 0.78},
    {"name": "casual",          "count": 50, "check_rate": 0.55},
    {"name": "sporadic",        "count": 30, "check_rate": 0.35},
    {"name": "churner",         "count": 20, "check_rate": 0.80, "decay_after": 14, "decay_rate": 0.05},
]

SCORE_SKILL_LEVELS = [
    {"name": "bad",     "count": 60,  "win_rate": 0.35, "avg_pnl": -0.5, "pnl_std": 2.0,
     "rejection_rate": 0.3, "override_rate": 0.15, "recovery_speed": 5},
    {"name": "average", "count": 80,  "win_rate": 0.48, "avg_pnl": 0.1,  "pnl_std": 1.5,
     "rejection_rate": 0.6, "override_rate": 0.05, "recovery_speed": 3},
    {"name": "good",    "count": 40,  "win_rate": 0.58, "avg_pnl": 0.5,  "pnl_std": 1.2,
     "rejection_rate": 0.8, "override_rate": 0.02, "recovery_speed": 2},
    {"name": "elite",   "count": 20,  "win_rate": 0.68, "avg_pnl": 1.0,  "pnl_std": 1.0,
     "rejection_rate": 0.92, "override_rate": 0.01, "recovery_speed": 1},
]

FUNNEL_RATES = [
    ("visit",           "apply",          0.15),
    ("apply",           "approved",       0.80),
    ("approved",        "install",        0.70),
    ("install",         "first_session",  0.85),
    ("first_session",   "second_session", 0.60),
    ("second_session",  "day_7_active",   0.50),
    ("day_7_active",    "day_30_active",  0.40),
    ("day_30_active",   "paying_customer",0.30),
]


# ─── HELPERS ────────────────────────────────────────────────────────────────────

def _pick_strategy(strats: dict[str, float]) -> str:
    r = random.random()
    cumulative = 0.0
    for strat, prob in strats.items():
        cumulative += prob
        if r <= cumulative:
            return strat
    return list(strats.keys())[-1]


def _score_multiplier(score: float) -> float:
    for threshold, mult in SCORE_MULTIPLIER:
        if score >= threshold:
            return mult
    return 1.0


def _fmt_table(headers: list[str], rows: list[list], title: str = "") -> str:
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = [str(v) for v in row]
        str_rows.append(sr)
        for i, v in enumerate(sr):
            widths[i] = max(widths[i], len(v))

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    hdr = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"

    lines = []
    if title:
        lines.append(f"\n{'='*len(sep)}")
        lines.append(f" {title}")
        lines.append(f"{'='*len(sep)}")
    lines.append(sep)
    lines.append(hdr)
    lines.append(sep)
    for sr in str_rows:
        lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(sr, widths)) + " |")
    lines.append(sep)
    return "\n".join(lines)


# ─── SIMULATION 1: CREDIT ECONOMY ──────────────────────────────────────────────

def sim_credit_economy() -> dict[str, Any]:
    """200 operators, 90 days. Track credit flow and revenue."""

    @dataclass
    class Operator:
        profile: str
        credits: int = GENESIS_CREDITS
        total_spent: int = 0
        total_earned: int = 0
        total_purchased: int = 0
        packs_bought: int = 0
        churned: bool = False
        churn_day: int = 0
        streak: int = 0
        first_purchase_day: int = -1
        active: bool = True

    operators: list[Operator] = []
    for prof in CREDIT_PROFILES:
        for _ in range(prof["count"]):
            operators.append(Operator(profile=prof["name"]))

    daily_snapshots = []
    arena_weekly_pool = 500

    for day in range(1, DAYS + 1):
        prof_map = {p["name"]: p for p in CREDIT_PROFILES}

        # Arena rewards (weekly, top 10 get 50 each)
        if day % 7 == 0:
            active_ops = [o for o in operators if o.active and not o.churned]
            if active_ops:
                top_10 = sorted(active_ops, key=lambda o: o.total_earned, reverse=True)[:10]
                per_op = arena_weekly_pool // len(top_10)
                for o in top_10:
                    o.credits += per_op
                    o.total_earned += per_op

        for op in operators:
            prof = prof_map[op.profile]

            # Churner logic
            if op.profile == "churner" and day > 14 and not op.churned:
                op.churned = True
                op.churn_day = day
                op.active = False
                continue
            if op.churned:
                continue

            # Determine if session happens today (sessions_week / 7)
            daily_prob = prof["sessions_week"] / 7.0
            if random.random() > daily_prob:
                # No session — streak check for rewards still applies via check-in
                op.streak += 1
                for threshold, reward in STREAK_REWARDS.items():
                    if op.streak == threshold:
                        op.credits += reward
                        op.total_earned += reward
                continue

            # Pick strategy and run session
            strat = _pick_strategy(prof["strats"])
            cost_info = SESSION_COSTS[strat]
            net_cost = cost_info["used"]  # actual spend after refund

            # Early quit — only pay partial
            if random.random() < prof["early_quit"]:
                net_cost = cost_info["used"] // 2

            # Check if operator can afford
            if op.credits < cost_info["cost"]:
                # Need to buy credits?
                if prof["buy_below"] > 0 and op.credits < prof["buy_below"]:
                    op.credits += PACK_CREDITS
                    op.total_purchased += PACK_CREDITS
                    op.packs_bought += 1
                    if op.first_purchase_day < 0:
                        op.first_purchase_day = day
                elif op.credits < cost_info["cost"]:
                    # Can't afford, skip
                    continue

            # Spend
            op.credits -= net_cost
            op.total_spent += net_cost

            # Earn credits from trades
            trades = TRADES_PER_SESSION.get(strat, 0)
            actual_trades = int(trades) + (1 if random.random() < (trades % 1) else 0)
            base_earn = EARN_BASE.get(strat, 5)
            mult = _score_multiplier(prof["score"])
            earned = int(actual_trades * base_earn * mult)
            op.credits += earned
            op.total_earned += earned

            # Streak
            op.streak += 1
            for threshold, reward in STREAK_REWARDS.items():
                if op.streak == threshold:
                    op.credits += reward
                    op.total_earned += reward

        # Daily snapshot
        active_count = sum(1 for o in operators if o.active and not o.churned)
        total_bal = sum(o.credits for o in operators)
        daily_snapshots.append({
            "day": day,
            "active_operators": active_count,
            "total_balance": total_bal,
            "avg_balance": total_bal / max(active_count, 1),
        })

    # Aggregate
    total_spent = sum(o.total_spent for o in operators)
    total_earned = sum(o.total_earned for o in operators)
    total_purchased = sum(o.total_purchased for o in operators)
    who_bought = sum(1 for o in operators if o.packs_bought > 0)
    who_churned = sum(1 for o in operators if o.churned)
    at_zero = sum(1 for o in operators if o.credits <= 0 and not o.churned)
    revenue = sum(o.packs_bought for o in operators) * PACK_PRICE_USD

    purchase_days = [o.first_purchase_day for o in operators if o.first_purchase_day > 0]
    avg_first_purchase = statistics.mean(purchase_days) if purchase_days else 0

    results = {
        "total_credits_spent": total_spent,
        "total_credits_earned": total_earned,
        "total_credits_purchased": total_purchased,
        "net_credit_flow": total_earned - total_spent,
        "operators_who_bought_more": who_bought,
        "operators_who_churned": who_churned,
        "revenue_from_purchases_usd": revenue,
        "operators_at_zero_balance": at_zero,
        "pct_at_zero": round(at_zero / len(operators) * 100, 1),
        "avg_days_until_first_purchase": round(avg_first_purchase, 1),
        "daily_snapshots": daily_snapshots,
    }

    # Decision checks
    checks = {
        "net_flow_negative": total_spent > total_earned,
        "at_zero_under_20pct": (at_zero / len(operators)) < 0.20,
        "first_purchase_week_6_10": 42 <= avg_first_purchase <= 70 if purchase_days else False,
    }
    results["decision_checks"] = checks

    return results


# ─── SIMULATION 2: STREAK MECHANICS ────────────────────────────────────────────

def sim_streak_mechanics() -> dict[str, Any]:
    """200 operators, 90 days. Streak check-in simulation."""

    @dataclass
    class StreakOp:
        profile: str
        base_rate: float
        current_streak: int = 0
        longest_streak: int = 0
        total_breaks: int = 0
        warning_saves: int = 0
        rewards_earned: int = 0
        milestones: dict = field(default_factory=dict)
        decay_after: int = 0
        decay_rate: float = 0.0

    operators: list[StreakOp] = []
    for prof in STREAK_PROFILES:
        for _ in range(prof["count"]):
            operators.append(StreakOp(
                profile=prof["name"],
                base_rate=prof["check_rate"],
                decay_after=prof.get("decay_after", 0),
                decay_rate=prof.get("decay_rate", 0.0),
            ))

    for day in range(1, DAYS + 1):
        for op in operators:
            # Compute effective check rate
            rate = op.base_rate
            if op.decay_after > 0 and day > op.decay_after:
                decay_days = day - op.decay_after
                rate = max(0.0, rate - decay_days * op.decay_rate)

            # Warning boost: +20% if streak >= 3
            boosted = False
            if op.current_streak >= 3 and random.random() > rate:
                # Would have missed — apply warning boost
                if random.random() < 0.20:
                    boosted = True
                    op.warning_saves += 1

            checked_in = boosted or (random.random() < rate)

            if checked_in:
                op.current_streak += 1
                op.longest_streak = max(op.longest_streak, op.current_streak)
                # Check milestones
                for threshold, reward in STREAK_REWARDS.items():
                    if op.current_streak == threshold and threshold not in op.milestones:
                        op.milestones[threshold] = day
                        op.rewards_earned += reward
            else:
                if op.current_streak > 0:
                    op.total_breaks += 1
                op.current_streak = 0

    # Aggregate
    reach_7 = sum(1 for o in operators if o.longest_streak >= 7)
    reach_30 = sum(1 for o in operators if o.longest_streak >= 30)
    reach_90 = sum(1 for o in operators if o.longest_streak >= 90)
    total_rewards = sum(o.rewards_earned for o in operators)
    streaks = [o.longest_streak for o in operators]
    total_breaks = sum(o.total_breaks for o in operators)
    total_saves = sum(o.warning_saves for o in operators)

    return {
        "operators_reaching_7d": reach_7,
        "operators_reaching_30d": reach_30,
        "operators_reaching_90d": reach_90,
        "total_streak_rewards_paid": total_rewards,
        "avg_longest_streak": round(statistics.mean(streaks), 1),
        "median_longest_streak": round(statistics.median(streaks), 1),
        "total_streak_breaks": total_breaks,
        "warning_saves": total_saves,
        "distribution": {
            "0-6": sum(1 for s in streaks if s < 7),
            "7-13": sum(1 for s in streaks if 7 <= s < 14),
            "14-29": sum(1 for s in streaks if 14 <= s < 30),
            "30-59": sum(1 for s in streaks if 30 <= s < 60),
            "60-89": sum(1 for s in streaks if 60 <= s < 90),
            "90": sum(1 for s in streaks if s >= 90),
        },
    }


# ─── SIMULATION 3: SCORE PROGRESSION ───────────────────────────────────────────

def _compute_immune(uptime_days: int, saves: int, checks: int) -> float:
    stop_rate = 1.0  # simulated operators always verify stops
    save_rate = saves / max(checks, 1)
    uptime_pct = min(uptime_days / 90, 1.0)
    raw = stop_rate * 4 + (1 - min(save_rate * 10, 1)) * 3 + uptime_pct * 3
    return min(10.0, max(0.0, raw))


def _compute_discipline(total_trades: int, total_evals: int, overrides: int, config_changes: int) -> float:
    rejection_rate = 1 - (total_trades / max(total_evals, total_trades))
    rej_score = min(rejection_rate * 10, 9.5)
    override_penalty = min(overrides * 0.5, 3)
    config_penalty = min(config_changes * 0.2, 2)
    return max(0.0, min(10.0, rej_score - override_penalty - config_penalty))


def _compute_performance(returns: list[float]) -> float:
    if not returns:
        return BAYESIAN_PRIOR
    avg_return = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    downside_dev = (math.sqrt(sum(r ** 2 for r in downside) / len(downside))
                    if downside else 0.001)
    sortino = avg_return / max(downside_dev, 0.001)
    # Max drawdown
    peak = cumulative = 0.0
    max_dd = 0.0
    for r in returns:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    dd_penalty = min(max_dd * 20, 4)
    raw = min(10, max(0, sortino * 2 + 5)) - dd_penalty
    return max(0.0, min(10.0, raw))


def _compute_consistency(returns: list[float], days_active: int) -> float:
    if len(returns) < 5:
        return BAYESIAN_PRIOR
    # Positive day rate (approximate: chunk returns into daily groups)
    chunk_size = max(1, len(returns) // max(days_active, 1))
    daily_pnls = []
    for i in range(0, len(returns), max(chunk_size, 1)):
        daily_pnls.append(sum(returns[i:i + chunk_size]))
    pos_days = sum(1 for p in daily_pnls if p > 0)
    pos_rate = pos_days / max(len(daily_pnls), 1)
    pos_day_score = pos_rate * 10

    # Rolling win-rate stability
    n_chunks = 4
    chunk_len = max(1, len(returns) // n_chunks)
    win_rates = []
    for i in range(n_chunks):
        chunk = returns[i * chunk_len:(i + 1) * chunk_len]
        if chunk:
            win_rates.append(sum(1 for r in chunk if r > 0) / len(chunk))
    wr_std = statistics.stdev(win_rates) if len(win_rates) > 1 else 0
    stability_score = max(0, 10 - wr_std * 30)

    return min(10.0, pos_day_score * 0.6 + stability_score * 0.4)


def _compute_resilience(returns: list[float], recovery_speed: int) -> float:
    if len(returns) < 10:
        return BAYESIAN_PRIOR
    # Simulate recovery times based on skill level
    drawdowns = [i for i, r in enumerate(returns) if r < 0]
    if not drawdowns:
        return 9.0  # no losses = excellent resilience

    recovery_times = []
    for idx in drawdowns:
        # Recovery time modeled from operator skill
        rt = max(1, recovery_speed + random.randint(-1, 1))
        recovery_times.append(rt)

    avg_recovery = sum(recovery_times) / len(recovery_times)
    recovery_score = max(0, 10 - avg_recovery * 2)

    # Regime change win rate (simulate ~20% of trades during regime changes)
    regime_trades = returns[::5]  # every 5th trade as "regime change"
    if regime_trades:
        regime_wr = sum(1 for r in regime_trades if r > 0) / len(regime_trades)
    else:
        regime_wr = 0.5
    regime_score = regime_wr * 10

    return min(10.0, recovery_score * 0.6 + regime_score * 0.4)


def _final_score(components: dict[str, float], trade_count: int) -> float:
    weights = {"immune": 0.25, "discipline": 0.25, "performance": 0.20,
               "consistency": 0.20, "resilience": 0.10}
    raw = sum(components[k] * weights[k] for k in weights)
    # Bayesian convergence
    alpha = min(1.0, trade_count / CONVERGENCE_TRADES)
    score = alpha * raw + (1 - alpha) * BAYESIAN_PRIOR
    return round(min(10.0, max(0.0, score)), 1)


def sim_score_progression() -> dict[str, Any]:
    """200 operators, 90 days. Score evolution per Score System 2."""

    @dataclass
    class ScoreOp:
        skill: str
        win_rate: float
        avg_pnl: float
        pnl_std: float
        rejection_rate: float
        override_rate: float
        recovery_speed: int
        trades: list = field(default_factory=list)
        total_evals: int = 0
        overrides: int = 0
        config_changes: int = 0
        immune_saves: int = 0
        immune_checks: int = 0
        scores_at: dict = field(default_factory=dict)  # day -> score

    operators: list[ScoreOp] = []
    for level in SCORE_SKILL_LEVELS:
        for _ in range(level["count"]):
            operators.append(ScoreOp(
                skill=level["name"],
                win_rate=level["win_rate"] + random.gauss(0, 0.03),
                avg_pnl=level["avg_pnl"],
                pnl_std=level["pnl_std"],
                rejection_rate=level["rejection_rate"],
                override_rate=level["override_rate"],
                recovery_speed=level["recovery_speed"],
            ))

    trades_per_day = 3  # average trades per active day

    for day in range(1, DAYS + 1):
        for op in operators:
            # Generate daily trades
            n_trades = random.randint(1, trades_per_day * 2)
            n_evals = int(n_trades / (1 - op.rejection_rate + 0.01))
            op.total_evals += n_evals

            for _ in range(n_trades):
                if random.random() < op.win_rate:
                    pnl = abs(random.gauss(op.avg_pnl, op.pnl_std))
                else:
                    pnl = -abs(random.gauss(op.avg_pnl * 0.8, op.pnl_std))
                op.trades.append(pnl)

            # Overrides and config changes
            if random.random() < op.override_rate:
                op.overrides += 1
            if random.random() < 0.02:  # small config change rate
                op.config_changes += 1

            # Immune system
            op.immune_checks += random.randint(1, 3)
            if random.random() < 0.05:
                op.immune_saves += 1

            # Score at checkpoints
            if day in (30, 60, 90) or day == 1:
                returns = op.trades
                components = {
                    "immune": _compute_immune(day, op.immune_saves, op.immune_checks),
                    "discipline": _compute_discipline(len(returns), op.total_evals, op.overrides, op.config_changes),
                    "performance": _compute_performance(returns),
                    "consistency": _compute_consistency(returns, day),
                    "resilience": _compute_resilience(returns, op.recovery_speed),
                }
                op.scores_at[day] = {
                    "total": _final_score(components, len(returns)),
                    "components": {k: round(v, 1) for k, v in components.items()},
                }

    # Aggregate
    def scores_at_day(d):
        return [op.scores_at[d]["total"] for op in operators if d in op.scores_at]

    final_scores = scores_at_day(90)
    milestones = {f">={m}": sum(1 for s in final_scores if s >= m) for m in [5.0, 6.0, 7.0, 8.0, 9.0]}

    # Component analysis at day 90
    component_avgs = {}
    for comp in ["immune", "discipline", "performance", "consistency", "resilience"]:
        vals = [op.scores_at[90]["components"][comp] for op in operators if 90 in op.scores_at]
        component_avgs[comp] = round(statistics.mean(vals), 2) if vals else 0

    # Find lagging dimensions
    sorted_comps = sorted(component_avgs.items(), key=lambda x: x[1])
    lagging = [sorted_comps[0][0], sorted_comps[1][0]] if len(sorted_comps) >= 2 else []

    # Distribution buckets
    dist = {"0-3": 0, "3-5": 0, "5-6": 0, "6-7": 0, "7-8": 0, "8-9": 0, "9-10": 0}
    for s in final_scores:
        if s < 3: dist["0-3"] += 1
        elif s < 5: dist["3-5"] += 1
        elif s < 6: dist["5-6"] += 1
        elif s < 7: dist["6-7"] += 1
        elif s < 8: dist["7-8"] += 1
        elif s < 9: dist["8-9"] += 1
        else: dist["9-10"] += 1

    # By skill level
    by_skill = {}
    for level in SCORE_SKILL_LEVELS:
        level_ops = [op for op in operators if op.skill == level["name"]]
        level_scores = [op.scores_at[90]["total"] for op in level_ops if 90 in op.scores_at]
        by_skill[level["name"]] = {
            "avg": round(statistics.mean(level_scores), 2) if level_scores else 0,
            "min": round(min(level_scores), 1) if level_scores else 0,
            "max": round(max(level_scores), 1) if level_scores else 0,
        }

    return {
        "milestones": milestones,
        "avg_score_day_30": round(statistics.mean(scores_at_day(30)), 2),
        "avg_score_day_60": round(statistics.mean(scores_at_day(60)), 2),
        "avg_score_day_90": round(statistics.mean(scores_at_day(90)), 2),
        "distribution_day_90": dist,
        "component_averages": component_avgs,
        "lagging_dimensions": lagging,
        "by_skill_level": by_skill,
    }


# ─── SIMULATION 4: CONVERSION FUNNEL ───────────────────────────────────────────

def sim_conversion_funnel() -> dict[str, Any]:
    """1000 visitors, 90-day funnel."""
    visitors = 1000
    stages = {"visit": visitors}
    current = visitors

    for from_stage, to_stage, rate in FUNNEL_RATES:
        # Stochastic: each individual converts independently
        converted = sum(1 for _ in range(current) if random.random() < rate)
        stages[to_stage] = converted
        current = converted

    paying = stages.get("paying_customer", 0)
    revenue = paying * PACK_PRICE_USD  # first purchase
    # Assume 30% of paying buy a second pack over 90 days
    repeat_buyers = sum(1 for _ in range(paying) if random.random() < 0.30)
    total_revenue = revenue + repeat_buyers * PACK_PRICE_USD

    # CAC calculation (assume $50 CPA for visits)
    cpa = 50
    total_spend = visitors * cpa
    cac = total_spend / max(paying, 1)

    # LTV estimate: avg 3 packs over lifetime
    ltv = PACK_PRICE_USD * 3
    ltv_cac_ratio = round(ltv / max(cac, 1), 2)

    return {
        "funnel_stages": stages,
        "paying_customers": paying,
        "first_purchase_revenue_usd": revenue,
        "repeat_buyers": repeat_buyers,
        "total_90d_revenue_usd": total_revenue,
        "total_marketing_spend_usd": total_spend,
        "cac_usd": round(cac, 2),
        "estimated_ltv_usd": ltv,
        "ltv_cac_ratio": ltv_cac_ratio,
        "conversion_rate_pct": round(paying / visitors * 100, 2),
    }


# ─── MAIN ───────────────────────────────────────────────────────────────────────

def run_all() -> dict[str, Any]:
    random.seed(42)

    print("\n" + "=" * 72)
    print("  ZERO SIMULATION MACHINE — 4 Mechanical Validations")
    print("=" * 72)

    # ── SIM 1: CREDIT ECONOMY ──
    print("\n[1/4] Running credit economy simulation...")
    eco = sim_credit_economy()

    rows = [
        ["Total credits spent",     f"{eco['total_credits_spent']:,}"],
        ["Total credits earned",    f"{eco['total_credits_earned']:,}"],
        ["Total credits purchased", f"{eco['total_credits_purchased']:,}"],
        ["Net credit flow",         f"{eco['net_credit_flow']:,}"],
        ["Operators who bought",    str(eco['operators_who_bought_more'])],
        ["Operators who churned",   str(eco['operators_who_churned'])],
        ["Revenue (USD)",           f"${eco['revenue_from_purchases_usd']:,}"],
        ["Operators at zero",       f"{eco['operators_at_zero_balance']} ({eco['pct_at_zero']}%)"],
        ["Avg days to 1st purchase", str(eco['avg_days_until_first_purchase'])],
    ]
    print(_fmt_table(["Metric", "Value"], rows, "SIMULATION 1: CREDIT ECONOMY (200 ops, 90 days)"))

    checks = eco["decision_checks"]
    print("\n  Decision checks:")
    for k, v in checks.items():
        status = "PASS" if v else "FAIL"
        print(f"    [{status}] {k}")

    # ── SIM 2: STREAK MECHANICS ──
    print("\n[2/4] Running streak mechanics simulation...")
    streaks = sim_streak_mechanics()

    rows = [
        ["Reached 7-day streak",   str(streaks['operators_reaching_7d'])],
        ["Reached 30-day streak",  str(streaks['operators_reaching_30d'])],
        ["Reached 90-day streak",  str(streaks['operators_reaching_90d'])],
        ["Total rewards paid",     f"{streaks['total_streak_rewards_paid']:,}"],
        ["Avg longest streak",     str(streaks['avg_longest_streak'])],
        ["Median longest streak",  str(streaks['median_longest_streak'])],
        ["Total streak breaks",    f"{streaks['total_streak_breaks']:,}"],
        ["Warning saves",          str(streaks['warning_saves'])],
    ]
    print(_fmt_table(["Metric", "Value"], rows, "SIMULATION 2: STREAK MECHANICS (200 ops, 90 days)"))

    print("\n  Streak distribution:")
    for bucket, count in streaks["distribution"].items():
        bar = "#" * (count // 2)
        print(f"    {bucket:>5}d: {count:3d} {bar}")

    # ── SIM 3: SCORE PROGRESSION ──
    print("\n[3/4] Running score progression simulation...")
    scores = sim_score_progression()

    rows = [
        ["Avg score day 30", str(scores['avg_score_day_30'])],
        ["Avg score day 60", str(scores['avg_score_day_60'])],
        ["Avg score day 90", str(scores['avg_score_day_90'])],
        ["Lagging dimensions", ", ".join(scores['lagging_dimensions'])],
    ]
    print(_fmt_table(["Metric", "Value"], rows, "SIMULATION 3: SCORE PROGRESSION (200 ops, 90 days)"))

    print("\n  Score milestones at day 90:")
    for milestone, count in scores["milestones"].items():
        print(f"    {milestone}: {count} operators")

    print("\n  Distribution at day 90:")
    for bucket, count in scores["distribution_day_90"].items():
        bar = "#" * (count // 2)
        print(f"    {bucket:>5}: {count:3d} {bar}")

    print("\n  Component averages:")
    for comp, avg in scores["component_averages"].items():
        print(f"    {comp:<14}: {avg}")

    print("\n  By skill level:")
    for level, data in scores["by_skill_level"].items():
        print(f"    {level:<8}: avg={data['avg']}  min={data['min']}  max={data['max']}")

    # ── SIM 4: CONVERSION FUNNEL ──
    print("\n[4/4] Running conversion funnel simulation...")
    funnel = sim_conversion_funnel()

    rows = []
    for stage, count in funnel["funnel_stages"].items():
        pct = round(count / 1000 * 100, 1)
        rows.append([stage, str(count), f"{pct}%"])
    print(_fmt_table(["Stage", "Count", "% of Visitors"], rows,
                     "SIMULATION 4: CONVERSION FUNNEL (1000 visitors, 90 days)"))

    summary_rows = [
        ["Paying customers",    str(funnel['paying_customers'])],
        ["1st purchase revenue", f"${funnel['first_purchase_revenue_usd']:,}"],
        ["Repeat buyers",       str(funnel['repeat_buyers'])],
        ["Total 90d revenue",   f"${funnel['total_90d_revenue_usd']:,}"],
        ["Marketing spend",     f"${funnel['total_marketing_spend_usd']:,}"],
        ["CAC",                 f"${funnel['cac_usd']}"],
        ["Est. LTV",            f"${funnel['estimated_ltv_usd']}"],
        ["LTV/CAC ratio",       str(funnel['ltv_cac_ratio'])],
        ["Conversion rate",     f"{funnel['conversion_rate_pct']}%"],
    ]
    print(_fmt_table(["Metric", "Value"], summary_rows, "FUNNEL ECONOMICS"))

    print("\n" + "=" * 72)
    print("  Simulation complete.")
    print("=" * 72 + "\n")

    # Compile all results
    all_results = {
        "credit_economy": {k: v for k, v in eco.items() if k != "daily_snapshots"},
        "credit_economy_daily": eco["daily_snapshots"],
        "streak_mechanics": streaks,
        "score_progression": scores,
        "conversion_funnel": funnel,
    }

    # Save to JSON
    out_path = Path(__file__).parent / "simulation_results.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"Results saved to {out_path}")

    return all_results


if __name__ == "__main__":
    run_all()

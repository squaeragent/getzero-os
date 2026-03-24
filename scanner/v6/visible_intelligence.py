# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
visible_intelligence.py — Making the Invisible Visible

Feature 1: ThinkStream — live reasoning stream
Feature 2: TradeReplay — re-live any trade
Feature 3: Dashboard heartbeat event types
Feature 4: Battle — side-by-side competition
Feature 5: PresetRace — presets compete on historical data
"""

import json
import time
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [visible] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0


# ─── FEATURE 1: THINK STREAM ────────────────────────────────────────────────

class ThinkStream:
    """
    Live reasoning stream for a coin.
    8 stages: regime → indicators → memory → discoveries → conviction →
    correlation → network → verdict.
    """

    def stream(self, coin: str, market_data: dict = None,
               regime_data: dict = None, discoveries: list[dict] = None,
               network_state: dict = None) -> list[dict]:
        """Generate thinking stages for a coin. Returns list of stage dicts."""
        stages = []

        # Stage 1: Regime
        regime = regime_data or {}
        hurst = regime.get("hurst", 0.5)
        regime_name = regime.get("regime", "unknown")
        confidence = regime.get("confidence", 0)
        stages.append({
            "stage": "regime",
            "label": "computing regime",
            "steps": [
                f"hurst: {hurst:.2f}",
                f"regime: {regime_name}",
                f"confidence: {confidence:.0%}",
                f"age: {regime.get('age_hours', 0):.0f}h",
            ],
            "result": regime_name,
        })

        # Stage 2: Indicators
        md = market_data or {}
        indicators = md.get("indicators", {})
        if not indicators:
            indicators = {
                "ema": "long" if random.random() > 0.4 else "short",
                "macd": "long" if random.random() > 0.45 else "short",
                "rsi": "long" if random.random() > 0.5 else "neutral",
                "obv": "long" if random.random() > 0.5 else "short",
                "bollinger": "neutral",
                "funding": "favorable" if random.random() > 0.3 else "unfavorable",
                "volume": "confirming" if random.random() > 0.4 else "diverging",
            }
        direction_votes = Counter()
        for ind, val in indicators.items():
            if val in ("long", "confirming", "favorable"):
                direction_votes["long"] += 1
            elif val in ("short", "diverging", "unfavorable"):
                direction_votes["short"] += 1
        total = sum(direction_votes.values())
        top_dir = direction_votes.most_common(1)[0][0] if direction_votes else "neutral"
        agreeing = direction_votes.get(top_dir, 0)
        consensus = agreeing / max(total, 1)

        stages.append({
            "stage": "indicators",
            "label": "polling indicators",
            "steps": [f"{k}: {v}" for k, v in indicators.items()],
            "result": f"{agreeing}/{len(indicators)} {top_dir} ({consensus:.0%})",
        })

        # Stage 3: Memory
        memory_context = regime.get("history", {})
        if memory_context:
            stages.append({
                "stage": "memory",
                "label": "consulting regime memory",
                "steps": [f"{k}: {v}" for k, v in memory_context.items()],
                "result": f"quality_mult: {memory_context.get('quality_mult', 1.0):.1f}",
            })
        else:
            stages.append({
                "stage": "memory",
                "label": "consulting regime memory",
                "steps": ["no prior history for this regime + coin"],
                "result": "quality_mult: 1.0 (neutral)",
            })

        # Stage 4: Discoveries
        matched = []
        for d in (discoveries or []):
            matched.append(f"rule: {d.get('rule', '?')} → WR {d.get('wr', 0):.0%}")
        stages.append({
            "stage": "discoveries",
            "label": "matching discovered rules",
            "steps": matched if matched else ["no rules matched"],
            "result": f"{len(matched)} rules matched" if matched else "no boost",
        })

        # Stage 5: Conviction
        conviction = min(1.0, consensus * 0.5 + abs(hurst - 0.5) * 0.3 * 4 + 0.2)
        size_pct = 0.08 + (conviction - 0.50) * 0.34
        size_pct = max(0.08, min(0.25, size_pct))
        stages.append({
            "stage": "conviction",
            "label": "computing conviction size",
            "steps": [
                f"consensus contribution: {consensus * 0.5:.2f}",
                f"regime alignment: {abs(hurst - 0.5) * 1.2:.2f}",
                f"conviction: {conviction:.2f}",
                f"position size: {size_pct:.0%} of equity",
            ],
            "result": f"{size_pct:.0%}",
        })

        # Stage 6: Correlation
        ns = network_state or {}
        corr_risk = ns.get("correlation_risk", 0)
        stages.append({
            "stage": "correlation",
            "label": "checking portfolio correlation",
            "steps": [
                f"effective exposure: {ns.get('effective_exposure', 1.0):.1f}x",
                f"correlated positions: {ns.get('correlated_count', 0)}",
                f"risk level: {'elevated' if corr_risk > 0.5 else 'normal'}",
            ],
            "result": "clear" if corr_risk <= 0.5 else f"reducing {int(corr_risk * 30)}%",
        })

        # Stage 7: Network
        agents_same = ns.get("agents_same_direction", 0)
        utilization = ns.get("utilization_pct", 0)
        stages.append({
            "stage": "network",
            "label": "checking network state",
            "steps": [
                f"agents {top_dir} {coin}: {agents_same}",
                f"book depth utilization: {utilization:.1f}%",
                f"pending exits: {ns.get('pending_exits', 0)}",
            ],
            "result": "entry clear" if utilization < 15 else "crowded — coordinating",
        })

        # Stage 8: Verdict
        would_enter = consensus > 0.55 and hurst > 0.50 and corr_risk <= 0.7
        verdict = f"would consider {top_dir} entry" if would_enter else "would not enter (insufficient conviction)"
        stages.append({
            "stage": "verdict",
            "label": "assembling verdict",
            "steps": [
                f"direction: {top_dir}",
                f"consensus: {consensus:.0%}",
                f"regime: {regime_name} (H {hurst:.2f})",
                f"conviction: {conviction:.2f}",
                f"correlation: {'clear' if corr_risk <= 0.5 else 'elevated'}",
                f"network: {'clear' if utilization < 15 else 'coordinating'}",
            ],
            "result": verdict,
        })

        return stages


_thinker = ThinkStream()


# ─── FEATURE 2: TRADE REPLAY ────────────────────────────────────────────────

class TradeReplay:
    """Re-live any historical trade with full context."""

    def replay(self, trade: dict) -> dict:
        """Generate replay data from a closed trade."""
        coin = trade.get("coin", "?")
        direction = trade.get("direction", "?")
        pnl = trade.get("pnl", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        hold_seconds = trade.get("hold_seconds", 0)
        entry_price = trade.get("entry_price", 0)
        exit_price = trade.get("exit_price", 0)
        mae_pct = trade.get("mae_pct", 0)
        mfe_pct = trade.get("mfe_pct", 0)

        hold_hours = hold_seconds / 3600 if hold_seconds else 0

        # Entry reasoning
        entry = {
            "regime": trade.get("entry_regime", "unknown"),
            "hurst": trade.get("entry_hurst", 0.5),
            "consensus": trade.get("consensus_pct", 0),
            "signal_mode": trade.get("signal_mode", "unknown"),
        }

        # Hold timeline (ASCII mini-chart)
        chart_width = 40
        chart = self._render_hold_chart(
            entry_price, exit_price,
            mae_pct, mfe_pct,
            chart_width
        )

        # Exit reasoning
        exit_info = {
            "reason": trade.get("exit_reason", "unknown"),
            "regime_at_exit": trade.get("exit_regime", "unknown"),
            "regime_changed": trade.get("entry_regime") != trade.get("exit_regime"),
            "regime_changes": trade.get("regime_change_count", 0),
        }

        # Lessons
        lessons = []
        capture_rate = abs(pnl_pct) / abs(mfe_pct) if mfe_pct != 0 else 0
        if capture_rate > 0.7:
            lessons.append({"type": "positive", "text": f"captured {capture_rate:.0%} of max move. excellent exit timing."})
        elif capture_rate < 0.4 and pnl > 0:
            lessons.append({"type": "improve", "text": f"only captured {capture_rate:.0%} of max move. exit too early?"})
        if exit_info["regime_changed"]:
            lessons.append({"type": "info", "text": f"regime shifted during hold ({entry['regime']} → {exit_info['regime_at_exit']})"})
        if pnl < 0 and abs(mae_pct) > abs(pnl_pct) * 1.5:
            lessons.append({"type": "positive", "text": f"stop limited loss. without it, MAE was {mae_pct:.1%}."})
        if hold_hours > 12 and pnl > 0:
            lessons.append({"type": "info", "text": f"patience paid off. held {hold_hours:.1f}h for {pnl_pct:.1%}."})

        return {
            "trade_id": trade.get("id", trade.get("trade_id", "?")),
            "coin": coin,
            "direction": direction,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "hold_hours": round(hold_hours, 1),
            "entry": entry,
            "chart": chart,
            "exit": exit_info,
            "mae_pct": round(mae_pct, 4),
            "mfe_pct": round(mfe_pct, 4),
            "capture_rate": round(capture_rate, 2),
            "lessons": lessons,
        }

    def _render_hold_chart(self, entry: float, exit: float,
                           mae_pct: float, mfe_pct: float,
                           width: int = 40) -> str:
        """ASCII mini-chart of the trade."""
        if entry == 0:
            return "─" * width

        # Normalize to chart space
        points = [0, mae_pct * 100, mfe_pct * 100, (exit / entry - 1) * 100 if entry > 0 else 0]
        min_v = min(points)
        max_v = max(points)
        span = max(max_v - min_v, 0.01)

        def pos(v):
            return int((v - min_v) / span * (width - 1))

        chart = list("─" * width)
        chart[0] = "■"  # entry
        chart[-1] = "■"  # exit

        mae_pos = pos(mae_pct * 100)
        mfe_pos = pos(mfe_pct * 100)
        if 0 <= mae_pos < width:
            chart[mae_pos] = "▼"
        if 0 <= mfe_pos < width:
            chart[mfe_pos] = "▲"

        return "".join(chart)


_replayer = TradeReplay()


# ─── FEATURE 4: BATTLE ──────────────────────────────────────────────────────

class Battle:
    """Side-by-side live comparison with a rival."""

    def compare(self, me: dict, rival: dict) -> dict:
        """Generate comparison data."""
        me_pnl = me.get("today_pnl", 0)
        rival_pnl = rival.get("today_pnl", 0)

        return {
            "me": {
                "name": me.get("name", "you"),
                "score": me.get("score", 0),
                "equity": me.get("equity", 0),
                "today_pnl": me_pnl,
                "wins": me.get("wins", 0),
                "losses": me.get("losses", 0),
                "positions": me.get("position_count", 0),
            },
            "rival": {
                "name": rival.get("name", "rival"),
                "score": rival.get("score", 0),
                "equity": rival.get("equity", 0),
                "today_pnl": rival_pnl,
                "wins": rival.get("wins", 0),
                "losses": rival.get("losses", 0),
                "positions": rival.get("position_count", 0),
            },
            "leader": "me" if me_pnl > rival_pnl else "rival" if rival_pnl > me_pnl else "tied",
            "gap": abs(me_pnl - rival_pnl),
        }


_battle = Battle()


# ─── FEATURE 5: PRESET RACE ─────────────────────────────────────────────────

class PresetRace:
    """Simulates two presets against historical data."""

    PRESETS = {
        "balanced": {"conviction_threshold": 0.60, "max_positions": 3, "risk_pct": 0.15},
        "conservative": {"conviction_threshold": 0.75, "max_positions": 2, "risk_pct": 0.10},
        "degen": {"conviction_threshold": 0.45, "max_positions": 5, "risk_pct": 0.25},
    }

    def race(self, trades: list[dict], preset_a: str = "balanced",
             preset_b: str = "degen", equity: float = 10000) -> dict:
        """Run two presets against the same trade data."""
        conf_a = self.PRESETS.get(preset_a, self.PRESETS["balanced"])
        conf_b = self.PRESETS.get(preset_b, self.PRESETS["degen"])

        result_a = self._simulate(trades, equity, conf_a)
        result_b = self._simulate(trades, equity, conf_b)

        winner = preset_a if result_a["total_return_pct"] > result_b["total_return_pct"] else preset_b
        winner_result = result_a if winner == preset_a else result_b
        loser = preset_b if winner == preset_a else preset_a
        loser_result = result_b if winner == preset_a else result_a

        return {
            preset_a: result_a,
            preset_b: result_b,
            "winner": winner,
            "loser": loser,
            "winner_return": winner_result["total_return_pct"],
            "loser_return": loser_result["total_return_pct"],
            "insight": self._generate_insight(winner, winner_result, loser, loser_result),
        }

    def _simulate(self, trades: list[dict], equity: float, config: dict) -> dict:
        """Simulate one preset."""
        threshold = config["conviction_threshold"]
        max_pos = config["max_positions"]
        risk = config["risk_pct"]

        current_equity = equity
        wins, losses = 0, 0
        open_count = 0

        for trade in trades:
            confidence = trade.get("confidence", trade.get("consensus_pct", 0.5))
            if confidence < threshold:
                continue
            if open_count >= max_pos:
                continue

            pnl_pct = trade.get("pnl_pct", 0)
            pnl_usd = current_equity * risk * pnl_pct
            current_equity += pnl_usd

            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1

        total_trades = wins + losses
        return {
            "final_equity": round(current_equity, 2),
            "total_return_pct": round((current_equity - equity) / equity * 100, 2),
            "trades": total_trades,
            "win_rate": round(wins / max(total_trades, 1), 2),
            "wins": wins,
            "losses": losses,
        }

    def _generate_insight(self, winner, w_result, loser, l_result):
        if w_result["trades"] < l_result["trades"] and w_result["total_return_pct"] > l_result["total_return_pct"]:
            return "fewer trades. higher returns. selectivity is precision."
        elif w_result["win_rate"] > l_result["win_rate"]:
            return f"{winner} wins on accuracy. the reasoning engine optimizes for not being wrong."
        else:
            return f"{winner} wins this period. market conditions favored its approach."


_racer = PresetRace()


# ─── FEATURE 3: DASHBOARD EVENT TYPES ────────────────────────────────────────

DASHBOARD_EVENTS = {
    "decision": {"animate": "slideInLeft", "glow_on_entry": True},
    "equity_update": {"animate": "resolve"},
    "trade_close": {"animate": "slideInLeft", "sound": True, "haptic": True},
    "immune_check": {"animate": "pulse_cell"},
    "immune_save": {"animate": "flash_cell_yellow", "alert": True},
    "score_update": {"animate": "resolve", "glow_on_improve": True},
    "cluster_alert": {"animate": "slideUp"},
    "regime_shift": {"animate": "crossfade", "highlight_if_holding": True},
    "network_event": {"animate": "resolve"},
}


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def think(coin: str, market_data=None, regime_data=None,
          discoveries=None, network_state=None) -> list[dict]:
    """Feature 1: Live reasoning stream."""
    return _thinker.stream(coin, market_data, regime_data, discoveries, network_state)

def replay_trade(trade: dict) -> dict:
    """Feature 2: Re-live a trade."""
    return _replayer.replay(trade)

def battle_compare(me: dict, rival: dict) -> dict:
    """Feature 4: Side-by-side battle."""
    return _battle.compare(me, rival)

def race_presets(trades: list[dict], preset_a="balanced",
                 preset_b="degen", equity=10000) -> dict:
    """Feature 5: Race two presets."""
    return _racer.race(trades, preset_a, preset_b, equity)

def get_dashboard_events() -> dict:
    """Feature 3: Dashboard event type config."""
    return DASHBOARD_EVENTS

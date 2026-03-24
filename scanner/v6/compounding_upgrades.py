# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
compounding_upgrades.py — Upgrades 6-11 for the compounding layer.

6. PredictiveImmune — act BEFORE damage (funding spike, vol expansion, corr shift, liquidity drain, cascade)
7. SkillDecomposition — actionable score insights
8. StrategyMarketplace — config sharing between operators
9. RegimeAlertSystem — network-level regime shift early warning
10. ExitCoordinator — stagger crowded exits to reduce slippage
11. SyntheticBacktester — test weights against rare/unseen regimes
"""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [compounding] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0

def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return 0
    a, b = a[:n], b[:n]
    ma, mb = sum(a)/n, sum(b)/n
    cov = sum((a[i]-ma)*(b[i]-mb) for i in range(n)) / n
    sa = math.sqrt(sum((x-ma)**2 for x in a) / n)
    sb = math.sqrt(sum((x-mb)**2 for x in b) / n)
    return cov / (sa * sb) if sa > 0 and sb > 0 else 0

# ─── UPGRADE 6: PREDICTIVE IMMUNE ────────────────────────────────────────────

class PredictiveImmune:
    """Monitors leading indicators of danger. Acts BEFORE stops are needed."""

    def scan(self, positions: list[dict], market: dict) -> list[dict]:
        """Returns list of ImmuneAction dicts."""
        actions = []
        actions.extend(self._check_funding_spike(positions, market))
        actions.extend(self._check_volatility_expansion(positions, market))
        actions.extend(self._check_correlation_shift(positions, market))
        actions.extend(self._check_liquidity_drain(positions, market))
        actions.extend(self._check_cascade(positions, market))
        return actions

    def _check_funding_spike(self, positions, market):
        actions = []
        for pos in positions:
            coin = pos.get("coin", "")
            coin_data = market.get(coin, {})
            current = coin_data.get("funding_rate", 0)
            history = coin_data.get("funding_history", [])
            if len(history) < 3:
                continue
            prev = history[-3] if len(history) >= 3 else current
            if abs(prev) < 1e-8:
                continue
            roc = abs(current - prev) / abs(prev)

            direction = pos.get("direction", "LONG").upper()
            paying = (direction == "LONG" and current > 0) or (direction == "SHORT" and current < 0)

            if roc > 2.0 and abs(current) > 0.0005 and paying:
                if abs(current) > 0.002:
                    actions.append({"type": "emergency_close", "coin": coin,
                        "reason": f"funding spike: {current:.4%}. closing.", "severity": "critical"})
                elif abs(current) > 0.001:
                    actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.60,
                        "reason": f"funding spiking: {current:.4%}. tightening 40%.", "severity": "warning"})
                elif abs(current) > 0.0005:
                    actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.80,
                        "reason": f"funding elevated: {current:.4%}. tightening 20%.", "severity": "info"})
        return actions

    def _check_volatility_expansion(self, positions, market):
        actions = []
        for pos in positions:
            coin = pos.get("coin", "")
            coin_data = market.get(coin, {})
            current_atr = coin_data.get("atr", 0)
            atr_history = coin_data.get("atr_history", [])
            if not atr_history or len(atr_history) < 20:
                continue
            old_atr = atr_history[-20] if len(atr_history) >= 20 else atr_history[0]
            if old_atr <= 0:
                continue
            expansion = current_atr / old_atr
            if expansion > 2.0:
                actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.70,
                    "reason": f"volatility expanding {expansion:.1f}x. tightening 30%.", "severity": "warning"})
            elif expansion > 1.5:
                actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.85,
                    "reason": f"volatility rising {expansion:.1f}x. tightening 15%.", "severity": "info"})
        return actions

    def _check_correlation_shift(self, positions, market):
        actions = []
        if len(positions) < 2:
            return actions
        corr_matrix = market.get("correlation_matrix", {})
        for i in range(len(positions)):
            for j in range(i+1, len(positions)):
                a, b = positions[i], positions[j]
                ca, cb = a.get("coin",""), b.get("coin","")
                pair = tuple(sorted([ca, cb]))
                baseline = 0
                for k, v in corr_matrix.items():
                    if tuple(sorted(k if isinstance(k, (list, tuple)) else k.split(":"))) == pair:
                        baseline = v
                        break
                closes_a = market.get(ca, {}).get("closes_2h", [])
                closes_b = market.get(cb, {}).get("closes_2h", [])
                if len(closes_a) < 5 or len(closes_b) < 5:
                    continue
                recent = _pearson(closes_a, closes_b)
                if recent - baseline > 0.3 and recent > 0.8:
                    da, db = a.get("direction",""), b.get("direction","")
                    if da == db:
                        smaller = ca if a.get("size_usd",0) <= b.get("size_usd",0) else cb
                        actions.append({"type": "reduce_size", "coin": smaller, "adjustment": 0.5,
                            "reason": f"{ca}/{cb} corr spiked {baseline:.2f}→{recent:.2f}. halving {smaller}.",
                            "severity": "warning"})
        return actions

    def _check_liquidity_drain(self, positions, market):
        actions = []
        for pos in positions:
            coin = pos.get("coin", "")
            depth = market.get(coin, {}).get("book_depth_1pct", 0)
            size = pos.get("size_usd", 0)
            if depth <= 0:
                continue
            ratio = size / depth
            if ratio > 0.20:
                actions.append({"type": "emergency_close", "coin": coin,
                    "reason": f"liquidity drain. depth ${depth:,.0f}, position ${size:,.0f}.", "severity": "critical"})
            elif ratio > 0.10:
                actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.70,
                    "reason": f"{coin} book thinning. tightening stop.", "severity": "warning"})
        return actions

    def _check_cascade(self, positions, market):
        actions = []
        network_exits = market.get("network_exit_velocity", {})
        for pos in positions:
            coin = pos.get("coin", "")
            direction = pos.get("direction", "LONG")
            key = f"{coin}:{direction}"
            exits_10min = network_exits.get(key, 0)
            if exits_10min >= 10:
                actions.append({"type": "emergency_close", "coin": coin,
                    "reason": f"cascade: {exits_10min} agents exiting {coin} {direction}.", "severity": "critical"})
            elif exits_10min >= 5:
                actions.append({"type": "tighten_stop", "coin": coin, "adjustment": 0.50,
                    "reason": f"{exits_10min} agents exited {coin} {direction} in 10min.", "severity": "warning"})
        return actions


_predictive_immune = PredictiveImmune()


# ─── UPGRADE 7: SKILL DECOMPOSITION ──────────────────────────────────────────

class SkillDecomposition:
    """For each weak score component, identify the ONE behavior to change."""

    def decompose(self, score_components: dict, trades: list[dict]) -> list[dict]:
        if not score_components or not trades:
            return []
        insights = []
        sorted_components = sorted(score_components.items(), key=lambda x: x[1])
        for comp_name, comp_score in sorted_components[:2]:
            insight = getattr(self, f"_analyze_{comp_name}", self._generic)(comp_name, comp_score, trades)
            if insight:
                insights.append(insight)
        return insights

    def _analyze_resilience(self, name, score, trades):
        transition = [t for t in trades if t.get("regime_changes_during_hold", 0) > 0]
        normal = [t for t in trades if t.get("regime_changes_during_hold", 0) == 0]
        if transition and normal:
            t_hold = _mean([t.get("hold_hours", 0) for t in transition])
            n_hold = _mean([t.get("hold_hours", 0) for t in normal])
            if t_hold > n_hold * 1.5 and n_hold > 0:
                return {"component": "resilience", "score": score,
                    "finding": f"hold {t_hold:.1f}h during regime transitions vs {n_hold:.1f}h normally. too long when conditions change.",
                    "action": "regime exit engine catches transitions faster.", "impact": "high"}
            t_wr = _mean([1 if t.get("pnl_pct",0)>0 else 0 for t in transition])
            n_wr = _mean([1 if t.get("pnl_pct",0)>0 else 0 for t in normal])
            if t_wr < n_wr * 0.5:
                return {"component": "resilience", "score": score,
                    "finding": f"WR drops from {n_wr:.0%} to {t_wr:.0%} during transitions.",
                    "action": "avoid entries late in regime periods. regime memory detects aging.", "impact": "high"}
        return {"component": "resilience", "score": score, "finding": "no specific issue.", "impact": "low"}

    def _analyze_performance(self, name, score, trades):
        wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
        if wins:
            captures = [t.get("capture_rate", 0) for t in wins if t.get("capture_rate")]
            if captures and _mean(captures) < 0.70:
                avg_c = _mean(captures)
                return {"component": "performance", "score": score,
                    "finding": f"capturing {avg_c:.0%} of available moves.",
                    "action": "exit intelligence improves capture rate.", "impact": "high"}
        losses = [t for t in trades if t.get("pnl_pct", 0) < 0]
        if losses:
            stop_outs = [t for t in losses if t.get("exit_reason") == "stop_loss"]
            if len(stop_outs) / max(len(losses), 1) > 0.7:
                return {"component": "performance", "score": score,
                    "finding": f"{len(stop_outs)}/{len(losses)} losses are stop-outs. stops may be too tight.",
                    "action": "conviction sizing widens stops on high-conviction entries.", "impact": "medium"}
        return {"component": "performance", "score": score, "finding": "no specific issue.", "impact": "low"}

    def _analyze_consistency(self, name, score, trades):
        if len(trades) < 14:
            return {"component": "consistency", "score": score, "finding": "not enough trades.", "impact": "low"}
        weekly = defaultdict(float)
        for t in trades:
            ts = t.get("exit_time") or t.get("entry_time", "")
            if ts:
                try:
                    week = datetime.fromisoformat(ts.replace("Z", "+00:00")).isocalendar()[1]
                    weekly[week] += t.get("pnl_pct", 0)
                except Exception:
                    pass
        if weekly:
            vals = list(weekly.values())
            neg = [v for v in vals if v < 0]
            if len(neg) / max(len(vals), 1) > 0.4:
                return {"component": "consistency", "score": score,
                    "finding": f"{len(neg)}/{len(vals)} weeks negative.",
                    "action": "regime memory reduces entries during historically weak periods.", "impact": "high"}
        return {"component": "consistency", "score": score, "finding": "acceptable.", "impact": "low"}

    def _analyze_discipline(self, name, score, trades):
        return {"component": "discipline", "score": score, "finding": "no specific issue.", "impact": "low"}

    def _analyze_immune(self, name, score, trades):
        return {"component": "immune", "score": score, "finding": "no specific issue.", "impact": "low"}

    def _generic(self, name, score, trades):
        return {"component": name, "score": score, "finding": "no specific issue.", "impact": "low"}


_skill_decomp = SkillDecomposition()


# ─── UPGRADE 8: STRATEGY MARKETPLACE ─────────────────────────────────────────

# Config parameter bounds (safety)
CONFIG_BOUNDS = {
    "conviction_min": (0.60, 0.95),
    "max_positions": (1, 5),
    "hold_time_max_hours": (1, 48),
    "min_size_pct": (0.05, 0.15),
    "max_size_pct": (0.15, 0.30),
}

REGIME_OPTIONS = {"strong_trend", "moderate_trend", "stable", "mean_revert", "chaotic"}

def validate_config(config: dict) -> tuple[bool, str]:
    """Validate a shared config. Immune system and gates are NOT configurable."""
    for key, (lo, hi) in CONFIG_BOUNDS.items():
        val = config.get(key)
        if val is not None and not (lo <= val <= hi):
            return False, f"{key}={val} out of bounds [{lo}, {hi}]"
    regimes = config.get("regime_filter", [])
    if regimes:
        invalid = set(regimes) - REGIME_OPTIONS
        if invalid:
            return False, f"invalid regimes: {invalid}"
    if config.get("immune_disabled"):
        return False, "cannot disable immune system"
    if config.get("min_stop_atr", 1.0) < 1.0:
        return False, "min stop cannot be below 1x ATR"
    return True, "valid"


def publish_config(name: str, base_preset: str, modifications: dict,
                   author: str, author_score: float) -> dict:
    """Publish a config to the marketplace."""
    valid, reason = validate_config(modifications)
    if not valid:
        return {"error": reason}
    config_dir = Path.home() / ".zeroos" / "state" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "base": base_preset,
        "modifications": modifications,
        "author": author,
        "author_score": author_score,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "installs": 0,
    }
    (config_dir / f"{name}.json").write_text(json.dumps(config, indent=2))
    return config


def install_config(name: str) -> dict:
    """Install a published config."""
    config_dir = Path.home() / ".zeroos" / "state" / "configs"
    fpath = config_dir / f"{name}.json"
    if not fpath.exists():
        return {"error": f"config {name} not found"}
    config = json.loads(fpath.read_text())
    config["installs"] = config.get("installs", 0) + 1
    fpath.write_text(json.dumps(config, indent=2))
    return config


# ─── UPGRADE 9: REGIME ALERT SYSTEM ──────────────────────────────────────────

class RegimeAlertSystem:
    """Aggregates regime detections. When threshold agents detect same shift, alert."""

    def __init__(self):
        self._shift_counts: dict[str, list[dict]] = {}
        self._alerts: list[dict] = []
        self._state_file = Path.home() / ".zeroos" / "state" / "regime_alerts.json"

    def report_regime(self, coin: str, regime: str, prev_regime: str,
                      agent_id: str = "local") -> dict | None:
        """Called when an agent detects a regime. Returns alert if threshold hit."""
        if regime == prev_regime:
            return None

        key = f"{coin}:{prev_regime}:{regime}"
        now = time.time()

        if key not in self._shift_counts:
            self._shift_counts[key] = []

        # Clean old reports (>10 min)
        self._shift_counts[key] = [r for r in self._shift_counts[key] if now - r["ts"] < 600]
        self._shift_counts[key].append({"agent": agent_id, "ts": now})

        count = len(set(r["agent"] for r in self._shift_counts[key]))

        if count >= 5:
            alert = {
                "coin": coin,
                "from_regime": prev_regime,
                "to_regime": regime,
                "agents_detecting": count,
                "confidence": min(count / 10, 1.0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._alerts.append(alert)
            self._save()
            _log(f"REGIME ALERT: {coin} {prev_regime}→{regime} ({count} agents)")
            return alert
        return None

    def get_recent_alerts(self, minutes: int = 10) -> list[dict]:
        """Get alerts from last N minutes."""
        cutoff = time.time() - minutes * 60
        return [a for a in self._alerts
                if datetime.fromisoformat(a["timestamp"]).timestamp() > cutoff]

    def react_to_alerts(self, positions: list[dict]) -> list[dict]:
        """Check alerts against held positions. Returns actions."""
        actions = []
        alerts = self.get_recent_alerts(10)
        for alert in alerts:
            for pos in positions:
                if pos.get("coin") == alert["coin"]:
                    if alert["confidence"] > 0.8:
                        actions.append({"type": "tighten_stop", "coin": pos["coin"],
                            "adjustment": 0.70,
                            "reason": f"network: {alert['agents_detecting']} agents see "
                                      f"{alert['coin']} {alert['from_regime']}→{alert['to_regime']}.",
                            "severity": "warning"})
                    elif alert["confidence"] > 0.5:
                        actions.append({"type": "flag", "coin": pos["coin"],
                            "reason": f"network alert: {alert['coin']} regime shift ({alert['confidence']:.0%}).",
                            "severity": "info"})
        return actions

    def _save(self):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        recent = self._alerts[-100:]
        self._state_file.write_text(json.dumps(recent, indent=2, default=str))


_regime_alerts = RegimeAlertSystem()


# ─── UPGRADE 10: EXIT COORDINATOR ────────────────────────────────────────────

class ExitCoordinator:
    """Prevents network self-harm from crowded exits. Staggers by size."""

    def __init__(self):
        self._queue: dict[str, list[dict]] = {}

    def request_exit(self, agent_id: str, coin: str, direction: str,
                     size_usd: float, urgency: str = "normal") -> dict:
        """
        urgency: critical (immediate), normal (may stagger), low (can wait)
        Returns: {approved, delay_seconds, message}
        """
        if urgency == "critical":
            return {"approved": True, "delay_seconds": 0, "message": "critical: immediate exit"}

        key = f"{coin}:{direction}"
        now = time.time()

        if key not in self._queue:
            self._queue[key] = []

        # Clean stale entries (>2 min)
        self._queue[key] = [e for e in self._queue[key] if now - e["ts"] < 120]

        pending = len(self._queue[key])

        if pending < 3:
            return {"approved": True, "delay_seconds": 0, "message": "no congestion"}

        # Register
        self._queue[key].append({"agent": agent_id, "size": size_usd, "ts": now})

        # Sort by size ascending (smallest first)
        sorted_q = sorted(self._queue[key], key=lambda x: x["size"])
        position = next((i for i, e in enumerate(sorted_q) if e["agent"] == agent_id), 0)

        delay = min(position * 15, 120)

        return {
            "approved": True,
            "delay_seconds": delay,
            "queue_position": position + 1,
            "queue_total": len(sorted_q),
            "message": f"{len(sorted_q)} agents exiting {coin} {direction}. "
                       f"#{position+1} in queue. delay: {delay}s.",
        }


_exit_coordinator = ExitCoordinator()


# ─── UPGRADE 11: SYNTHETIC BACKTESTER ────────────────────────────────────────

class SyntheticBacktester:
    """Tests current weights against rare/unseen regime conditions."""

    def find_rare_regimes(self, regime_memory_dir: Path,
                          recent_days: int = 90) -> list[dict]:
        """Find regime+coin combos not seen in recent_days."""
        if not regime_memory_dir.exists():
            return []

        all_combos: dict[str, float] = {}
        for fpath in regime_memory_dir.glob("*.json"):
            coin = fpath.stem.upper()
            try:
                periods = json.loads(fpath.read_text())
            except Exception:
                continue
            for p in periods:
                regime = p.get("regime", "")
                recorded = p.get("recorded_at", "")
                if recorded:
                    try:
                        ts = datetime.fromisoformat(recorded).timestamp()
                        key = f"{coin}:{regime}"
                        all_combos[key] = max(all_combos.get(key, 0), ts)
                    except Exception:
                        pass

        cutoff = time.time() - recent_days * 86400
        rare = []
        for key, last_ts in all_combos.items():
            if last_ts < cutoff:
                coin, regime = key.split(":", 1)
                days_ago = int((time.time() - last_ts) / 86400)
                rare.append({
                    "coin": coin,
                    "regime": regime,
                    "days_since": days_ago,
                    "last_seen": datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat(),
                })

        return sorted(rare, key=lambda x: -x["days_since"])

    def estimate_performance(self, coin: str, regime: str,
                             regime_memory_dir: Path) -> dict:
        """Estimate how current weights would perform in a rare regime."""
        fpath = regime_memory_dir / f"{coin.lower()}.json"
        if not fpath.exists():
            return {"coin": coin, "regime": regime, "status": "no_data"}
        try:
            periods = json.loads(fpath.read_text())
        except Exception:
            return {"coin": coin, "regime": regime, "status": "parse_error"}

        matching = [p for p in periods if p.get("regime") == regime]
        if not matching:
            return {"coin": coin, "regime": regime, "status": "no_matching_periods"}

        avg_pnl = _mean([p.get("total_pnl_pct", 0) for p in matching])
        avg_trades = _mean([p.get("trade_count", 0) for p in matching])
        total_wins = sum(p.get("wins", 0) for p in matching)
        total_trades = sum(p.get("trade_count", 0) for p in matching)
        wr = total_wins / max(total_trades, 1)

        warning = None
        if avg_pnl < -0.01:
            warning = (f"current weights LOSE money in {regime} for {coin}. "
                       f"avg P&L: {avg_pnl*100:.1f}%. if this regime returns, expect difficulty.")
        elif total_trades == 0:
            warning = (f"reasoning engine would NOT TRADE during {regime} for {coin}. "
                       f"all signals rejected.")

        return {
            "coin": coin, "regime": regime,
            "periods": len(matching),
            "avg_pnl_pct": round(avg_pnl * 100, 2),
            "avg_trades": round(avg_trades, 1),
            "win_rate": round(wr, 2),
            "warning": warning,
            "verdict": "ok" if avg_pnl >= 0 else "weak",
        }


_backtester = SyntheticBacktester()


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def predictive_immune_scan(positions: list[dict], market: dict) -> list[dict]:
    """Upgrade 6."""
    return _predictive_immune.scan(positions, market)

def decompose_score(score_components: dict, trades: list[dict]) -> list[dict]:
    """Upgrade 7."""
    return _skill_decomp.decompose(score_components, trades)

def publish_strategy(name, base, mods, author, score):
    """Upgrade 8."""
    return publish_config(name, base, mods, author, score)

def install_strategy(name):
    """Upgrade 8."""
    return install_config(name)

def report_regime_shift(coin, regime, prev, agent_id="local"):
    """Upgrade 9."""
    return _regime_alerts.report_regime(coin, regime, prev, agent_id)

def get_regime_alerts(minutes=10):
    """Upgrade 9."""
    return _regime_alerts.get_recent_alerts(minutes)

def react_to_regime_alerts(positions):
    """Upgrade 9."""
    return _regime_alerts.react_to_alerts(positions)

def coordinate_exit(agent_id, coin, direction, size_usd, urgency="normal"):
    """Upgrade 10."""
    return _exit_coordinator.request_exit(agent_id, coin, direction, size_usd, urgency)

def find_rare_regimes(recent_days=90):
    """Upgrade 11."""
    mem_dir = Path.home() / ".zeroos" / "state" / "regime_memory"
    return _backtester.find_rare_regimes(mem_dir, recent_days)

def backtest_rare(coin, regime):
    """Upgrade 11."""
    mem_dir = Path.home() / ".zeroos" / "state" / "regime_memory"
    return _backtester.estimate_performance(coin, regime, mem_dir)

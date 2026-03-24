# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
category_changers.py — Upgrades 12-14 that change what zero IS.

12. MarketObserver — agent as analyst (observations, not just trades)
13. ClusterEngine — agents that trade similarly form automatic teams
14. StrategyDiscovery — machine finds rules nobody programmed
"""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [category] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0


# ─── UPGRADE 12: MARKET OBSERVER ─────────────────────────────────────────────

class MarketObserver:
    """Runs alongside reasoning engine. Makes OBSERVATIONS, not trades."""

    def observe(self, market_data: dict, regime_data: dict) -> list[dict]:
        """Return top 3 notable observations."""
        findings = []
        findings.extend(self._regime_alignment(regime_data))
        findings.extend(self._funding_anomaly(market_data))
        findings.extend(self._regime_transition(regime_data))
        findings.extend(self._volume_divergence(market_data))
        findings.extend(self._cross_coin_pattern(regime_data))
        # Rank by significance, top 3
        findings.sort(key=lambda f: f.get("significance", 0), reverse=True)
        return findings[:3]

    def _regime_alignment(self, regime_data):
        """Detect when 4+ coins share same regime for unusual duration."""
        groups = defaultdict(list)
        for coin, data in regime_data.items():
            regime = data.get("current_regime", data.get("regime", ""))
            if regime:
                groups[regime].append({
                    "coin": coin,
                    "duration_hours": data.get("regime_duration_hours",
                                               data.get("duration_hours", 0)),
                })
        findings = []
        for regime, coins in groups.items():
            if len(coins) >= 4:
                avg_dur = _mean([c["duration_hours"] for c in coins])
                coin_names = ", ".join(c["coin"] for c in coins[:4])
                if avg_dur > 48:  # unusual if > 48h aligned
                    findings.append({
                        "type": "regime_alignment",
                        "significance": 0.85,
                        "title": f"{len(coins)} coins aligned in {regime} for {avg_dur:.0f}h",
                        "detail": f"{coin_names} in {regime} together for {avg_dur:.0f}h. "
                                  f"when alignment breaks, expect divergence.",
                        "actionable": True,
                        "action_hint": "watch for the first coin to shift.",
                    })
        return findings

    def _funding_anomaly(self, market_data):
        """Detect when majority of coins have same-sign funding."""
        rates = {coin: d.get("funding_rate", 0) for coin, d in market_data.items()}
        if not rates:
            return []
        total = len(rates)
        neg = sum(1 for f in rates.values() if f < -0.0005)
        pos = sum(1 for f in rates.values() if f > 0.0005)
        if neg >= total * 0.75 and total >= 4:
            return [{"type": "funding_anomaly", "significance": 0.90,
                     "title": f"{neg}/{total} coins have negative funding",
                     "detail": "crowd is heavily short. imbalance historically resolves upward.",
                     "actionable": True, "action_hint": "long entries have funding tailwind."}]
        if pos >= total * 0.75 and total >= 4:
            return [{"type": "funding_anomaly", "significance": 0.88,
                     "title": f"{pos}/{total} coins have positive funding",
                     "detail": "crowd is heavily long. historically resolves with correction.",
                     "actionable": True, "action_hint": "short entries have funding tailwind."}]
        return []

    def _regime_transition(self, regime_data):
        """Predict upcoming regime transitions from hurst velocity."""
        findings = []
        for coin, data in regime_data.items():
            regime = data.get("current_regime", data.get("regime", ""))
            hurst = data.get("hurst", 0.5)
            hurst_vel = data.get("hurst_velocity", 0)
            if regime in ("trending", "strong_trend", "moderate_trend"):
                if hurst_vel < -0.008 and hurst < 0.58:
                    hours = (hurst - 0.55) / abs(hurst_vel) if abs(hurst_vel) > 0 else 999
                    if 2 < hours < 24:
                        findings.append({
                            "type": "transition_prediction", "significance": 0.82,
                            "title": f"{coin}: {regime} may end within {hours:.0f}h",
                            "detail": f"hurst declining at {hurst_vel:.3f}/h. "
                                      f"currently {hurst:.2f}, boundary 0.55.",
                            "actionable": True,
                            "action_hint": f"avoid new {coin} entries. tighten existing stops.",
                        })
            if regime in ("stable", "random_quiet"):
                if abs(hurst_vel) > 0.01 and (hurst > 0.53 or hurst < 0.47):
                    direction = "trending" if hurst > 0.53 else "reverting"
                    findings.append({
                        "type": "transition_prediction", "significance": 0.78,
                        "title": f"{coin}: may shift from {regime} to {direction}",
                        "detail": f"hurst moving {'up' if hurst > 0.5 else 'down'} "
                                  f"at {abs(hurst_vel):.3f}/h.",
                        "actionable": True,
                        "action_hint": f"watch for {coin} {direction} signals.",
                    })
        return findings

    def _volume_divergence(self, market_data):
        """Price vs volume divergence detection."""
        findings = []
        for coin, data in market_data.items():
            price_chg = data.get("price_change_24h_pct", 0)
            vol_chg = data.get("volume_change_24h_pct", 0)
            if price_chg > 3.0 and vol_chg < -30:
                findings.append({
                    "type": "volume_divergence", "significance": 0.75,
                    "title": f"{coin}: price +{price_chg:.1f}% but volume -{abs(vol_chg):.0f}%",
                    "detail": "rally lacks volume support. may reverse.",
                    "actionable": True,
                    "action_hint": f"be cautious with {coin} longs.",
                })
            if abs(price_chg) < 1.0 and vol_chg > 100:
                findings.append({
                    "type": "volume_divergence", "significance": 0.80,
                    "title": f"{coin}: volume +{vol_chg:.0f}% with flat price",
                    "detail": "accumulation or distribution. large move may follow.",
                    "actionable": True,
                    "action_hint": f"watch {coin} for breakout.",
                })
        return findings

    def _cross_coin_pattern(self, regime_data):
        """Detect when alt-coins are all in same regime (macro move)."""
        alts = {c: d for c, d in regime_data.items() if c not in ("BTC", "ETH")}
        if len(alts) < 3:
            return []
        regimes = [d.get("current_regime", d.get("regime", "")) for d in alts.values()]
        counts = Counter(regimes)
        if not counts:
            return []
        dominant, count = counts.most_common(1)[0]
        if count >= len(alts) * 0.8:
            return [{"type": "cross_coin_pattern", "significance": 0.85,
                     "title": f"{count}/{len(alts)} alts in {dominant}",
                     "detail": "alt market moving together. macro regime. "
                               "individual analysis less reliable.",
                     "actionable": True,
                     "action_hint": "treat alt positions as correlated."}]
        return []


_observer = MarketObserver()


# ─── UPGRADE 13: AGENT CLUSTERS ──────────────────────────────────────────────

class ClusterEngine:
    """Detects agents with similar behavior, groups them for fast alerts."""

    def __init__(self):
        self._clusters: list[dict] = []
        self._state_file = Path.home() / ".zeroos" / "state" / "clusters.json"
        self._load()

    def compute_similarity(self, a: dict, b: dict) -> float:
        """Behavior similarity: 0 = different, 1 = identical."""
        coins_a = set(a.get("coins", {}).keys())
        coins_b = set(b.get("coins", {}).keys())
        coin_sim = len(coins_a & coins_b) / max(len(coins_a | coins_b), 1)

        regimes_a = set(a.get("regimes", {}).keys())
        regimes_b = set(b.get("regimes", {}).keys())
        regime_sim = len(regimes_a & regimes_b) / max(len(regimes_a | regimes_b), 1)

        shared = coins_a & coins_b
        if shared:
            dir_a = a.get("direction_bias", {})
            dir_b = b.get("direction_bias", {})
            dir_sim = _mean([1 - abs(dir_a.get(c, 0) - dir_b.get(c, 0)) / 2 for c in shared])
        else:
            dir_sim = 0

        ha = a.get("avg_hold_hours", 4)
        hb = b.get("avg_hold_hours", 4)
        hold_sim = 1 - min(abs(ha - hb) / max(ha, hb, 1), 1)

        return coin_sim * 0.35 + regime_sim * 0.25 + dir_sim * 0.25 + hold_sim * 0.15

    def detect_clusters(self, agent_vectors: list[dict], threshold: float = 0.75) -> list[dict]:
        """Group agents with similarity > threshold."""
        clusters = []
        assigned = set()
        ids = [v.get("agent_id", str(i)) for i, v in enumerate(agent_vectors)]

        for i in range(len(agent_vectors)):
            if ids[i] in assigned:
                continue
            cluster_members = [ids[i]]
            assigned.add(ids[i])
            for j in range(i + 1, len(agent_vectors)):
                if ids[j] in assigned:
                    continue
                sim = self.compute_similarity(agent_vectors[i], agent_vectors[j])
                if sim >= threshold:
                    cluster_members.append(ids[j])
                    assigned.add(ids[j])
            if len(cluster_members) >= 2:
                v = agent_vectors[i]
                top_coin = max(v.get("coins", {"?": 1}).items(), key=lambda x: x[1])[0]
                top_regime = max(v.get("regimes", {"?": 1}).items(), key=lambda x: x[1])[0]
                clusters.append({
                    "name": f"{top_coin}_{top_regime}",
                    "members": cluster_members,
                    "size": len(cluster_members),
                    "dominant_coin": top_coin,
                    "dominant_regime": top_regime,
                })
        self._clusters = clusters
        self._save()
        return clusters

    def get_clusters_for_agent(self, agent_id: str) -> list[dict]:
        return [c for c in self._clusters if agent_id in c.get("members", [])]

    def _load(self):
        if self._state_file.exists():
            try:
                self._clusters = json.loads(self._state_file.read_text())
            except Exception:
                self._clusters = []

    def _save(self):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._clusters, indent=2, default=str))


_cluster_engine = ClusterEngine()


# ─── UPGRADE 14: STRATEGY DISCOVERY ──────────────────────────────────────────

class StrategyDiscovery:
    """Mines collective trade data for rules that predict profitability."""

    # Conditions the engine can test
    CONDITIONS = [
        ("regime", "eq", "trending"),
        ("regime", "eq", "reverting"),
        ("regime", "eq", "stable"),
        ("regime", "eq", "chaotic"),
        ("hurst", "gt", 0.60),
        ("hurst", "gt", 0.65),
        ("hurst", "lt", 0.45),
        ("consensus", "gt", 0.70),
        ("consensus", "gt", 0.80),
        ("consensus", "gt", 0.90),
        ("funding_sign", "eq", "negative"),
        ("funding_sign", "eq", "positive"),
        ("abs_funding", "gt", 0.05),
        ("regime_hours", "gt", 48),
        ("regime_hours", "lt", 12),
        ("regime_hours", "gt", 96),
        ("direction", "eq", "long"),
        ("direction", "eq", "short"),
        ("h_dfa_aligned", "eq", True),
        ("h_dfa_divergent", "eq", True),
    ]

    def discover(self, trades: list[dict], min_sample: int = 30) -> list[dict]:
        """Find 3-condition rules with significantly better/worse win rate."""
        if len(trades) < 100:
            return []

        baseline_wr = _mean([1 if t.get("pnl_pct", 0) > 0 else 0 for t in trades])
        rules = []

        # Test all 3-condition combinations
        from itertools import combinations
        for combo in combinations(range(len(self.CONDITIONS)), 3):
            conds = [self.CONDITIONS[i] for i in combo]
            matching = [t for t in trades if self._matches(t, conds)]

            if len(matching) < min_sample:
                continue

            wr = _mean([1 if t.get("pnl_pct", 0) > 0 else 0 for t in matching])
            avg_pnl = _mean([t.get("pnl_pct", 0) for t in matching])

            # Significant improvement or degradation
            improvement = wr - baseline_wr
            if abs(improvement) > 0.12:
                desc = self._describe(conds, wr, avg_pnl)
                rules.append({
                    "conditions": [{"field": c[0], "op": c[1], "value": c[2]} for c in conds],
                    "sample_size": len(matching),
                    "win_rate": round(wr, 3),
                    "avg_pnl_pct": round(avg_pnl * 100, 2),
                    "baseline_wr": round(baseline_wr, 3),
                    "improvement": round(improvement, 3),
                    "direction": "positive" if improvement > 0 else "negative",
                    "description": desc,
                })

        rules.sort(key=lambda r: abs(r["improvement"]) * math.log(max(r["sample_size"], 2)),
                   reverse=True)
        return rules[:10]

    def _matches(self, trade: dict, conditions: list[tuple]) -> bool:
        for field, op, value in conditions:
            t_val = self._get_field(trade, field)
            if t_val is None:
                return False
            if op == "eq" and t_val != value:
                return False
            if op == "gt" and t_val <= value:
                return False
            if op == "lt" and t_val >= value:
                return False
        return True

    def _get_field(self, trade: dict, field: str):
        if field == "regime":
            return trade.get("entry_regime", trade.get("regime"))
        if field == "hurst":
            return trade.get("hurst")
        if field == "consensus":
            return trade.get("confidence", trade.get("consensus"))
        if field == "funding_sign":
            f = trade.get("funding_rate", 0)
            return "negative" if f < -0.0005 else "positive" if f > 0.0005 else "neutral"
        if field == "abs_funding":
            return abs(trade.get("funding_rate", 0))
        if field == "regime_hours":
            return trade.get("regime_duration_hours", trade.get("hold_hours"))
        if field == "direction":
            return trade.get("direction", "").lower()
        if field == "h_dfa_aligned":
            h = trade.get("hurst", 0.5)
            d = trade.get("dfa", 0.5)
            return abs((h - 0.5) - (d - 0.5)) < 0.05
        if field == "h_dfa_divergent":
            h = trade.get("hurst", 0.5)
            d = trade.get("dfa", 0.5)
            return abs((h - 0.5) - (d - 0.5)) > 0.15
        return trade.get(field)

    def _describe(self, conditions, wr, avg_pnl):
        parts = []
        for field, op, value in conditions:
            if field == "regime":
                parts.append(f"regime is {value}")
            elif field == "hurst" and op == "gt":
                parts.append(f"hurst > {value}")
            elif field == "hurst" and op == "lt":
                parts.append(f"hurst < {value}")
            elif field == "consensus" and op == "gt":
                parts.append(f"consensus > {value:.0%}")
            elif field == "funding_sign":
                parts.append(f"funding is {value}")
            elif field == "regime_hours" and op == "lt":
                parts.append(f"regime fresh (<{value}h)")
            elif field == "regime_hours" and op == "gt":
                parts.append(f"regime aged ({value}+h)")
            elif field == "direction":
                parts.append(f"direction is {value}")
            elif field == "h_dfa_aligned":
                parts.append("H-DFA aligned")
            elif field == "h_dfa_divergent":
                parts.append("H-DFA divergent")
            else:
                parts.append(f"{field} {op} {value}")
        return f"when {' AND '.join(parts)}: WR {wr:.0%}, avg {avg_pnl*100:+.1f}%"

    def evaluate_signal(self, signal: dict, rules: list[dict]) -> dict | None:
        """Check if any discovered rule applies to a pending signal."""
        for rule in rules:
            conds = [(c["field"], c["op"], c["value"]) for c in rule["conditions"]]
            if self._matches(signal, conds):
                if rule["direction"] == "negative":
                    return {"action": "reject", "rule": rule,
                            "message": f"discovered pattern: {rule['description']}. historically loses."}
                else:
                    return {"action": "boost", "conviction_bonus": 0.10, "rule": rule,
                            "message": f"discovered pattern: {rule['description']}. historically profitable."}
        return None


_discovery = StrategyDiscovery()


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def observe_market(market_data: dict, regime_data: dict) -> list[dict]:
    """Upgrade 12: agent as analyst."""
    return _observer.observe(market_data, regime_data)

def detect_clusters(agent_vectors: list[dict], threshold: float = 0.75) -> list[dict]:
    """Upgrade 13: agent clusters."""
    return _cluster_engine.detect_clusters(agent_vectors, threshold)

def get_agent_clusters(agent_id: str) -> list[dict]:
    """Upgrade 13: get clusters for an agent."""
    return _cluster_engine.get_clusters_for_agent(agent_id)

def compute_agent_similarity(a: dict, b: dict) -> float:
    """Upgrade 13: behavior similarity."""
    return _cluster_engine.compute_similarity(a, b)

def discover_strategies(trades: list[dict], min_sample: int = 30) -> list[dict]:
    """Upgrade 14: strategy discovery."""
    return _discovery.discover(trades, min_sample)

def evaluate_with_discoveries(signal: dict, rules: list[dict]) -> dict | None:
    """Upgrade 14: check signal against discovered rules."""
    return _discovery.evaluate_signal(signal, rules)

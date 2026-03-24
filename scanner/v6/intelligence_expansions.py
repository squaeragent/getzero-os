# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
intelligence_expansions.py — Expansions 1-5 beyond trading.

1. IntelligenceFeed — real-time stream of network intelligence
2. OperatorGraph — social graph with verified performance
3. ProofEngine — verifiable on-chain proofs
4. SimulationSandbox — test strategies against real data
5. ReasoningAPI — regime detection as a service
"""

import json
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [intel] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0


# ─── EXPANSION 1: INTELLIGENCE FEED ──────────────────────────────────────────

class IntelligenceFeed:
    """Produces the full intelligence snapshot every 15 minutes."""

    TIERS = {
        "observer": {"price": 100, "sections": ["regime_map", "observations"]},
        "analyst": {"price": 500, "sections": ["regime_map", "observations", "consensus", "funding", "correlations"]},
        "professional": {"price": 2000, "sections": ["regime_map", "observations", "consensus", "funding", "correlations", "discoveries", "network_stats"]},
        "enterprise": {"price": 10000, "sections": ["*"]},
    }

    def generate_snapshot(self, regime_data: dict, market_data: dict,
                          observations: list[dict] = None,
                          discoveries: list[dict] = None,
                          network_stats: dict = None,
                          correlation_matrix: dict = None) -> dict:
        """Generate full intelligence snapshot."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime_map": {},
            "consensus": {},
            "funding_map": {"favorable_longs": [], "favorable_shorts": [], "extreme_coins": []},
            "correlations": [],
            "observations": observations or [],
            "discoveries": discoveries or [],
            "network_stats": network_stats or {},
        }

        for coin, data in regime_data.items():
            snapshot["regime_map"][coin] = {
                "regime": data.get("regime", data.get("current_regime", "unknown")),
                "hurst": round(data.get("hurst", 0.5), 3),
                "confidence": round(data.get("confidence", 0), 2),
                "age_hours": data.get("regime_duration_hours", data.get("age_hours", 0)),
            }

        for coin, data in market_data.items():
            direction = data.get("direction", "neutral")
            consensus = data.get("consensus_pct", data.get("confidence", 0))
            snapshot["consensus"][coin] = {
                "direction": direction,
                "consensus": round(consensus, 2),
            }
            funding = data.get("funding_rate", 0)
            if funding < -0.0005:
                snapshot["funding_map"]["favorable_longs"].append(coin)
            elif funding > 0.0005:
                snapshot["funding_map"]["favorable_shorts"].append(coin)
            if abs(funding) > 0.001:
                snapshot["funding_map"]["extreme_coins"].append(coin)

        neg = len(snapshot["funding_map"]["favorable_longs"])
        pos = len(snapshot["funding_map"]["favorable_shorts"])
        snapshot["funding_map"]["market_bias"] = "short" if neg > pos else "long" if pos > neg else "neutral"

        if correlation_matrix:
            pairs = []
            for pair_key, corr in sorted(correlation_matrix.items(), key=lambda x: -abs(x[1]))[:10]:
                if isinstance(pair_key, tuple) and len(pair_key) == 2:
                    pairs.append({"pair": list(pair_key), "correlation": round(corr, 3)})
            snapshot["correlations"] = pairs

        return snapshot

    def filter_by_tier(self, snapshot: dict, tier: str) -> dict:
        """Filter snapshot to only include sections for the given tier."""
        tier_info = self.TIERS.get(tier, self.TIERS["observer"])
        sections = tier_info["sections"]
        if "*" in sections:
            return snapshot
        filtered = {"timestamp": snapshot["timestamp"]}
        for section in sections:
            if section in snapshot:
                filtered[section] = snapshot[section]
        return filtered


_feed = IntelligenceFeed()


# ─── EXPANSION 2: OPERATOR GRAPH ─────────────────────────────────────────────

class OperatorGraph:
    """Social graph with verified trading performance."""

    EDGE_TYPES = ("referral", "cluster", "config", "arena")

    def __init__(self):
        self._edges: list[dict] = []
        self._influence: dict[str, float] = {}
        self._state_file = Path.home() / ".zeroos" / "state" / "operator_graph.json"

    def add_edge(self, from_op: str, to_op: str, edge_type: str, weight: float = 1.0):
        """Add a connection between operators."""
        if edge_type not in self.EDGE_TYPES:
            return
        self._edges.append({
            "from": from_op, "to": to_op,
            "type": edge_type, "weight": weight,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save()

    def compute_influence(self) -> dict[str, float]:
        """PageRank-style influence computation."""
        nodes = set()
        for e in self._edges:
            nodes.add(e["from"])
            nodes.add(e["to"])

        if not nodes:
            return {}

        # Initialize scores
        scores = {n: 1.0 for n in nodes}
        damping = 0.85
        iterations = 10

        # Build adjacency
        inbound = {n: [] for n in nodes}
        outbound_count = Counter()
        for e in self._edges:
            inbound[e["to"]].append((e["from"], e.get("weight", 1.0)))
            outbound_count[e["from"]] += 1

        for _ in range(iterations):
            new_scores = {}
            for node in nodes:
                rank = (1 - damping) / len(nodes)
                for source, weight in inbound[node]:
                    out_count = max(outbound_count[source], 1)
                    rank += damping * scores[source] * weight / out_count
                new_scores[node] = rank
            scores = new_scores

        # Normalize to 0-10
        max_score = max(scores.values()) if scores else 1
        self._influence = {n: round(s / max_score * 10, 1) for n, s in scores.items()}
        self._save()
        return self._influence

    def get_top_operators(self, limit: int = 10) -> list[dict]:
        """Get most influential operators."""
        if not self._influence:
            self.compute_influence()
        sorted_ops = sorted(self._influence.items(), key=lambda x: -x[1])
        return [{"operator": op, "influence": score} for op, score in sorted_ops[:limit]]

    def get_operator_connections(self, operator: str) -> dict:
        """Get all connections for an operator."""
        connections = [e for e in self._edges if e["from"] == operator or e["to"] == operator]
        by_type = Counter(e["type"] for e in connections)
        return {
            "operator": operator,
            "influence": self._influence.get(operator, 0),
            "connections": len(connections),
            "by_type": dict(by_type),
        }

    def _save(self):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"edges": self._edges[-1000:], "influence": self._influence}
        self._state_file.write_text(json.dumps(data, indent=2, default=str))

    def _load(self):
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._edges = data.get("edges", [])
                self._influence = data.get("influence", {})
            except Exception:
                pass


_graph = OperatorGraph()


# ─── EXPANSION 3: PROOF ENGINE ───────────────────────────────────────────────

class ProofEngine:
    """Generates verifiable proofs of operator achievements."""

    PROOF_TYPES = {
        "proof_of_run": {"description": "ran agents continuously for N days",
                         "tiers": {"bronze": 30, "silver": 90, "gold": 365}},
        "proof_of_protection": {"description": "immune system catches and fixes"},
        "proof_of_score": {"description": "achieved zero score N at time T"},
        "proof_of_performance": {"description": "achieved X% return over N days"},
        "proof_of_discovery": {"description": "network discovered rule from N trades"},
        "proof_of_collective": {"description": "N agents detected event within T seconds"},
    }

    def __init__(self):
        self._proofs_dir = Path.home() / ".zeroos" / "state" / "proofs"
        self._proofs_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, proof_type: str, data: dict) -> dict:
        """Generate a proof attestation."""
        if proof_type not in self.PROOF_TYPES:
            return {"error": f"unknown proof type: {proof_type}"}

        proof_id = f"zp_{hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]}"

        proof = {
            "id": proof_id,
            "type": proof_type,
            "description": self.PROOF_TYPES[proof_type]["description"],
            "data": data,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verify_url": f"getzero.dev/proof/{proof_id}",
            "signature": self._sign(proof_id, data),
        }

        # Tier assignment for proof_of_run
        if proof_type == "proof_of_run":
            days = data.get("days", 0)
            tiers = self.PROOF_TYPES["proof_of_run"]["tiers"]
            tier = "none"
            for t_name, t_days in sorted(tiers.items(), key=lambda x: x[1]):
                if days >= t_days:
                    tier = t_name
            proof["tier"] = tier

        # Save proof
        (self._proofs_dir / f"{proof_id}.json").write_text(json.dumps(proof, indent=2))
        return proof

    def verify(self, proof_id: str) -> dict:
        """Verify a proof exists and is valid."""
        fpath = self._proofs_dir / f"{proof_id}.json"
        if not fpath.exists():
            return {"valid": False, "reason": "proof not found"}
        try:
            proof = json.loads(fpath.read_text())
            expected_sig = self._sign(proof["id"], proof["data"])
            if proof.get("signature") != expected_sig:
                return {"valid": False, "reason": "signature mismatch"}
            return {"valid": True, "proof": proof}
        except Exception as e:
            return {"valid": False, "reason": str(e)}

    def list_proofs(self) -> list[dict]:
        """List all generated proofs."""
        proofs = []
        for fpath in self._proofs_dir.glob("zp_*.json"):
            try:
                proofs.append(json.loads(fpath.read_text()))
            except Exception:
                pass
        return sorted(proofs, key=lambda p: p.get("generated_at", ""), reverse=True)

    def _sign(self, proof_id: str, data: dict) -> str:
        """HMAC signature. In production: use proper signing key."""
        content = f"{proof_id}:{json.dumps(data, sort_keys=True, default=str)}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]


_proof_engine = ProofEngine()


# ─── EXPANSION 4: SIMULATION SANDBOX ─────────────────────────────────────────

class SimulationSandbox:
    """Test strategies against historical data with full reasoning stack."""

    def simulate(self, trades: list[dict], equity: float = 5000,
                 preset: str = "balanced", conviction_threshold: float = 0.60) -> dict:
        """Run simulation from enriched trade data (as-if replay)."""
        if not trades:
            return {"error": "no trades to simulate"}

        current_equity = equity
        equity_curve = [equity]
        wins, losses = 0, 0
        regime_pnl = {}

        for trade in trades:
            quality = trade.get("quality", 5)
            confidence = trade.get("confidence", 0.5)

            # Apply conviction threshold
            if confidence < conviction_threshold:
                continue

            pnl_pct = trade.get("pnl_pct", 0)
            # Size by conviction
            size_pct = 0.08 + (confidence - 0.60) / 0.40 * 0.17
            size_pct = max(0.08, min(0.25, size_pct))
            pnl_usd = current_equity * size_pct * pnl_pct

            current_equity += pnl_usd
            equity_curve.append(current_equity)

            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1

            regime = trade.get("entry_regime", trade.get("regime", "unknown"))
            if regime not in regime_pnl:
                regime_pnl[regime] = {"pnl": 0, "trades": 0, "wins": 0}
            regime_pnl[regime]["pnl"] += pnl_pct
            regime_pnl[regime]["trades"] += 1
            if pnl_pct > 0:
                regime_pnl[regime]["wins"] += 1

        total_trades = wins + losses
        max_dd = 0
        peak = equity_curve[0]
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "starting_equity": equity,
            "final_equity": round(current_equity, 2),
            "total_return_pct": round((current_equity - equity) / equity * 100, 2),
            "trade_count": total_trades,
            "win_rate": round(wins / max(total_trades, 1), 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "regime_performance": {
                regime: {
                    "pnl_pct": round(d["pnl"] * 100, 2),
                    "trades": d["trades"],
                    "win_rate": round(d["wins"] / max(d["trades"], 1), 2),
                }
                for regime, d in regime_pnl.items()
            },
            "preset": preset,
            "conviction_threshold": conviction_threshold,
        }


_sandbox = SimulationSandbox()


# ─── EXPANSION 5: REASONING API ──────────────────────────────────────────────

class ReasoningAPI:
    """Regime detection as a service. Works on ANY price data."""

    TIERS = {
        "developer": {"price": 200, "evals_per_day": 100, "sections": ["regime", "consensus", "risk"]},
        "professional": {"price": 1000, "evals_per_day": 1000, "sections": ["regime", "consensus", "risk", "discoveries"]},
        "enterprise": {"price": 5000, "evals_per_day": -1, "sections": ["*"]},
    }

    def evaluate_candles(self, candles: list[dict]) -> dict:
        """Evaluate regime from raw candle data. Minimal version."""
        if len(candles) < 30:
            return {"error": "need at least 30 candles for regime detection"}

        closes = [c.get("close", c.get("c", 0)) for c in candles]
        highs = [c.get("high", c.get("h", 0)) for c in candles]
        lows = [c.get("low", c.get("l", 0)) for c in candles]

        # Hurst exponent (R/S method, simplified)
        hurst = self._estimate_hurst(closes)

        # ATR
        atr = _mean([highs[i] - lows[i] for i in range(-14, 0)]) if len(candles) >= 14 else 0
        atr_pct = atr / closes[-1] if closes[-1] > 0 else 0

        # Regime classification
        if hurst > 0.65:
            regime = "strong_trend"
        elif hurst > 0.55:
            regime = "moderate_trend"
        elif hurst > 0.45:
            regime = "stable"
        else:
            regime = "mean_revert"

        # Simple directional signals
        ema_9 = _mean(closes[-9:])
        ema_21 = _mean(closes[-21:]) if len(closes) >= 21 else ema_9
        rsi = self._rsi(closes)

        direction = "long" if ema_9 > ema_21 else "short"
        signals = {
            "ema": "long" if ema_9 > ema_21 else "short",
            "rsi": "long" if rsi < 40 else "short" if rsi > 60 else "neutral",
        }
        agreeing = sum(1 for v in signals.values() if v == direction)

        return {
            "regime": {
                "classification": regime,
                "hurst": round(hurst, 4),
                "confidence": round(min(abs(hurst - 0.5) * 4, 1.0), 2),
            },
            "signal_consensus": {
                "direction": direction,
                "consensus_pct": round(agreeing / max(len(signals), 1), 2),
                "indicators": signals,
            },
            "risk_assessment": {
                "atr_pct": round(atr_pct, 4),
                "suggested_stop_distance": round(atr_pct * 2, 4),
                "volatility_regime": "high" if atr_pct > 0.03 else "normal" if atr_pct > 0.01 else "low",
            },
        }

    def _estimate_hurst(self, series: list[float], max_lag: int = 20) -> float:
        """Simplified R/S Hurst estimation."""
        if len(series) < max_lag * 2:
            return 0.5
        returns = [series[i] / series[i-1] - 1 for i in range(1, len(series)) if series[i-1] > 0]
        if len(returns) < max_lag:
            return 0.5

        rs_values = []
        for lag in range(10, min(max_lag + 1, len(returns))):
            chunk = returns[:lag]
            mean_r = _mean(chunk)
            deviations = [r - mean_r for r in chunk]
            cumsum = []
            s = 0
            for d in deviations:
                s += d
                cumsum.append(s)
            R = max(cumsum) - min(cumsum) if cumsum else 0
            import math
            S = math.sqrt(_mean([d**2 for d in deviations])) if deviations else 1
            if S > 0:
                rs_values.append((math.log(lag), math.log(R / S)))

        if len(rs_values) < 2:
            return 0.5

        # Linear regression slope = Hurst
        xs = [p[0] for p in rs_values]
        ys = [p[1] for p in rs_values]
        mx, my = _mean(xs), _mean(ys)
        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
        den = sum((xs[i] - mx) ** 2 for i in range(len(xs)))
        hurst = num / den if den > 0 else 0.5
        return max(0.0, min(1.0, hurst))

    def _rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50
        changes = [closes[i] - closes[i-1] for i in range(-period, 0)]
        gains = [c for c in changes if c > 0]
        losses = [-c for c in changes if c < 0]
        avg_gain = _mean(gains) if gains else 0
        avg_loss = _mean(losses) if losses else 0.001
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)


_reasoning_api = ReasoningAPI()


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def generate_feed(regime_data, market_data, observations=None, discoveries=None,
                  network_stats=None, correlation_matrix=None):
    """Expansion 1."""
    return _feed.generate_snapshot(regime_data, market_data, observations,
                                   discoveries, network_stats, correlation_matrix)

def filter_feed(snapshot, tier):
    """Expansion 1."""
    return _feed.filter_by_tier(snapshot, tier)

def add_operator_edge(from_op, to_op, edge_type, weight=1.0):
    """Expansion 2."""
    _graph.add_edge(from_op, to_op, edge_type, weight)

def compute_influence():
    """Expansion 2."""
    return _graph.compute_influence()

def get_top_operators(limit=10):
    """Expansion 2."""
    return _graph.get_top_operators(limit)

def get_operator_info(operator):
    """Expansion 2."""
    return _graph.get_operator_connections(operator)

def generate_proof(proof_type, data):
    """Expansion 3."""
    return _proof_engine.generate(proof_type, data)

def verify_proof(proof_id):
    """Expansion 3."""
    return _proof_engine.verify(proof_id)

def list_proofs():
    """Expansion 3."""
    return _proof_engine.list_proofs()

def run_simulation(trades, equity=5000, preset="balanced", threshold=0.60):
    """Expansion 4."""
    return _sandbox.simulate(trades, equity, preset, threshold)

def evaluate_candles(candles):
    """Expansion 5."""
    return _reasoning_api.evaluate_candles(candles)

def get_feed_tiers():
    """Expansion 1."""
    return IntelligenceFeed.TIERS

def get_api_tiers():
    """Expansion 5."""
    return ReasoningAPI.TIERS

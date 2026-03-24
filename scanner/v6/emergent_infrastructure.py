# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
emergent_infrastructure.py — Emergences 1-5

1. Computed Conviction Index (CCI) — price discovery layer
2. Trust Certification — immune system as certification service
3. Inter-Agent Negotiation — entry/exit coordination
4. Strategy Allocation — universe diversification suggestions
5. zero-immune SDK core — standalone immune protocol
"""

import json
import time
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [emergent] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0

STATE_DIR = Path.home() / ".zeroos" / "state"


# ─── EMERGENCE 1: COMPUTED CONVICTION INDEX ──────────────────────────────────

class ComputedConvictionIndex:
    """
    Aggregates agent evaluations into a conviction signal per asset.
    CCI: -1.0 (max short conviction) to +1.0 (max long conviction).
    Not a prediction. Computed conviction from reasoning engines.
    """

    def __init__(self):
        self._state_file = STATE_DIR / "cci.json"
        self._history_file = STATE_DIR / "cci_history.json"

    def compute(self, evaluations: list[dict]) -> dict[str, dict]:
        """
        Compute CCI for all coins from evaluation reports.
        Each evaluation: {coin, direction, consensus_pct, regime, regime_confidence, agent_score}
        """
        by_coin: dict[str, list[dict]] = {}
        for ev in evaluations:
            coin = ev.get("coin", "")
            if coin:
                by_coin.setdefault(coin, []).append(ev)

        cci_results = {}
        for coin, evals in by_coin.items():
            cci_results[coin] = self._compute_coin(coin, evals)

        result = {
            "cci": cci_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "network_agents": len(set(e.get("agent_id", "") for e in evaluations)),
            "computation_window": "15min",
        }

        self._save(result)
        self._append_history(result)
        return result

    def _compute_coin(self, coin: str, evals: list[dict]) -> dict:
        if len(evals) < 1:
            return {"value": 0, "direction": "neutral", "confidence": 0,
                    "agents": 0, "regime": "unknown"}

        weighted_long = 0
        weighted_short = 0
        total_weight = 0

        for ev in evals:
            score = ev.get("agent_score", 5.0)
            regime_conf = ev.get("regime_confidence", 0.5)
            consensus = ev.get("consensus_pct", 0.5)
            weight = score * regime_conf
            total_weight += weight

            direction = ev.get("direction", "neutral")
            if direction == "long":
                weighted_long += weight * consensus
            elif direction == "short":
                weighted_short += weight * consensus

        if total_weight == 0:
            cci = 0
        else:
            long_pct = weighted_long / total_weight
            short_pct = weighted_short / total_weight
            cci = long_pct - short_pct

        cci = max(-1.0, min(1.0, cci))

        agent_factor = min(len(evals) / 100, 1.0)
        agreement_factor = abs(cci)
        confidence = agent_factor * 0.5 + agreement_factor * 0.5

        direction = "long" if cci > 0.1 else "short" if cci < -0.1 else "neutral"

        regimes = Counter(e.get("regime", "unknown") for e in evals)
        top_regime = regimes.most_common(1)[0][0] if regimes else "unknown"

        return {
            "value": round(cci, 3),
            "direction": direction,
            "confidence": round(confidence, 2),
            "agents": len(evals),
            "regime": top_regime,
        }

    def get_current(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except Exception:
                pass
        return {"cci": {}, "timestamp": None}

    def get_history(self, hours: int = 24) -> list[dict]:
        if not self._history_file.exists():
            return []
        try:
            data = json.loads(self._history_file.read_text())
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            return [d for d in data if d.get("timestamp", "") >= cutoff]
        except Exception:
            return []

    def _save(self, data: dict):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(data, indent=2))

    def _append_history(self, data: dict):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        history = []
        if self._history_file.exists():
            try:
                history = json.loads(self._history_file.read_text())
            except Exception:
                pass
        summary = {"timestamp": data["timestamp"], "cci": {}}
        for coin, vals in data.get("cci", {}).items():
            summary["cci"][coin] = {"value": vals["value"], "direction": vals["direction"]}
        history.append(summary)
        # Keep last 2000 entries (~7 days at 15min intervals)
        history = history[-2000:]
        self._history_file.write_text(json.dumps(history))


_cci = ComputedConvictionIndex()


# ─── EMERGENCE 2: TRUST CERTIFICATION ────────────────────────────────────────

class TrustCertification:
    """
    Certification service for autonomous agents.
    Based on immune system compliance over time.
    Bronze: 30 days, 99%+ pass rate
    Silver: 90 days, 100% resolution rate
    Gold: 365 days, independently verifiable
    """

    TIERS = {
        "bronze": {"days": 30, "min_pass_rate": 0.99, "checks_per_day": 1440},
        "silver": {"days": 90, "min_pass_rate": 0.99, "max_unresolved": 0},
        "gold": {"days": 365, "min_pass_rate": 0.999, "max_unresolved": 0},
    }

    def __init__(self):
        self._state_file = STATE_DIR / "trust_certification.json"
        self._compliance: dict = {}
        self._load()

    def record_cycle(self, agent_id: str, checks_run: int, checks_passed: int,
                     checks_fixed: int, duration_ms: float):
        """Record an immune cycle result for certification tracking."""
        if agent_id not in self._compliance:
            self._compliance[agent_id] = {
                "started": datetime.now(timezone.utc).isoformat(),
                "total_cycles": 0,
                "total_checks": 0,
                "total_passed": 0,
                "total_fixed": 0,
                "total_unresolved": 0,
                "daily_log": [],
            }

        c = self._compliance[agent_id]
        c["total_cycles"] += 1
        c["total_checks"] += checks_run
        c["total_passed"] += checks_passed
        c["total_fixed"] += checks_fixed
        failed = checks_run - checks_passed
        unresolved = failed - checks_fixed
        c["total_unresolved"] += max(0, unresolved)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not c["daily_log"] or c["daily_log"][-1].get("date") != today:
            c["daily_log"].append({"date": today, "checks": 0, "passed": 0, "fixed": 0})
        c["daily_log"][-1]["checks"] += checks_run
        c["daily_log"][-1]["passed"] += checks_passed
        c["daily_log"][-1]["fixed"] += checks_fixed

        # Keep last 400 days
        c["daily_log"] = c["daily_log"][-400:]
        self._save()

    def get_certification(self, agent_id: str) -> dict:
        """Determine current certification level."""
        c = self._compliance.get(agent_id)
        if not c:
            return {"agent_id": agent_id, "level": "none", "reason": "no compliance data"}

        started = c.get("started", "")
        try:
            start_dt = datetime.fromisoformat(started)
            days_active = (datetime.now(timezone.utc) - start_dt).days
        except Exception:
            days_active = len(c.get("daily_log", []))

        total = c["total_checks"]
        passed = c["total_passed"]
        pass_rate = passed / max(total, 1)
        unresolved = c["total_unresolved"]

        level = "none"
        next_level = "bronze"
        days_needed = 30

        if days_active >= 365 and pass_rate >= 0.999 and unresolved == 0:
            level = "gold"
            next_level = None
            days_needed = 0
        elif days_active >= 90 and pass_rate >= 0.99 and unresolved == 0:
            level = "silver"
            next_level = "gold"
            days_needed = 365 - days_active
        elif days_active >= 30 and pass_rate >= 0.99:
            level = "bronze"
            next_level = "silver"
            days_needed = 90 - days_active

        return {
            "agent_id": agent_id,
            "level": level,
            "days_active": days_active,
            "total_checks": total,
            "pass_rate": round(pass_rate, 4),
            "unresolved_failures": unresolved,
            "total_saves": c["total_fixed"],
            "next_level": next_level,
            "days_to_next": max(0, days_needed),
        }

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._compliance, indent=2, default=str))

    def _load(self):
        if self._state_file.exists():
            try:
                self._compliance = json.loads(self._state_file.read_text())
            except Exception:
                self._compliance = {}


_trust = TrustCertification()


# ─── EMERGENCE 3: INTER-AGENT NEGOTIATION ────────────────────────────────────

class AgentNegotiator:
    """
    Agents express intentions. Network coordinates timing and sizing.
    Minimizes market impact, maximizes collective P&L.
    """

    def __init__(self):
        self._state_file = STATE_DIR / "negotiations.json"
        self._intentions: list[dict] = []
        self._exit_intentions: list[dict] = []

    def negotiate_entry(self, agent_id: str, coin: str, direction: str,
                        size_usd: float, book_depth_1pct: float = 100000,
                        active_agents: list[dict] = None) -> dict:
        """
        Agent declares entry intention. Network decides: proceed / wait / reduce.
        """
        active_agents = active_agents or []

        same_dir = [a for a in active_agents
                    if a.get("coin") == coin and a.get("direction") == direction]
        total_exposure = sum(a.get("size_usd", 0) for a in same_dir)
        utilization = total_exposure / max(book_depth_1pct, 1)

        # Check pending exits
        pending_exits = [e for e in self._exit_intentions
                         if e.get("coin") == coin and e.get("direction") == direction
                         and e.get("expected_time", "") > datetime.now(timezone.utc).isoformat()]

        # Case 1: spacious market
        if utilization < 0.05 and len(same_dir) < 10:
            return {
                "action": "proceed",
                "size": size_usd,
                "delay_seconds": 0,
                "message": "market spacious. enter freely.",
                "agents_same_direction": len(same_dir),
                "utilization_pct": round(utilization * 100, 1),
            }

        # Case 2: crowded + pending exit
        if utilization > 0.15 and pending_exits:
            return {
                "action": "wait",
                "size": size_usd,
                "delay_seconds": 60,
                "message": f"another agent exiting {coin} {direction} soon. "
                           f"entering after reduces market impact for both.",
                "agents_same_direction": len(same_dir),
                "utilization_pct": round(utilization * 100, 1),
            }

        # Case 3: crowded, no pending exit → reduce
        if utilization > 0.15:
            reduction = min(0.15 / utilization, 1.0)
            reduced = size_usd * reduction
            return {
                "action": "reduce",
                "size": round(reduced, 2),
                "original_size": size_usd,
                "delay_seconds": 0,
                "message": f"network exposure {utilization:.0%} of book depth. "
                           f"reducing ${size_usd:.0f} → ${reduced:.0f}.",
                "agents_same_direction": len(same_dir),
                "utilization_pct": round(utilization * 100, 1),
            }

        # Case 4: moderate
        return {
            "action": "proceed_with_info",
            "size": size_usd,
            "delay_seconds": 0,
            "message": f"{len(same_dir)} agents also {direction} {coin}. "
                       f"utilization {utilization:.1%}. proceeding.",
            "agents_same_direction": len(same_dir),
            "utilization_pct": round(utilization * 100, 1),
        }

    def declare_exit(self, agent_id: str, coin: str, direction: str,
                     size_usd: float, minutes_until: int = 15, reason: str = ""):
        """Agent declares intent to exit."""
        intention = {
            "agent_id": agent_id,
            "coin": coin,
            "direction": direction,
            "size_usd": size_usd,
            "expected_time": (datetime.now(timezone.utc) + timedelta(minutes=minutes_until)).isoformat(),
            "reason": reason,
            "declared_at": datetime.now(timezone.utc).isoformat(),
        }
        self._exit_intentions.append(intention)
        # Prune old intentions
        cutoff = datetime.now(timezone.utc).isoformat()
        self._exit_intentions = [e for e in self._exit_intentions
                                  if e.get("expected_time", "") > cutoff][-100:]
        self._save()
        return intention

    def get_pending_exits(self, coin: str = None) -> list[dict]:
        cutoff = datetime.now(timezone.utc).isoformat()
        exits = [e for e in self._exit_intentions if e.get("expected_time", "") > cutoff]
        if coin:
            exits = [e for e in exits if e.get("coin") == coin]
        return exits

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = {"exit_intentions": self._exit_intentions[-100:]}
        self._state_file.write_text(json.dumps(data, indent=2))


_negotiator = AgentNegotiator()


# ─── EMERGENCE 4: STRATEGY ALLOCATION ────────────────────────────────────────

class StrategyAllocator:
    """
    Suggests universe diversification to improve network coverage.
    Not forced. Following suggestions earns score bonus.
    """

    def suggest(self, agent_universe: list[str],
                network_coverage: dict[str, int],
                total_agents: int,
                all_coins: list[str] = None) -> dict:
        """
        Suggest coin additions/removals for better network coverage.
        network_coverage: {coin: agent_count}
        """
        all_coins = all_coins or list(network_coverage.keys())
        overcovered_threshold = max(total_agents * 0.4, 10)

        suggestions = []

        # Suggest dropping overcovered
        for coin in agent_universe:
            count = network_coverage.get(coin, 0)
            if count > overcovered_threshold:
                suggestions.append({
                    "action": "consider_dropping",
                    "coin": coin,
                    "agents_watching": count,
                    "reason": f"{count} agents already watch {coin}. your contribution is marginal.",
                })

        # Suggest adding undercovered
        undercovered = [(c, network_coverage.get(c, 0)) for c in all_coins
                        if c not in agent_universe and network_coverage.get(c, 0) < 5]
        undercovered.sort(key=lambda x: x[1])

        for coin, count in undercovered[:3]:
            suggestions.append({
                "action": "consider_adding",
                "coin": coin,
                "agents_watching": count,
                "reason": f"only {count} agents watch {coin}. "
                          f"adding it strengthens collective intelligence.",
            })

        additions = len([s for s in suggestions if s["action"] == "consider_adding"])
        bonus = additions * 0.02

        return {
            "suggestions": suggestions,
            "network_contribution_bonus": round(bonus, 2),
            "message": "diversifying your universe helps the network. "
                       "agents covering unique coins earn a contribution bonus.",
        }


_allocator = StrategyAllocator()


# ─── EMERGENCE 5: ZERO-IMMUNE SDK CORE ───────────────────────────────────────

class ImmuneCheck:
    """A single check in the immune protocol."""

    def __init__(self, name: str, execute_fn=None, fix_fn=None, critical: bool = False):
        self.name = name
        self._execute = execute_fn
        self._fix = fix_fn
        self.critical = critical
        self.last_action = None

    def execute(self) -> bool:
        if self._execute:
            return self._execute()
        return True

    def auto_fix(self) -> bool:
        if self._fix:
            result = self._fix()
            self.last_action = f"auto_fix:{self.name}"
            return result
        return False


class ImmuneProtocol:
    """
    Standalone immune system for ANY agent.
    Register checks. Run cycles. Log everything. Generate proofs.
    """

    def __init__(self, agent_id: str, check_interval: int = 60):
        self.agent_id = agent_id
        self.interval = check_interval
        self.checks: list[ImmuneCheck] = []
        self.log: list[dict] = []
        self._state_file = STATE_DIR / f"immune_protocol_{agent_id}.json"

    def register_check(self, check: ImmuneCheck):
        self.checks.append(check)

    def run_cycle(self) -> dict:
        """Run all checks. Fix failures. Log everything."""
        results = []
        for check in self.checks:
            start = time.monotonic()
            try:
                passed = check.execute()
                duration_ms = (time.monotonic() - start) * 1000

                if not passed:
                    fixed = check.auto_fix()
                    fix_ms = (time.monotonic() - start) * 1000
                    results.append({
                        "check": check.name, "passed": False,
                        "fixed": fixed, "duration_ms": round(fix_ms, 1),
                        "action": check.last_action, "critical": check.critical,
                    })
                else:
                    results.append({
                        "check": check.name, "passed": True,
                        "duration_ms": round(duration_ms, 1),
                    })
            except Exception as e:
                results.append({
                    "check": check.name, "passed": False,
                    "fixed": False, "error": str(e),
                    "critical": check.critical,
                })

        cycle = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks_run": len(results),
            "checks_passed": len([r for r in results if r["passed"]]),
            "checks_failed": len([r for r in results if not r["passed"]]),
            "checks_fixed": len([r for r in results if not r["passed"] and r.get("fixed")]),
            "duration_ms": round(sum(r.get("duration_ms", 0) for r in results), 1),
            "details": results,
        }

        self.log.append(cycle)
        self.log = self.log[-10000:]  # Keep last 10K cycles

        # Report for certification
        _trust.record_cycle(
            self.agent_id,
            cycle["checks_run"], cycle["checks_passed"],
            cycle["checks_fixed"], cycle["duration_ms"],
        )

        return cycle

    def get_stats(self) -> dict:
        total_checks = sum(c["checks_run"] for c in self.log)
        total_passed = sum(c["checks_passed"] for c in self.log)
        total_fixed = sum(c["checks_fixed"] for c in self.log)
        return {
            "agent_id": self.agent_id,
            "total_cycles": len(self.log),
            "total_checks": total_checks,
            "pass_rate": round(total_passed / max(total_checks, 1), 4),
            "total_saves": total_fixed,
            "certification": _trust.get_certification(self.agent_id),
        }


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def compute_cci(evaluations: list[dict]) -> dict:
    """Emergence 1: Compute CCI from agent evaluations."""
    return _cci.compute(evaluations)

def get_cci() -> dict:
    """Emergence 1: Get current CCI."""
    return _cci.get_current()

def get_cci_history(hours: int = 24) -> list[dict]:
    """Emergence 1: Get historical CCI."""
    return _cci.get_history(hours)

def record_immune_cycle(agent_id: str, checks_run: int, checks_passed: int,
                        checks_fixed: int, duration_ms: float):
    """Emergence 2: Record immune cycle for certification."""
    _trust.record_cycle(agent_id, checks_run, checks_passed, checks_fixed, duration_ms)

def get_certification(agent_id: str) -> dict:
    """Emergence 2: Get agent certification level."""
    return _trust.get_certification(agent_id)

def negotiate_entry(agent_id: str, coin: str, direction: str, size_usd: float,
                    book_depth: float = 100000, active_agents: list[dict] = None) -> dict:
    """Emergence 3: Negotiate entry with network."""
    return _negotiator.negotiate_entry(agent_id, coin, direction, size_usd, book_depth, active_agents)

def declare_exit(agent_id: str, coin: str, direction: str, size_usd: float,
                 minutes: int = 15, reason: str = "") -> dict:
    """Emergence 3: Declare exit intention."""
    return _negotiator.declare_exit(agent_id, coin, direction, size_usd, minutes, reason)

def get_pending_exits(coin: str = None) -> list[dict]:
    """Emergence 3: Get pending exit intentions."""
    return _negotiator.get_pending_exits(coin)

def suggest_universe(agent_universe: list[str], coverage: dict[str, int],
                     total_agents: int, all_coins: list[str] = None) -> dict:
    """Emergence 4: Get universe diversification suggestions."""
    return _allocator.suggest(agent_universe, coverage, total_agents, all_coins)

def create_immune_protocol(agent_id: str, interval: int = 60) -> ImmuneProtocol:
    """Emergence 5: Create a standalone immune protocol."""
    return ImmuneProtocol(agent_id, interval)

def create_immune_check(name: str, execute_fn=None, fix_fn=None,
                        critical: bool = False) -> ImmuneCheck:
    """Emergence 5: Create an immune check."""
    return ImmuneCheck(name, execute_fn, fix_fn, critical)

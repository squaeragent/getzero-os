# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
connective_tissue.py — 10 gaps between layers

GAP 1: Entry Snapshot (freeze evaluation state at entry)
GAP 2: MAE/MFE Tracker (continuous, not 5-min cycles)
GAP 3: Regime Period Manager (transitions + archival)
GAP 4: Local Persistence (crash recovery)
GAP 5: Intelligence Sync Response (complete payload)
GAP 6: 28-Step Evaluation Loop (orchestrator)
GAP 7: Onboarding Messages (patience is the product)
GAP 8: Resilient API Client (retry + queue + fallback)
GAP 9: Migration Checklist (current → target)
GAP 10: Test Plan (unit + integration + system + chaos)
"""

import json
import time
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [tissue] [{ts}] {msg}", flush=True)

STATE_DIR = Path.home() / ".zeroos"


# ─── GAP 1: ENTRY SNAPSHOT ───────────────────────────────────────────────────

@dataclass
class EntrySnapshot:
    """Freezes complete evaluation state at the moment of entry.
    ROOT of the entire data pipeline — without this, trades_enriched is empty."""

    # Regime
    regime: str = ""
    hurst: float = 0.0
    dfa: float = 0.0
    regime_confidence: float = 0.0
    regime_age_hours: float = 0.0
    previous_regime: str = ""
    # Indicators
    indicator_votes: dict = field(default_factory=dict)
    consensus: float = 0.0
    # Conviction
    conviction: float = 0.0
    consensus_velocity: float = 0.0
    rejection_streak: int = 0
    funding_rate: float = 0.0
    atr_ratio: float = 0.0
    # Context
    discovery_match: str = ""
    regime_memory_context: str = ""
    correlation_check: dict = field(default_factory=dict)
    network_negotiation: dict = field(default_factory=dict)
    # Metadata
    timestamp: str = ""
    coin: str = ""
    direction: str = ""
    size_usd: float = 0.0
    size_pct: float = 0.0
    entry_price: float = 0.0
    stop_price: float = 0.0
    stop_distance_pct: float = 0.0

    @classmethod
    def capture(cls, coin: str, direction: str, regime: str, hurst: float,
                dfa: float, consensus: float, conviction: float,
                indicator_votes: dict, entry_price: float, stop_price: float,
                size_usd: float, equity: float, streak: int = 0,
                velocity: float = 0, funding_rate: float = 0,
                atr_ratio: float = 1.0, regime_age: float = 0,
                discovery: str = "", memory_ctx: str = "",
                correlation: dict = None, negotiation: dict = None,
                **kwargs) -> "EntrySnapshot":
        return cls(
            regime=regime, hurst=hurst, dfa=dfa,
            regime_age_hours=regime_age,
            indicator_votes=indicator_votes,
            consensus=consensus, conviction=conviction,
            consensus_velocity=velocity,
            rejection_streak=streak,
            funding_rate=funding_rate, atr_ratio=atr_ratio,
            discovery_match=discovery,
            regime_memory_context=memory_ctx,
            correlation_check=correlation or {},
            network_negotiation=negotiation or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
            coin=coin, direction=direction,
            size_usd=size_usd,
            size_pct=round(size_usd / equity, 3) if equity > 0 else 0,
            entry_price=entry_price, stop_price=stop_price,
            stop_distance_pct=round(abs(entry_price - stop_price) / entry_price * 100, 2) if entry_price > 0 else 0,
        )

    def save_to_disk(self, trade_id: str):
        path = STATE_DIR / "snapshots" / f"{trade_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self)))

    @classmethod
    def load_from_disk(cls, trade_id: str):
        path = STATE_DIR / "snapshots" / f"{trade_id}.json"
        if path.exists():
            try:
                return cls(**json.loads(path.read_text()))
            except Exception:
                return None
        return None

    def cleanup(self, trade_id: str):
        path = STATE_DIR / "snapshots" / f"{trade_id}.json"
        if path.exists():
            path.unlink()


# ─── GAP 2: MAE/MFE TRACKER ──────────────────────────────────────────────────

@dataclass
class PositionTracker:
    """Tracks MAE/MFE continuously during hold. Updates every price tick."""

    trade_id: str
    coin: str
    direction: str
    entry_price: float
    entry_time: str  # ISO format
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_price: float = 0.0
    mfe_price: float = 0.0
    mae_minutes: int = 0
    mfe_minutes: int = 0
    funding_accumulated: float = 0.0
    regime_change_count: int = 0
    _last_regime: str = ""

    def update(self, current_price: float, current_regime: str = None):
        """Called every price update (10s-60s depending on setup)."""
        if self.direction.lower() == "long":
            pnl_pct = (current_price - self.entry_price) / self.entry_price * 100
        else:
            pnl_pct = (self.entry_price - current_price) / self.entry_price * 100

        try:
            entry_dt = datetime.fromisoformat(self.entry_time)
            minutes = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
        except Exception:
            minutes = 0

        if pnl_pct < self.mae_pct:
            self.mae_pct = round(pnl_pct, 4)
            self.mae_price = current_price
            self.mae_minutes = int(minutes)

        if pnl_pct > self.mfe_pct:
            self.mfe_pct = round(pnl_pct, 4)
            self.mfe_price = current_price
            self.mfe_minutes = int(minutes)

        if current_regime and self._last_regime and current_regime != self._last_regime:
            self.regime_change_count += 1
        if current_regime:
            self._last_regime = current_regime

    def get_state(self) -> dict:
        return {
            "mae_pct": self.mae_pct, "mfe_pct": self.mfe_pct,
            "mae_minutes": self.mae_minutes, "mfe_minutes": self.mfe_minutes,
            "mae_price": self.mae_price, "mfe_price": self.mfe_price,
            "funding_accumulated": self.funding_accumulated,
            "regime_changes": self.regime_change_count,
        }


class TrackerManager:
    """Manages trackers for all open positions."""

    def __init__(self):
        self.trackers: dict[str, PositionTracker] = {}

    def start(self, trade_id: str, coin: str, direction: str, entry_price: float):
        self.trackers[trade_id] = PositionTracker(
            trade_id=trade_id, coin=coin, direction=direction,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc).isoformat(),
        )

    def update(self, trade_id: str, price: float, regime: str = None):
        t = self.trackers.get(trade_id)
        if t:
            t.update(price, regime)

    def stop(self, trade_id: str) -> dict:
        t = self.trackers.pop(trade_id, None)
        return t.get_state() if t else {}

    def get(self, trade_id: str):
        return self.trackers.get(trade_id)


# ─── GAP 3: REGIME PERIOD MANAGER ────────────────────────────────────────────

@dataclass
class ActivePeriod:
    coin: str = ""
    regime: str = ""
    hurst_avg: float = 0.0
    hurst_samples: int = 0
    start_time: str = ""
    trade_count: int = 0
    wins: int = 0
    total_pnl_pct: float = 0.0
    end_time: str = ""
    duration_hours: float = 0.0
    transition_to: str = ""


class RegimePeriodManager:
    """Tracks ongoing regime periods per coin. Detects transitions."""

    def __init__(self):
        self.active: dict[str, ActivePeriod] = {}

    def load(self, coins: list[str]):
        for coin in coins:
            path = STATE_DIR / "regimes" / f"{coin}.json"
            if path.exists():
                try:
                    self.active[coin] = ActivePeriod(**json.loads(path.read_text()))
                except Exception:
                    pass

    def update(self, coin: str, regime: str, hurst: float) -> dict | None:
        """Returns completed period if transition detected, else None."""
        current = self.active.get(coin)

        if current is None:
            self.active[coin] = ActivePeriod(
                coin=coin, regime=regime, hurst_avg=hurst, hurst_samples=1,
                start_time=datetime.now(timezone.utc).isoformat(),
            )
            self._save(coin)
            return None

        if regime == current.regime:
            current.hurst_samples += 1
            current.hurst_avg = round(
                (current.hurst_avg * (current.hurst_samples - 1) + hurst) / current.hurst_samples, 4
            )
            self._save(coin)
            return None

        # Transition detected
        completed = ActivePeriod(**asdict(current))
        completed.end_time = datetime.now(timezone.utc).isoformat()
        try:
            start = datetime.fromisoformat(completed.start_time)
            end = datetime.fromisoformat(completed.end_time)
            completed.duration_hours = round((end - start).total_seconds() / 3600, 2)
        except Exception:
            completed.duration_hours = 0
        completed.transition_to = regime
        self._archive(completed)

        # Start new period
        self.active[coin] = ActivePeriod(
            coin=coin, regime=regime, hurst_avg=hurst, hurst_samples=1,
            start_time=datetime.now(timezone.utc).isoformat(),
        )
        self._save(coin)

        return asdict(completed)

    def record_trade(self, coin: str, pnl_pct: float):
        p = self.active.get(coin)
        if p:
            p.trade_count += 1
            if pnl_pct > 0:
                p.wins += 1
            p.total_pnl_pct = round(p.total_pnl_pct + pnl_pct, 4)
            self._save(coin)

    def get_age_hours(self, coin: str) -> float:
        p = self.active.get(coin)
        if not p or not p.start_time:
            return 0
        try:
            start = datetime.fromisoformat(p.start_time)
            return (datetime.now(timezone.utc) - start).total_seconds() / 3600
        except Exception:
            return 0

    def _save(self, coin: str):
        path = STATE_DIR / "regimes" / f"{coin}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self.active[coin])))

    def _archive(self, period: ActivePeriod):
        ts = period.start_time.replace(":", "-").replace("T", "_")[:19]
        path = STATE_DIR / "regimes" / "archive" / f"{period.coin}_{ts}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(period)))


# ─── GAP 4: LOCAL PERSISTENCE ────────────────────────────────────────────────

class LocalStore:
    """Manages all persistent local state at ~/.zeroos/"""

    BASE = STATE_DIR

    def __init__(self):
        self.BASE.mkdir(exist_ok=True)

    def save(self, key: str, data) -> None:
        path = self.BASE / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def load(self, key: str, default=None):
        path = self.BASE / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return default

    def save_personal_weights(self, weights: dict):
        self.save("state/personal_weights", weights)

    def load_personal_weights(self) -> dict | None:
        return self.load("state/personal_weights")

    def save_streaks(self, streaks: dict):
        self.save("rejection_streak", streaks)

    def load_streaks(self) -> dict:
        return self.load("rejection_streak", {})

    def save_consensus_history(self, history: dict):
        self.save("consensus_history", history)

    def load_consensus_history(self) -> dict:
        return self.load("consensus_history", {})

    def save_intelligence(self, key: str, data):
        path = self.BASE / "intelligence" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def load_intelligence(self, key: str):
        path = self.BASE / "intelligence" / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return None


# ─── GAP 7: ONBOARDING MESSAGES ──────────────────────────────────────────────

ONBOARDING = {
    "first_boot": (
        "\n"
        "  your agent will reject most signals.\n"
        "  that's the design. patience is the product.\n"
        "\n"
        "  when something passes every check, it enters.\n"
        "  this might take hours.\n"
        "\n"
        "  close your terminal if you want.\n"
        "  your agent keeps running.\n"
    ),
    "ten_rejections": (
        "\n"
        "  10 signals evaluated. 10 rejected.\n"
        "  the machine is being selective.\n"
        "  this is normal.\n"
    ),
    "fifty_rejections": (
        "\n"
        "  50 signals evaluated. 0 entries.\n"
        "  the market hasn't presented anything\n"
        "  that passes every check. that's the intelligence.\n"
        "\n"
        "  the machine will trade when it's ready.\n"
    ),
}


def get_onboarding_message(rejection_count: int, shown: set) -> str | None:
    if rejection_count == 0 and "first_boot" not in shown:
        shown.add("first_boot")
        return ONBOARDING["first_boot"]
    if rejection_count >= 10 and "ten" not in shown:
        shown.add("ten")
        return ONBOARDING["ten_rejections"]
    if rejection_count >= 50 and "fifty" not in shown:
        shown.add("fifty")
        return ONBOARDING["fifty_rejections"]
    return None


# ─── GAP 8: RESILIENT API CLIENT ─────────────────────────────────────────────

class LocalQueue:
    """Disk-persisted queue at ~/.zeroos/queue/"""

    PATH = STATE_DIR / "queue"

    def add(self, endpoint: str, data: dict):
        self.PATH.mkdir(parents=True, exist_ok=True)
        item_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        (self.PATH / f"{item_id}.json").write_text(json.dumps({
            "id": item_id, "endpoint": endpoint, "data": data,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }))

    def get_all(self) -> list[dict]:
        if not self.PATH.exists():
            return []
        items = []
        for f in sorted(self.PATH.glob("*.json")):
            try:
                items.append(json.loads(f.read_text()))
            except Exception:
                pass
        return items

    def remove(self, item_id: str):
        for f in self.PATH.glob(f"{item_id}.json"):
            f.unlink()

    def size(self) -> int:
        return len(list(self.PATH.glob("*.json"))) if self.PATH.exists() else 0


class ResilientClient:
    """Every API call has retry, backoff, local queue, fallback."""

    # Critical endpoints that have Supabase Edge Function fallback
    CRITICAL_ENDPOINTS = {"/api/agents/heartbeat", "/api/agents/decision", "/api/agents/trade"}

    def __init__(self, base_url: str = "https://getzero.dev", max_retries: int = 3):
        self.base = base_url.rstrip("/")
        self.fallback_base = os.environ.get("ZEROOS_FALLBACK_URL", "").rstrip("/")  # e.g. https://api.getzero.dev
        self.max_retries = max_retries
        self.queue = LocalQueue()
        self._backoff = 2

    def post(self, endpoint: str, data: dict, critical: bool = False) -> dict | None:
        """Synchronous post with retry. critical=True queues on failure."""
        import urllib.request
        import urllib.error

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    f"{self.base}{endpoint}",
                    data=json.dumps(data).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    _log(f"auth failed on {endpoint}")
                    return None
                if e.code >= 500:
                    time.sleep(self._backoff ** attempt)
                    continue
                return None
            except Exception:
                time.sleep(self._backoff ** attempt)

        # Fallback to Supabase Edge Function for critical endpoints
        if self.fallback_base and endpoint in self.CRITICAL_ENDPOINTS:
            try:
                fallback_endpoint = endpoint.replace("/api/agents/", "/agents/")
                req = urllib.request.Request(
                    f"{self.fallback_base}{fallback_endpoint}",
                    data=json.dumps(data).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        _log(f"{endpoint} → fallback succeeded")
                        return json.loads(resp.read())
            except Exception:
                pass

        if critical:
            self.queue.add(endpoint, data)
            _log(f"{endpoint} queued for later delivery")
        return None

    def drain_queue(self) -> int:
        """Retry queued requests. Returns count drained."""
        drained = 0
        for item in self.queue.get_all():
            result = self.post(item["endpoint"], item["data"])
            if result is not None:
                self.queue.remove(item["id"])
                drained += 1
        return drained


# ─── GAP 9: MIGRATION CHECKLIST ──────────────────────────────────────────────

MIGRATION_CHECKLIST = {
    "database": [
        "ALTER agents: add short_id, agent_type, wallet_address, wallet_verified, zero_score, score_components, fee_violations, weights_version, personal_weights",
        "CREATE trades_enriched", "CREATE rejections", "CREATE collective_reports",
        "CREATE regime_periods", "CREATE discoveries", "CREATE observations",
        "CREATE cci_history (TimescaleDB)", "CREATE score_history",
        "CREATE fee_ledger", "CREATE operator_hwm", "CREATE clusters",
        "CREATE configs", "CREATE proofs", "CREATE messages",
        "CREATE api_keys", "CREATE operator_events",
        "UPDATE RLS policies on all new tables",
    ],
    "api_routes": [
        "/api/agents/challenge", "/api/agents/heartbeat", "/api/agents/decision",
        "/api/agents/trade", "/api/intelligence/sync", "/api/intelligence/report",
        "/api/intelligence/feed", "/api/reasoning/evaluate", "/api/score",
        "/api/cci", "/api/cron/* (5 endpoints)", "pg_cron jobs (5 scheduled)",
    ],
    "signal_migration": [
        "SmartProvider → DEFAULT signal source",
        "ENVY → FALLBACK (if SmartProvider fails)",
        "Config: signal_source: smart | envy | both",
        "After 30 days comparison: drop ENVY",
    ],
    "cli_commands": [
        "KEEP: start, stop, status, init",
        "ADD: 23 new commands (think, replay, score, battle, etc.)",
        "No backward compatibility breaks",
    ],
}


# ─── GAP 10: TEST PLAN ───────────────────────────────────────────────────────

TEST_PLAN = {
    "unit": {
        "count": 58,
        "categories": [
            ("indicators", 11), ("regime_classifier", 13), ("conviction_sizer", 10),
            ("conviction_adjustments", 5), ("hwm_fee", 10), ("entry_snapshot", 4),
            ("mae_mfe_tracker", 5),
        ],
    },
    "integration": {
        "count": 5,
        "categories": [
            ("full_evaluation_cycle", 1), ("trade_lifecycle", 1),
            ("fee_lifecycle", 1), ("intelligence_sync", 1), ("regime_period", 1),
        ],
    },
    "system": {
        "count": 3,
        "categories": [
            ("full_loop", 1), ("paper_24h", 1), ("multi_agent", 1),
        ],
    },
    "chaos": {
        "count": 3,
        "categories": [
            ("server_unreachable_5min", 1), ("hl_api_errors", 1), ("realtime_disconnect", 1),
        ],
    },
    "performance": {
        "count": 4,
        "categories": [
            ("collective_100k_trades", 1), ("discovery_4000_combos", 1),
            ("cci_8coins_500evals", 1), ("eval_28steps_per_coin", 1),
        ],
    },
}


# ─── GAP 6: 28-STEP EVALUATION SUMMARY ───────────────────────────────────────

EVALUATION_STEPS = [
    # Phase A: New entries (1-21)
    "1. Fetch market data",
    "2. Compute regime (Hurst + DFA → 13 states)",
    "3. Update regime period manager",
    "4. Record consensus velocity",
    "5. Compute indicators (11 indicators)",
    "6. Compute consensus (weighted vote)",
    "7. Record velocity delta",
    "8. Check regime memory (quality multiplier)",
    "9. Check discovered rules (bonus/reject)",
    "10. Apply consensus velocity adjustment",
    "11. Apply funding adjustment (earn/pay)",
    "12. Apply regime age discount (fresh → old)",
    "13. Apply rejection streak adjustment",
    "14. Apply ATR volatility adjustment",
    "15. Correlation check (block at 2.5x exposure)",
    "16. Network negotiation (crowded → delay)",
    "17. Compute final size (8-25% of equity)",
    "18. Execute entry on HL",
    "19. Create + persist entry snapshot",
    "20. Start MAE/MFE tracker",
    "21. Report decision to server",
    # Phase B: Open positions (22-28)
    "22. Fetch market data for held coins",
    "23. Check exit intelligence (trailing/regime/profit)",
    "24. Check predictive immune (5 detectors)",
    "25. Hold lifecycle assessment (phase)",
    "26. Consensus velocity check (prepare exit?)",
    "27. Cluster alerts (async listener)",
    "28. Network events (async listener)",
]


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def create_snapshot(**kwargs) -> EntrySnapshot:
    return EntrySnapshot.capture(**kwargs)

def load_snapshot(trade_id: str) -> EntrySnapshot | None:
    return EntrySnapshot.load_from_disk(trade_id)

def create_tracker_manager() -> TrackerManager:
    return TrackerManager()

def create_regime_manager() -> RegimePeriodManager:
    return RegimePeriodManager()

def create_local_store() -> LocalStore:
    return LocalStore()

def create_resilient_client(base_url="https://getzero.dev") -> ResilientClient:
    return ResilientClient(base_url)

def get_migration_checklist() -> dict:
    return MIGRATION_CHECKLIST

def get_test_plan() -> dict:
    return TEST_PLAN

def get_evaluation_steps() -> list[str]:
    return EVALUATION_STEPS

def get_onboarding(count: int, shown: set) -> str | None:
    return get_onboarding_message(count, shown)

# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
perfect_machine.py — Server-side compute pipelines

7 pipelines:
  P1: Agent → Server (real-time ingestion)
  P2: Server → Dashboard (Supabase Realtime)
  P3: Trade Close → Enrichment (synchronous)
  P4: Collective Learning (daily cron)
  P5: CCI Computation (every 5 min)
  P6: Morning Brief (daily 06:00 UTC)
  P7: Agent ← Intelligence Sync (pull-based)

Database schema: 25 tables, 7 Realtime channels
Compute: pg_cron + Edge Functions (no external infra)
"""

import json
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [machine] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0

STATE_DIR = Path.home() / ".zeroos" / "state"


# ─── P1: AGENT → SERVER INGESTION ────────────────────────────────────────────

class AgentReporter:
    """Agent-side: report events to server."""

    def __init__(self, base_url: str = "https://getzero.dev", agent_id: str = None):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self._queue: list[dict] = []

    def report_heartbeat(self, equity: float, positions: int, trades: int,
                          immune_status: str = "healthy") -> dict:
        return {
            "endpoint": "/api/agents/heartbeat",
            "payload": {
                "agent_id": self.agent_id,
                "equity": equity,
                "positions": positions,
                "total_trades": trades,
                "immune_status": immune_status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

    def report_decision(self, coin: str, direction: str, action: str,
                         regime: str = None, consensus: float = None,
                         votes: dict = None, reason: str = None) -> dict:
        return {
            "endpoint": "/api/agents/decision",
            "payload": {
                "agent_id": self.agent_id,
                "coin": coin, "direction": direction, "action": action,
                "regime": regime, "consensus": consensus,
                "indicator_votes": votes, "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

    def report_trade(self, trade: dict, enrichment: dict = None) -> dict:
        payload = {
            "agent_id": self.agent_id,
            **trade,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if enrichment:
            payload["enrichment"] = enrichment
        return {
            "endpoint": "/api/agents/trade",
            "payload": payload,
        }

    def report_rejection(self, coin: str, regime: str, consensus: float,
                          reason: str, direction: str = None, votes: dict = None) -> dict:
        return {
            "endpoint": "/api/agents/rejection",
            "payload": {
                "agent_id": self.agent_id,
                "coin": coin, "regime": regime, "consensus": consensus,
                "rejection_reason": reason, "direction": direction,
                "indicator_votes": votes,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }


# ─── P3: TRADE ENRICHMENT (synchronous on close) ─────────────────────────────

class TradeEnricher:
    """Compute all enriched fields when a trade closes."""

    def enrich(self, trade: dict, market: dict, tracker: dict,
               immune_log: dict = None, velocity: float = 0,
               streak: int = 0, regime_age: float = 0,
               funding_rate: float = 0, atr_ratio: float = 1.0,
               discovery_match: str = None, regime_context: str = None) -> dict:
        """Full enrichment computation."""
        mfe = tracker.get("mfe_pct", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        capture = pnl_pct / mfe if mfe > 0 else 0

        return {
            "trade_id": trade.get("id"),
            # Regime context
            "entry_regime": trade.get("entry_regime"),
            "entry_hurst": trade.get("entry_hurst"),
            "entry_dfa": trade.get("entry_dfa"),
            "exit_regime": market.get("regime"),
            "exit_hurst": market.get("hurst"),
            "regime_changes_during_hold": tracker.get("regime_changes", 0),
            # Indicator votes
            "indicator_votes_at_entry": trade.get("indicator_votes"),
            "consensus_at_entry": trade.get("consensus"),
            "conviction_at_entry": trade.get("conviction"),
            # Hold behavior
            "hold_seconds": trade.get("hold_seconds", 0),
            "mae_pct": tracker.get("mae_pct", 0),
            "mfe_pct": mfe,
            "mae_minutes_after_entry": tracker.get("mae_minutes", 0),
            "mfe_minutes_after_entry": tracker.get("mfe_minutes", 0),
            "capture_rate": round(capture, 3),
            # Immune
            "immune_checks_during_hold": (immune_log or {}).get("checks", 0),
            "immune_saves_during_hold": (immune_log or {}).get("saves", 0),
            # Funding
            "funding_cost_during_hold": tracker.get("funding_accumulated", 0),
            # Conviction factors
            "consensus_velocity_at_entry": round(velocity, 4),
            "rejection_streak_before_entry": streak,
            "regime_age_at_entry_hours": round(regime_age, 1),
            "funding_rate_at_entry": funding_rate,
            "atr_ratio_at_entry": round(atr_ratio, 3),
            # Discovery
            "discovery_match": discovery_match,
            "regime_memory_context": regime_context,
        }


# ─── P4: COLLECTIVE LEARNING PIPELINE ────────────────────────────────────────

class CollectiveLearner:
    """Retrain weights from collective trade data."""

    def compute_weights(self, reports: list[dict], min_count: int = 1000) -> dict | None:
        if len(reports) < min_count:
            _log(f"insufficient data: {len(reports)} < {min_count}")
            return None

        # Group by regime
        by_regime = {}
        for r in reports:
            regime = r.get("regime", "unknown")
            by_regime.setdefault(regime, []).append(r)

        weights = {}
        for regime, trades in by_regime.items():
            if len(trades) < 30:
                continue
            wins = sum(1 for t in trades if (t.get("pnl_pct") or 0) > 0)
            wr = wins / len(trades)

            # Average capture rate
            captures = [t.get("capture_rate", 0) for t in trades if t.get("capture_rate")]
            avg_capture = _mean(captures) if captures else 0

            weights[regime] = {
                "win_rate": round(wr, 3),
                "sample_size": len(trades),
                "avg_capture": round(avg_capture, 3),
                "weight": round(wr * (1 + avg_capture * 0.5), 3),
            }

        return weights

    def validate_against_thesis(self, learned: dict, thesis: dict) -> dict:
        """Ensure learned weights don't underperform thesis."""
        if not learned or not thesis:
            return {"valid": False, "reason": "missing data"}

        learned_avg = _mean([v.get("weight", 0) for v in learned.values()])
        thesis_avg = _mean([v for v in thesis.values()])

        valid = learned_avg >= thesis_avg * 0.95
        return {
            "valid": valid,
            "learned_avg": round(learned_avg, 3),
            "thesis_avg": round(thesis_avg, 3),
            "improvement": round((learned_avg / max(thesis_avg, 0.01) - 1) * 100, 1),
        }

    def compute_blacklist(self, reports: list[dict], threshold: float = 0.25,
                          min_trades: int = 20) -> list[str]:
        """Coins that consistently lose."""
        by_coin = {}
        for r in reports:
            coin = r.get("coin")
            if not coin:
                continue
            by_coin.setdefault(coin, []).append(r)

        blacklist = []
        for coin, trades in by_coin.items():
            if len(trades) < min_trades:
                continue
            wins = sum(1 for t in trades if (t.get("pnl_pct") or 0) > 0)
            if wins / len(trades) < threshold:
                blacklist.append(coin)

        return blacklist


# ─── P5: CCI COMPUTATION ─────────────────────────────────────────────────────

class CCIComputer:
    """Computed Conviction Index — network-wide consensus per coin."""

    COINS = ["SOL", "ETH", "BTC", "WLD", "AVAX", "TIA", "NEAR", "APT"]

    def compute(self, evaluations: list[dict]) -> dict:
        """Compute CCI from recent evaluations."""
        results = {}

        for coin in self.COINS:
            coin_evals = [e for e in evaluations if e.get("coin") == coin]
            if not coin_evals:
                continue

            weighted_long = 0
            weighted_short = 0
            total_weight = 0

            for ev in coin_evals:
                score = ev.get("agent_score", 5)
                consensus = ev.get("consensus", 0.5)
                weight = score * consensus
                total_weight += weight

                direction = ev.get("direction", "")
                if direction == "long":
                    weighted_long += weight * consensus
                elif direction == "short":
                    weighted_short += weight * consensus

            cci = (weighted_long - weighted_short) / total_weight if total_weight > 0 else 0
            results[coin] = {
                "value": round(cci, 3),
                "direction": "long" if cci > 0.1 else "short" if cci < -0.1 else "neutral",
                "agents": len(coin_evals),
            }

        return results


# ─── P7: INTELLIGENCE SYNC ───────────────────────────────────────────────────

class IntelligenceSync:
    """Agent pulls intelligence from server every 6 hours."""

    def __init__(self):
        self._sync_file = STATE_DIR / "last_sync.json"
        self._last_sync = self._load()

    def needs_sync(self, interval_hours: int = 6) -> bool:
        last = self._last_sync.get("synced_at")
        if not last:
            return True
        try:
            dt = datetime.fromisoformat(last)
            return (datetime.now(timezone.utc) - dt).total_seconds() > interval_hours * 3600
        except Exception:
            return True

    def record_sync(self, version: int, discoveries: int):
        self._last_sync = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "weights_version": version,
            "discoveries": discoveries,
        }
        self._save()

    def build_sync_response(self, weights: dict, blacklist: list,
                             cci: dict, network_stats: dict,
                             lifecycle: dict, discoveries: list) -> dict:
        """Server builds the sync payload."""
        return {
            "weights": weights,
            "weights_version": weights.get("_version", 1),
            "blacklist": blacklist,
            "cci": cci,
            "network": network_stats,
            "lifecycle": lifecycle,
            "discoveries": [d for d in discoveries if d.get("active", True)],
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._sync_file.write_text(json.dumps(self._last_sync))

    def _load(self):
        if self._sync_file.exists():
            try:
                return json.loads(self._sync_file.read_text())
            except Exception:
                pass
        return {}


_sync = IntelligenceSync()


# ─── CRON SCHEDULER ──────────────────────────────────────────────────────────

CRON_SCHEDULE = {
    "every_5min": [
        {"job": "cci_compute", "desc": "CCI computation + broadcast"},
        {"job": "regime_consensus", "desc": "Regime consensus aggregation"},
        {"job": "network_stats", "desc": "Network stats update"},
    ],
    "hourly": [
        {"job": "fee_verify", "desc": "Fee verification (on-chain cross-ref)"},
        {"job": "fee_settle", "desc": "Pending fee settlement"},
        {"job": "correlation_update", "desc": "Correlation matrix update"},
        {"job": "cluster_refresh", "desc": "Cluster detection refresh"},
        {"job": "behavior_analysis", "desc": "Operator behavior analysis"},
    ],
    "daily_0400": [
        {"job": "collective_learn", "desc": "Collective learner (retrain weights)"},
        {"job": "blacklist", "desc": "Blacklist computation"},
        {"job": "discovery", "desc": "Discovery engine"},
        {"job": "rejection_analysis", "desc": "Rejection analysis"},
        {"job": "lifecycle", "desc": "Hold lifecycle computation"},
        {"job": "score_recompute", "desc": "Score recomputation (all agents)"},
        {"job": "backtest", "desc": "Synthetic backtester (rare regimes)"},
        {"job": "regime_archive", "desc": "Regime period archival"},
    ],
    "daily_0600": [
        {"job": "morning_brief", "desc": "Morning brief generation"},
    ],
    "weekly_sunday": [
        {"job": "fee_sweep", "desc": "Fee wallet sweep → treasury"},
        {"job": "clustering_analysis", "desc": "Win/loss clustering analysis"},
        {"job": "graph_recompute", "desc": "Network graph recomputation"},
        {"job": "proof_generation", "desc": "Proof generation for eligible operators"},
    ],
}


# ─── DATABASE SCHEMA GENERATOR ───────────────────────────────────────────────

def generate_schema() -> str:
    """Generate the complete Supabase SQL schema."""
    return """
-- ZERO OS DATABASE SCHEMA
-- 25 tables, 7 Realtime channels, pg_cron jobs

-- ═══ CORE ═══

CREATE TABLE operators (
  id uuid PRIMARY KEY REFERENCES auth.users(id),
  display_name text UNIQUE NOT NULL,
  wallet_address text,
  wallet_verified boolean DEFAULT false,
  tier text DEFAULT 'free',
  created_at timestamptz DEFAULT now(),
  referral_code text UNIQUE,
  referred_by uuid REFERENCES operators(id),
  settings jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE agents (
  id uuid PRIMARY KEY,
  operator_id uuid REFERENCES operators(id) NOT NULL,
  short_id text UNIQUE NOT NULL,
  agent_type text DEFAULT 'zeroos_cli',
  preset text NOT NULL,
  mode text DEFAULT 'paper',
  status text DEFAULT 'active',
  wallet_address text,
  wallet_verified boolean DEFAULT false,
  config jsonb DEFAULT '{}'::jsonb,
  current_equity float,
  current_positions int DEFAULT 0,
  total_trades int DEFAULT 0,
  last_heartbeat timestamptz,
  immune_status text DEFAULT 'healthy',
  uptime_seconds bigint DEFAULT 0,
  zero_score float,
  score_components jsonb,
  score_updated_at timestamptz,
  registered_at timestamptz DEFAULT now(),
  deactivated_at timestamptz,
  fee_violations int DEFAULT 0,
  weights_version int DEFAULT 0,
  personal_weights jsonb
);

CREATE INDEX idx_agents_operator ON agents(operator_id);
CREATE INDEX idx_agents_short_id ON agents(short_id);
CREATE INDEX idx_agents_status ON agents(status);

-- ═══ TRADES ═══

CREATE TABLE trades (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid REFERENCES agents(id) NOT NULL,
  coin text NOT NULL,
  direction text NOT NULL,
  entry_price float NOT NULL,
  exit_price float,
  size_usd float NOT NULL,
  size_coins float NOT NULL,
  pnl float,
  pnl_pct float,
  stop_price float,
  status text DEFAULT 'open',
  exit_reason text,
  entered_at timestamptz NOT NULL,
  closed_at timestamptz,
  zero_fee float DEFAULT 0,
  fee_tx_hash text
);

CREATE INDEX idx_trades_agent ON trades(agent_id);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_entered ON trades(entered_at);

CREATE TABLE trades_enriched (
  trade_id uuid PRIMARY KEY REFERENCES trades(id),
  entry_regime text, entry_hurst float, entry_dfa float,
  exit_regime text, exit_hurst float,
  regime_changes_during_hold int DEFAULT 0,
  regime_shift_time timestamptz,
  indicator_votes_at_entry jsonb,
  consensus_at_entry float, conviction_at_entry float,
  hold_seconds int,
  mae_pct float, mfe_pct float,
  mae_minutes_after_entry int, mfe_minutes_after_entry int,
  capture_rate float,
  immune_checks_during_hold int DEFAULT 0,
  immune_saves_during_hold int DEFAULT 0,
  funding_cost_during_hold float DEFAULT 0,
  consensus_velocity_at_entry float,
  rejection_streak_before_entry int,
  regime_age_at_entry_hours float,
  funding_rate_at_entry float,
  atr_ratio_at_entry float,
  discovery_match text,
  regime_memory_context text
);

CREATE INDEX idx_trades_enriched_regime ON trades_enriched(entry_regime);

-- ═══ DECISIONS ═══

CREATE TABLE decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid REFERENCES agents(id) NOT NULL,
  coin text NOT NULL,
  direction text,
  action text NOT NULL,
  reason text,
  regime text,
  consensus float,
  indicator_votes jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_decisions_agent ON decisions(agent_id);
CREATE INDEX idx_decisions_created ON decisions(created_at);

CREATE TABLE rejections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid REFERENCES agents(id) NOT NULL,
  coin text NOT NULL,
  direction text,
  regime text,
  consensus float,
  indicator_votes jsonb,
  rejection_reason text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_rejections_created ON rejections(created_at);
CREATE INDEX idx_rejections_coin ON rejections(coin);

-- ═══ INTELLIGENCE ═══

CREATE TABLE collective_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  coin text NOT NULL, direction text NOT NULL,
  event_type text NOT NULL,
  regime text, hurst float,
  indicator_votes jsonb,
  consensus_pct float, pnl_pct float,
  hold_hours float,
  mae_pct float, mfe_pct float,
  capture_rate float, exit_reason text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_collective_regime ON collective_reports(regime);
CREATE INDEX idx_collective_created ON collective_reports(created_at);

CREATE TABLE regime_periods (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  coin text NOT NULL, regime text NOT NULL,
  start_time timestamptz NOT NULL,
  end_time timestamptz,
  duration_hours float,
  hurst_avg float, transition_to text,
  trade_count int DEFAULT 0,
  wins int DEFAULT 0, total_pnl_pct float DEFAULT 0,
  UNIQUE(coin, start_time)
);

CREATE INDEX idx_regime_periods_coin ON regime_periods(coin, regime);

CREATE TABLE discoveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conditions jsonb NOT NULL, direction text NOT NULL,
  train_wr float NOT NULL, validation_wr float NOT NULL,
  sample_size int NOT NULL, p_value float NOT NULL,
  description text, version int NOT NULL,
  active boolean DEFAULT true,
  discovered_at timestamptz DEFAULT now()
);

CREATE TABLE observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type text NOT NULL, title text NOT NULL,
  detail text, significance float,
  actionable boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE cci_history (
  time timestamptz NOT NULL,
  coin text NOT NULL,
  value float NOT NULL,
  direction text,
  agents_count int,
  regime_consensus text
);

-- TimescaleDB hypertable
-- SELECT create_hypertable('cci_history', 'time');
CREATE INDEX idx_cci_coin ON cci_history(coin, time DESC);

-- ═══ SOCIAL ═══

CREATE TABLE score_history (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid REFERENCES agents(id),
  operator_id uuid REFERENCES operators(id),
  score float NOT NULL, components jsonb NOT NULL,
  trade_count int,
  recorded_at timestamptz DEFAULT now()
);

CREATE INDEX idx_score_history ON score_history(agent_id, recorded_at);

CREATE TABLE achievements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_id uuid REFERENCES operators(id) NOT NULL,
  agent_id uuid REFERENCES agents(id),
  achievement_key text NOT NULL,
  earned_at timestamptz DEFAULT now(),
  UNIQUE(operator_id, achievement_key)
);

CREATE TABLE rivalries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_a uuid REFERENCES operators(id) NOT NULL,
  operator_b uuid REFERENCES operators(id) NOT NULL,
  created_at timestamptz DEFAULT now(),
  status text DEFAULT 'active',
  UNIQUE(operator_a, operator_b)
);

CREATE TABLE clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL, dominant_coin text,
  dominant_regime text, members uuid[] NOT NULL,
  similarity float,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE configs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  author_id uuid REFERENCES operators(id) NOT NULL,
  name text UNIQUE NOT NULL,
  base_preset text NOT NULL,
  parameters jsonb NOT NULL,
  users_count int DEFAULT 0,
  avg_pnl_30d float,
  published_at timestamptz DEFAULT now()
);

CREATE TABLE proofs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_id uuid REFERENCES operators(id) NOT NULL,
  type text NOT NULL, data jsonb NOT NULL,
  signature text NOT NULL, verify_url text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rivalry_id uuid REFERENCES rivalries(id) NOT NULL,
  sender_id uuid REFERENCES operators(id) NOT NULL,
  content text NOT NULL CHECK (length(content) <= 280),
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_messages_rivalry ON messages(rivalry_id, created_at);

-- ═══ FINANCIAL ═══

CREATE TABLE fee_ledger (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid REFERENCES agents(id) NOT NULL,
  trade_id uuid REFERENCES trades(id),
  gross_pnl float NOT NULL,
  fee_amount float NOT NULL,
  fee_rate float DEFAULT 0.10,
  tx_hash text,
  status text DEFAULT 'pending',
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_fee_ledger_agent ON fee_ledger(agent_id);
CREATE INDEX idx_fee_ledger_status ON fee_ledger(status);

CREATE TABLE operator_hwm (
  agent_id uuid PRIMARY KEY REFERENCES agents(id),
  high_water_mark float DEFAULT 0,
  cumulative_pnl float DEFAULT 0,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner text NOT NULL,
  key_hash text UNIQUE NOT NULL,
  tier text NOT NULL,
  requests_today int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  last_used_at timestamptz
);

-- ═══ BEHAVIOR ═══

CREATE TABLE operator_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_id uuid REFERENCES operators(id) NOT NULL,
  event_type text NOT NULL,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_op_events ON operator_events(operator_id, created_at);
"""


# ─── REALTIME CHANNELS ───────────────────────────────────────────────────────

REALTIME_CHANNELS = {
    "decisions:{operator_id}": "New decisions for operator's agents",
    "trades:{operator_id}": "Trade opens/closes with equity updates",
    "immune:{operator_id}": "Immune checks and saves",
    "regime_alerts": "Network-wide regime shift alerts",
    "cluster_alerts:{agent_id}": "Per-agent cluster notifications",
    "cci_updates": "CCI values per coin (every 5 min)",
    "network_events": "Agent count, trade count, intelligence updates",
}


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def create_reporter(base_url="https://getzero.dev", agent_id=None):
    """Create an agent reporter."""
    return AgentReporter(base_url, agent_id)

def create_enricher():
    """Create a trade enricher."""
    return TradeEnricher()

def create_learner():
    """Create a collective learner."""
    return CollectiveLearner()

def create_cci():
    """Create a CCI computer."""
    return CCIComputer()

def needs_sync(hours=6):
    """Check if intelligence sync needed."""
    return _sync.needs_sync(hours)

def record_sync(version, discoveries):
    """Record sync completion."""
    _sync.record_sync(version, discoveries)

def get_cron_schedule():
    """Return the full cron schedule."""
    return CRON_SCHEDULE

def get_schema_sql():
    """Return the complete database schema SQL."""
    return generate_schema()

def get_realtime_channels():
    """Return Realtime channel definitions."""
    return REALTIME_CHANNELS

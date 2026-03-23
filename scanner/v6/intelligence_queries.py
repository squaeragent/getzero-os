#!/usr/bin/env python3
"""
Intelligence Query Library

Pre-built queries for the improvement engine.
Each query returns structured results that become improvement proposals.

Usage:
    from intelligence_queries import IntelligenceEngine
    engine = IntelligenceEngine()
    results = engine.win_rate_by_regime()
"""

import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("intelligence_queries")


def _get_client():
    """Get Supabase client."""
    try:
        from supabase import create_client
        env_file = Path.home() / ".config" / "openclaw" / ".env"
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "SUPABASE_URL": url = v
                    elif k == "SUPABASE_SERVICE_KEY": key = v
        if url and key:
            return create_client(url, key)
    except Exception as e:
        log.warning(f"Intelligence: Supabase unavailable: {e}")
    return None


class IntelligenceEngine:
    """Runs analytical queries against enriched trading data."""

    QUERIES = {
        "win_rate_by_regime": """
            SELECT
                entry_regime as regime,
                COUNT(*) as trades,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                AVG(pnl) as avg_pnl,
                SUM(pnl) as total_pnl,
                AVG(hold_duration_seconds) / 3600.0 as avg_hold_hours
            FROM trades_enriched
            WHERE status = 'closed' AND entry_regime IS NOT NULL
            GROUP BY entry_regime
            ORDER BY win_rate DESC
        """,

        "time_of_day_performance": """
            SELECT
                entry_utc_hour / 6 as time_bucket,
                CASE
                    WHEN entry_utc_hour < 6 THEN 'late_night'
                    WHEN entry_utc_hour < 12 THEN 'morning'
                    WHEN entry_utc_hour < 18 THEN 'afternoon'
                    ELSE 'evening'
                END as period,
                COUNT(*) as trades,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                SUM(pnl) as total_pnl
            FROM trades_enriched
            WHERE status = 'closed' AND entry_utc_hour IS NOT NULL
            GROUP BY 1, 2
            ORDER BY total_pnl DESC
        """,

        "trade_efficiency": """
            SELECT
                coin,
                COUNT(*) as trades,
                AVG(efficiency) as avg_efficiency,
                AVG(max_favorable_excursion_pct) as avg_mfe_pct,
                AVG(pnl_pct) as avg_captured_pct,
                AVG(max_adverse_excursion_pct) as avg_mae_pct
            FROM trades_enriched
            WHERE status = 'closed' AND was_profitable = true
                AND efficiency IS NOT NULL
            GROUP BY coin
            HAVING COUNT(*) >= 3
            ORDER BY avg_efficiency
        """,

        "regime_transition_danger": """
            SELECT
                entry_regime,
                exit_regime,
                COUNT(*) as trades,
                AVG(pnl) as avg_pnl,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate
            FROM trades_enriched
            WHERE status = 'closed'
                AND entry_regime IS NOT NULL
                AND exit_regime IS NOT NULL
                AND entry_regime != exit_regime
            GROUP BY entry_regime, exit_regime
            HAVING COUNT(*) >= 3
            ORDER BY avg_pnl
        """,

        "immune_effectiveness": """
            SELECT
                CASE WHEN immune_alerts_during_hold > 0 THEN 'alerted' ELSE 'clean' END as category,
                COUNT(*) as trades,
                AVG(pnl) as avg_pnl,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                AVG(max_adverse_excursion_pct) as avg_mae_pct
            FROM trades_enriched
            WHERE status = 'closed'
                AND immune_checks_during_hold IS NOT NULL
            GROUP BY 1
        """,

        "funding_cost_impact": """
            SELECT
                coin,
                COUNT(*) as trades,
                SUM(pnl) as gross_pnl,
                SUM(funding_cost) as total_funding,
                SUM(pnl) - SUM(COALESCE(funding_cost, 0)) as net_pnl,
                AVG(hold_duration_seconds) / 3600.0 as avg_hold_hours
            FROM trades_enriched
            WHERE status = 'closed' AND funding_cost IS NOT NULL
            GROUP BY coin
            HAVING COUNT(*) >= 3
            ORDER BY total_funding
        """,

        "signal_mode_performance": """
            SELECT
                entry_signal_mode as mode,
                COUNT(*) as trades,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                AVG(pnl) as avg_pnl,
                SUM(pnl) as total_pnl
            FROM trades_enriched
            WHERE status = 'closed' AND entry_signal_mode IS NOT NULL
            GROUP BY entry_signal_mode
            ORDER BY win_rate DESC
        """,

        "hold_duration_optimization": """
            SELECT
                CASE
                    WHEN hold_duration_seconds < 3600 THEN '<1h'
                    WHEN hold_duration_seconds < 14400 THEN '1-4h'
                    WHEN hold_duration_seconds < 43200 THEN '4-12h'
                    WHEN hold_duration_seconds < 86400 THEN '12-24h'
                    ELSE '>24h'
                END as hold_bucket,
                COUNT(*) as trades,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                AVG(pnl) as avg_pnl,
                AVG(efficiency) as avg_efficiency
            FROM trades_enriched
            WHERE status = 'closed' AND hold_duration_seconds IS NOT NULL
            GROUP BY 1
            ORDER BY avg_pnl DESC
        """,

        "gate_rejection_analysis": """
            SELECT
                gate_that_rejected as gate,
                COUNT(*) as rejections,
                AVG(assembled_sharpe) as avg_sharpe_at_rejection,
                AVG(price) as avg_price,
                COUNT(DISTINCT coin) as unique_coins
            FROM decisions_enriched
            WHERE decision = 'rejected' AND gate_that_rejected IS NOT NULL
            GROUP BY gate_that_rejected
            ORDER BY rejections DESC
        """,

        "coin_performance_summary": """
            SELECT
                coin,
                COUNT(*) as trades,
                SUM(CASE WHEN was_profitable THEN 1 ELSE 0 END) as wins,
                AVG(CASE WHEN was_profitable THEN 1.0 ELSE 0.0 END) as win_rate,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl,
                AVG(hold_duration_seconds) / 3600.0 as avg_hold_hours,
                AVG(efficiency) as avg_efficiency
            FROM trades_enriched
            WHERE status = 'closed'
            GROUP BY coin
            HAVING COUNT(*) >= 2
            ORDER BY total_pnl DESC
        """,
    }

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def run_query(self, query_name: str) -> list[dict] | None:
        """Run a named query and return results."""
        if query_name not in self.QUERIES:
            log.error(f"Unknown query: {query_name}")
            return None

        try:
            # Use Supabase Management API for raw SQL
            import urllib.request
            import urllib.error

            env_file = Path.home() / ".config" / "openclaw" / ".env"
            access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
            if not access_token:
                if env_file.exists():
                    for line in env_file.read_text().splitlines():
                        if line.strip().startswith("SUPABASE_ACCESS_TOKEN"):
                            access_token = line.split("=", 1)[1].strip().strip('"').strip("'")

            if not access_token:
                log.warning("No SUPABASE_ACCESS_TOKEN for raw SQL queries")
                return None

            sql = self.QUERIES[query_name]
            req = urllib.request.Request(
                "https://api.supabase.com/v1/projects/fzzotmxxrcnmrqtmsesi/database/query",
                data=json.dumps({"query": sql}).encode(),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log.warning(f"Intelligence query failed ({query_name}): {e}")
            return None

    def run_all(self) -> dict[str, list[dict] | None]:
        """Run all queries and return results keyed by query name."""
        results = {}
        for name in self.QUERIES:
            results[name] = self.run_query(name)
        return results

    def generate_insights(self) -> list[dict]:
        """Run all queries and generate actionable insights."""
        results = self.run_all()
        insights = []

        # Analyze regime performance
        regime_data = results.get("win_rate_by_regime")
        if regime_data:
            for row in regime_data:
                if row.get("win_rate", 0) < 0.35 and row.get("trades", 0) >= 5:
                    insights.append({
                        "dimension": "trading",
                        "pattern": f"Low win rate in {row['regime']} regime",
                        "evidence": row,
                        "confidence": "high" if row["trades"] >= 10 else "medium",
                        "impact": "high",
                        "proposal": f"Consider reducing or eliminating trades in {row['regime']} regime (WR: {row['win_rate']:.0%}, {row['trades']} trades)",
                    })

        # Analyze regime transitions
        transition_data = results.get("regime_transition_danger")
        if transition_data:
            for row in transition_data:
                if row.get("win_rate", 0) < 0.30 and row.get("trades", 0) >= 3:
                    insights.append({
                        "dimension": "trading",
                        "pattern": f"Dangerous regime transition: {row['entry_regime']} → {row['exit_regime']}",
                        "evidence": row,
                        "confidence": "medium",
                        "impact": "high",
                        "proposal": f"Add exit trigger when regime transitions from {row['entry_regime']} to {row['exit_regime']}",
                    })

        # Analyze trade efficiency
        efficiency_data = results.get("trade_efficiency")
        if efficiency_data:
            for row in efficiency_data:
                if row.get("avg_efficiency", 1) < 0.40 and row.get("trades", 0) >= 3:
                    insights.append({
                        "dimension": "trading",
                        "pattern": f"Low trade efficiency on {row['coin']}",
                        "evidence": row,
                        "confidence": "medium",
                        "impact": "medium",
                        "proposal": f"Adjust exit logic for {row['coin']}: capturing only {row['avg_efficiency']:.0%} of favorable moves (MFE: {row['avg_mfe_pct']:.1f}%)",
                    })

        return insights


if __name__ == "__main__":
    engine = IntelligenceEngine()
    insights = engine.generate_insights()
    print(f"\n{'='*60}")
    print(f"INTELLIGENCE REPORT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")
    if not insights:
        print("No data yet. Enriched tables need population.")
    for i, insight in enumerate(insights, 1):
        print(f"{i}. [{insight['impact'].upper()}] {insight['pattern']}")
        print(f"   Confidence: {insight['confidence']}")
        if insight.get("proposal"):
            print(f"   → {insight['proposal']}")
        print()

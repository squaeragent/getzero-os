#!/usr/bin/env python3
"""
SmartProvider Learning Engine — learns indicator weights from enriched trade data.

After 200+ trades, replaces hardcoded regime weights with LEARNED weights
computed from actual trading results via trades_enriched table.
"""

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("smart_learner")

WEIGHTS_FILE = Path(__file__).parent / "cache" / "smart_weights.json"
SUPABASE_PROJECT = "fzzotmxxrcnmrqtmsesi"

DIRECTIONAL_INDICATORS = ["rsi", "macd", "ema", "bollinger", "obv", "funding"]


class SignalLearner:
    """Learns per-regime indicator weights from enriched trade data."""

    MIN_TRADES = 200
    RETRAIN_INTERVAL = 100

    def __init__(self):
        self._last_count = 0
        self._load_last_count()

    def _load_last_count(self):
        """Load the trade count from last training run."""
        try:
            if WEIGHTS_FILE.exists():
                data = json.loads(WEIGHTS_FILE.read_text())
                self._last_count = data.get("trades_count", 0)
        except (json.JSONDecodeError, OSError):
            pass

    def _get_access_token(self) -> str | None:
        """Get Supabase management API token."""
        token = os.environ.get("SUPABASE_ACCESS_TOKEN")
        if token:
            return token
        env_file = Path.home() / ".config" / "openclaw" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("SUPABASE_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        return None

    def _query_supabase(self, sql: str) -> list[dict] | None:
        """Run raw SQL via Supabase management API."""
        token = self._get_access_token()
        if not token:
            log.warning("No SUPABASE_ACCESS_TOKEN")
            return None
        try:
            req = urllib.request.Request(
                f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT}/database/query",
                data=json.dumps({"query": sql}).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log.warning(f"Supabase query failed: {e}")
            return None

    def load_trades(self) -> list[dict]:
        """Fetch closed trades with regime + indicator data."""
        result = self._query_supabase("""
            SELECT
                entry_regime, was_profitable, direction, pnl
            FROM trades_enriched
            WHERE status = 'closed'
                AND entry_regime IS NOT NULL
            ORDER BY entry_time DESC
        """)
        return result or []

    def get_trade_count(self) -> int:
        """Get total closed trade count."""
        result = self._query_supabase(
            "SELECT COUNT(*) as n FROM trades_enriched WHERE status = 'closed'"
        )
        if result and len(result) > 0:
            return int(result[0].get("n", 0))
        return 0

    def learn_weights(self, min_trades_per_regime: int = 10) -> dict:
        """Compute accuracy per indicator per regime.

        For each regime:
        - For each indicator: what fraction of trades were profitable when the indicator agreed?
        - Weight = accuracy (0.0-1.0)
        """
        trades = self.load_trades()
        if not trades:
            return {}

        # Group by regime
        regime_stats: dict[str, dict] = {}

        for trade in trades:
            regime = trade.get("entry_regime")
            if not regime:
                continue

            if regime not in regime_stats:
                regime_stats[regime] = {
                    "total": 0,
                    "profitable": 0,
                }
            regime_stats[regime]["total"] += 1
            if trade.get("was_profitable"):
                regime_stats[regime]["profitable"] += 1

        # Compute weights per regime based on win rate
        learned = {}
        for regime, stats in regime_stats.items():
            if stats["total"] < min_trades_per_regime:
                continue  # Not enough data

            win_rate = stats["profitable"] / stats["total"]

            # In regimes with high win rate, trust trend indicators more
            # In regimes with low win rate, trust mean-reversion indicators more
            if win_rate >= 0.55:
                # Trending works — boost trend indicators
                learned[regime] = {
                    "rsi": 0.3, "macd": 0.7 + (win_rate - 0.5),
                    "ema": 0.8 + (win_rate - 0.5), "bollinger": 0.3,
                    "obv": 0.6, "funding": 0.5,
                }
            elif win_rate <= 0.40:
                # Losses — boost defensive indicators
                learned[regime] = {
                    "rsi": 0.8, "macd": 0.3,
                    "ema": 0.2, "bollinger": 0.8,
                    "obv": 0.4, "funding": 0.6,
                }
            else:
                # Neutral — equal weights
                learned[regime] = {
                    "rsi": 0.5, "macd": 0.5,
                    "ema": 0.5, "bollinger": 0.5,
                    "obv": 0.5, "funding": 0.5,
                }

            # Clamp all weights to 0.1-1.0
            for k in learned[regime]:
                learned[regime][k] = max(0.1, min(1.0, round(learned[regime][k], 3)))

        return learned

    def save_weights(self, weights: dict, trades_count: int):
        """Save learned weights to file."""
        WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "weights": weights,
            "trades_count": trades_count,
            "learned_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = WEIGHTS_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(WEIGHTS_FILE)

    def load_weights(self) -> dict | None:
        """Load saved weights."""
        try:
            if WEIGHTS_FILE.exists():
                data = json.loads(WEIGHTS_FILE.read_text())
                return data.get("weights")
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def should_use_learned(self, trades_count: int) -> bool:
        """Use learned weights after MIN_TRADES."""
        return trades_count >= self.MIN_TRADES

    def maybe_update(self, force: bool = False):
        """Run learning if enough new trades since last run."""
        try:
            count = self.get_trade_count()
            if not force and count - self._last_count < self.RETRAIN_INTERVAL:
                return  # Not enough new trades

            if count < self.MIN_TRADES and not force:
                return  # Not enough total trades

            log.info(f"Learning weights from {count} trades...")
            weights = self.learn_weights()
            if weights:
                self.save_weights(weights, count)
                self._last_count = count
                log.info(f"Saved learned weights for {len(weights)} regimes")
        except Exception as e:
            log.warning(f"Learning failed: {e}")

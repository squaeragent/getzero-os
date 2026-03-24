# Copyright (c) 2026 zero. All rights reserved.
# This file is proprietary and confidential.
# Unauthorized copying, modification, or distribution is prohibited.

"""
hidden_gems.py — 6 gems + 3 low-hanging fruits

GEM 1: Rejection Database & Analysis
GEM 2: Hold Lifecycle Intelligence
GEM 3: Consensus Velocity
GEM 4: Funding-Adjusted Conviction
GEM 5: Operator Behavior Tracking
GEM 6: Regime-Age Conviction Discount
LHF 1: Rejection Streak → Conviction
LHF 2: ATR Ratio → Conviction
LHF 3: Win/Loss Clustering Test
"""

import json
import time
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, deque

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [gems] [{ts}] {msg}", flush=True)

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0

def _median(vals):
    if not vals: return 0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

STATE_DIR = Path.home() / ".zeroos" / "state"


# ─── GEM 1: REJECTION DATABASE ──────────────────────────────────────────────

class RejectionAnalyzer:
    """Analyzes rejection patterns for intelligence."""

    def __init__(self):
        self._rejections_file = STATE_DIR / "rejections.json"
        self._rejections: list[dict] = []
        self._load()

    def record(self, coin: str, regime: str, consensus: float,
               reason: str, direction: str = "neutral",
               indicator_votes: dict = None, sample_rate: float = 0.20):
        """Record a rejection (sampled)."""
        if random.random() > sample_rate:
            return
        self._rejections.append({
            "coin": coin, "regime": regime, "consensus": round(consensus, 3),
            "reason": reason, "direction": direction,
            "votes": indicator_votes,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self._rejections = self._rejections[-50000:]  # Keep last 50K
        self._save()

    def analyze(self, entries: list[dict] = None) -> list[str]:
        """Generate insights from rejection data."""
        if not self._rejections:
            return ["no rejection data yet"]

        insights = []

        # 1. Always-rejected coins
        coin_counts = Counter(r["coin"] for r in self._rejections)
        entry_coins = Counter(e.get("coin") for e in (entries or []))
        for coin, rej_count in coin_counts.most_common():
            entry_count = entry_coins.get(coin, 0)
            total = rej_count + entry_count
            if total > 20 and rej_count / total > 0.995:
                insights.append(f"{coin}: {rej_count/total:.1%} rejection rate. consider removing.")

        # 2. "Almost passed" signals (consensus 55-59%)
        almost = [r for r in self._rejections if r.get("consensus", 0) >= 0.55 and r.get("consensus", 0) < 0.60]
        if almost:
            almost_coins = Counter(r["coin"] for r in almost)
            top = almost_coins.most_common(3)
            insights.append(f"almost passed: {', '.join(f'{c}({n}×)' for c, n in top)}")

        # 3. Rejection reasons
        reasons = Counter(r.get("reason", "unknown") for r in self._rejections)
        top_reason = reasons.most_common(1)[0] if reasons else ("none", 0)
        insights.append(f"top rejection reason: {top_reason[0]} ({top_reason[1]}×)")

        # 4. Regime dead zones
        regime_counts = Counter(r.get("regime", "unknown") for r in self._rejections)
        for regime, count in regime_counts.most_common():
            regime_entries = sum(1 for e in (entries or []) if e.get("regime") == regime)
            total = count + regime_entries
            if total > 10 and count / total > 0.99:
                insights.append(f"regime dead zone: {regime} ({count/total:.1%} rejection)")

        return insights

    def get_almost_passed(self, coin: str = None) -> list[dict]:
        """Get recent 'almost passed' rejections."""
        almost = [r for r in self._rejections
                  if r.get("consensus", 0) >= 0.55 and r.get("consensus", 0) < 0.60]
        if coin:
            almost = [r for r in almost if r.get("coin") == coin]
        return almost[-20:]

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._rejections_file.write_text(json.dumps(self._rejections[-10000:]))

    def _load(self):
        if self._rejections_file.exists():
            try:
                self._rejections = json.loads(self._rejections_file.read_text())
            except Exception:
                pass


_rejection_db = RejectionAnalyzer()


# ─── GEM 2: HOLD LIFECYCLE INTELLIGENCE ──────────────────────────────────────

class HoldLifecycle:
    """Analyze trade lifecycle patterns from MAE/MFE timing."""

    def compute_lifecycle(self, trades: list[dict], regime: str = None) -> dict:
        """Compute lifecycle profile from enriched trades."""
        if regime:
            trades = [t for t in trades if t.get("entry_regime") == regime]

        winners = [t for t in trades if t.get("pnl_pct", 0) > 0]
        losers = [t for t in trades if t.get("pnl_pct", 0) <= 0]

        winner_mae_timing = [t.get("mae_minutes", 0) for t in winners if t.get("mae_minutes")]
        winner_mfe_timing = [t.get("mfe_minutes", 0) for t in winners if t.get("mfe_minutes")]
        loser_mae_timing = [t.get("mae_minutes", 0) for t in losers if t.get("mae_minutes")]

        # Early MAE = likely winner
        early_mae = [t for t in trades if t.get("mae_minutes", 999) <= 15]
        early_mae_wr = len([t for t in early_mae if t.get("pnl_pct", 0) > 0]) / max(len(early_mae), 1)

        late_mae = [t for t in trades if t.get("mae_minutes", 0) > 60]
        late_mae_wr = len([t for t in late_mae if t.get("pnl_pct", 0) > 0]) / max(len(late_mae), 1)

        return {
            "regime": regime,
            "sample_size": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "winner_mae_median_min": round(_median(winner_mae_timing), 1),
            "winner_mfe_median_min": round(_median(winner_mfe_timing), 1),
            "loser_mae_median_min": round(_median(loser_mae_timing), 1),
            "early_mae_wr": round(early_mae_wr, 3),
            "late_mae_wr": round(late_mae_wr, 3),
            "insight": "early MAE predicts winners" if early_mae_wr > late_mae_wr + 0.05
                       else "no clear timing signal",
        }

    def assess_position(self, hold_minutes: float, mae_minutes: float,
                        lifecycle: dict) -> dict:
        """Where is this position in the typical lifecycle?"""
        winner_mae = lifecycle.get("winner_mae_median_min", 15)
        winner_mfe = lifecycle.get("winner_mfe_median_min", 120)

        if hold_minutes < winner_mae:
            phase = "early"
            note = "worst moment typically hasn't happened yet"
        elif hold_minutes < winner_mfe * 0.5:
            phase = "developing"
            note = "trade is developing. MAE behind, MFE ahead."
        elif hold_minutes < winner_mfe:
            phase = "growth"
            note = "approaching typical peak. watch for exit signals."
        else:
            phase = "extended"
            note = "past typical MFE timing. consider taking profit."

        confidence = "high" if mae_minutes <= 15 else "moderate" if mae_minutes <= 60 else "low"

        return {
            "phase": phase,
            "note": note,
            "hold_confidence": confidence,
            "hold_minutes": hold_minutes,
        }


_lifecycle = HoldLifecycle()


# ─── GEM 3: CONSENSUS VELOCITY ──────────────────────────────────────────────

class ConsensusVelocity:
    """Track consensus changes over time per coin."""

    def __init__(self):
        self._history: dict[str, deque] = {}

    def record(self, coin: str, consensus: float, direction: str):
        if coin not in self._history:
            self._history[coin] = deque(maxlen=12)
        self._history[coin].append((time.time(), consensus, direction))

    def get_velocity(self, coin: str) -> float:
        """Consensus change per cycle. Positive = strengthening."""
        history = self._history.get(coin)
        if not history or len(history) < 3:
            return 0
        recent = [h[1] for h in list(history)[-6:]]
        # Linear regression slope
        n = len(recent)
        x_mean = (n - 1) / 2
        y_mean = _mean(recent)
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0

    def apply_to_conviction(self, coin: str, base_conviction: float) -> float:
        """Adjust conviction based on consensus velocity."""
        velocity = self.get_velocity(coin)
        if velocity > 0.02:
            return base_conviction * 1.10  # rising: +10%
        elif velocity < -0.02:
            return base_conviction * 0.90  # falling: -10%
        return base_conviction

    def should_prepare_exit(self, coin: str) -> bool:
        """Is consensus weakening for a held position?"""
        history = self._history.get(coin)
        if not history or len(history) < 4:
            return False
        recent = [h[1] for h in list(history)[-4:]]
        declining = all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))
        velocity = self.get_velocity(coin)
        return declining and velocity < -0.03


_velocity = ConsensusVelocity()


# ─── GEM 4: FUNDING-ADJUSTED CONVICTION ──────────────────────────────────────

def funding_adjusted_conviction(base_conviction: float, funding_rate: float,
                                 direction: str, expected_hold_hours: float = 8) -> float:
    """Adjust conviction based on funding income/cost."""
    earning = (
        (direction.lower() == "long" and funding_rate < 0) or
        (direction.lower() == "short" and funding_rate > 0)
    )
    impact = abs(funding_rate) * (expected_hold_hours / 8) * 365
    adjustment = min(impact * 0.001, 0.15)

    if earning:
        return base_conviction * (1 + adjustment)
    else:
        return base_conviction * (1 - adjustment)


# ─── GEM 5: OPERATOR BEHAVIOR TRACKING ──────────────────────────────────────

TRACKED_EVENTS = [
    "dashboard_open", "brief_read", "brief_read_full",
    "cli_status", "cli_think", "cli_score",
    "agent_pause", "agent_resume", "agent_override",
    "config_change", "arena_visit", "battle_start",
]


class OperatorTracker:
    """Track operator behavior for product intelligence."""

    def __init__(self):
        self._events_file = STATE_DIR / "operator_events.json"
        self._events: list[dict] = []
        self._load()

    def track(self, event_type: str, details: dict = None):
        if event_type not in TRACKED_EVENTS:
            return
        self._events.append({
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        })
        self._events = self._events[-10000:]
        self._save()

    def get_daily_frequency(self, event_type: str, days: int = 30) -> float:
        relevant = [e for e in self._events if e["type"] == event_type]
        return len(relevant) / max(days, 1)

    def days_since_last_check(self) -> int:
        checks = [e for e in self._events if e["type"] in ("dashboard_open", "cli_status")]
        if not checks:
            return 999
        try:
            last = datetime.fromisoformat(checks[-1]["ts"])
            return (datetime.now(timezone.utc) - last).days
        except Exception:
            return 999

    def check_ghost_protocol(self) -> bool:
        """Achievement: 7 days without checking dashboard."""
        return self.days_since_last_check() >= 7

    def _save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._events_file.write_text(json.dumps(self._events[-5000:]))

    def _load(self):
        if self._events_file.exists():
            try:
                self._events = json.loads(self._events_file.read_text())
            except Exception:
                pass


_tracker = OperatorTracker()


# ─── GEM 6: REGIME-AGE CONVICTION DISCOUNT ──────────────────────────────────

def regime_age_discount(conviction: float, regime_age_hours: float,
                         historical_avg_duration: float) -> float:
    """Discount conviction as regime ages."""
    if historical_avg_duration <= 0:
        return conviction
    age_ratio = regime_age_hours / historical_avg_duration
    if age_ratio < 0.2:
        return conviction * 1.05   # fresh: slight boost
    elif age_ratio < 0.6:
        return conviction          # middle: no change
    elif age_ratio < 1.0:
        return conviction * 0.90   # aging: 10% discount
    else:
        return conviction * 0.75   # old: 25% discount


# ─── LHF 1: REJECTION STREAK → CONVICTION ───────────────────────────────────

def streak_adjusted_conviction(conviction: float, rejection_streak: int) -> float:
    """Long rejection streaks = market cleared = stronger entry."""
    if rejection_streak >= 30:
        return conviction * 1.12  # +12%
    elif rejection_streak >= 15:
        return conviction * 1.06  # +6%
    elif rejection_streak <= 3:
        return conviction * 0.95  # -5% borderline
    return conviction


# ─── LHF 2: ATR RATIO → CONVICTION ──────────────────────────────────────────

def volatility_adjusted_conviction(conviction: float, current_atr: float,
                                    avg_atr_30d: float) -> float:
    """Expanding vol = lower conviction. Contracting = higher."""
    atr_ratio = current_atr / max(avg_atr_30d, 0.0001)
    if atr_ratio > 1.5:
        return conviction * 0.85  # expanding: reduce
    elif atr_ratio < 0.7:
        return conviction * 1.10  # contracting: increase
    return conviction


# ─── LHF 3: WIN/LOSS CLUSTERING TEST ────────────────────────────────────────

def test_clustering(outcomes: list[int]) -> dict:
    """Test if wins/losses cluster beyond random chance. outcomes: list of 1/0."""
    if len(outcomes) < 20:
        return {"clusters": False, "z_score": 0, "recommendation": "insufficient data"}

    runs = 1
    for i in range(1, len(outcomes)):
        if outcomes[i] != outcomes[i - 1]:
            runs += 1

    n1 = sum(outcomes)
    n2 = len(outcomes) - n1
    n = len(outcomes)

    if n1 == 0 or n2 == 0:
        return {"clusters": False, "z_score": 0, "recommendation": "all same outcome"}

    expected = (2 * n1 * n2) / n + 1
    var_num = 2 * n1 * n2 * (2 * n1 * n2 - n)
    var_den = n * n * (n - 1)
    std = math.sqrt(max(var_num / var_den, 0.0001))
    z = (runs - expected) / std

    if z < -2.0:
        return {"clusters": True, "direction": "positive", "z_score": round(z, 2),
                "recommendation": "size down after 2+ losses. size up after 2+ wins."}
    elif z > 2.0:
        return {"clusters": True, "direction": "negative", "z_score": round(z, 2),
                "recommendation": "size up after losses (reversion expected)."}
    return {"clusters": False, "z_score": round(z, 2),
            "recommendation": "no significant clustering. sizing is correct."}


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def record_rejection(coin, regime, consensus, reason, direction="neutral", votes=None):
    """GEM 1."""
    _rejection_db.record(coin, regime, consensus, reason, direction, votes)

def analyze_rejections(entries=None):
    """GEM 1."""
    return _rejection_db.analyze(entries)

def get_almost_passed(coin=None):
    """GEM 1."""
    return _rejection_db.get_almost_passed(coin)

def compute_lifecycle(trades, regime=None):
    """GEM 2."""
    return _lifecycle.compute_lifecycle(trades, regime)

def assess_hold(hold_min, mae_min, lifecycle):
    """GEM 2."""
    return _lifecycle.assess_position(hold_min, mae_min, lifecycle)

def record_consensus(coin, consensus, direction):
    """GEM 3."""
    _velocity.record(coin, consensus, direction)

def get_consensus_velocity(coin):
    """GEM 3."""
    return _velocity.get_velocity(coin)

def velocity_conviction(coin, base):
    """GEM 3."""
    return _velocity.apply_to_conviction(coin, base)

def should_prepare_exit(coin):
    """GEM 3."""
    return _velocity.should_prepare_exit(coin)

def funding_conviction(base, rate, direction, hold_hours=8):
    """GEM 4."""
    return funding_adjusted_conviction(base, rate, direction, hold_hours)

def track_event(event_type, details=None):
    """GEM 5."""
    _tracker.track(event_type, details)

def is_ghost_protocol():
    """GEM 5."""
    return _tracker.check_ghost_protocol()

def regime_discount(conviction, age_hours, avg_duration):
    """GEM 6."""
    return regime_age_discount(conviction, age_hours, avg_duration)

def streak_conviction(conviction, streak):
    """LHF 1."""
    return streak_adjusted_conviction(conviction, streak)

def vol_conviction(conviction, current_atr, avg_atr):
    """LHF 2."""
    return volatility_adjusted_conviction(conviction, current_atr, avg_atr)

def clustering_test(outcomes):
    """LHF 3."""
    return test_clustering(outcomes)

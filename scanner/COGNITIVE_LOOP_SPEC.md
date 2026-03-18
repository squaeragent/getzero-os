# COGNITIVE LOOP SPEC — ZERO OS AGI Architecture

## Current State: Pipeline
```
Envy API → Harvester → Correlation → Execution → Log
           (fixed)      (fixed)       (fixed)     (dead end)
```
No feedback. No learning. No self-modification.

## Target State: Cognitive Loop
```
        ┌─────────────────────────────────────────┐
        │                                         │
   PERCEPTION ──→ COGNITION ──→ ACTION ──→ REFLECTION
        │              ↑                      │
        │              └──────────────────────┘
        │                                     │
        └─────────────────────────────────────┘
```

---

## Layer 1: PERCEPTION

**What it replaces:** regime_agent, funding_agent, spread_monitor, liquidity_agent, cross_timeframe_agent

**What it does:** Builds a real-time WORLD MODEL of the market. Not just "BTC regime is trending." Instead: a structured state object that captures everything the system knows right now.

```python
world_state = {
    "timestamp": "2026-03-18T15:30:00Z",
    "coins": {
        "BTC": {
            "regime": "trending",
            "regime_age_hours": 14.5,
            "regime_transitioning": False,
            "funding": {"rate": 0.0012, "velocity": "stable", "reversal": False},
            "spread": {"mark_oracle_pct": 0.04, "status": "NORMAL"},
            "liquidity": {"score": 94, "tradeable": True, "depth_1pct": 2400000},
            "timeframe": {"fast": "bullish", "slow": "bullish", "pattern": "CONFIRMATION_LONG"},
            "indicators": {"hurst": 0.72, "lyapunov": 1.45, "rsi_4h": 62, ...},
            "narrative": None  # filled by LLM context layer
        },
        ...
    },
    "portfolio": {
        "equity": 115,
        "open_positions": [...],
        "realized_pnl": -0.91,
        "drawdown_pct": 0.8,
        "utilization_pct": 67
    },
    "meta": {
        "cycle_number": 847,
        "hours_since_start": 8.5,
        "total_closed_trades": 9,
        "regime_distribution": {"trending": 20, "chaotic": 10, "stable": 9, "shift": 1}
    }
}
```

**One process, not 6.** Perception runs every 5 minutes, queries all data sources, builds the world state once. Every downstream component reads from this single object.

**LLM Context Layer (new):**
Every 30 minutes, an LLM reads:
- Recent significant indicator changes
- Funding rate anomalies
- Any regime transitions
- Open position P&L trajectories

And produces a SHORT narrative context per coin:
```
"BTC: Trending regime for 14h, but Hurst declining from 0.78 to 0.72
in last 6h. Funding stable. No catalyst for reversal visible. Trend
may be exhausting. Weight long signals lower."
```

This narrative goes into `world_state.coins.BTC.narrative` and is available to Cognition. The LLM doesn't decide. It interprets.

**Cost:** One Ollama call (llama3:70b) every 30 min for 40 coins = ~$0. Local inference. Or batch 40 coins into one prompt.

---

## Layer 2: COGNITION

**What it replaces:** harvester, signal_evolution, correlation_agent

**What it does:** Three sub-processes that form the thinking layer.

### 2A: HYPOTHESIS GENERATOR

Takes world_state + signal cache + memory. Produces HYPOTHESES, not candidates.

```python
hypothesis = {
    "id": "hyp_20260318_153000_BTC_LONG_001",
    "coin": "BTC",
    "direction": "LONG",
    "thesis": "Hurst 0.72 + CONFIRMATION_LONG + funding stable → momentum continuation",
    "anti_thesis": "Hurst declining 6h, regime may be exhausting",
    "signal": "WEIGHTED_TREND_CONFIRMATION_LONG_MH24_Q2",
    "confidence": 0.68,
    "evidence_for": ["hurst > 0.5", "fast+slow bullish", "funding stable"],
    "evidence_against": ["hurst declining", "14h into trend"],
    "similar_past_trades": [
        {"id": "trade_007", "outcome": "win", "pnl_pct": 1.2, "conditions": "similar hurst, same regime"},
        {"id": "trade_003", "outcome": "loss", "pnl_pct": -3.6, "conditions": "similar but chaotic regime"}
    ],
    "expected_edge_pct": 0.8,
    "kill_conditions": ["hurst drops below 0.45", "regime shifts to chaotic", "funding reverses"]
}
```

Key difference from current harvester: every hypothesis has an ANTI-THESIS and KILL CONDITIONS. The system argues with itself before trading.

### 2B: ADVERSARY

A dedicated process that tries to KILL every hypothesis.

For each hypothesis from 2A:
1. Search memory for similar setups that FAILED
2. Check if kill conditions are already partially met
3. Stress test: "What happens to this portfolio if BTC drops 5% in 1 hour?"
4. Check correlation with existing positions
5. Check concentration (signal type, direction, sector)

Output: each hypothesis gets a SURVIVAL SCORE (0-1). Below 0.5 = killed.

```python
adversary_result = {
    "hypothesis_id": "hyp_20260318_153000_BTC_LONG_001",
    "survival_score": 0.62,
    "attacks": [
        {"attack": "hurst_decline", "severity": 0.3, "detail": "Hurst down 0.06 in 6h"},
        {"attack": "similar_failure", "severity": 0.4, "detail": "trade_003 lost 3.6% in similar setup"},
        {"attack": "portfolio_stress", "severity": 0.2, "detail": "5% BTC drop = -$1.80 portfolio impact"}
    ],
    "verdict": "PROCEED_WITH_CAUTION",
    "recommended_size_modifier": 0.7  # reduce size due to anti-thesis strength
}
```

### 2C: PARAMETER EVOLUTION (replaces signal_evolution)

Every 100 trades (or weekly, whichever comes first):
1. Analyze all closed trades by: regime, signal type, direction, time of day, hold duration
2. Find EDGES: "Short signals in chaotic regimes win 72% vs 48% overall"
3. Find TRAPS: "MACD crossover signals lose 80% when Hurst < 0.5"
4. Generate RULES from patterns:
   ```
   RULE_001: IF regime=chaotic AND direction=SHORT → boost confidence +0.15
   RULE_002: IF hurst < 0.5 AND signal_type=MACD → kill hypothesis
   RULE_003: IF funding_reversal=True AND direction matches reversal → boost +0.20
   ```
5. Test rules against held-out trades (last 20%)
6. Promote rules that improve Sharpe, demote rules that don't
7. Rules have a GENERATION counter — old rules that stop working get killed

```python
evolved_rules = {
    "active_rules": [
        {"id": "RULE_001", "generation": 3, "trades_tested": 47, "impact": "+0.8% avg PnL", "status": "active"},
        {"id": "RULE_002", "generation": 1, "trades_tested": 12, "impact": "-0.2% avg PnL", "status": "probation"},
    ],
    "pending_rules": [...],  # being tested
    "killed_rules": [...]    # failed validation
}
```

---

## Layer 3: ACTION

**What it replaces:** execution_agent

**What it does:** Executes surviving hypotheses. But ACTION includes two things current execution doesn't:

### 3A: EXECUTION (same as now, improved)
- Opens positions on HL with on-chain stops
- Tracks exec quality (slippage, fill time, fees)
- Manages trailing stops on-chain
- Runs exit expressions

### 3B: OBSERVATION
After every trade CLOSES:
```python
observation = {
    "trade_id": "trade_010",
    "hypothesis_id": "hyp_20260318_153000_BTC_LONG_001",
    "outcome": "loss",
    "pnl_pct": -2.3,
    "pnl_usd": -0.85,
    "fees": 0.014,
    "hold_hours": 4.2,
    "exit_reason": "stop_loss",
    "thesis_correct": False,
    "anti_thesis_correct": True,  # hurst DID decline further
    "kill_condition_hit": "hurst drops below 0.45",
    "adversary_was_right": True,  # adversary flagged hurst decline
    "world_state_at_entry": {...},  # snapshot
    "world_state_at_exit": {...},   # snapshot
    "lesson": "Declining Hurst invalidated momentum thesis despite CONFIRMATION pattern"
}
```

This observation feeds BACK into:
- Memory (for similar_past_trades lookup)
- Parameter Evolution (for rule generation)
- Adversary (for future attack patterns)

---

## Layer 4: REFLECTION (new — doesn't exist at all today)

Runs every 6 hours. Uses LLM (local Ollama, not cloud).

**Input:** Last N observations + current portfolio state + current rules

**Process:**
1. "What patterns do I see in my recent losses?"
2. "Am I overweight in any regime/direction/signal type?"
3. "Are my rules still valid given market changes?"
4. "What am I NOT seeing that I should be?"

**Output:** A REFLECTION document stored in memory.

```python
reflection = {
    "timestamp": "2026-03-18T18:00:00Z",
    "cycle": 47,
    "observations_since_last": 3,
    "patterns_noticed": [
        "All 3 recent losses were LONG positions entered during declining Hurst",
        "Chaotic regime produced 2 wins out of 2 — contradicts my avoidance bias"
    ],
    "rule_proposals": [
        "PROPOSED: Block LONG entries when Hurst has declined >0.05 in 6h",
        "PROPOSED: Increase chaotic regime confidence instead of penalizing"
    ],
    "self_assessment": {
        "biggest_mistake": "Trusting CONFIRMATION_LONG when Hurst contradicts",
        "biggest_edge": "Short signals in regime transitions",
        "confidence_calibration": "I'm overconfident on long signals (predicted 68% conf, actual 40% WR)"
    },
    "action_items": [
        "Lower confidence on all LONG hypotheses by 0.10 until WR improves",
        "Test chaotic-regime-only trading as a sub-strategy"
    ]
}
```

The reflection doesn't auto-modify the system. It produces PROPOSALS that Parameter Evolution tests. Human-in-the-loop: Igor can review reflections and approve/reject proposals.

---

## Memory Architecture

**Current:** signal_weights.json (flat dict of numbers)

**Cognitive Loop:**

```
memory/
├── episodes/           # every trade as a full observation
│   ├── trade_001.json
│   ├── trade_002.json
│   └── ...
├── rules/              # evolved parameter rules
│   ├── active.json
│   ├── probation.json
│   └── killed.json
├── reflections/        # 6-hourly self-assessments
│   ├── reflection_001.json
│   └── ...
├── narratives/         # LLM context interpretations
│   └── latest.json
├── world_states/       # snapshots at each trade entry/exit
│   └── (compressed, rolling 7 days)
└── meta.json           # system-level stats, generation counters
```

**Retrieval:** When generating a hypothesis for BTC LONG, the system queries:
1. All past BTC LONG trades
2. All past trades in current regime type
3. All past trades with similar Hurst/funding/spread conditions
4. Most recent reflection mentioning BTC

This is vector-searchable. Each episode gets embedded on creation.

---

## Process Architecture

```
Current: 10 separate Python processes, JSON bus files

Cognitive Loop: 3 processes + 1 periodic

Process 1: PERCEPTION (every 5 min)
  - Fetch all indicators from Envy
  - Fetch HL data (prices, orderbook, funding)
  - Build world_state.json
  - Every 30 min: LLM narrative layer (Ollama)

Process 2: COGNITION (every 5 min, after Perception)
  - Read world_state
  - Generate hypotheses (replaces harvester + correlation)
  - Run adversary against each hypothesis
  - Apply evolved rules
  - Write decisions.json

Process 3: ACTION (every 5 min, after Cognition)
  - Read decisions.json
  - Execute trades
  - Monitor positions
  - Record observations on close
  - Feed observations back to memory

Process 4: REFLECTION (every 6 hours)
  - Read recent observations
  - LLM analysis (Ollama)
  - Propose rule changes
  - Update confidence calibration

Total: 4 processes. Down from 10. More intelligent.
```

---

## Migration Path

Phase 1 (Week 1): Merge 6 agents into PERCEPTION
  - Combine regime, liquidity, cross_timeframe, funding, spread into one process
  - Single world_state.json output
  - No behavior change, just consolidation

Phase 2 (Week 2): Replace Harvester with Hypothesis Generator
  - Add anti-thesis and kill conditions to every candidate
  - Add similar_past_trades lookup (simple: exact match on coin+direction+regime)
  - No LLM yet, pure quantitative

Phase 3 (Week 3): Build Adversary
  - Stress testing against hypotheses
  - Survival scoring
  - Size modification based on adversary confidence

Phase 4 (Week 4): Observation Loop
  - Full trade observation records
  - World state snapshots at entry/exit
  - Outcome tagging (thesis_correct, anti_thesis_correct)

Phase 5 (Week 5-6): Reflection + LLM Integration
  - Ollama-based reflection every 6h
  - Narrative context layer
  - Rule proposal system

Phase 6 (Ongoing): Parameter Evolution
  - Automatic rule generation from 100+ trades
  - Rule testing against held-out data
  - Generation-based rule lifecycle

---

## Cost

- Envy API: unchanged (free)
- HL API: unchanged (free)
- LLM (Ollama local): $0 compute cost, ~2GB RAM for inference
- Total additional cost: $0

The cognitive loop runs entirely on existing hardware with existing data sources. The improvement is architectural, not resource-based.

---

## What This Enables

1. The system gets BETTER with every trade, not just bigger
2. It can explain WHY it made a decision (hypothesis + evidence)
3. It argues with itself before risking money (adversary)
4. It discovers new edges from its own data (parameter evolution)
5. It recognizes when it's wrong and adjusts (reflection)
6. It remembers what happened last time in similar conditions (episode memory)

This is the difference between a bot and intelligence.
A bot does what you programmed. Intelligence does what works.

---

## Phase 1 Status: COMPLETE (2026-03-18)

- perception.py: 1,151 lines, fully operational
- 40 coins, 23 indicators per coin, 20s cycle time
- Legacy bus files written for backward compatibility
- Supervisor updated: 10 → 6 processes
- Bug found: first run had empty API key (env not sourced), loads from .env file correctly
- Commit: 180083d

## Phase 2 Status: COMPLETE (2026-03-18)
- hypothesis_generator.py: 1,405 lines
- 13 hypotheses generated with thesis/anti-thesis/kill conditions/confidence scoring
- Episode memory foundation: 36 episode files
- Backward compatible with correlation agent

## Phase 3 Status: COMPLETE (2026-03-18)
- adversary.py: 370 lines, 6 attack vectors
- First run: 18 hypotheses → 6 killed, 12 survivors
- Execution agent wired with size modifiers
- Commit: 1573ac9

## Phase 4 Status: COMPLETE (2026-03-18)
- observer.py: kill condition monitoring + structured observations
- 14 historical trades retroactively observed
- Kill signals wired into execution agent
- hypothesis_id stored on positions
- Commit: 25c1637

## Phase 5 Status: COMPLETE (2026-03-18)
- reflection.py: Ollama-based, 6h deep reflection + 30min narrative
- parameter_evolution.py: rule generation from trade patterns
- Rule lifecycle: proposed → probation → active → killed
- Rules wired into hypothesis generator + adversary
- First narrative generated via llama3:8b
- 9 processes total
- All phases completed in single session (~45 minutes)

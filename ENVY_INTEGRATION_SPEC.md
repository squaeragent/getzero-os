# ENVY-FIRST INTEGRATION SPEC
## Goal: Maximum ENVY utilization — they are our partners

### Current State (4/10)
- Pack signals generate 93% of candidates (14/15 last cycle)
- Exit expressions exist in pack data but exit indicator fetch uses API poll (slow, rate-limited)
- WebSocket overlays on perception but isn't used for exit evaluation
- Pack refresh pulls ~10 random signals per 2h call (26% coverage of ~16K total)
- History endpoint completely unused
- MIN_SHARPE=1.0 in hypothesis_generator (should be 1.5+)
- Signal-level P&L not tracked

### Target State (9/10)
- WebSocket as PRIMARY data source for both entry AND exit evaluation
- Pack exit expressions actively closing positions (not just our heuristic exits)
- Complete signal library (~16K signals)
- History-validated signal quality
- Signal-level performance tracking with auto-blacklisting
- Real-time evaluation cycle: WS push (15s) → expression evaluation → immediate action

---

## CHANGE 1: WebSocket as Primary Data Source
**Files:** `perception.py`, `execution_agent.py`, `hypothesis_generator.py`

### 1A. Perception: WS-first, API-fallback
Currently: API poll → WS overlay
Change to: WS primary → API fallback only when WS stale (>60s)

```
In perception.py fetch_all_indicators():
1. Check ws_indicators.json freshness
2. If fresh (<60s): use WS data as base, skip API poll entirely
3. If stale (>60s): fall back to API poll, then overlay WS
4. Save perception cycle time: 0s (WS) vs 30-45s (API batching)
```

### 1B. Execution: Read WS for exit indicators
Currently: fetch_indicators_for_exit() calls ENVY API per cycle
Change to: Read ws_indicators.json directly (already has all 81 indicators for all 40 coins)

```
In execution_agent.py:
Replace fetch_indicators_for_exit() API call with ws_indicators.json read
Fallback: API poll if WS stale
```

### 1C. Hypothesis: Real-time expression evaluation
Currently: Evaluates pack expressions against world_state (5-min old data)
Change to: Read ws_indicators.json for freshest data when evaluating expressions

---

## CHANGE 2: Complete Signal Library Crawl
**Files:** `pack_refresher.py`

### 2A. Systematic crawl (one-time)
The API returns random signals — we need to call it many times per coin to build coverage.
Current: 10 signals × 3 types × ~10 coins = ~300 signals per 2h cycle
Need: Exhaust the signal pool for each coin.

```
Strategy:
- For each coin (40 total):
  - For each pack type (common, rare, trump):
    - Call /paid/signals/pack repeatedly until we get <5 new signals (diminishing returns)
    - Rate limit: 0.3s between calls
    - Deduplicate by signal name
- Run as one-time script, then maintain with periodic refresh
- Estimated API calls: ~200-400 (40 coins × 3 types × 2-3 calls each)
- Expected total: ~12,000-16,000 unique signals
```

### 2B. Quality scoring on ingest
When adding to cache, compute:
- Tier 1 (tradeable): Sharpe ≥ 2.0, WR ≥ 60%, N ≥ 10
- Tier 2 (watchlist): Sharpe ≥ 1.5, WR ≥ 55%, N ≥ 5
- Tier 3 (archive): everything else
Only Tier 1+2 get evaluated each cycle.

---

## CHANGE 3: History-Based Signal Validation
**Files:** New `scanner/agents/history_validator.py`

### 3A. Backtest validation agent
For each Tier 1 signal:
1. Fetch 7 days of indicator history for all referenced indicators
2. Walk through history evaluating the entry expression at each 15-min point
3. When entry fires, evaluate exit expression forward
4. Calculate independent Sharpe/WR and compare to ENVY's claimed values
5. Store validation result: `{signal_name, claimed_sharpe, validated_sharpe, drift_pct}`

### 3B. Drift detection
If validated Sharpe differs from claimed by >30%, flag the signal.
Signals with validated Sharpe < 1.0 → auto-blacklist.

### 3C. Regime conditioning
Run validation separately for each regime (trending, ranging, chaotic).
Store regime-specific performance: some signals only work in certain conditions.

---

## CHANGE 4: Signal-Level P&L Tracking
**Files:** `execution_agent.py`, `scanner/supabase/client.py`

### 4A. Track signal performance
When closing a trade, record in Supabase:
```sql
ALTER TABLE trades ADD COLUMN signal_name TEXT;
-- Already have signal column, but ensure it's populated
```

### 4B. Auto-blacklist losers
After each trade close:
1. Query last 5 trades for same signal_name
2. If 3+ consecutive losses → add to SIGNAL_BLACKLIST for 24h
3. Log: "[blacklist] {signal_name} — 3 consecutive losses, blocked for 24h"

### 4C. Boost winners
Signals with 3+ consecutive wins → boost size modifier to 1.2x (max).

---

## CHANGE 5: Fix MIN_SHARPE
**File:** `hypothesis_generator.py`

Change line 140: `MIN_SHARPE = 1.0` → `MIN_SHARPE = 1.5`
Add regime adjustment:
```python
if regime == "chaotic":
    effective_min_sharpe = max(MIN_SHARPE, CHAOTIC_MIN_SHARPE)  # 2.5
elif fg < 30:  # Fear market
    effective_min_sharpe = max(MIN_SHARPE, 2.0)
else:
    effective_min_sharpe = MIN_SHARPE
```

---

## CHANGE 6: Accelerated Evaluation Loop
**Files:** New `scanner/agents/realtime_evaluator.py`

### 6A. Dedicated real-time evaluator
Instead of waiting for hypothesis generator (10-min cycle):
1. Read ws_indicators.json every 30s
2. Evaluate ALL Tier 1 signals against current WS data
3. When expression fires → write to candidates.json immediately
4. Adversary can then evaluate within next 5-min cycle

This doesn't replace hypothesis generator — it adds a fast path for WS-triggered signals.

### 6B. Exit evaluation acceleration  
Same loop also evaluates exit expressions for open positions:
1. Read positions.json
2. For each position with exit_expression
3. Evaluate against WS data
4. If exit fires → write to bus/exit_signals.json
5. Execution agent reads exit_signals.json alongside its own exit logic

---

## Implementation Order

1. **Change 5: MIN_SHARPE fix** (5 min, trivial)
2. **Change 1B: WS exit indicators** (30 min, immediate impact)
3. **Change 1A: WS-first perception** (30 min, saves API calls)
4. **Change 2: Complete library crawl** (1h script + 30 min to run)
5. **Change 4: Signal P&L tracking** (45 min)
6. **Change 6: Real-time evaluator** (1.5h, biggest architectural change)
7. **Change 3: History validator** (2h, runs overnight)

## Expected Impact
| Metric | Current | After |
|--------|---------|-------|
| Signal evaluation latency | 5 min | 30s |
| Exit indicator freshness | 5 min poll | 15s WS |
| Signal library coverage | 4,246 (26%) | ~14,000 (85%+) |
| MIN_SHARPE filter | 1.0 | 1.5 (2.0 in fear) |
| Signal-level tracking | None | Full P&L per signal |
| Validated signals | 0 | All Tier 1 |
| Win Rate target | 38% | 55-65% |

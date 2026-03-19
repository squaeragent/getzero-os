# STRATEGY FIX SPEC — Target: 9.5/10
## Red Team Score: 4.5/10 → 9.5/10

### Root Cause Analysis

The system has strong data infrastructure (81 indicators, 3,732 signals, 18 adversary attacks, WebSocket streaming) but **three critical disconnections** prevent it from working:

1. **Adversary kills everything** — 45/45 hypotheses killed last cycle. Zero survivors. The adversary is too aggressive, so correlation agent gets empty approved list, and execution opens trades from stale approved data or when adversary hasn't run yet (race condition).

2. **Metadata not propagating** — `adversary_verdict`, `regime`, and `strategy_version` are all `None` in Supabase trades. The execution agent builds the trade dict but doesn't pull these fields from the approved trade data correctly.

3. **alignment_exit destroys all gains** — 10 trades, 0% WR, -$1.79. This single exit mechanism accounts for ALL net losses.

---

## FIX 1: Adversary Calibration (Critical — Score Impact: +2.0)

### Problem
Adversary total severity across 18 attacks accumulates too high. With 18 weighted attacks, even moderate individual severities (0.2-0.3 each) sum to 3.0+, which pushes survival_score below the KILLED threshold for every hypothesis.

### Fix
**File: `scanner/agents/adversary.py`**

#### A. Recalibrate severity-to-verdict thresholds
Current thresholds (find `score_to_verdict` function):
```python
# Current (too aggressive):
if survival_score < 0.3:  → KILLED
elif survival_score < 0.5: → WEAK  
elif survival_score < 0.7: → PROCEED_WITH_CAUTION
else:                      → PROCEED
```

Change to:
```python
if survival_score < 0.15:  → KILLED        # Only kill truly terrible signals
elif survival_score < 0.35: → WEAK          # Reduced size
elif survival_score < 0.55: → PROCEED_WITH_CAUTION
else:                       → PROCEED
```

Rationale: With 18 attacks, a "normal" signal accumulates ~2.0 total severity. The survival formula `1 / (1 + total_severity)` maps that to 0.33 — currently KILLED, should be WEAK.

#### B. Cap total severity at 3.0
After all attacks run, before computing survival_score:
```python
total_severity = min(total_severity, 3.0)  # Prevent severity runaway
```

#### C. Reduce default weights on low-signal attacks
These attacks fire too broadly (severity > 0 on almost every trade):
- `attack_session_risk`: weight 1.3x → 1.0x (fires every US session)
- `attack_timeframe_alignment`: weight 1.5x → 1.0x (fires when 1/3 timeframes disagree — too common)
- `attack_regime_transition`: weight 1.2x → 0.8x (fires during any transition period)
- `attack_data_disagreement`: weight 1.2x → 0.8x (sources always slightly disagree)

#### D. Add logging for adversary decisions
At the end of the adversary loop, log:
```
[ADV] 45 hypotheses → 8 PROCEED, 5 CAUTION, 12 WEAK, 20 KILLED
[ADV] Avg severity: 1.8 | Avg survival: 0.42
```
This tells us immediately if calibration is off.

### Validation
After fix: at least 20% of hypotheses should survive (not 0%). Target: 30-50% survival rate.

---

## FIX 2: Metadata Propagation (Critical — Score Impact: +1.5)

### Problem
Execution agent receives approved trades from correlation, opens positions, but writes `adversary_verdict=None`, `regime=None`, `strategy_version=None` to Supabase.

### Fix
**File: `scanner/agents/execution_agent.py`**

#### A. Propagate fields from approved trade to position/closed trade dicts

Find the `open_trade()` function. Where it builds the position dict (around line 992), ensure these fields are copied from the incoming `trade` parameter:

```python
position = {
    ...
    "regime": trade.get("regime") or _get_current_regime(trade["coin"]),
    "adversary_verdict": trade.get("adversary_verdict", "UNKNOWN"),
    "survival_score": trade.get("survival_score", 0),
    "strategy_version": STRATEGY_VERSION,  # Use the constant, not trade data
    ...
}
```

#### B. Add `_get_current_regime()` helper
Read from `scanner/bus/regimes.json`:
```python
def _get_current_regime(coin):
    try:
        with open(BUS_DIR / "regimes.json") as f:
            regimes = json.load(f)
        return regimes.get("coins", {}).get(coin, {}).get("regime", "unknown")
    except Exception:
        return "unknown"
```

#### C. Ensure close_trade also propagates
When closing a trade, copy `adversary_verdict`, `regime`, `survival_score`, `strategy_version` from the position dict to the closed trade dict.

Find where closed trades are built (grep for `"exit_reason"` assignment) and add:
```python
closed = {
    ...
    "adversary_verdict": pos.get("adversary_verdict"),
    "regime": pos.get("regime"),
    "survival_score": pos.get("survival_score"),
    "strategy_version": pos.get("strategy_version", STRATEGY_VERSION),
    ...
}
```

#### D. Fix STRATEGY_VERSION constant
Verify `STRATEGY_VERSION = 5` (current) is set at the top of execution_agent.py. If it's still 4, bump it.

**File: `scanner/agents/risk_agent.py`**
Same fix — verify STRATEGY_VERSION matches.

**File: `scanner/supabase/client.py`**
Verify `insert_trade()` and `upsert_position()` actually write these fields. Current code already does (`line 194-197`), so this should work once execution sends non-None values.

### Validation
After fix: every new trade in Supabase should have non-None values for `adversary_verdict`, `regime`, `strategy_version`.

---

## FIX 3: Kill alignment_exit (Critical — Score Impact: +1.0)

### Problem
`alignment_exit`: 10 trades, 0% WR, -$1.79. This exit fires when the cross-timeframe pattern flips to opposing direction. It's too trigger-happy — closes positions at the worst time.

### Fix
**File: `scanner/agents/execution_agent.py`**

#### A. Remove alignment_exit entirely
Find the alignment_exit logic (grep for `alignment_exit`). Replace with a log-only warning:

```python
# DISABLED: alignment_exit was 0% WR, -$1.79 across 10 trades
# Instead of exiting, we log the divergence for analysis
if alignment_conflict:
    log(f"  INFO [{coin}] alignment conflict (pattern={tf_pattern}) — monitoring, no exit")
```

The `kill_condition` exit (43% WR, +$0.78) and trailing stops handle actual exits effectively.

#### B. Also remove `alignment_exit_trap`
3 trades, 33% WR, -$0.00. Same family, equally useless. Convert to log-only.

### Validation
After fix: zero trades with exit_reason `alignment_exit` or `alignment_exit_trap`. All exits via `kill_condition`, `exit_expression`, `trailing_stop`, `time_decay_stop`, or `max_hold`.

---

## FIX 4: Blacklist Bad Signals (Score Impact: +0.5)

### Problem
`ARCH_CHAOS_REGIME_CONVERGENCE`: 6 trades, 0% WR, -$0.41. Consistently loses.
`ARCH_SOCIAL_EXHAUSTION_LONG`: 1 trade, 0% WR, -$0.26. LONGs in fear market.

### Fix
**File: `scanner/agents/signal_harvester.py`**

Add to the signal family blacklist (or create one if `SIGNAL_FAMILY_BLACKLIST` was removed):
```python
SIGNAL_BLACKLIST = {
    "ARCH_CHAOS_REGIME_CONVERGENCE",
    "ARCH_SOCIAL_EXHAUSTION_LONG",
}
```

In `generate_archetype_signals()`, skip blacklisted signals:
```python
if signal_name in SIGNAL_BLACKLIST:
    continue
```

**File: `scanner/agents/adversary.py`**

Add `attack_family_track_record` to penalize signal families with <30% WR over 5+ trades — severity 0.8.

### Validation
No new trades from blacklisted signals. Adversary penalizes weak signal families automatically.

---

## FIX 5: LONG Suppression in Fear Market (Score Impact: +0.5)

### Problem
LONG: 27 trades, 30% WR, -$0.56. F&G=23 (Extreme Fear). The F&G adversary attack exists but isn't filtering because the entire adversary is too aggressive (kills everything equally, see Fix 1).

### Fix (after Fix 1 is applied)
**File: `scanner/agents/adversary.py`**

Verify `attack_fear_greed` severity values:
```python
# F&G ≤ 20: LONG severity 0.9 (near-guaranteed kill)
# F&G ≤ 30: LONG severity 0.6
# F&G ≤ 45: LONG severity 0.2
# F&G ≥ 70: SHORT severity 0.6
# F&G ≥ 80: SHORT severity 0.9
```

These values are already in the code (v5 commit `10f92c6`). Once Fix 1 makes the adversary not kill everything, these will actually start filtering LONGs.

Additionally, in the **correlation agent**, add a hard gate:
```python
# Hard reject LONGs when F&G < 25
macro = load_json(BUS_DIR / "macro_intel.json", {})
fg = macro.get("fear_greed", 50)
if fg < 25 and trade["direction"] == "LONG":
    blocked.append({"trade": trade, "reason": f"LONG blocked: F&G={fg} (Extreme Fear)"})
    continue
```

This is a safety net — the adversary should handle it, but in Extreme Fear we want a hard gate.

### Validation
After fix: <10% of trades should be LONGs when F&G < 30.

---

## FIX 6: Race Condition — Adversary vs Execution Timing (Score Impact: +0.5)

### Problem
Execution reads `approved.json` every 5 minutes. If it reads after signal_harvester writes candidates but before adversary evaluates them, it could open unvetted trades. The `adversary_timestamp` check in correlation mitigates this, but there's a window.

### Fix
**File: `scanner/agents/correlation_agent.py`**

Already has the `adversary_timestamp` check. Strengthen it:
```python
# Reject candidates more than 10 minutes old
cand_age = (now - cand_timestamp).total_seconds()
if cand_age > 600:
    print(f"  [stale] candidates are {cand_age:.0f}s old — skipping")
    return []

# Reject if adversary hasn't run on these candidates
if not adversary_ts or adversary_ts < cand_timestamp:
    print(f"  [wait] adversary hasn't evaluated current candidates — skipping")
    return []
```

**File: `scanner/run_agents.py`**

Ensure agent ordering: perception → hypothesis → adversary → correlation → execution.
The current cycle times already approximate this (hypothesis 600s, adversary 300s, correlation 300s, execution 300s), but add a comment documenting the dependency chain.

### Validation
No trades should open without a valid `adversary_timestamp` in candidates.json.

---

## FIX 7: Position Sizing from Adversary Verdict (Score Impact: +0.5)

### Problem
All positions use max size regardless of conviction. A WEAK signal should get smaller size.

### Fix
**File: `scanner/agents/execution_agent.py`**

In `open_trade()`, apply size modifier based on adversary verdict:
```python
verdict = trade.get("adversary_verdict", "PROCEED")
size_modifier = 1.0
if verdict == "WEAK":
    size_modifier = 0.4
elif verdict == "PROCEED_WITH_CAUTION":
    size_modifier = 0.7
# PROCEED = 1.0 (full size)

position_size = min(max_position_usd, target_size * size_modifier)
```

### Validation
WEAK trades should open at 40% of normal size. PROCEED_WITH_CAUTION at 70%.

---

## FIX 8: Exit Optimization (Score Impact: +0.5)

### Problem
`exit_expression`: 9 trades, 44% WR, -$0.01. Break-even despite decent WR because exits trigger too early (before full profit captured).

### Fix
**File: `scanner/agents/execution_agent.py`**

#### A. Trailing stop improvements
Current: trigger at 0.3%, lock 60%. This is correct and working.

#### B. Time-decay stop improvements
Current: 30m+ losing >0.5% → tighten to 60%; 60m+ → 40%.
Change: Make time-decay more gradual:
```python
# 45m+ losing > 0.5%: tighten to 70% (was 30m/60%)
# 90m+ losing > 0.3%: tighten to 50% (was 60m/40%)
# 120m+ losing > 0.1%: tighten to 35%
```

#### C. Add profit target exit
No explicit take-profit exists. Add:
```python
# If unrealized P&L > 3% AND trailing has locked > 2%, take profit
if pnl_pct > 3.0 and trailing_locked_pct > 2.0:
    return "take_profit"
```

### Validation
Fewer premature exits. Avg P&L per winning trade should increase from current $0.12 to >$0.20.

---

## FIX 9: Signal Quality Gate (Score Impact: +0.5)

### Problem
Signal packs contain signals with negative Sharpe and low WR. The harvester uses them anyway. Config says `min_sharpe=1.5, min_win_rate=55` but these filters may not apply to all signal sources.

### Fix
**File: `scanner/agents/hypothesis_generator.py`**

Add hard filter before outputting candidates:
```python
# Quality gate — no signal with Sharpe < 1.5 or WR < 55% should become a hypothesis
if signal.get("sharpe", 0) < 1.5:
    continue
if signal.get("win_rate", 0) < 55:
    continue
if signal.get("trade_count", 0) < 5:
    continue  # Not enough data to trust
```

Also filter in `scanner/agents/signal_harvester.py` for archetype signals:
```python
# Archetype signals must meet same quality bar
if composite_score < 6.5:
    continue
```

### Validation
All hypotheses should have Sharpe ≥ 1.5 and WR ≥ 55%. Zero garbage signals reaching the adversary.

---

## FIX 10: Equity Tracking Hardening (Score Impact: +0.5)

### Problem
Equity went through 3 incorrect values ($114 → $320 → $746) before being correct. `STARTING_EQUITY` was hardcoded. No validation.

### Fix (already partially done)

#### A. Remove STARTING_EQUITY entirely
**File: `scanner/agents/risk_agent.py`**

The spot USDC query is already implemented (Fix from tonight). Ensure the fallback is reasonable:
```python
# If spot query fails, read from config.yaml capital field
account_value = config.get("capital", 750) + unrealized_pnl
```

#### B. Add equity sanity check
Before writing to Supabase:
```python
# Sanity check — equity shouldn't change >50% between snapshots
if prev_equity and abs(account_value / prev_equity - 1) > 0.5:
    log(f"WARN: equity jumped {prev_equity} → {account_value} (>50%) — possible error")
    # Still write it but flag it
```

#### C. Portfolio page resilience
Already fixed tonight (reads from Supabase, no hardcoded 115).

### Validation
Equity never shows wrong values. Jumps >50% are logged as warnings.

---

## Implementation Order

1. **Fix 1** (Adversary calibration) — unlocks all other fixes
2. **Fix 2** (Metadata propagation) — enables measurement
3. **Fix 3** (Kill alignment_exit) — immediate P&L improvement: +$1.79
4. **Fix 5** (LONG suppression) — immediate P&L improvement in fear market
5. **Fix 4** (Blacklist bad signals)
6. **Fix 6** (Race condition)
7. **Fix 7** (Position sizing from verdict)
8. **Fix 8** (Exit optimization)
9. **Fix 9** (Signal quality gate)
10. **Fix 10** (Equity hardening)

## Expected Outcome

| Metric | Current | Target |
|--------|---------|--------|
| Win Rate | 38% | 55%+ |
| LONG WR (fear market) | 30% | Suppressed (<10% of trades) |
| SHORT WR | 44% | 55%+ |
| Adversary survival rate | 0% | 30-50% |
| alignment_exit trades | 10 | 0 |
| Metadata completeness | 0% | 100% |
| Avg P&L/trade | $0.014 | $0.10+ |
| Strategy version tracking | 3% correct | 100% correct |
| Regime tracking | 0% | 100% |

## Measurement Plan

After all 10 fixes:
1. Reset strategy_version to 5 with epoch timestamp
2. Run 100 trades minimum (3-5 days)
3. Re-run Red Team evaluation
4. Compare v5 metrics vs v3/v4 baseline

Only after 100 v5 trades with positive expectancy should we scale position sizes.

---

*Spec version: 1.0 | Author: SERAPHIM Red Team | Date: 2026-03-20*

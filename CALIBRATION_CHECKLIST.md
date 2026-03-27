# ENGINE CALIBRATION CHECKLIST
*Saved from Igor's calibration spec — the minimum viable test plan before real money.*

## CATEGORY 1: DOES THE ENGINE ACTUALLY START?
- [ ] supervisor.py starts without import errors
- [ ] controller.py initializes with momentum.yaml
- [ ] monitor.py starts evaluation cycle
- [ ] immune starts and writes first heartbeat
- [ ] All bus/ files created on startup
- [ ] No dead module references anywhere in startup path

## CATEGORY 2: DOES THE ENGINE EVALUATE CORRECTLY?
- [ ] Pick 5 coins. Run 1 evaluation cycle manually.
- [ ] All 7 layers return results?
- [ ] Consensus computed correctly?
- [ ] Regime classification makes sense vs chart?
- [ ] Funding direction matches HL actual?
- [ ] OI direction matches actual OI change?
- [ ] Fear & greed matches alternative.me?

## CATEGORY 3: DOES THE ENGINE RESPECT RISK GATES?
- [ ] max_positions: rejected when full
- [ ] max_daily_loss_pct: circuit breaker triggers
- [ ] reserve_pct: rejected when reserve violated
- [ ] max_hold_hours: auto-close on time limit
- [ ] Hard cap: YAML 50% forced to 25%

## CATEGORY 4: DOES THE ENGINE ACTUALLY TRADE ON HL?
- [ ] Paper mode: no real HL orders placed
- [ ] One real order on HL (even $1, even canceled)
- [ ] get_positions() matches HL dashboard
- [ ] Stop loss appears in HL open orders

## CATEGORY 5: DOES THE SESSION LIFECYCLE WORK?
- [ ] Start paper Momentum session → ACTIVE
- [ ] Session completes → COMPLETED
- [ ] Result card generated with all fields
- [ ] Near misses detected
- [ ] Session history logged
- [ ] New session starts after old completes

## CATEGORY 6: DOES THE SIGNAL STATE MACHINE WORK?
- [ ] 4/7 → 6/7: ENTRY emitted
- [ ] 6/7 → 6/7: NO re-emit
- [ ] 6/7 → 4/7: ENTRY_END emitted
- [ ] Exit conditions: EXIT emitted
- [ ] 4/7 → 5/7 (threshold=6): APPROACHING emitted

## CATEGORY 7: DOES THE BUS FILE SYSTEM WORK?
- [ ] signals.json updates every cycle
- [ ] decisions_*.jsonl logging
- [ ] events.jsonl capturing events
- [ ] heartbeat.json updating every 60s
- [ ] session.json persisting
- [ ] Shutdown: controller_state.json written
- [ ] Restart: controller_state.json loaded

## CATEGORY 8: DOES THE NARRATIVE MAKE SENSE?
- [ ] Paper session with ≥1 trade produces meaningful narrative
- [ ] Rejection rate matches reality
- [ ] Timeline has hour markers

## CATEGORY 9: STRESS AND EDGE CASES
- [ ] HL API error on price fetch → skip cycle, no crash
- [ ] All coins fail evaluation → continue, no crash
- [ ] Controller crash mid-session → state persisted, recoverable
- [ ] Session expires with open positions → close all, then complete
- [ ] $0 equity → block entries, no divide-by-zero
- [ ] Invalid YAML → clear error, no crash

---

## MINIMUM VIABLE CALIBRATION (3 tests, 2 hours)

1. **START ENGINE 1 HOUR** — starts, evaluates, doesn't crash, bus files written, heartbeat updating
2. **1-HOUR PAPER MOMENTUM SESSION** — session starts, evaluates, signals emitted, completes, result card produced
3. **ONE REAL HL API CALL** — get_positions() or get_balance() returns correct data

If all 3 pass: engine is calibrated for Phase 2.
Trailing stops + immune rebuild required before LIVE money.

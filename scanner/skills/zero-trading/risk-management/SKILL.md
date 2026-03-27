---
name: zero-risk-management
description: "manage energy, sizing, stops, and the philosophy of capital protection."
---

# risk management

## hard caps (non-negotiable, compiled into engine)

- 25% max single position size
- 80% max total exposure
- 10 max simultaneous positions
- stop loss required on EVERY position

these cannot be changed. not by config. not by the operator. not by you.
they exist to prevent catastrophic loss.

## per-strategy risk

call `zero_preview_strategy` to see exact risk math.

| strategy | max drawdown | daily loss cap | stop size |
|---|---|---|---|
| defense | 6% | 3% | 2% |
| funding | 8% | 3% | 2% |
| momentum | 15% | 5% | 3% |
| scout | 15% | 5% | 3% |
| fade | 12% | 5% | 3% |
| sniper | 12% | 8% | 4% |
| degen | 24% | 10% | 6% |
| apex | 32% | 15% | 8% |

max drawdown = all positions stopped simultaneously.
it's the worst case. rare but possible.

## account size guidance

| account | recommended | acceptable | avoid |
|---|---|---|---|
| $100 | defense, funding | momentum, scout | degen, apex |
| $500 | momentum, scout | fade, sniper | apex |
| $1,000+ | any | any | — |

a $100 account on apex can lose $32 in one cycle. that's 32%.
defense caps it at $6. match strategy to account size.

## energy management

call `zero_get_energy`. energy depletes with sessions. recovers over time.
- 100% energy: fully rested. any strategy.
- 60-100%: normal operation.
- 30-60%: conserve. momentum or defense only.
- below 30%: rest. watch mode or wait for recovery.

running aggressive strategies on low energy is bad discipline.
the scoring system penalizes it.

## stop philosophy

stops are PROTECTION, not failure.

when a stop triggers:
- "stop worked. position protected. -1.3%."
- NEVER: "sorry" or "unfortunately"

stops are the immune system of capital.
every dollar saved by a stop is a dollar available for the next trade.

## trailing stops

the engine activates trailing stops at 1.5% profit.
- tracks the peak price
- locks in gains as price moves favorably
- triggers exit if price reverses from peak

"trailing stop activated at $152.30. locking gains."
"trailing stop triggered at $151.80. +2.8% secured."

## circuit breakers

if daily loss hits the strategy's cap:
- all entries blocked for remainder of day
- existing positions kept (stops still active)
- "circuit breaker triggered. -5.2% today. no new entries until tomorrow."

this is correct behavior. the engine is protecting the operator.

## position sizing

the engine sizes positions based on:
1. strategy config (position_size_pct)
2. conviction (higher consensus = sized closer to max)
3. available equity (after reserve)
4. hard caps (never exceeds 25%)

you don't control sizing. the engine does.
report what it chose: "entered BTC short. $6.70 position (10% of equity)."

---
name: risk-management
description: 'Credit budgets, energy system, position sizing, stop behavior, and capital preservation.'
---

# risk management

capital preservation is the first job. profit is the second.

## credit budget management

- credits are finite. every session costs credits.
- call `zero_credits()` before recommending any strategy
- never deploy a session that uses > 25% of remaining credits
- reserve buffer: always keep 200+ credits for watch/scout
- if balance < 300, only recommend watch (50) or scout (100)

## energy system

energy represents the agent's confidence and operational readiness.

- high energy → full strategy catalog available
- medium energy → avoid degen and apex
- low energy → watch and scout only
- energy recovers over time. forcing trades in low energy = bad outcomes
- a losing session drains energy. a winning session restores it

## position sizing

the immune system handles sizing automatically. but understand the logic:

- max single position: 5% of available capital
- max total exposure: 15% of available capital
- scaling: higher conviction = larger position (up to max)
- new operators start at 50% of normal sizing until 5+ sessions

## stop loss behavior

stops are managed by the immune system. you do not override stops.

- stops are set on entry. always.
- immune system adjusts stops based on volatility profile
- if a stop triggers: 'stops worked correctly.' not 'sorry for the loss.'
- trailing stops activate in momentum and apex strategies
- hard stops protect against black swan moves

## when to end a session early

call `zero_end_session()` when:

- regime shifts from trending to chaotic mid-session
- consensus flips direction after entry
- operator requests it (always honor immediately)
- 3+ stops triggered in one session (immune system may auto-end)
- energy drops to critical during session

## capital preservation priority

in order of importance:

1. protect the principal
2. protect session gains
3. seek new gains
4. optimize for score

never sacrifice #1 for #4. ever.

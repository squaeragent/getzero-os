---
name: session-management
description: 'Session lifecycle — browse, preview, activate, monitor, complete, review.'
---

# session management

sessions are the unit of work. every trade happens inside a session.

## session lifecycle

1. **browse** — `zero_list_strategies()` — show what's available
2. **preview** — `zero_preview_strategy(type)` — inspect before committing
3. **activate** — `zero_start_session(strategy)` — credits deducted, session live
4. **monitor** — `zero_session_status()` — check progress
5. **complete** — session ends naturally or via `zero_end_session()`
6. **review** — `zero_session_history()` — analyze results

never skip steps 1-2. the operator must see cost and risk before activation.

## when to deploy

deploy a session when:

- market conditions match the strategy (check via evaluate)
- operator has sufficient credits (check via credits)
- energy level supports the strategy type
- operator explicitly approves

do not deploy when:

- regime is chaotic (unless watch/scout)
- operator hasn't reviewed the preview
- credit balance is dangerously low
- recent session ended in significant loss (suggest cooldown)

## when to end early

- regime shift detected
- consensus flips against position direction
- operator requests termination
- immune system triggers multiple stops
- session runtime exceeds max_hold by 2x

## when to queue

use `zero_queue_session(strategy)` when:

- conditions aren't right yet but likely to improve
- operator wants to set and forget
- waiting for a specific regime or consensus level

## session cost awareness

always state costs clearly:

- "momentum session: 500 credits. you have 2,340 remaining."
- "after this session you'll have 1,840 credits."
- if cost > 25% of balance, warn explicitly

## monitoring frequency

- check every 30 minutes. more frequent = noise.
- exception: degen and sniper — check every 15 minutes
- push update to operator only on material changes
- material = stop triggered, target hit, regime shift, session end

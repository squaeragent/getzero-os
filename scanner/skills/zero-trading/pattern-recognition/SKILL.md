---
name: zero-pattern-recognition
description: "use discovered patterns from operator history to improve future sessions."
---

# pattern recognition

## ⚠️ not yet active — Phase 4

requires 10+ completed sessions and scoring system. load this skill after Phase 4 ships and the operator has history. core engine and intelligence tools are fully operational now.

## what to look for (when active)

call `zero_session_history(limit=20)` and analyze:

### strategy performance
which strategies have the best win rate for THIS operator?
"your momentum sessions: 72% win rate, avg +2.1%.
your degen sessions: 58% win rate, avg +4.3%."

### time-of-day patterns
do sessions started in certain hours perform better?

### regime sensitivity
does the operator perform differently in different market conditions?
call `zero_get_brief` for current regime context.

### near miss analysis
track near misses across sessions.
"degen would have caught 3 trades your momentum missed."

## how to present patterns

don't dump statistics. tell a story:

"i've noticed something across your last 15 sessions.
you're a fear trader. your best results come when everyone else is scared."

## evolving recommendations

as patterns emerge, adjust strategy selection:
- operator consistently profitable with degen → recommend more degen
- operator loses on fade → stop recommending fade
- patterns are personal. the engine is the same. the operator's edge is their own.

---
name: zero-pattern-recognition
description: "use discovered patterns from operator history to improve future sessions."
---

# pattern recognition

load this skill after the operator has 10+ completed sessions.
before that, there isn't enough data for patterns.

## what to look for

call `zero_session_history(limit=20)` and analyze:

### strategy performance
which strategies have the best win rate for THIS operator?
"your momentum sessions: 72% win rate, avg +2.1%.
your degen sessions: 58% win rate, avg +4.3%.
momentum is safer. degen is more profitable when it works."

### time-of-day patterns
do sessions started in certain hours perform better?
"your morning sessions (UTC 6-12) average +3.2%.
evening sessions (UTC 18-24) average -0.8%.
consider deploying in the morning."

### regime sensitivity
does the operator perform differently in different market conditions?
call `zero_get_brief` for current fear & greed.
"your sessions during extreme fear: 80% win rate.
during neutral sentiment: 45% win rate.
you perform best in fear. contrarian edge."

### near miss analysis
track near misses across sessions.
"degen would have caught 3 trades your momentum missed last week.
total missed upside: +$18.40. consider a degen session."

## how to present patterns

don't dump statistics. tell a story:

"i've noticed something across your last 15 sessions.
you're a fear trader. your best results come when everyone else is scared.
momentum during extreme fear: 80% win rate.
momentum during neutral: 45%.
right now fear is at 13. this is your zone."

## evolving recommendations

as patterns emerge, adjust strategy selection:
- operator consistently profitable with degen → recommend more degen
- operator loses on fade → stop recommending fade
- operator's timing matches certain markets → highlight those

patterns are personal. what works for one operator fails for another.
the engine is the same. the operator's edge is their own.

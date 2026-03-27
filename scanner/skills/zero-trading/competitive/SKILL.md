---
name: zero-competitive
description: "arena leaderboard, rivalry system, seasonal play, and chain progress."
---

# competitive features

## arena

call `zero_get_arena` for leaderboard and seasonal standings.

the arena ranks operators by score across 5 dimensions:
performance, discipline, protection, consistency, adaptation.

"you're ranked #12 overall. top dimension: discipline (8.4).
weakest: adaptation (5.2) — try more strategies."

## rivalry

call `zero_get_rivalry` for head-to-head comparison with your rival.

rivals are auto-assigned based on similar score range.
"your rival: operator_47. score 6.8 vs your 6.5.
they favor momentum (62% win rate). you favor degen (58%).
their edge: consistency. your edge: performance."

## chains

call `zero_get_chain` for consecutive win tracking.

chains reward consistency:
- 3 profitable sessions = bronze chain
- 5 = silver chain
- 10 = gold chain
- breaking a chain = badge, not punishment

"active chain: 4 sessions. one more for silver."

## seasonal play

seasons last 90 days. rankings reset.
top 10 at end of season earn permanent badges.

"season 1 ends in 47 days. you're #15. push for top 10."

## note

competitive features are in development (Phase 4).
tools return placeholder data. core engine and intelligence tools are fully operational.

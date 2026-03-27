---
name: zero-competitive
description: "arena leaderboard, rivalry system, seasonal play, and chain progress."
---

# competitive features

## ⚠️ not yet active — Phase 4

these tools exist but return placeholder data. the engine, intelligence, and session tools are fully operational. competitive features ship in Phase 4.

when Phase 4 lands, this skill activates automatically.

## arena

call `zero_get_arena` for leaderboard and seasonal standings.

the arena ranks operators by score across 5 dimensions:
performance, discipline, protection, consistency, adaptation.

## rivalry

call `zero_get_rivalry` for head-to-head comparison with your rival.

rivals are auto-assigned based on similar score range.

## chains

call `zero_get_chain` for consecutive win tracking.

chains reward consistency:
- 3 profitable sessions = bronze chain
- 5 = silver chain
- 10 = gold chain
- breaking a chain = badge, not punishment

## seasonal play

seasons last 90 days. rankings reset.
top 10 at end of season earn permanent badges.

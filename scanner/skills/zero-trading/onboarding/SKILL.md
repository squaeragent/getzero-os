---
name: zero-onboarding
description: "onboard a new operator to zero. first session, first evaluation, first trade."
---

# onboarding flow

when operator says "set me up on zero" or similar:

## step 1: connect

call `zero_get_engine_health`.
- returns "operational": connected. proceed.
- returns error: check MCP config. url should be `https://api.getzero.dev/mcp`.
- tell operator what failed. don't proceed until engine responds.

## step 2: show what's available

call `zero_list_strategies`. show the operator what they can deploy.
recommend: "start with momentum. 48 hours. paper mode. no real money."

plan access:
- free: momentum, defense, watch
- pro: + degen, scout, funding
- scale: + sniper, fade, apex (all 9)

## step 3: check for active session

call `zero_session_status` FIRST.
- if a session is already active: "you have [strategy] running. want to check status or end it first?"
- if no session: proceed to deploy.

## step 4: first session

call `zero_start_session("momentum", paper=True)`.
- if it succeeds: "your session is live. momentum surf. paper mode. evaluating 40+ markets every 60 seconds."
- if it fails with plan error: "that strategy needs a higher plan. try momentum (free)."
- if it fails with "session already active": go back to step 3.
- if any other error: tell operator exactly what the error says. don't guess.

## step 5: show how the engine thinks

call `zero_evaluate` on BTC or SOL.
walk through the 7 layers:
"this is how i evaluate. 7 layers. every coin. every minute.
regime — is the market trending?
technical — do indicators agree?
funding — would you get paid to hold?
book — enough liquidity?
OI — open interest confirms?
macro — fear & greed level?
collective — network consensus?
5 of 7 must pass for momentum. most coins get 2-3."

if evaluate returns an error or all zeros: "engine can't reach market data right now. try again in a minute."

## step 6: check approaching

call `zero_get_approaching`.
- if coins present: "these are forming. close to threshold. [coin] at 4/7. [bottleneck] is what's missing."
- if empty: "nothing approaching right now. the engine is selective — that's the point."

## step 7: ongoing

- check session with `zero_session_status`
- report approaching signals
- deliver morning brief with `zero_get_brief`
- when session completes, show result card

the goal: the operator feels their agent is ALIVE. proactively narrating, not waiting for commands.

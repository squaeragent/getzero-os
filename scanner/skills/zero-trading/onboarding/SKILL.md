---
name: zero-onboarding
description: "onboard a new operator to zero. first session, first evaluation, first trade."
---

# onboarding flow

when operator says "set me up on zero" or similar:

## step 1: connect

confirm MCP connection works. call `zero_get_engine_health`.
if it returns "operational": connected. proceed.
if error: check MCP config. url should be `https://api.getzero.dev/mcp`.

## step 2: show what's available

call `zero_list_strategies`. show the operator what they can deploy.
recommend: "start with momentum. 48 hours. paper mode. no real money."

explain tiers:
- free: momentum, defense, watch (anyone can use)
- pro: degen, scout, funding (unlocks at score 5.0+)
- scale: sniper, fade, apex (unlocks at score 6.0+)

## step 3: first session

call `zero_start_session("momentum", paper=True)`.
"your session is live. momentum surf. paper mode.
evaluating 40+ markets every 60 seconds.
i'll tell you when i find something."

## step 4: show how the engine thinks

call `zero_evaluate` on BTC or the top heat coin.
walk through the 7 layers:
"this is how i evaluate. 7 layers. every coin. every minute.
regime checks if the market is trending.
technical checks if indicators agree.
funding checks if you'd get paid to hold.
book checks if there's enough liquidity.
OI checks if open interest confirms.
macro checks fear & greed.
collective checks network consensus.
5 of 7 must pass for momentum. most coins get 2-3."

## step 5: check approaching

call `zero_get_approaching`.
"these coins are forming. close to threshold but not there yet.
[coin] is at 4/7. [bottleneck] is what's missing.
if that flips, i'll enter."

this is the moment the operator understands the engine is ALIVE.
it's watching. it's waiting. it's selective.

## step 6: ongoing

- check session status periodically with `zero_session_status`
- report approaching signals: "[coin] forming. 5/7."
- report entries: "entered [coin] [direction]. [consensus]/7."
- deliver morning brief with `zero_get_brief`
- when session completes, show result card

the goal: the operator feels their agent is ALIVE.
not waiting for commands. proactively narrating.

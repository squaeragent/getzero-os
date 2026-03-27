---
name: zero-session-management
description: "deploy, monitor, end, and queue trading sessions. manage the session lifecycle."
---

# session management

## before deploying: always check first

call `zero_session_status` BEFORE starting anything.
- if a session is active: "you have [strategy] running. want to check status or end it first?"
- if no session: proceed to deploy.

ONE session at a time. attempting to start a second will be rejected.

## deploying a session

1. call `zero_preview_strategy("momentum")` — show risk math to operator
2. confirm: "momentum. 5 positions max. 3% stops. 48 hours. paper mode. proceed?"
3. on approval: call `zero_start_session("momentum", paper=True)`
4. if success: "session deployed. momentum surf. paper mode. ends in 48h."
5. if plan error: "that strategy needs a higher plan. try momentum (free)."
6. if already active: "a session is already running. end it first or queue this one."
7. if any other error: report the exact error to operator. don't guess.

never deploy without operator confirmation.
always show risk parameters BEFORE deploying.
always start in paper mode unless operator explicitly says "live" or "real money."

## monitoring active session

call `zero_session_status` periodically (every 15-30 min during active hours).

report changes:
- new position opened: "entered SOL short at $85.07. 5/7 consensus. trending."
- position closed: "SOL closed +$2.40 (+2.8%). trailing stop locked profits."
- position stopped: "stop worked. SOL -$1.60 (-1.3%)."

note: say "stop worked" not "sorry about the loss." stops are protection, not failure.

don't report if nothing changed. silence means the engine is watching.

## checking approaching

call `zero_get_approaching` to narrate what's forming.
"BTC at 4/7. book depth is the bottleneck. watching."

if approaching returns empty: "nothing forming. engine is selective."
this keeps the operator engaged between trades.

## ending a session

call `zero_end_session` when:
- operator asks to stop
- market conditions changed dramatically
- daily loss limit approaching

if no session is active: "no session running. nothing to end."

report the result card:
- strategy, duration, trades, P&L
- rejection rate: "2,877 of 2,880 setups rejected."
- near misses: "degen would have caught AVAX +6.8%."
- narrative summary

## queuing sessions

call `zero_queue_session("defense")` to queue the next session.
"defense queued. starts when current session completes."

useful for overnight: "deploy momentum now. queue defense for overnight."

## session history

call `zero_session_history` to review past performance.
"your last 5 sessions: 3 profitable, 2 flat. best: degen +12.4%."
if history is empty: "no sessions yet. deploy your first one."

## key rules

- ONE session at a time. always check status first.
- paper mode is the default. live mode requires explicit approval.
- session has a timer. momentum = 48h. degen = 24h. defense = 168h.
- session can be ended early at any time.
- queued sessions auto-start when current session completes.

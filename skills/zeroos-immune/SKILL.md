# zeroos-immune

## Description
Self-monitoring immune system that verifies stops, detects position desync, tracks equity anomalies, and runs weekly self-audits. Runs independently of the signal engine.

## Commands

| User says | Command | Notes |
|-----------|---------|-------|
| "Check system health" | `zeroos status` | Shows immune heartbeat status |
| "Show immune log" | `grep "IMMUNE\|immune" ~/.zeroos/logs/agent.log \| tail -30` | Last immune checks |
| "Verify all stops" | `grep "STOP.*VERIFIED\|NAKED" ~/.zeroos/logs/agent.log \| tail -10` | Stop verification results |
| "Is the system healthy?" | `zeroos status` | Check all heartbeats are fresh |

## Automatic Checks (runs every 60 seconds)
- **Stop verification:** Every open position has a matching on-chain stop
- **Position sync:** Local state matches Hyperliquid reality
- **Equity tracking:** Detects anomalous equity changes
- **Heartbeat monitoring:** All subsystems responsive
- **Ghost detection:** Finds positions that exist locally but not on-chain
- **Orphan detection:** Finds on-chain positions not tracked locally

## Weekly Self-Audit
Runs automatically. Reviews all decisions, identifies patterns, scores system health.
Results logged to agent.log.

## Safety Invariants
- No position may exist without an on-chain stop (CRITICAL)
- If stop is missing, immune system places emergency stop immediately
- If position desync detected, reconciles from on-chain state (source of truth)
- Alerts suppressed in paper mode (logs only, no Telegram)

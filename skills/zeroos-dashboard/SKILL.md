# zeroos-dashboard

## Description
Opt-in telemetry that pushes performance data to getzero.dev for visual monitoring. The dashboard is strictly read-only — it cannot control, start, stop, or modify the agent.

## Commands

| User says | Command | Notes |
|-----------|---------|-------|
| "Connect to dashboard" | `zeroos dashboard --connect` | Generates token, starts telemetry |
| "Disconnect from dashboard" | `zeroos dashboard --disconnect` | Stops telemetry, revokes token |
| "Show dashboard status" | `zeroos status` | Shows connection state |
| "Open dashboard" | Open https://getzero.dev/app in browser | Web UI |

## Privacy
- Telemetry is opt-in (off by default)
- No keys or wallet addresses are ever shared
- Only performance data: equity snapshots, trade decisions, P&L, position count
- User can disconnect at any time with one command
- Dashboard token stored locally at ~/.zeroos/config.yaml
- Data pushed to Supabase (user's row only, RLS enforced)

## What the Dashboard Shows
- Live equity curve
- Decision stream (entries, exits, rejections with reasons)
- Open positions with unrealized P&L
- Signal universe (active/watching/blacklisted coins)
- System health indicators

## What the Dashboard CANNOT Do
- Start or stop the agent
- Modify configuration
- Execute trades
- Access private keys
- Change risk parameters

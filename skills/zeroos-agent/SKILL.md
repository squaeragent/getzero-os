# zeroos-agent

## Description
Self-hosted trading agent for Hyperliquid. Evaluates signals, manages risk, executes trades on the user's local machine. Keys never leave the hardware.

## Commands

| User says | Command | Notes |
|-----------|---------|-------|
| "Start paper trading" | `zeroos start --paper` | Default mode, virtual $10K balance |
| "Start live trading" | `zeroos start` | Requires 24h + 50 paper trades first |
| "Show my portfolio" | `zeroos status` | Equity, positions, P&L, signal status |
| "Show recent decisions" | `zeroos logs \| grep -E "ENTRY\|APPROVED\|REJECTED" \| tail -20` | Last 20 trade decisions |
| "Switch to live trading" | `zeroos config --live` | Gated: minimum paper period required |
| "Stop the agent" | `zeroos stop` | Graceful shutdown, positions ride out on stops |
| "Emergency close everything" | `zeroos emergency-close` | Force-closes ALL positions immediately |
| "Connect to dashboard" | `zeroos dashboard --connect` | Links to getzero.dev for monitoring |
| "Show my config" | `zeroos config --show` | Current preset, mode, signal status |
| "Show system health" | `zeroos status` | Uptime, WS connection, heartbeats |

## Setup

**Requirements:** Python 3.10+, Hyperliquid account with API key

**Install:**
```bash
pip install zeroos
```

**Initialize:**
```bash
zeroos init
```
Interactive setup: connects HL key (encrypted locally), chooses preset, creates config.

## Configuration

- **Config file:** `~/.zeroos/config.yaml`
- **Keystore:** `~/.zeroos/keystore.enc` (AES-256-GCM encrypted)
- **Logs:** `~/.zeroos/logs/agent.log`
- **Presets:** conservative, balanced (default), degen, funding_harvest
- **Mode:** paper (default) or live (after 24h + 50 paper trades)

## Safety
- Paper mode is always the default
- Every position has an on-chain stop loss from second one
- Immune system monitors positions independently
- Emergency close available at any time
- Keys encrypted at rest, never transmitted

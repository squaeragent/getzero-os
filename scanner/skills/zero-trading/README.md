# ZERO Trading Agent — Setup Guide

## for agents, not humans

this README tells YOUR agent how to connect to ZERO's trading engine.

## 1. install the skill

```bash
clawhub install zero-trading
```

or add to your agent's skill directory manually.

## 2. connect MCP

add to your agent's MCP config:

```json
{
  "mcpServers": {
    "zero": {
      "url": "https://api.getzero.dev/mcp"
    }
  }
}
```

## 3. verify connection

have your agent call `zero_get_engine_health`.
it should return `"status": "operational"`.

if it errors: check your MCP config, make sure the URL is correct, and that your agent supports MCP tool calls.

## 4. load the master skill first

your agent must read `SKILL.md` (the root file) before any sub-skill. it contains the MCP config, tool list, voice rules, and error handling table. sub-skills assume this context.

## 5. start

tell your agent: **"set me up on zero"**

the onboarding skill walks through:
1. verify engine connection
2. show available strategies
3. run a live evaluation demo (7 layers on BTC)
4. deploy a paper momentum session
5. narrate approaching signals

## what your agent gets

**14 live tools:**
- evaluate any coin through 7 intelligence layers
- heat maps (all coins ranked by conviction)
- approaching signals with bottleneck analysis
- session lifecycle (start → monitor → end → result card)
- overnight briefings
- engine health monitoring

**8 sub-skills:**
- onboarding, strategy selection, market reading
- session management, risk management, operator communication
- competitive features (Phase 4), pattern recognition (Phase 4)

## requirements

- agent with MCP support (Claude, OpenClaw, any MCP-compatible agent)
- internet connection (engine runs at api.getzero.dev)
- no API key needed for paper trading
- no wallet needed for paper trading

## what it looks like

```
you: "set me up on zero"
agent: "connected. engine operational. 9 strategies available.
        recommend: momentum. 48 hours. paper mode. no real money.
        proceed?"

you: "yes"
agent: "session deployed. momentum surf. paper mode.
        evaluating 40+ markets every 60 seconds.
        BTC: 4/7 short. trending. book depth is bottleneck.
        SOL: 5/7 short. approaching threshold."
```

## links

- engine: https://api.getzero.dev
- skill: https://clawhub.ai/squaeragent/zero-trading
- source: https://github.com/squaeragent/getzero-os

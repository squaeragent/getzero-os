# ZERO OS — Product Specification v1.0
*An operating system for autonomous trading agents*
*March 17, 2026*

---

## One-Liner

ZERO OS is a self-custodial runtime where anyone can deploy, evaluate, and trust autonomous trading agents across any market.

## The Problem

By 2028, every serious trader will use an autonomous agent. The problem isn't building agents — anyone can build one. The problems are:

1. **Trust.** How do you trust an agent with real money? There's no standardized way to evaluate agent performance, verify claims, or earn trust incrementally.

2. **Lock-in.** Every trading bot today is a black box. You can't compare strategies. You can't switch without losing your history. You can't run multiple agents and see which actually performs.

3. **Distribution.** Great strategy builders have no way to monetize. Great traders have no way to discover strategies. There's no marketplace where trust is earned, not claimed.

## The Product

ZERO OS is three things:

### 1. Runtime
The execution environment where trading agents run. Handles:
- Market data ingestion (any source, any asset)
- Signal evaluation against live market state
- Order execution (any exchange, any chain)
- Risk enforcement (position limits, stop losses, daily loss caps)
- Audit logging (every decision, every reason, every outcome)

The runtime is open-source. Agents plug into it like apps into an OS.

### 2. Trust Framework
A standardized system where agents earn trust through verified performance:

| Trust Level | Requirement | Capability |
|-------------|-------------|------------|
| **UNVERIFIED** | Newly deployed | Paper trading only. Observe mode. |
| **VERIFIED** | 30 days paper trading, >100 trades | Signal mode. Generates alerts, user approves each trade. |
| **TRUSTED** | 90 days live trading, positive Sharpe, <15% max drawdown | Semi-auto. One-tap approve with kill switch. |
| **AUTONOMOUS** | 1 year live trading, Sharpe >1.5, verified by protocol | Full auto within user's risk parameters. |

Trust is earned, not configured. An agent can't skip levels. Track records are cryptographically signed and publicly verifiable.

### 3. Marketplace
Strategy creators publish agents. Users subscribe. The protocol takes a fee.

- **For strategy builders:** Deploy your strategy as a ZERO OS agent. Set your fee (flat monthly or % of profits). Your track record is public and verified.
- **For traders:** Browse agents by asset class, risk profile, track record. Paper trade any agent for free. Pay only when you go live.
- **For the protocol:** 15% of all strategy fees. Compounds with volume.

---

## Architecture

### Agent Specification

An agent is a package containing:

```yaml
# agent.yaml
name: "momentum-regime-btc"
version: "1.0.0"
author: "0x1234...abcd"
description: "Trend-following BTC strategy using regime detection"
assets: ["BTC", "ETH", "SOL"]
exchanges: ["hyperliquid", "binance"]
risk:
  max_position_pct: 15
  max_daily_loss_pct: 10
  stop_loss_pct: 5
  max_leverage: 1
  max_open_positions: 5
signals:
  source: "envy"  # or "custom", "talib", "ml-model"
  entry_logic: "./signals/entry.py"
  exit_logic: "./signals/exit.py"
trust_level: "unverified"  # set by protocol, not by author
track_record: "https://zeroos.dev/agents/momentum-regime-btc/record"
```

### Runtime Layers

```
┌─────────────────────────────────────────┐
│           USER INTERFACE                │
│   CLI / Web Dashboard / Telegram Bot    │
├─────────────────────────────────────────┤
│           TRUST LAYER                   │
│   Enforces trust level permissions      │
│   Signs and verifies track records      │
├─────────────────────────────────────────┤
│           RISK ENGINE                   │
│   Position sizing, stop losses,         │
│   daily loss caps, kill switch          │
│   CANNOT be overridden by agent         │
├─────────────────────────────────────────┤
│           EXECUTION LAYER               │
│   Order routing, exchange adapters,     │
│   slippage control, retry logic         │
├─────────────────────────────────────────┤
│           SIGNAL LAYER                  │
│   Agent's strategy logic runs here      │
│   Ingests data, evaluates signals,      │
│   outputs trade decisions               │
├─────────────────────────────────────────┤
│           DATA LAYER                    │
│   Market data, indicators, sentiment    │
│   Envy API, custom feeds, on-chain      │
├─────────────────────────────────────────┤
│           KEY VAULT                     │
│   Locally encrypted API keys            │
│   Never transmitted, never accessible   │
│   by agent code                         │
└─────────────────────────────────────────┘
```

Key design principle: **The agent NEVER touches keys.** The signal layer outputs a trade decision. The risk engine validates it. The execution layer handles keys and places orders. The agent can't bypass the risk engine.

### Data Flow

```
Market Data (Envy, exchanges, on-chain)
    ↓
Signal Layer (agent's strategy logic)
    ↓
Trade Decision {coin, direction, size, entry, exit_rules}
    ↓
Risk Engine — VALIDATES
    ├── Within position limits? 
    ├── Within daily loss cap?
    ├── Stop loss defined?
    ├── Trust level allows this action?
    ├── NO → Block + log reason
    └── YES ↓
Trust Layer — ENFORCES MODE
    ├── OBSERVE → Log only, no execution
    ├── SIGNAL → Alert user, wait for approval
    ├── SEMI-AUTO → Execute, notify user, kill switch active
    └── AUTO → Execute silently within parameters
        ↓
Execution Layer
    ├── Route to exchange adapter
    ├── Place order (IOC/limit)
    ├── Confirm fill
    └── Log outcome
        ↓
Audit Log (immutable, signed)
    ├── Decision + reasoning
    ├── Risk checks passed/failed
    ├── Order details + fill price
    └── P&L attribution
```

### Track Record Verification

Every trade is logged with:
```json
{
  "agent_id": "momentum-regime-btc-v1",
  "timestamp": "2026-03-17T16:00:00Z",
  "coin": "BTC",
  "direction": "LONG",
  "entry_price": 74069.50,
  "exit_price": 74312.00,
  "pnl_pct": 0.33,
  "pnl_usd": 4.95,
  "hold_hours": 6.2,
  "signal_name": "XONE_AU_DIV_CONTRARIAN_LONG",
  "signal_sharpe": 2.45,
  "exit_reason": "signal",
  "risk_checks_passed": ["position_limit", "daily_loss", "stop_loss"],
  "signature": "0x..."
}
```

Track records are:
- **Signed** by the runtime (can't be fabricated by agent author)
- **Append-only** (can't delete losing trades)
- **Publicly queryable** (anyone can verify any agent's claims)
- **Standardized** (same metrics across all agents for comparison)

---

## Interfaces

### 1. CLI (Day 1)

```bash
# Install
npm install -g zero-os

# Deploy an agent
zero-os deploy ./my-strategy/agent.yaml

# List running agents
zero-os agents
# ID                  TRUST       STATUS    TRADES  SHARPE  WR
# momentum-btc        VERIFIED    running   143     1.82    64%
# mean-rev-eth        UNVERIFIED  paper     28      0.91    52%

# View agent reasoning in real-time
zero-os watch momentum-btc
# [16:00] BTC — Hurst 0.80 (trending), DFA 0.61, Lyapunov 1.68
# [16:00] 12 signals evaluated. 0 fired. Waiting.
# [16:15] 🔥 BTC LONG — XONE contrarian divergence, Sharpe 2.45
# [16:15] Risk check: PASS (position 12% < 15% limit)
# [16:15] Trust: SEMI-AUTO — awaiting approval
# → [A]pprove  [R]eject  [D]etails

# Portfolio across all agents
zero-os portfolio
# AGENT              VALUE      P&L     TRADES  WR
# momentum-btc       $1,247     +24.7%  143     64%
# mean-rev-eth       $980       -2.0%   28      52%
# TOTAL              $2,227     +11.4%

# Kill switch
zero-os kill-all
# All positions closed. All agents paused.

# Browse marketplace
zero-os browse --sort sharpe --min-trades 100
# Top agents by verified Sharpe ratio...
```

### 2. Web Dashboard (Month 2)

- Agent management (deploy, monitor, pause)
- Live reasoning view per agent
- Portfolio aggregation across agents
- Marketplace browsing and subscription
- Mobile-responsive

### 3. Telegram Bot (Month 3)

- Signal alerts from all running agents
- One-tap approve/reject in SIGNAL mode
- Portfolio summary on demand
- Kill switch command

---

## Marketplace Economics

### For Strategy Creators

| Model | Example | Platform Fee |
|-------|---------|-------------|
| **Monthly subscription** | $30/mo for signal access | 15% ($4.50) |
| **Performance fee** | 20% of profits above high-water mark | 15% of the 20% (3% effective) |
| **Free + premium** | Basic signals free, Trump-tier signals paid | 15% of paid tier |

Strategy creators set their own pricing. The protocol enforces payment and track record verification. Creators can't fake performance — the runtime signs all trades.

### For Users

- **Paper trade any agent for free** — no risk, evaluate before committing
- **Pay only for live trading** — subscription or performance fee to strategy creator
- **Run multiple agents** — diversify across strategies
- **Self-custodial always** — whether free or paid, your keys stay local

### Protocol Revenue

At scale:
- 10,000 users × $50/mo avg spend × 15% take rate = $900K/year
- 100,000 users × $50/mo avg spend × 15% take rate = $9M/year
- 1,000,000 users × $50/mo avg spend × 15% take rate = $90M/year

Performance fee model scales faster:
- $1B AUM × 10% avg annual return × 20% performance fee × 15% protocol = $3M/year
- $100B AUM × 10% avg annual return × 20% performance fee × 15% protocol = $300M/year

---

## Multi-Asset Expansion

The runtime is asset-agnostic. The signal layer and data layer are the only asset-specific components.

### Phase 1: Crypto Perpetuals (NOW)
- Hyperliquid, Binance, Bybit
- BTC, ETH, SOL + 37 altcoins via Envy
- 79 indicators, 15-min updates

### Phase 2: Crypto Spot (Month 6)
- DEX execution (Uniswap, Jupiter)
- On-chain sentiment (whale tracking, DEX flows)
- Same trust framework, same risk engine

### Phase 3: Tokenized Assets (Year 1)
- Tokenized equities (via Synthetix, dYdX)
- Forex pairs
- Commodities
- Same agents, new data sources

### Phase 4: Traditional Markets (Year 2)
- Interactive Brokers, Alpaca API
- US equities, options, futures
- Requires broker integration + compliance layer

---

## Security Model

### Key Isolation
```
Agent Code (untrusted)
    ↓ outputs trade decisions only
Risk Engine (trusted, runtime-controlled)
    ↓ validates decisions
Execution Layer (trusted, runtime-controlled)
    ↓ accesses keys
Key Vault (encrypted at rest, never in memory longer than execution)
```

An agent is treated like an untrusted plugin. It can:
- Read market data
- Output trade decisions
- Read its own historical performance

It CANNOT:
- Access API keys
- Bypass risk limits
- Modify its own trust level
- Delete audit logs
- Execute orders directly

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Malicious agent drains account | Agent can't access keys. Risk engine caps losses. |
| Agent manipulates track record | Runtime signs all trades. Append-only log. |
| Strategy creator front-runs users | Execution is local — creator never sees user's orders. |
| Exchange API key leak | Keys encrypted at rest. Trade-only permissions (no withdrawal). |
| Runtime compromise | Open source. Auditable. Local execution. |

---

## Competitive Positioning

|  | HyperAgent | MoltBot | Ethy | CoinVora | **ZERO OS** |
|---|---|---|---|---|---|
| What it is | Trading bot | Execution infra | Token+agent | Consumer bot | **Agent runtime** |
| Intelligence | 4-alpha | None (BYOS) | Vague | Basic | **Pluggable (Envy + custom)** |
| Self-hosted | ❌ | ✅ | ❌ | ❌ | **✅** |
| Hosted option | ✅ | ❌ | ✅ | ✅ | **Planned** |
| Trust framework | ❌ | ❌ | ❌ | ❌ | **✅ 4-level earned trust** |
| Multi-agent | ❌ | ✅ (manual) | ❌ | ❌ | **✅ (core feature)** |
| Marketplace | ❌ | ❌ | ❌ | ❌ | **✅ (planned)** |
| Multi-asset | ❌ crypto only | ✅ multi-exchange | ❌ Base only | ❌ Bitstamp only | **✅ (by design)** |
| Track verification | ❌ | ❌ | ❌ | ❌ | **✅ signed, public** |
| Open source | ❌ | ❌ | ❌ | ❌ | **✅** |

The key differentiator: **Nobody else is building a platform for multiple agents with earned trust and verified track records.**

---

## Roadmap

### Phase 1: Prove (NOW — Month 3)
- ✅ Signal scanner running (300+ signals, 10 coins, 15-min cycles)
- ✅ Paper trading with live P&L tracking
- ✅ Portfolio page showing real results
- ✅ Hyperliquid executor ready
- [ ] Live trading with $100 (validate execution)
- [ ] 90-day verified track record
- [ ] CLI packaging (npm install -g zero-os)
- [ ] Agent specification format (agent.yaml)

### Phase 2: Ship (Month 3-6)
- [ ] Open-source the runtime
- [ ] Agent SDK (Python + TypeScript)
- [ ] Second agent (different strategy, same runtime)
- [ ] Web dashboard
- [ ] Telegram bot for signals
- [ ] Hosted option (beta)

### Phase 3: Scale (Month 6-12)
- [ ] Strategy marketplace (v1)
- [ ] Track record verification system
- [ ] Multi-exchange support (Binance, Bybit)
- [ ] Spot trading support
- [ ] Performance fee infrastructure
- [ ] 10 verified agents on marketplace

### Phase 4: Platform (Year 1-2)
- [ ] Agent SDK public + documentation
- [ ] Community-built agents
- [ ] Multi-asset expansion
- [ ] Mobile app
- [ ] Institutional tier
- [ ] $10M+ AUM on platform

---

## What We're NOT Building

- **Not a hedge fund.** We don't manage money. Users manage their own.
- **Not a signal service.** We don't sell signals. Strategy creators do.
- **Not a black box.** Every decision is logged and verifiable.
- **Not a token project.** The product is the product. Token is optional infrastructure, not the business model.
- **Not a copy-trading platform.** Agents run independently. No leader/follower dynamic. Each agent has its own verified track record.

---

## Success Metrics

| Timeframe | Metric | Target |
|-----------|--------|--------|
| Month 3 | Paper trading track record | 90 days, >500 trades, Sharpe >1.0 |
| Month 6 | Live trading verification | $1,000+ AUM, positive returns |
| Month 6 | Open source | Runtime on GitHub, first external contributor |
| Month 9 | Marketplace v1 | 3+ agents, 1+ external strategy creator |
| Month 12 | Users | 100+ running agents on ZERO OS |
| Month 12 | Revenue | $10K MRR from marketplace fees |
| Year 2 | AUM on platform | $10M+ |
| Year 2 | Agents | 50+ verified agents |
| Year 3 | Multi-asset | 3+ asset classes |

---

*"Run any trading agent. Trust none of them blindly."*

*ZERO OS — the operating system for autonomous trading.*

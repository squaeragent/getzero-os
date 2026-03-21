# ZERO OS — System Specification V3

> This document supersedes V1 and V2. One document, complete picture.
> Audience: investors, partners, users, lawyers.
> Last updated: March 2026

---

## 1. What It Is

ZERO OS is a self-hosted operating system for trading agents on Hyperliquid.

---

## 2. The System in One Paragraph

ZERO OS runs on the user's own machine. Private keys never leave their hardware. Signal intelligence is sourced from the NVProtocol API — a catalog of 85 indicators, 370+ signal expressions, and 365-day backtested tournament assemblies. An immune system runs 13 continuous monitors and a weekly self-audit to catch and correct system drift before it becomes loss. Multiple agents can share signal intelligence while maintaining fully isolated execution environments. Paper mode is the default; live trading is gated behind demonstrated paper performance. If the signal API becomes unavailable, the system degrades gracefully across four signal modes — down to a protection state that manages existing positions without opening new ones.

---

## 3. Architecture

ZERO OS has three components. Each has a distinct role and a hard boundary.

### 3.1 Local Agent (User's Machine)

The agent is the only component with trading authority. It runs entirely on the user's hardware.

**Evaluator** — Consumes a real-time WebSocket signal stream from NVProtocol. Evaluates market conditions against active signal expressions. Determines whether trade entry, exit, or hold conditions are met.

**Executor** — Manages all order interactions with Hyperliquid. Places, monitors, amends, and closes positions. Handles fill verification and partial fills.

**Immune System** — 13 continuous monitors running on a 60-second cycle. Detects and reconciles state drift between the agent's internal model and on-chain reality. Runs a weekly self-audit with human-reviewed recommendations.

**CLI Control Plane** — All agent control is local command-line. No remote control surface. No web dashboard that can issue orders.

### 3.2 Signal API (NVProtocol)

The NVProtocol API is a data vendor. It does not trade. It does not hold funds. It provides signal intelligence that the local agent consumes to make its own decisions.

- **85 indicators** spanning Technical, Social, Chaos, Predictor, and CrossAsset categories
- **370+ signal expressions** with 365-day backtests and Monte Carlo overfit validation
- **Tournament assembly** — signals compete per coin; the best combination is selected, not assumed
- **Portfolio optimization** — correlation-aware coin selection across the agent's active universe
- **Deterministic evaluation** — same market state produces the same signal output, every time

Access is via x402 micropayments (pay-per-call, no account required) or API key.

### 3.3 Dashboard (getzero.dev)

The dashboard is a read-only telemetry viewer. It cannot issue orders, modify configuration, or control the agent in any way.

- Shows: equity curve, open positions, recent decisions, signal status
- Opt-in: the agent pushes telemetry only if the user enables it
- No authentication required to view (if the user shares their telemetry URL)

**Custody boundary:** The dashboard has no access to keys, no trading authority, and no ability to move funds.

### 3.4 Signal Degradation Modes

The agent operates across four signal modes depending on API availability. Degradation is automatic and logged.

| Mode | Condition | Behavior |
|------|-----------|----------|
| **FULL** | Signal API available | Normal operation — live signal stream |
| **CACHED** | API down, local cache fresh | Trades continue on cached signals |
| **BASIC** | API down, cache stale | Local RSI/EMA/MACD fallback — reduced confidence |
| **PROTECTION** | All else fails | No new trades. Manages existing positions safely until recovery |

The system never stops unexpectedly. It steps down.

---

## 4. Agent Presets

Presets define the agent's trading character. Users select one at setup and can reconfigure later.

**Conservative** — Few trades, high conviction entries only. Universe limited to BTC and ETH. Appropriate for users who want market exposure with minimal active management.

**Balanced** — Moderate risk across the top coins by signal quality. The default preset. Designed for consistent engagement without aggressive position sizing.

**Degen** — Higher trade frequency, wider coin universe, higher risk tolerance. For users who understand the risk profile and want maximum signal utilization.

**Funding Harvest** — Collects funding payments via delta-neutral positions. Does not take directional bets. Performance is driven by funding rate capture, not price movement.

---

## 5. Signal Engine

The signal engine is the NVProtocol API as consumed by the local evaluator. It does not make trades. It produces evaluations.

**Indicator catalog** — 85 indicators across five categories:
- Technical (price action, momentum, volume)
- Social (on-chain sentiment, community signals)
- Chaos (regime detection via chaos theory)
- Predictor (forward-looking composite models)
- CrossAsset (inter-market correlation and flow)

**Signal expressions** — 370+ expressions derived from the indicator catalog. Each expression has a 365-day backtest. Monte Carlo validation screens for overfit — expressions that perform well by luck are excluded.

**Regime detection** — 13 market regime states identified using three chaos theory indicators: Hurst exponent (trend persistence), Lyapunov exponent (system stability), and DFA alpha (long-range correlation). Regime state gates which signal expressions are active for a given coin at a given time.

**Tournament assembly** — For each coin, available signal expressions compete. The best-performing combination for current regime conditions is selected dynamically. No static signal stack.

**Portfolio optimization** — Coin selection is correlation-aware. The agent avoids concentrating capital in instruments that move together, reducing unintentional directional exposure.

**Determinism** — The signal engine is fully deterministic. Given the same market state, it produces the same evaluation. There is no randomness in the decision layer.

---

## 6. Execution Engine

The execution engine translates signal evaluations into Hyperliquid orders.

**Order types:**
- GTC limit orders (maker execution, 0.015% fee) — used for non-urgent signals
- IOC market orders — used for fresh, time-sensitive signals
- Native stop-loss triggers — placed on-chain at entry, not managed locally

**Stop offset** — The stop trigger price and the stop limit price are not the same. A slippage buffer is built in so that stop orders fill in fast markets rather than missing execution.

**Pre-trade risk gates** — Before any order is placed, the following are checked:
- Order book depth (sufficient liquidity at target price)
- Funding cost (position cost over expected hold)
- Alpha vs. cost (signal strength must exceed execution cost)
- Maximum concurrent positions
- Portfolio concentration

**Signal age routing** — Signals older than 10 minutes route to GTC limit. Fresh signals route to IOC market to avoid stale entries.

**Fill handling** — The executor verifies fills, handles partial fills, and retries on rejection. It does not assume an order filled because it was placed.

**Minimum hold time** — A minimum hold period prevents the agent from entering and immediately exiting a position on noise. Whipsaw exits are suppressed.

---

## 7. Immune System

The immune system is ZERO OS's self-protection layer. It operates independently of the trading loop.

### Continuous Monitors (every 60 seconds)

| Monitor | Function |
|---------|----------|
| Stop verification | Confirms every open position has an active on-chain stop |
| Position sync | Validates that local position state matches Hyperliquid reality |
| Equity anomaly | Detects unexpected changes in account equity |
| Heartbeat monitoring | Confirms all agent subsystems are responsive |
| Ghost detection | Finds positions the agent doesn't know about (on-chain but not local) |
| Orphan detection | Finds positions the agent thinks exist but don't (local but not on-chain) |
| Auto-reconciliation | Resolves detected desyncs automatically where safe; flags others for review |

When the immune system detects a critical discrepancy it cannot safely resolve automatically, it halts new trading and alerts the user.

### Weekly Self-Audit

Once per week, the agent reviews its full trade history and produces a structured report:

- Win rate and expectation by coin, preset, and regime
- Pattern identification (e.g., persistent losses on specific instruments)
- Concrete recommendations (e.g., "exclude coin X — 0% win rate over N trades")

**The human reviews and approves all recommendations.** The agent does not modify its own configuration autonomously. The immune system diagnoses. The human decides.

---

## 8. Multi-Agent

ZERO OS supports running multiple agents from a single installation.

**Shared signal intelligence** — All agents consume the same NVProtocol signal stream. Signal API calls are not duplicated per agent.

**Isolated execution** — Each agent maintains its own positions, equity accounting, stop orders, and decision log. Agents do not share capital or orders.

**Per-agent configuration** — Each agent can have a different preset, coin universe, and risk parameters.

**Virtual capital allocation** *(beta)* — Agents can be allocated a defined virtual capital budget within a single funded account. Useful for running parallel strategies without multiple wallets.

**Independent decision streams** — Each agent evaluates signals and places orders independently. One agent's drawdown does not affect another's position management.

> Multi-agent is fully specced and operational in the development environment. External user deployment is in progress.

---

## 9. Reasoning

ZERO OS can explain its decisions in natural language.

**What it explains:**
- Why a trade was entered (which signals, what regime, what conviction)
- Why a trade was exited (signal reversal, stop trigger, hold time)
- Why a trade was rejected (gate failed, liquidity insufficient, cost exceeded alpha)

**How it works:**
- Explanations are generated asynchronously after each decision — they do not block execution
- Explanations are delivered in the user's configured language
- The system prompt governing reasoning is designed to prevent leaking internal parameters, thresholds, or proprietary signal logic

**Optional:** Reasoning can be disabled to reduce API costs. The trading system operates identically with or without it.

---

## 10. CLI Reference

All agent control is via the `zeroos` command-line interface. There is no remote control surface.

```
zeroos init              # Interactive setup: key, preset, config
zeroos start --paper     # Paper mode — virtual $10K, no real orders
zeroos start             # Live mode — gated behind paper performance criteria
zeroos status            # Current equity, positions, P&L, signal mode
zeroos logs              # Tail the unified agent log
zeroos stop              # Graceful shutdown — finishes current cycle, exits cleanly
zeroos emergency-close   # Force-close ALL open positions immediately
zeroos config --show     # Display current configuration (keys redacted)
zeroos config --live     # Switch to live mode if paper criteria are met
```

**Security:**
- Private keys are stored in an encrypted keystore using AES-256-GCM encryption with Argon2 key derivation
- Keys are never written to disk unencrypted
- Keys are never transmitted to any external service

**State management:**
- All agent state lives in `~/.zeroos/` — config, cache, keystore, logs
- Daemon mode with PID file management
- Clean shutdown on SIGTERM; emergency-close on SIGINT or explicit command

**Paper mode:**
- Default for all new installations
- Simulates trade execution with virtual capital
- Full signal evaluation, full immune system, full logging — nothing is skipped
- Live mode requires meeting paper performance criteria and explicit opt-in

---

## 11. Known Limitations

ZERO OS is honest about what it is and what it isn't.

**Backtest vs. reality gap** — Backtested signal performance does not guarantee realized performance. The immune system's weekly audit actively tracks this gap and surfaces it for human review.

**Self-diagnosing, not self-adjusting** — The immune system identifies problems and recommends fixes. It does not apply fixes autonomously. Human review and approval is required for all configuration changes.

**Limited live trading history** — Approximately 90 trades have been executed on a development wallet. Performance on a funded production wallet has not yet been established.

**Multi-agent availability** — Multi-agent support is fully specced and operational internally. It has not yet been shipped to external users.

**x402 payment flow** — The x402 micropayment integration for Signal API access has not yet been tested with real external users in production conditions.

**Correlation analysis** — Portfolio concentration risk from correlated coin selection is acknowledged. Correlation-aware optimization is in the signal engine, but real-world correlation behavior during market stress has not been validated at scale.

---

## 12. Legal Structure

ZERO OS is designed from the ground up to operate outside the perimeter of regulated asset management.

**Software vendor, not asset manager** — ZERO OS sells software. It does not manage assets on behalf of users. The user's agent, running on the user's hardware, makes all trading decisions.

**Non-custodial** — The user's private keys stay on the user's machine. ZERO OS (the company) never holds, touches, or has access to user funds or keys at any point.

**Signal API as data vendor** — NVProtocol provides market data and signal intelligence via x402 micropayments. It is a data vendor. It does not execute trades, hold funds, or manage accounts. No subscription, no account, no custody.

**Dashboard as read-only telemetry** — getzero.dev displays information the user's agent chooses to share. It cannot issue orders, modify configuration, or interact with Hyperliquid in any way.

**No custody at any point in the stack** — The local agent holds keys. The signal API holds data. The dashboard holds telemetry. Nothing in this architecture constitutes custody of user assets.

> *This structure is intentional and architectural, not incidental. If you are evaluating ZERO OS from a regulatory perspective, start with the custody question. The answer is always: the user's machine, the user's keys, the user's money.*

---

*ZERO OS System Specification V3 — March 2026*
*Replaces V1 and V2. Contact: degenie@getzero.dev*

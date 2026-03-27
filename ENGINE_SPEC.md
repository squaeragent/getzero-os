# ZERO BUGATTI ENGINE — V1 SPECIFICATION

**Build:** v1.0.0-bugatti
**Codebase:** 6,315 lines across 8 core files
**Test suite:** 506+ tests, 1 skipped, 0 failures
**Exchange:** Hyperliquid (perpetuals, cross-margin)

---

## ARCHITECTURE

```
┌─────────────────────────────────────────────────┐
│                   SESSION                        │
│   lifecycle · result cards · narrative builder    │
├──────────────┬──────────────────────────────────┤
│   MONITOR    │         CONTROLLER               │
│   stateless  │         stateful                  │
│   evaluates  │         executes                  │
│   7 layers   │         manages positions         │
│   emits      │         reads signals             │
│   signals    │         enforces risk              │
├──────────────┴──────────────────────────────────┤
│                   HL CLIENT                      │
│   API adapter · order routing · position query   │
├─────────────────────────────────────────────────┤
│               STRATEGY LOADER                    │
│   9 YAML configs · consensus thresholds · scope  │
├─────────────────────────────────────────────────┤
│                  EVENT BUS                       │
│   signals.json · decisions.jsonl · events.jsonl  │
│   positions.json · trades.jsonl · metrics.jsonl  │
│   heartbeat.json · session.json · approaching    │
└─────────────────────────────────────────────────┘
```

**Separation of concerns:**
- Monitor says WHAT the market shows
- Controller decides WHAT TO DO
- Session manages WHEN it starts and ends
- HLClient handles HOW to talk to HL
- Strategy Loader defines THE RULES

---

## CORE FILES

| File | Lines | Responsibility |
|------|-------|----------------|
| `controller.py` | 2,437 | Execution, risk, positions, trailing stops, hard caps, reconciliation, shutdown, event bus |
| `monitor.py` | 1,309 | 7-layer evaluation, signal state machine, approaching detection, cycle metrics |
| `session.py` | 861 | Lifecycle state machine, result cards, narrative builder, near miss, cost tracking |
| `hl_client.py` | 340 | HL API adapter: orders, positions, balance, book, funding |
| `strategy_loader.py` | 432 | YAML config loading, validation, 9 strategy definitions |
| `smart_provider.py` | 349 | Raw market data → evaluation indicators |
| `config.py` | 243 | Environment, paths, dynamic limits |
| `immune_v2.py` | 344 | Independent position protection, stop verification, dead man's switch |

---

## EVALUATION ENGINE — 7 LAYERS

Every coin, every cycle, evaluated through 7 independent layers:

| # | Layer | What it measures | Data source |
|---|-------|-----------------|-------------|
| 1 | **Regime** | Market regime (trending/ranging/volatile) | Price action, volatility |
| 2 | **Technical** | Directional indicators (RSI, EMA, MACD) | SmartProvider technicals |
| 3 | **Funding** | Funding rate dislocation | HL predictedFundings API |
| 4 | **Book** | Order book depth imbalance (bid vs ask) | HL L2 book |
| 5 | **OI** | Open interest trend (rising/falling) | HL metaAndAssetCtxs |
| 6 | **Macro** | Fear & Greed index extremes | Alternative.me API |
| 7 | **Collective** | V1: cross-indicator composite score. Network agent consensus planned for V2 | Composite of above |

**Consensus model:** Each layer votes pass/fail. Consensus = count of passes.
Strategy threshold determines minimum consensus (5/7, 6/7, 7/7).
Conviction = weighted score of passing layers (0.0–1.0).

**Evaluation is PURE:** `evaluate_coin()` has zero side effects.
All state changes happen in `run_cycle()` after ALL coins are evaluated.

---

## SIGNAL STATE MACHINE

```
inactive ──[consensus ≥ threshold]──→ entry
    ↑                                    │
    │                          [consensus drops OR
    │                           RSI overbought]
    │                                    ↓
    └──────[regime shift]──── entry_end
                                    │
                          [consensus < threshold
                           OR regime shift]
                                    ↓
                                  exit
```

**Signal types:** ENTRY, ENTRY_END, EXIT
**Deduplication:** No re-emit if state hasn't changed
**Data freshness:** V1 uses REST polling (batch allMids). WebSocket planned for V2.

---

## 9 STRATEGIES

| Strategy | Emoji | Duration | Scope | Threshold | MaxPos | Stop | Size | Risk |
|----------|-------|----------|-------|-----------|--------|------|------|------|
| Momentum | 🌊 | 48h | top 50 | 5/7 | 5 | 3% | 10% | moderate |
| Degen | 🔥 | 24h | top 100 | 5/7 | 6 | 6% | 15% | high |
| Defense | 🛡 | 7d | top 20 | 6/7 | 3 | 2% | 7% | low |
| Sniper | 🎯 | 72h | top 20 | 7/7 | 3 | 4% | 20% | high |
| Scout | 🔍 | 72h | top 200 | 5/7 | 5 | 3% | 10% | moderate |
| Fade | 🔄 | 7d | top 50 | 5/7 | 4 | 3% | 10% | moderate |
| Funding | 💰 | 48h | top 50 | 6/7 | 4 | 2% | 8% | low |
| Watch | 👁 | 48h | top 50 | 5/7 | 0 | 0% | 0% | none |
| Apex | ⚡ | 7d | top 100 | 6/7 | 8 | 8% | 20% | extreme |

**YAML-enforced per strategy:** max_positions, max_daily_loss_pct, reserve_pct, max_hold_hours, entry_end_action, consensus_threshold, min_regime, position_size_pct, stop_loss_pct, trailing_stop, trailing_activation_pct, trailing_distance_pct.

---

## HARD CAPS (NON-CONFIGURABLE)

These cannot be overridden by YAML or operator:

| Cap | Value | Purpose |
|-----|-------|---------|
| Max position size | 25% of equity | Single position limit |
| Max exposure | 80% of equity | Total open positions |
| Orders per minute | 10 | Rate limiting |
| Orders per session | 100 | Runaway prevention |

Checked BEFORE strategy-level risk gates. Always enforced.

---

## ROLLS ROYCE SYSTEMS (7)

### 1. Decision Log
Every evaluation decision logged to `decisions_{session_id}.jsonl`.
Who was evaluated, what passed, what failed, why.
144,000 decisions per 48h session (50 coins × 60 cycles/h × 48h).

### 2. Position Reconciliation
Every 5 minutes: query HL for actual positions.
Compare local state vs HL state. HL is ALWAYS truth.
Auto-reconcile discrepancies. Log any drift.

### 3. Graceful Shutdown
SIGTERM/SIGINT handlers. Write full state to `controller_state.json`.
Session can be resumed from persisted state.
Log rotation on shutdown.

### 4. Dead Man's Switch
60-second heartbeat written to `heartbeat.json`.
If controller stops writing: external monitor detects death.
HEARTBEAT events on the event bus.

### 5. Event Bus
12 event types emitted to `events.jsonl`:
SESSION_STARTED, SESSION_COMPLETED, SESSION_FAILED,
TRADE_ENTERED, TRADE_OPENED, TRADE_EXITED, TRADE_CLOSED,
ENTRY_EXECUTED, EXIT_EXECUTED,
RISK_BREACH, NEAR_MISS, HEARTBEAT.

### 6. Hard Caps
4 non-configurable limits (see above).
Checked before YAML strategy validation.
Rejection logged with reason.

### 7. Rejection Counter + Narrative
Tracks every rejection with reason.
Builds human-readable session narrative:
"143,598 setups rejected. 114 passed. 1 trade. 1 win."

---

## BUGATTI SYSTEMS (6)

### B1. Approaching Detection
Coins within 2 layers of threshold flagged as APPROACHING.
Urgency: "high" (1 away), "low" (2 away), "cooling" (dropped).
Bottleneck analysis: identifies which failing layer is closest to flipping.
Deduplication: only emits on state changes.
"SOL forming. 5/7. Book depth is the bottleneck."

### B2. Slippage Tracking
Every trade: signal_price vs order_price vs fill_price.
Latency: signal_to_order_ms, order_to_fill_ms, signal_to_fill_ms.
Slippage in basis points. Per-session aggregates (avg/max).
Paper mode: 0 slippage.

### B3. Layer Accuracy Tracking
After every trade close: which layers voted correctly?
Pass vote + win = correct. Pass vote + loss = incorrect.
Per-layer accuracy percentages over time.
Data foundation for V2 adaptive consensus weighting.

### B4. Execution Metrics
Per-cycle: duration, data fetch time, evaluation time, signal emission time.
Data freshness tracking. Memory usage. Stale source detection.
Per-session aggregates: avg/max cycle duration, total cycles.

### B5. Cost Tracking
CPU seconds, API call counts by type, peak memory, log file sizes.
Estimated $/session based on compute model.

### B6. HL Testnet Verification
Documented round-trip test: place order → verify → cancel → verify gone.
Skip by default. Run manually with testnet credentials.

---

## IMMUNE SYSTEM (immune_v2.py)

Independent position protection — runs every 60 seconds, cannot be disabled while positions are open.
Completely independent of monitor and controller evaluation logic.

**Core responsibilities:**
1. **Stop Verification:** Query HL `get_open_orders()` for every open position. If any position is missing a stop order, auto-repair it immediately.
2. **Stop Repair:** Place missing stops via `hl_client.place_stop_loss()` using the position's configured stop_loss_pct from entry.
3. **Heartbeat:** Write `immune` timestamp to `bus/heartbeat.json` every cycle.
4. **Dead Man's Switch:** If controller heartbeat age > 300s (5 minutes):
   - Tighten ALL stops to 1% from current price
   - Emit `IMMUNE_DEAD_MAN_TRIGGERED` event
   - Alert: "Controller unresponsive. Immune protecting."
5. **Event Logging:** All actions logged to `events.jsonl`.

**Architecture:** `ImmuneSystem` class — reads `positions.json`, writes `heartbeat.json`, talks to HL directly.
Launched by supervisor as a background daemon (`immune_v2.py --loop`).

---

## TRAILING STOP EXECUTION

Per-cycle trailing stop logic in `controller.py` → `check_trailing_stops()`.

**Per position, every cycle:**
1. Get current price from HL
2. Read strategy trailing config: `trailing_stop`, `trailing_activation_pct`, `trailing_distance_pct`
3. If trailing enabled:
   - **Activation:** price crosses entry × (1 + activation_pct/100) for LONG (or 1 - for SHORT)
   - **Peak tracking:** `trailing_peak` = max(peak, current) for LONG, min(peak, current) for SHORT
   - **Trigger:** peak × (1 - distance_pct/100) for LONG, peak × (1 + distance_pct/100) for SHORT
   - **Close:** if current price crosses trigger → close with `exit_reason="trailing_stop"`
4. `trailing_peak` persisted in `positions.json` — survives restart.

**Strategy trailing configs (from YAML):**
| Strategy | Activation | Distance |
|----------|-----------|----------|
| Momentum | +1.5% | 1.0% |
| Degen | +2.0% | 1.5% |
| Defense | +1.0% | 0.7% |
| Sniper | +2.0% | 1.5% |
| Scout | +1.5% | 1.0% |
| Fade | +1.5% | 1.0% |
| Funding | +1.0% | 0.7% |
| Watch | disabled | — |
| Apex | +2.5% | 2.0% |

---

## SESSION LIFECYCLE

```
PENDING ──→ ACTIVE ──→ COMPLETING ──→ COMPLETED
                                ↗
   ANY ──────────────→ FAILED
```

### State Transitions
- **PENDING → ACTIVE:** Strategy validated, monitor started, timer set
- **ACTIVE → COMPLETING:** Duration expired OR operator ends early
- **COMPLETING → COMPLETED:** All positions closed, result card built
- **ANY → FAILED:** Unrecoverable error, positions force-closed

### Session Result Card
Generated at completion. Contains:
- Trade count, wins, losses, best/worst trade
- Total P&L (USD + %), max drawdown
- Evaluation count, rejection count, rejection rate
- Near misses with alternative strategy analysis
- Timeline events (hour-by-hour narrative)
- Layer accuracy summary
- Execution quality aggregates
- Session cost

### Narrative Builder
Produces human-readable session story:
```
48-hour Momentum session.
2,880 cycles. 143,598 rejected.
Hour 8: SOL emerged. Entered long at $148.20. conviction 0.85.
Hour 18: trailing triggered. +$2.90 (+1.96%).
Result: 1 trade. 1 win. +$2.90.
Near miss: AVAX +6.8%. Degen would have caught it.
```

---

## NEAR MISS DETECTION

Retrospective analysis at session end.
For each coin that MOVED significantly but was REJECTED:
- What strategy was active?
- Which strategies WOULD have caught it?
- Which layers failed for the active strategy?
- Estimated gain if entered.

"AVAX moved +6.8% during hours 22-30.
Your Momentum (6/7) saw it reach 5/7 but never 6/7.
Degen (5/7) would have entered at hour 23. Estimated +$4.10."

---

## RISK MANAGEMENT

### Per-Strategy (YAML-configurable)
- `max_positions`: Maximum concurrent open positions
- `max_daily_loss_pct`: Daily loss circuit breaker
- `reserve_pct`: Equity held in reserve
- `max_hold_hours`: Maximum hold time per position
- `stop_loss_pct`: Stop loss percentage per position
- `position_size_pct`: Position size as % of equity
- `consensus_threshold`: Minimum layers to agree
- `min_regime`: Required market regime
- `entry_end_action`: What to do when signal enters ENTRY_END

### Stop Loss Verification (Tier 1 Safety)
After placing stop: query HL open orders to CONFIRM stop is active.
2 attempts, 1 second apart. If not confirmed: emergency close position.
Live mode only.

### Private Key Security
grep-verified: private key NEVER appears in logs, alerts, jsonl files, or error messages.
hl_client.py logs wallet address only. config.py logs SET/MISSING, never value.

---

## DATA PIPELINE

### Batch API Calls (3 per cycle, not 150)
1. `allMids` — all prices in one call
2. `predictedFundings` — all funding rates
3. `metaAndAssetCtxs` — all OI data

Plus: Fear & Greed (cached, 10min TTL), L2 book (per qualifying coin only)

### Bus File Architecture
```
bus/
├── signals.json           # current signal state per coin
├── approaching.json       # coins near threshold (B1)
├── positions.json         # active positions
├── controller_state.json  # full controller state
├── session.json           # active session state
├── heartbeat.json         # dead man's switch
├── risk.json              # daily P&L, loss tracking
├── entries.json           # pending entries
├── exits.json             # pending exits
├── approved.json          # approved trades
├── decisions_{sid}.jsonl  # per-session decision log
├── events.jsonl           # event bus
├── trades.jsonl           # completed trades
├── near_misses.jsonl      # near miss records
├── metrics.jsonl          # cycle metrics (B4)
├── layer_accuracy.jsonl   # layer voting records (B3)
└── session_history.jsonl  # completed session results
```

---

## HL CLIENT CAPABILITIES

| Method | Purpose |
|--------|---------|
| `get_balance()` | Account equity |
| `get_positions()` | All open positions |
| `get_open_orders()` | All resting orders |
| `get_price(coin)` | Current mid price |
| `get_predicted_funding(coin)` | Predicted funding rate |
| `get_l2_book(coin, depth)` | Order book snapshot |
| `get_fee_rates()` | Maker/taker fees |
| `market_buy(coin, size, slippage)` | IOC buy |
| `market_sell(coin, size, slippage)` | IOC sell |
| `place_ioc_order(coin, is_buy, size, price)` | IOC limit |
| `place_gtc_order(coin, is_buy, size, price)` | GTC limit |
| `place_stop_loss(coin, is_buy, size, trigger)` | Stop order |
| `get_rate_limit()` | API rate limit status |

---

## CONVICTION-BASED SIZING

Position size scales with conviction:
- 6/7 at 0.85 conviction = larger position
- 6/7 at 0.62 conviction = smaller position
- Base size from strategy YAML, scaled by conviction score

---

## MODES

| Mode | Behavior |
|------|----------|
| **Paper** | Full pipeline, no real orders. All outputs marked `[PAPER]`. |
| **Live** | Real orders on HL. Stop verification active. |
| **Watch** | Evaluation only, no execution. Strategy = Watch Mode. |

Paper mode: every alert, log, and output CLEARLY marked `[PAPER]`.

---

## TEST COVERAGE

| Suite | Tests | What it covers |
|-------|-------|----------------|
| Controller | 146 | Risk gates, hard caps, position management, event bus |
| Monitor | 39 | 7 layers, signal state machine, data cache, near miss |
| Strategy Loader | 49 | YAML validation, all 9 strategies, edge cases |
| Integration | 5 | Full pipeline: monitor → controller → trades |
| Session | 31 | Lifecycle, result cards, narrative, state transitions |
| Bugatti | 18 | Approaching, slippage, layer accuracy, metrics, cost |
| Other | 204 | Config, calibration, bus I/O, risk guard legacy |
| **Total** | **492** | **0 failures, 1 skipped (testnet)** |

---

## SCALE

Per 48-hour Momentum session:
- 2,880 evaluation cycles
- 144,000 coin evaluations (50 coins × 2,880 cycles)
- 3 API calls per cycle = 8,640 HL API calls
- ~3 decision log entries per cycle = ~8,640 decisions logged

Per 168-hour Apex session:
- 10,080 cycles
- 504,000+ evaluations
- 30,240 API calls
- Decision log rotated per session to prevent unbounded growth

---

## WHAT THE ENGINE DOES NOT DO

- **No leverage management** — uses HL cross-margin defaults
- **No adaptive consensus weighting** — data collected (B3) but V2 feature
- **No WebSocket** — V1 uses REST polling (batch allMids). WebSocket planned for V2
- **No concurrent sessions** — V1: single session. Concurrent sessions planned for Scale tier
- **No Supabase** — all state is local files
- **No cloud dependency** — runs on a single machine
- **No backtesting** — forward-only evaluation

---

## ENGINE IDENTITY

**Name:** ZERO BUGATTI ENGINE
**Version:** 1.0.0
**Lines of code:** 5,840 (production) + 2,500+ (tests)
**Dead code:** 0 (12,097 lines purged in Session 11)
**Architecture:** 4 components (Monitor → Controller → Session → HLClient)
**Exchange:** Hyperliquid perpetuals
**Language:** Python 3.14
**Dependencies:** Minimal (requests, pathlib, dataclasses)

The engine doesn't just trade. It WATCHES. It MEASURES. It LEARNS.

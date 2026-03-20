# ZERO OS — Competitive Intelligence Report
*Generated: 2026-03-20 | Source: actual source code, not READMEs*

---

## 1. freqtrade (48K⭐) — The UX and Execution Bar

### Architecture Patterns to Adopt

**Strategy Interface (IStrategy):**
- 3 mandatory methods: `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`
- Returns DataFrame with columns: `enter_long`, `exit_long`, `enter_short`, `exit_short`
- Minimal strategy = ~40 lines of actual code (SampleStrategy template)
- Hyperopt parameters: `IntParameter`, `DecimalParameter`, `CategoricalParameter` — typed, ranged, and optimizable
- Built-in: `minimal_roi` (time-based exit table), `stoploss`, `trailing_stop`

**Trade Data Model (SQLAlchemy):**
- Full `Order` model with SQLAlchemy ORM, mapped columns, relationships
- Tracks: `order_id`, `status`, `filled`, `remaining`, `average`, `cost`, `funding_fee`, `ft_fee_base`
- `safe_*` properties for null-safe access (`safe_price`, `safe_filled`, `safe_remaining`)
- Order updates via `update_from_ccxt_object()` — normalizes exchange-specific responses
- Unique constraint: `(ft_pair, order_id)` — dedup at DB level

**Plugin Architecture (Exchange System):**
- Base class `Exchange` → per-exchange subclasses: `Hyperliquid`, `Binance`, `Bybit`, etc.
- 25+ exchange adapters in `/freqtrade/exchange/`
- `_ft_has` dict pattern: each exchange declares capabilities as feature flags
- `@retrier` decorator for automatic retry with exponential backoff
- CCXT as the universal exchange abstraction layer

### Features to Steal

| Feature | Implementation Effort | Priority |
|---------|----------------------|----------|
| Strategy hyperopt parameters | 2 days | P3 |
| SQLAlchemy trade model | 3 days | P2 |
| `_ft_has` feature flag pattern for exchange capabilities | 4 hours | P1 |
| `safe_*` null-safe property pattern | 2 hours | P0 |
| `@retrier` decorator with backoff | 2 hours | P0 (we partially have this) |
| Time-based `minimal_roi` exit | 4 hours | P2 |
| FreqAI continual learning with sliding window | 1 week | P4 |

### Mistakes to Avoid

1. **CCXT dependency = exchange at their mercy.** freqtrade's HL support depends on ccxt's `Hyperliquid` class. When ccxt breaks, freqtrade breaks. Our direct HL API approach is better.
2. **FreqAI complexity explosion.** `IFreqaiModel` is 500+ lines. Threading, data drawers, model caching, PCA pipelines — it became its own product inside freqtrade. We shouldn't build a general ML framework.
3. **`stoploss_on_exchange: False` for HL.** They set this to false in `_ft_has` because their HL integration can't handle native stops properly. We already do native HL stops.
4. **`ohlcv_has_history: False` for HL.** They can't backtest on HL historical data. We can via ENVY history endpoint.

### Gaps We Exploit (verified from code)

| Gap | Evidence | Our Advantage |
|-----|----------|---------------|
| No signal scoring | Strategy outputs binary enter/exit | We score every signal: Sharpe, WR, overfit detection via ENVY API |
| No adversarial quality gate | All entries are trusted | Our adversary challenges every trade before execution |
| No real-time indicator streaming | 5m OHLCV polling | 15s WebSocket from ENVY (85 indicators × 40 coins) |
| Limited HL order types | `stoploss_order_types: {"limit": "limit"}` only | We have IOC, GTC, ALO, limit stops with offset |
| No funding rate pre-check | Not in execution path | We query `predictedFundings` before every entry |
| No L2 book depth check | Not in execution path | We check depth vs order size before entry |
| No HL-native API | Uses CCXT abstraction | We use raw HL API — faster, more order types, no middleman |

### Viral / GTM Lessons

1. **"Works out of the box" matters more than sophistication.** 48K stars from `pip install freqtrade && freqtrade new-strategy` → running bot in 10 minutes
2. **Telegram integration drove adoption.** Users monitor bots via Telegram — we already have this
3. **FreqUI (web dashboard)** — visual interface was a major star driver
4. **Strategy marketplace** — community sharing strategies = network effect
5. **Docker-first deployment** — lowers the bar massively
6. **Documentation quality** — freqtrade.io is excellent, structured, searchable

---

## 2. nautilus_trader (21K⭐) — The Architecture Bar

### Architecture Patterns to Adopt

**Event-Driven Architecture (Cython):**
- Everything is an event: `OrderAccepted`, `OrderFilled`, `OrderCanceled`, `OrderRejected`, `OrderDenied`, `OrderEmulated`, `OrderExpired`, `OrderTriggered`, `OrderUpdated`, `OrderPendingCancel`, `OrderPendingUpdate`, `OrderReleased`
- Position events: `PositionOpened`, `PositionChanged`, `PositionClosed`
- `MessageBus` pub/sub: strategies subscribe to `events.order.{strategy_id}` and `events.position.{strategy_id}`
- Zero coupling between strategy and execution — everything flows through the message bus

**Strategy Interface (Cython):**
- Inherits from `Actor` (the generic component base)
- Override hooks: `on_start()`, `on_stop()`, `on_resume()`, `on_reset()`
- `OrderManager` handles contingent orders, GTC/GTD expiry, market exit logic
- `OrderFactory` creates orders with strategy-scoped client order IDs
- OMS types: `UNSPECIFIED`, `HEDGING` (per-position IDs), `NETTING` (single position per instrument)

**Key Design Principles:**
- **Backtest-to-live parity:** Same `Strategy` class runs in both modes. The engine swaps `Cache`, `Clock`, `MessageBus` implementations.
- **Typed everything:** Cython (`cdef`, `cpdef`) for hot path performance. Python for user-facing API.
- **Separation:** Strategy never talks to exchange directly. Orders go through `ExecutionEngine` → exchange adapter.

### Features to Steal

| Feature | Effort | Priority |
|---------|--------|----------|
| Event-based order state machine (12 states) | 3 days | P1 |
| MessageBus pub/sub for component decoupling | 2 days | P2 |
| OrderFactory with strategy-scoped client IDs | 4 hours | P0 (we have cloid, need factory) |
| OMS type abstraction (hedging vs netting) | 1 day | P3 |
| Market exit timer with configurable TIF | 4 hours | P2 |

### Mistakes to Avoid

1. **Cython = build complexity.** Great for perf, terrible for contribution barrier. Our Python-only approach is correct for the current stage.
2. **No HL adapter.** nautilus_trader doesn't support Hyperliquid at all. We're ahead here.
3. **Overhead for small operations.** The full event pipeline (Order → Event → MessageBus → Handler → Log) is overkill for our 3-position system. Adopt the pattern when we scale.

### Gaps We Exploit

| Gap | Evidence | Our Advantage |
|-----|----------|---------------|
| No AI/ML signal intelligence | Pure quant framework, no ML | We have 4,400 scored signals from ENVY |
| No Hyperliquid support | Not in adapter list | We have production HL execution |
| No adversarial quality gate | All strategy signals trusted | We gate every signal |
| No signal marketplace | Framework only | We have ENVY signal library |

### Viral / GTM Lessons

1. **"Institutional grade" positioning** — targets quant shops, not retail
2. **Rust core marketing** — "nanosecond precision" drives developer credibility
3. **Academic partnerships** — papers published using nautilus
4. **Slower growth but sticky users** — 21K stars but very engaged community

---

## 3. TradingAgents (33K⭐) — The Narrative Competitor

### Architecture Patterns to Adopt

**Multi-Agent via LangGraph:**
- `TradingAgentsGraph` orchestrates: Analyst Team → Researcher Debate → Trader → Risk Team → Portfolio Manager
- Two LLM tiers: `deep_think_llm` (GPT-5.2, complex reasoning) and `quick_think_llm` (GPT-5-mini, fast tasks)
- Tool nodes: `market`, `social`, `news`, `fundamentals` — each a LangGraph `ToolNode`
- Memory: `FinancialSituationMemory` per role (bull, bear, trader, judge, risk_manager)
- Debate rounds: configurable `max_debate_rounds`, `max_risk_discuss_rounds`
- Reflection: `reflect_and_remember(returns_losses)` — agents learn from P&L outcomes

**The Core Loop:**
```
ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
```
One function call: ticker + date → buy/sell/hold decision.

### Features to Steal

| Feature | Effort | Priority |
|---------|--------|----------|
| Dual LLM tier (deep vs quick) | Already have (Opus vs Sonnet) | Done |
| Bull/bear structured debate | 1 day | P3 (we have adversary, different approach) |
| Reflection with P&L feedback | 2 days | P2 (we have counterfactual, similar) |
| One-function propagation API | 4 hours | P1 (simplify our API surface) |

### Mistakes to Avoid

1. **NO LIVE TRADING.** This is the critical finding. TradingAgents has zero execution capability. `ta.propagate()` returns a text decision — no exchange connector, no orders, no P&L tracking. It's a research simulator.
2. **No backtesting rigor.** No slippage simulation, no fee modeling, no lookahead bias prevention. The "performance" in the paper is LLM-judged, not market-verified.
3. **LLM cost per decision = massive.** Each `propagate()` call runs 6+ LLM calls (4 analysts + debate rounds + trader + risk). At GPT-5.2 prices, that's $0.50-2.00 per trade decision. Our system: $0.00 per decision (quantitative signals, no LLM in the loop).
4. **Non-deterministic.** Same ticker + same date = different decisions. Temperature, model version, news API responses all vary. Our quantitative signals are deterministic.

### Gaps We Exploit (CRITICAL — marketing gold)

| Gap | Evidence from Source Code | Our Advantage |
|-----|--------------------------|---------------|
| No execution | `process_signal()` returns text | We fill orders on HL in <1s |
| No backtesting | No backtest engine | ENVY backtests every signal (365-day, Sharpe/WR) |
| No real P&L | Paper returns only | $747 live equity, 71+ real trades |
| No market data pipeline | Relies on Alpha Vantage | 85 indicators × 40 coins via WebSocket |
| $0.50-2/decision | 6+ LLM calls per trade | $0/decision (quantitative) |
| Non-deterministic | LLM temperature variance | Deterministic signal scoring |
| No risk management engine | LLM "discusses" risk | Per-coin stops, funding checks, L2 depth, adversary scoring |

### Viral / GTM Lessons (THIS IS THE MOST IMPORTANT SECTION)

1. **ArXiv paper drove the spike.** Paper published Dec 2024 (arXiv:2412.20138) → star explosion. Academic credibility = trust.
2. **"Multi-agent" is a buzzword magnet.** The framework itself is simple. The narrative is powerful: "A trading firm in your laptop."
3. **CLI demo with live progress.** Visual CLI showing agents "thinking" → screenshots went viral on Twitter/X.
4. **Multi-LLM support = broad appeal.** OpenAI, Google, Anthropic, xAI, Ollama — everyone can try it.
5. **YouTube demo video** linked in README — visual storytelling beats documentation.
6. **"Research purposes" disclaimer** — avoids regulatory scrutiny while building community.
7. **33K stars with NO execution capability.** Proof that narrative > product in open source. We have the product — we need the narrative.

---

## 4. Secondary Repos — Targeted Extractions

### Hummingbot HL Connector (18K⭐)

Files: `hyperliquid_exchange.py`, `hyperliquid_api_order_book_data_source.py`, `hyperliquid_api_user_stream_data_source.py`, `hyperliquid_auth.py`

Key findings:
- **Separate user stream data source** — WS subscription for user fills/orders. We poll instead.
- **Auth module** — dedicated `hyperliquid_auth.py` for wallet signature management
- **Order book data source** — dedicated class for L2 data streaming
- **More mature HL integration** than freqtrade (they wrote it native, not through CCXT)

**Applicable to ZERO OS:** User stream WebSocket for instant fill detection (P3 TODO). Their auth pattern is cleaner than our inline signing.

### FinRL (14K⭐) — RL for Trading

- Uses DRL algorithms: PPO, A2C, DDPG, SAC, TD3
- State space: OHLCV + technical indicators + turbulence index
- Action space: continuous [-1, 1] per asset (short to long)
- Reward: portfolio return
- **Viability for us:** Could optimize position sizing and order type selection. But we'd need 1000+ trades of clean data first. P4 at best.

### VectorBT (7K⭐) — Speed Patterns

- Vectorized backtesting using NumPy/pandas — no row-by-row iteration
- 100-1000x faster than event-driven backtesting
- **Applicable:** Our 270s signal scoring could benefit from vectorization if we batch signals. But our bottleneck is ENVY API latency, not compute.

### OctoBot (5K⭐) — HL Comparison

- Supports HL via community connector
- Less mature than hummingbot's integration
- Focus on social copy trading
- **Not directly useful** — we're ahead on HL integration

---

## 5. SYNTHESIS — V7 Architecture Decisions

### The Competitive Landscape in One Table

| Capability | freqtrade | nautilus | TradingAgents | ZERO OS |
|-----------|-----------|----------|---------------|---------|
| Live trading | ✅ 25+ exchanges | ✅ many | ❌ | ✅ HL |
| HL support | ✅ (via CCXT) | ❌ | ❌ | ✅ (native) |
| Backtesting | ✅ rigorous | ✅ best-in-class | ❌ | ✅ (via ENVY) |
| AI/ML signals | ✅ (FreqAI) | ❌ | ✅ (LLM) | ✅ (85 indicators, scored) |
| Signal scoring | ❌ | ❌ | ❌ | ✅ (Sharpe/WR/overfit) |
| Adversarial gate | ❌ | ❌ | ❌ (LLM "debate") | ✅ |
| Real-time streaming | ❌ (polling) | ✅ | ❌ | ✅ (15s WS) |
| Strategy UX | ✅ (40 lines) | ⚠️ (Cython) | ✅ (1 function) | ❌ (config only) |
| GitHub stars | 48K | 21K | 33K | 0 |

### What We're Missing for Stars

1. **Strategy interface for users** — Users can't define custom strategies. freqtrade's 40-line template is the bar. TradingAgents' 2-line `propagate()` is even simpler.
2. **Documentation website** — All 3 have dedicated docs sites. We have nothing.
3. **Docker/pip install** — `pip install zero-os` → running. We have manual setup.
4. **Academic paper** — TradingAgents got 33K stars largely from their arXiv paper.
5. **Visual demo** — CLI progress display, YouTube video, screenshots.
6. **Multi-exchange support** — or at least, pluggable exchange adapters.

### V7 Priority Stack (for GitHub stars)

| Priority | What | Why | Effort |
|----------|------|-----|--------|
| P0 | **Clean open-source repo** | Without this, nothing matters | 2 days |
| P0 | **One-command install** | `pip install zero-os` | 1 day |
| P0 | **Strategy template** | Users define entry/exit in <20 lines | 3 days |
| P1 | **docs.getzero.dev** | Searchable, structured, examples | 3 days |
| P1 | **Paper or technical blog post** | "Signal Intelligence: Scoring 4,400 trading signals" | 2 days |
| P1 | **Demo video** | CLI running, signals scoring, trade executing | 1 day |
| P2 | **Event-based order model** | Nautilus pattern, adapted for Python | 1 week |
| P2 | **Exchange adapter interface** | HL first, then Binance/Bybit | 1 week |
| P3 | **FreqAI-like ML integration** | But simpler — signal selection via ML | 2 weeks |
| P3 | **Strategy marketplace** | Community signals and strategies | 2 weeks |

### Our Narrative (verified against competition)

**What nobody else does:**
> "Every trading bot executes signals. None of them question the signal first."

- freqtrade: Strategy says buy → bot buys
- nautilus: Strategy says buy → bot buys (faster, with better state management)
- TradingAgents: LLM says buy → paper portfolio records it (no execution)
- **ZERO OS: Signal says buy → 85 indicators score it → adversary challenges it → funding/depth/alpha checks → IF it survives, bot buys**

That's the pitch. That's the paper. That's the star driver.

### The "Brain, Not Body" Positioning

Open source the signal intelligence layer. Keep the data source (ENVY equivalent) as the business model:
- **Open source:** Signal scoring, adversary gate, risk guard, executor
- **Proprietary/Paid:** Signal data (85 indicators, 40 coins, WebSocket), backtested signal library (4,400 signals)
- **freqtrade model:** Open source bot → paid signal marketplace. $180M+ in community value.

---

## Appendix: Key Numbers

- freqtrade: 48K stars, 7.8K forks, 2.1K issues closed, 25+ exchanges
- nautilus: 21K stars, Rust/Cython core, institutional target
- TradingAgents: 33K stars, 0 live trades, LLM-only
- hummingbot: 18K stars, $34B/yr volume, HFT focus
- FinRL: 14K stars, academic, DRL algorithms
- ZERO OS: 0 stars, 71+ live trades, $747 equity, 85 indicators, 4,400 scored signals

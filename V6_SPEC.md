# V6: ENVY-Native Trading Engine

## Context
V5 had 20 agents producing 41% WR and $0.14 P&L across 68 trades.
ENVY's backtested strategies show 60-80% WR, Sharpe 3-4, 80-162% returns over 90 days.
Our pipeline was destroying ENVY's signal quality with 15+ layers of interference.

## Architecture: 4 Components

### 1. Strategy Manager (`scanner/v6/strategy_manager.py`)
- On startup and every 6 hours: call `/paid/strategy/assemble` for each active coin
- Call `/paid/portfolio/optimize` to get allocation weights
- Store assembled strategies in `scanner/v6/strategies.json`
- Store portfolio allocation in `scanner/v6/allocation.json`
- Active coins: use portfolio/optimize to pick best 8 coins from the 40 available
- Each coin gets its top signals ordered by priority (ENVY handles the tournament)

### 2. Signal Evaluator (`scanner/v6/evaluator.py`)
- Connects to WebSocket (`wss://gate.getzero.dev/api/claw/ws/indicators?token=API_KEY`)
- Every 15s receives all indicator values for 40 coins
- For each active coin: evaluate entry expressions from strategy_manager's signal list
- For each open position: evaluate exit expressions
- When entry fires: write to `scanner/v6/bus/entries.json`
- When exit fires: write to `scanner/v6/bus/exits.json`
- Signal priority: higher priority signal can preempt lower (per ENVY docs)
- 15-minute minimum hold before evaluating exits (ENVY recommendation)
- Expression evaluation: reuse existing `evaluate_expression()` from hypothesis_generator

### 3. Risk Guard (`scanner/v6/risk_guard.py`)
- Reads entries.json and positions
- Applies ONLY these checks:
  - max_positions: 3
  - max_per_coin: 1
  - capital_floor: $500 (halt all trading if equity drops below)
  - stop_loss: use ENVY's `stop_loss_pct` from strategy/assemble (default 30% for normal mode — we override to 5-7% for our account size)
  - daily_loss_limit: $50 (halt for 24h)
- Writes approved entries to `scanner/v6/bus/approved.json`
- NO adversary, NO observer kills, NO alignment exits, NO correlation exposure calc

### 4. Executor (`scanner/v6/executor.py`)
- Reads approved.json and exits.json
- Opens positions on Hyperliquid using IOC orders (ENVY recommendation)
- Closes positions when exit signals fire or stop loss hits
- Position sizing: from allocation.json weights × available capital
- Telegram alerts on open/close (reuse existing alert code)
- Writes to Supabase (trades table) and local JSONL
- Tracks P&L per signal for performance monitoring

## Data Flow
```
ENVY WS (15s) → Evaluator → entries.json / exits.json
                                ↓              ↓
Strategy Manager (6h) →    Risk Guard      Executor
                              ↓              ↓
                        approved.json    Hyperliquid
                              ↓              ↓
                          Executor      Supabase + Telegram
```

## File Structure
```
scanner/v6/
├── strategy_manager.py    # Assembles strategies from ENVY
├── evaluator.py           # WebSocket + expression evaluation
├── risk_guard.py          # Position limits + stop loss
├── executor.py            # Hyperliquid execution
├── supervisor.py          # Runs all 4 components
├── config.py              # All configuration in one place
├── bus/
│   ├── strategies.json    # Assembled strategies per coin
│   ├── allocation.json    # Portfolio allocation weights
│   ├── entries.json       # Pending entry signals
│   ├── exits.json         # Pending exit signals
│   ├── approved.json      # Risk-cleared entries
│   ├── positions.json     # Open positions
│   └── risk.json          # Risk state
└── data/
    └── trades.jsonl       # Trade history
```

## Configuration (`config.py`)
```python
# Account
CAPITAL = 750.0
CAPITAL_FLOOR = 500.0
DAILY_LOSS_LIMIT = 50.0

# Position limits
MAX_POSITIONS = 3
MAX_PER_COIN = 1
MAX_POSITION_USD = 250.0
MIN_POSITION_USD = 50.0

# Risk
STOP_LOSS_PCT = 0.05  # 5% hard stop (override ENVY's 30%)
MIN_HOLD_MINUTES = 15  # ENVY recommendation

# Strategy refresh
STRATEGY_REFRESH_HOURS = 6
ACTIVE_COINS_COUNT = 8  # From portfolio/optimize

# ENVY API
ENVY_BASE_URL = "https://gate.getzero.dev/api/claw"
ENVY_WS_URL = "wss://gate.getzero.dev/api/claw/ws/indicators"

# Hyperliquid
HL_API_URL = "https://api.hyperliquid.xyz/info"
HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"

# Telegram alerts
TELEGRAM_CHAT_ID = "133058580"
TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

# Supabase
SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_SERVICE_KEY"

STRATEGY_VERSION = 6
```

## What We Keep From V5
- `evaluate_expression()` function (proven, works with ENVY expressions)
- Hyperliquid order placement code (IOC orders)
- Supabase client for trade recording
- Telegram alert formatting
- WebSocket connection code from ws_stream.py
- Portfolio page (reads from Supabase, no changes needed)

## What We Kill
- Adversary (18 attacks) → ENVY's backtesting handles signal quality
- Observer (7 kill conditions) → ENVY's exit expressions handle exits
- Correlation agent → simple max_positions check
- Cross-timeframe agent → ENVY already uses optimal timeframes
- Signal evolution → ENVY evolves signals server-side
- Parameter evolution → no parameters to evolve
- Counterfactual → ENVY provides backtests
- Genealogy → no signal lineage needed
- Hypothesis generator complexity → replaced by strategy/assemble
- Alignment exits → killed in v5, now fully removed
- Regime agent → ENVY indicators capture regime
- Funding agent → not needed as primary filter
- Pack refresher → replaced by strategy/assemble
- Perception polling → replaced by WS-only

## Supervisor (`supervisor.py`)
Simple process manager:
1. Start strategy_manager (runs once, then every 6h)
2. Start evaluator (continuous WebSocket loop)
3. Start risk_guard (continuous, checks entries every 5s)
4. Start executor (continuous, checks approved + exits every 5s)
5. Health check: restart any component that hasn't heartbeated in 60s

## Migration
- V5 agents continue running until V6 is tested
- V6 lives in scanner/v6/ — completely separate directory
- Can run V6 in paper mode first (log trades without executing)
- Switch over: stop V5 supervisor, start V6 supervisor
- Close any V5 positions manually before switching

## Success Criteria
- WR > 55% (ENVY backtests show 60-80%)
- Sharpe > 1.5
- Max drawdown < 15%
- Positive P&L after 50 trades
- All trades have ENVY signal attribution

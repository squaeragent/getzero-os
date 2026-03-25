# ZERO OS — Trading Agent Team Specification
*Version 1.0 — 2026-03-18*

## Architecture Overview

```
                    ┌─────────────┐
                    │  RISK AGENT │ ← master kill switch
                    │   (Agent 5) │
                    └──────┬──────┘
                           │ veto / throttle
            ┌──────────────┼──────────────┐
            │              │              │
     ┌──────┴──────┐ ┌────┴─────┐ ┌──────┴──────┐
     │   REGIME    │ │  SIGNAL  │ │ CORRELATION │
     │   AGENT    │ │ HARVESTER│ │   AGENT     │
     │  (Agent 1) │ │ (Agent 2)│ │  (Agent 3)  │
     └──────┬──────┘ └────┬─────┘ └──────┬──────┘
            │              │              │
            └──────────────┼──────────────┘
                           │ trade decisions
                    ┌──────┴──────┐
                    │ EXECUTION   │
                    │   AGENT     │
                    │  (Agent 4)  │
                    └─────────────┘
                           │
                    ┌──────┴──────┐
                    │ HYPERLIQUID │
                    └─────────────┘
```

**Data flow:**
1. Regime Agent reads Envy chaos indicators → publishes regime state
2. Signal Harvester evaluates signal packs against current indicators → publishes trade candidates
3. Correlation Agent filters candidates for portfolio-level risk → publishes approved trades
4. Execution Agent places orders on Hyperliquid with optimal pricing
5. Risk Agent monitors everything, can veto any trade or kill all positions

**Communication:** File-based message bus at `scanner/bus/`
- Each agent writes to its own output file
- Agents read from upstream agent files
- State persists across restarts

---

## Agent 1: REGIME AGENT

### Purpose
Detect market regime and regime *transitions* across all 40 coins using Envy chaos indicators. The money is in the transition window.

### Inputs
- Envy API: `DFA_24H`, `DFA_48H`, `HURST_24H`, `HURST_48H`, `LYAPUNOV_24H`, `LYAPUNOV_48H` for all 40 coins
- Runs every 5 minutes (3x faster than current 15-min cycle)

### Outputs → `scanner/bus/regimes.json`
```json
{
  "timestamp": "2026-03-18T05:30:00Z",
  "coins": {
    "BTC": {
      "regime": "trending",
      "confidence": 0.82,
      "prev_regime": "reverting",
      "transition": true,
      "transition_age_min": 12,
      "hurst_24h": 0.80,
      "dfa_24h": 0.64,
      "lyapunov_24h": 1.81,
      "hurst_trend": "rising",
      "dfa_trend": "rising"
    }
  }
}
```

### Regime Classification
| Regime | Condition | Trading implication |
|--------|-----------|-------------------|
| `trending` | Hurst > 0.55 AND DFA > 0.55 | Momentum strategies, ride trends |
| `reverting` | Hurst < 0.45 AND DFA < 0.45 | Mean-reversion, fade extremes |
| `chaotic` | Lyapunov > 0.85 | No edge, reduce exposure |
| `shift` | Hurst and DFA disagree (one high, one low) | Transition forming, prepare |
| `stable` | All indicators near 0.50 | Low vol, small positions |

### Transition Detection
- Compare current regime to regime 15 min ago (stored in `scanner/bus/regime_history.jsonl`)
- Flag `transition: true` when regime changes
- Track `transition_age_min` — how many minutes since the shift started
- Track `hurst_trend` / `dfa_trend` — "rising", "falling", "flat" (compare 24H vs 48H values)

### Key Logic
```python
def detect_transition(current, previous):
    if current["regime"] != previous["regime"]:
        return True
    # Also detect forming transitions: indicators moving toward threshold
    if previous["hurst_24h"] < 0.50 and current["hurst_24h"] > 0.50:
        return True  # crossing from reverting toward trending
    return False
```

### Constraints
- Envy API: max 10 coins per request, ~7 indicators per request
- Need 4 requests for 40 coins × 6 chaos indicators
- 5-min cycle = 288 requests/day (within limits)

---

## Agent 2: SIGNAL HARVESTER

### Purpose
Dynamically evaluate and rank ALL available signal packs. Track which signals are currently hot (hitting) and which are cold (missing). Weight the portfolio toward proven performers.

### Inputs
- Envy API: signal pack discovery + indicator snapshots
- Regime state from Agent 1 (`scanner/bus/regimes.json`)
- Historical fire data (`scanner/data/fires.jsonl`, `scanner/data/closed.jsonl`)
- Runs every 10 minutes

### Outputs → `scanner/bus/candidates.json`
```json
{
  "timestamp": "2026-03-18T05:30:00Z",
  "candidates": [
    {
      "coin": "LINK",
      "direction": "SHORT",
      "signal": "LINK_SHORT_REVERSAL_V3",
      "sharpe": 1.69,
      "win_rate": 77,
      "regime_match": true,
      "signal_heat": 0.85,
      "recent_record": "5W/1L",
      "composite_score": 8.2,
      "recommended_size_pct": 0.35
    }
  ]
}
```

### Signal Heat Scoring
```python
def signal_heat(signal_name, recent_fires, recent_closed):
    """
    Score 0-1 based on recent performance.
    Decay: most recent trades weighted 2x.
    """
    trades = [t for t in recent_closed if t["signal"] == signal_name]
    if len(trades) < 3:
        return 0.5  # neutral — not enough data
    
    wins = sum(1 for t in trades[-10:] if t["pnl_dollars"] > 0)
    total = len(trades[-10:])
    recency_bonus = 0.1 if trades[-1]["pnl_dollars"] > 0 else -0.1
    
    return min(1.0, max(0.0, wins / total + recency_bonus))
```

### Composite Score (0-10)
```
composite = (sharpe * 1.5) + (win_rate/100 * 3) + (signal_heat * 2) + (regime_match * 1.5)
```
- `regime_match`: does signal direction align with current regime?
  - Trending regime + momentum signal = +1.5
  - Reverting regime + reversal signal = +1.5
  - Chaotic regime + any signal = -1.0 (penalty)

### Signal Pack Rotation
- Current: 30 packs per coin (10% of available)
- Harvester fetches additional packs for coins in transition regime
- Rotates cold packs out, hot packs in
- Target: 100+ packs per coin active at any time

### Key feature: Regime-Signal Alignment
The harvester ONLY recommends signals that match the current regime:
- In trending regime → only trend-following signals (momentum, EMA cross, breakout)
- In reverting regime → only mean-reversion signals (RSI oversold, BB bounce)
- In chaotic regime → no signals (or only very high Sharpe > 2.5)

---

## Agent 3: CORRELATION AGENT

### Purpose
Prevent correlated bets. Detect divergences. Manage portfolio-level directional exposure.

### Inputs
- Trade candidates from Agent 2 (`scanner/bus/candidates.json`)
- Current live positions (`scanner/data/live/positions.json`)
- Envy API: `ROC_3H`, `ROC_6H`, `ROC_12H`, `ROC_24H` for correlation measurement
- Regime state from Agent 1
- Runs every 5 minutes (or triggered by new candidates)

### Outputs → `scanner/bus/approved.json`
```json
{
  "timestamp": "2026-03-18T05:30:00Z",
  "approved": [
    {
      "coin": "LINK",
      "direction": "SHORT",
      "signal": "LINK_SHORT_REVERSAL_V3",
      "size_usd": 40,
      "reason": "uncorrelated to existing BTC LONG, regime-aligned"
    }
  ],
  "blocked": [
    {
      "coin": "ETH",
      "direction": "LONG",
      "signal": "ETH_MOMENTUM_LONG",
      "reason": "correlated to existing BTC LONG (r=0.87)"
    }
  ],
  "portfolio_state": {
    "net_direction": "LONG",
    "net_exposure_pct": 35,
    "correlation_risk": "medium",
    "diversification_score": 0.6
  }
}
```

### Correlation Matrix
```python
def compute_correlation(coin_a, coin_b, roc_data, window="12H"):
    """
    Use ROC (rate of change) to measure co-movement.
    If two coins move together > 0.7 correlation, treat as correlated.
    """
    roc_a = roc_data[coin_a][f"ROC_{window}"]
    roc_b = roc_data[coin_b][f"ROC_{window}"]
    # Simplified: sign agreement + magnitude similarity
    same_sign = (roc_a > 0) == (roc_b > 0)
    mag_ratio = min(abs(roc_a), abs(roc_b)) / max(abs(roc_a), abs(roc_b), 0.001)
    return mag_ratio if same_sign else -mag_ratio
```

### Portfolio Rules
1. **Net exposure cap**: max 60% of capital in one direction (long or short)
2. **Correlation block**: don't add LONG if existing LONGs correlate > 0.7 with candidate
3. **Divergence bonus**: if a coin decouples from correlated peers, increase score
4. **Sector limits**: max 2 positions in "BTC-correlated" group (BTC, ETH, SOL), max 2 in "alt-coin" group

### Correlation Groups (pre-defined, refined by data)
```python
CORRELATION_GROUPS = {
    "majors": ["BTC", "ETH", "SOL"],
    "l1_alts": ["AVAX", "NEAR", "SUI", "ARB"],
    "defi": ["LINK", "UNI", "AAVE"],
    "meme": ["DOGE", "FARTCOIN", "PUMP"],
    "solo": ["INJ"]  # tends to decorrelate
}
```

---

## Agent 4: EXECUTION AGENT

### Purpose
Place orders with optimal pricing. Manage partial fills. Scale into positions.

### Inputs
- Approved trades from Agent 3 (`scanner/bus/approved.json`)
- Hyperliquid API (REST + WebSocket)
- Runs continuously (event-driven, not cron)

### Outputs → `scanner/data/live/positions.json` (updates in place)

### Execution Strategies

#### Strategy 1: Aggressive IOC (current, for small orders < $30)
```python
# Current approach: market order with 1% slippage
limit_px = mid_price * (1.01 if is_buy else 0.99)
place_order(coin, is_buy, size, limit_px, order_type="Ioc")
```

#### Strategy 2: Scaled Entry (for orders $30-100)
```python
# Split into 2-3 tranches at improving prices
tranche_1 = size * 0.5  # at mid price (IOC)
tranche_2 = size * 0.3  # at mid - 0.1% (limit, GTC 60s)
tranche_3 = size * 0.2  # at mid - 0.2% (limit, GTC 120s)
# Cancel unfilled limits after timeout
```

#### Strategy 3: TWAP (for orders > $100, future)
```python
# Hyperliquid has native TWAP orders
sdk.exchange.placeTwapOrder({
    coin, is_buy, sz, reduce_only=False,
    minutes=5, randomize=True
})
```

### WebSocket Integration
```python
# Subscribe to position updates (real-time fill notifications)
ws.subscribe({"type": "userFills", "user": MAIN_ADDRESS})
ws.subscribe({"type": "orderUpdates", "user": MAIN_ADDRESS})

# Subscribe to order book for execution quality
ws.subscribe({"type": "l2Book", "coin": coin})
```

### Position Lifecycle
1. Receive approved trade from bus
2. Check current HL state (positions, margin)
3. Choose execution strategy based on size
4. Place order(s)
5. Monitor fills via WebSocket
6. Update local state + write to bus
7. Set stop loss order on HL (native stop, not local check)

### Hyperliquid Native Stops
```python
# Place a stop loss as a separate trigger order
place_order(coin, is_buy=False, size=pos_size, 
            limit_px=stop_price,
            order_type={"trigger": {
                "isMarket": True,
                "triggerPx": str(stop_price),
                "tpsl": "sl"
            }})
```
This runs on HL's servers — executes even if our agent is down.

---

## Agent 5: RISK AGENT

### Purpose
Portfolio-level risk management. Kill switch. Drawdown detection. Strategy health monitoring.

### Inputs
- All bus files (regimes, candidates, approved, positions)
- Hyperliquid API (positions, account value, order history)
- `scanner/data/live/closed.jsonl` (trade history)
- Runs every 2 minutes

### Outputs → `scanner/bus/risk.json`
```json
{
  "timestamp": "2026-03-18T05:30:00Z",
  "status": "normal",
  "account_value": 116.50,
  "unrealized_pnl": 1.50,
  "realized_pnl_today": 0.35,
  "drawdown_pct": 0.0,
  "max_drawdown_pct": 0.0,
  "win_streak": 2,
  "lose_streak": 0,
  "strategy_health": "green",
  "throttle": 1.0,
  "kill_all": false,
  "blocked_coins": [],
  "alerts": []
}
```

### Risk Levels
| Level | Condition | Action |
|-------|-----------|--------|
| `green` | Drawdown < 3%, WR > 55% trailing | Full trading, throttle = 1.0 |
| `yellow` | Drawdown 3-7% OR 3+ consecutive losses | Reduce size 50%, throttle = 0.5 |
| `orange` | Drawdown 7-12% OR 5+ consecutive losses | Only close positions, no new trades |
| `red` | Drawdown > 12% OR daily loss > $15 | KILL ALL positions, halt trading |

### Drawdown Tracking
```python
def update_drawdown(account_history):
    peak = max(h["value"] for h in account_history)
    current = account_history[-1]["value"]
    drawdown = (peak - current) / peak * 100
    return drawdown
```

### Strategy Health
Track rolling 20-trade metrics:
- Win rate (must stay > 50%)
- Average win / average loss ratio (must stay > 1.0)
- Sharpe of actual P&L (must stay > 0)
- If any metric degrades for 10+ trades, flag `yellow`

### Kill Switch
```python
def check_kill(risk_state):
    if risk_state["drawdown_pct"] > 12:
        return True  # kill all
    if risk_state["realized_pnl_today"] < -15:
        return True  # daily loss limit
    if risk_state["lose_streak"] >= 5 and risk_state["strategy_health"] == "orange":
        return True  # strategy broken
    return False
```

### Alert Channel
Risk agent can send alerts via the OpenClaw message bus:
- Telegram alert to Igor on `orange` or `red`
- Daily P&L summary at end of trading day (00:00 UTC)

---

## Shared Infrastructure

### Message Bus (`scanner/bus/`)
```
scanner/bus/
├── regimes.json          # Agent 1 output (current regime per coin)
├── regime_history.jsonl  # Agent 1 append log
├── candidates.json       # Agent 2 output (trade candidates)
├── approved.json         # Agent 3 output (filtered trades)
├── risk.json             # Agent 5 output (risk state)
└── heartbeat.json        # All agents write their last-alive timestamp
```

### Heartbeat Monitor
Each agent writes to `heartbeat.json`:
```json
{
  "regime": "2026-03-18T05:30:00Z",
  "harvester": "2026-03-18T05:28:00Z",
  "correlation": "2026-03-18T05:29:00Z",
  "execution": "2026-03-18T05:30:01Z",
  "risk": "2026-03-18T05:30:02Z"
}
```
Risk agent checks heartbeats — if any agent is >10 min stale, alert.

### Shared Config (`scanner/config.yaml`)
```yaml
capital: 115
max_positions: 3
max_per_coin: 1
max_notional: 100
stop_loss_pct: 0.05
trailing_stop_trigger: 0.02
trailing_stop_lock: 0.50
min_sharpe: 1.5
min_win_rate: 60
envy_api_key: ${ENVY_API_KEY}
hl_secret: ${HYPERLIQUID_SECRET_KEY}
hl_main_address: "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e"
```

### Implementation Order
1. **Agent 1 (Regime)** — foundational, all others depend on it
2. **Agent 5 (Risk)** — safety first, must exist before scaling
3. **Agent 2 (Harvester)** — replaces current scanner signal logic
4. **Agent 3 (Correlation)** — portfolio intelligence
5. **Agent 4 (Execution)** — replaces current executor

### Technology
- Python 3.14, no heavy SDKs (raw HTTP for Envy + HL)
- WebSocket via `websockets` library for HL real-time data
- Each agent is a standalone Python script
- Orchestrated by a single `run_agents.py` supervisor
- LaunchAgent keeps supervisor alive

### File Structure
```
scanner/
├── agents/
│   ├── regime_agent.py
│   ├── signal_harvester.py
│   ├── correlation_agent.py
│   ├── execution_agent.py
│   └── risk_agent.py
├── bus/
│   ├── regimes.json
│   ├── regime_history.jsonl
│   ├── candidates.json
│   ├── approved.json
│   ├── risk.json
│   └── heartbeat.json
├── config.yaml
├── run_agents.py          # supervisor
├── signal_scanner.py      # existing (becomes redundant after Agent 2)
├── hyperliquid_executor.py # existing (becomes redundant after Agent 4)
└── data/
    ├── live/
    ├── signals_cache/
    ├── fires.jsonl
    ├── closed.jsonl
    └── positions.json
```

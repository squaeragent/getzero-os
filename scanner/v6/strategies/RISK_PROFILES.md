# Strategy Risk Profiles — ZERO Trading Engine V1

All strategies enforce **hard caps** (non-configurable):
- **25%** max single position
- **80%** max total exposure
- **10** max simultaneous positions
- **100%** hard equity floor (reserve never negative)

---

## Strategy Overview

| Strategy | Tier | Risk Level | Max Drawdown | Use Case |
|----------|------|------------|-------------|----------|
| Watch | — | None | 0% | Observe only, no trades |
| Defense | Free | Conservative | 6% | Capital preservation |
| Funding | Free | Conservative | 8% | Funding rate arbitrage |
| Momentum | Free | Moderate | 15% | Trend following |
| Scout | Free | Moderate | 15% | Wide scan, patient entry |
| Fade | Pro | Moderate | 12% | Mean reversion plays |
| Sniper | Pro | High | 12% | High conviction, strict filter |
| Degen | Scale | Aggressive | 24% | Fast, large positions |
| Apex | Scale | Extreme | 32% | Maximum aggression |

---

## Detailed Risk Parameters

### 🛡️ Defense Shield
```
Positions:     3 max
Size:          7% per position
Stop:          2%
Reserve:       35% (always held back)
Max exposure:  56% (3×7% + 35% reserve)
Max drawdown:  6% (3×2% all stopped)
Daily cap:     3% loss → halts trading
Hold:          up to 168h (7 days)
Consensus:     6/7 layers must agree
Regimes:       trending, stable only
```
**Who it's for:** Operators who can't afford to lose. Smallest positions, tightest stops, highest reserve. The engine barely trades in this mode.

### 💰 Funding Harvest
```
Positions:     4 max
Size:          8% per position
Stop:          2%
Reserve:       30%
Max exposure:  62% (4×8% + 30% reserve)
Max drawdown:  8% (4×2% all stopped)
Daily cap:     3% loss → halts trading
Hold:          up to 48h
Consensus:     6/7 layers must agree
Regimes:       trending, stable only
Entry end:     CLOSE (exits when signal fades)
```
**Who it's for:** Funding rate capture. Enters when funding is directionally favorable, exits when signal ends. The 2% stop is tight — may get noise-stopped on volatile altcoins. Consider widening to 3% if whipsawed.

### 🏄 Momentum Surf (DEFAULT)
```
Positions:     5 max
Size:          10% per position
Stop:          3%
Reserve:       20%
Max exposure:  70% (5×10% + 20% reserve)
Max drawdown:  15% (5×3% all stopped)
Daily cap:     5% loss → halts trading
Hold:          up to 48h
Consensus:     5/7 layers must agree
Regimes:       trending, stable
```
**Who it's for:** The default strategy. Balanced risk/reward. Follows trends with moderate conviction. Good starting point for $100-$500 accounts.

### 🔭 Scout Run
```
Positions:     5 max
Size:          10% per position
Stop:          3%
Reserve:       20%
Max exposure:  70% (5×10% + 20% reserve)
Max drawdown:  15% (5×3% all stopped)
Daily cap:     5% loss → halts trading
Hold:          up to 72h
Consensus:     5/7 layers must agree
Regimes:       trending, stable
```
**Who it's for:** Similar to Momentum but with longer hold time (72h vs 48h). Scans wide, enters patient. Lets winners run longer.

### 🔄 Fade the Crowd
```
Positions:     4 max
Size:          10% per position
Stop:          3%
Reserve:       25%
Max exposure:  65% (4×10% + 25% reserve)
Max drawdown:  12% (4×3% all stopped)
Daily cap:     5% loss → halts trading
Hold:          up to 168h (7 days)
Consensus:     5/7 layers must agree
Regimes:       reverting, stable (NOT trending)
```
**Who it's for:** Contrarian. Trades against the crowd when indicators show mean reversion. Only works in reverting/stable regimes — sits out trends.

### 🎯 Sniper Strike
```
Positions:     3 max
Size:          18% per position
Stop:          4%
Reserve:       22%
Max exposure:  76% (3×18% + 22% reserve)
Max drawdown:  12% (3×4% all stopped)
Daily cap:     8% loss → halts trading
Hold:          up to 72h
Consensus:     7/7 ALL layers must agree
Regimes:       trending ONLY
```
**Who it's for:** High conviction. Takes few trades but sizes them large. Requires perfect consensus (7/7). Only enters in strong trends. When it fires, it means it.

### 🎰 Degen Sprint
```
Positions:     4 max
Size:          15% per position
Stop:          6%
Reserve:       15%
Max exposure:  75% (4×15% + 15% reserve)
Max drawdown:  24% (4×6% all stopped)
Daily cap:     10% loss → halts trading
Hold:          up to 24h (day trades only)
Consensus:     5/7 layers must agree
Regimes:       trending, stable, reverting
```
**Who it's for:** Fast and aggressive. Large positions, wide stops, short hold times. Accepts most regimes. **Can lose 24% in a bad session.** Only for operators who understand the risk.

### ⚡ Apex Protocol
```
Positions:     4 max
Size:          18% per position
Stop:          8%
Reserve:       8%
Max exposure:  80% (4×18% + 8% reserve — AT THE HARD CAP)
Max drawdown:  32% (4×8% all stopped)
Daily cap:     15% loss → halts trading
Hold:          up to 168h (7 days)
Consensus:     6/7 layers must agree
Regimes:       trending, stable, reverting
```
**Who it's for:** Maximum aggression. 80% exposure at the hard cap. 8% stops mean positions need room to breathe. **Can lose 32% of account in worst case.** Only unlocked at Score 7.0+. This is the "I know what I'm doing" mode.

---

## Account Size Guidance

| Account | Recommended | Acceptable | Avoid |
|---------|------------|------------|-------|
| $100 | Defense, Funding | Momentum, Scout | Degen, Apex |
| $500 | Momentum, Scout | Fade, Sniper | Apex |
| $1,000+ | Any | Any | — |

**Why:** Smaller accounts can't absorb 24-32% drawdowns. A $100 account on Apex could lose $32 in one bad cycle. Defense caps losses at $6.

---

## Safety Systems (All Strategies)

1. **Hard caps** — 25% max position, 80% max exposure (compiled, not configurable)
2. **Stop verification** — Every entry requires confirmed stop on HL before proceeding
3. **Trailing stops** — Activate at 1.5% profit, lock in gains
4. **Immune system** — Independent 60s stop verification loop
5. **Dead man's switch** — If controller dies, immune tightens all stops to 1%
6. **Position reconciliation** — HL is truth, local state synced every 5min
7. **Circuit breaker** — Daily loss cap halts all entries for remainder of day
8. **Failed entry cooldown** — 15min per coin after failed order

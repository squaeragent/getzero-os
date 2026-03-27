---
name: zero-strategy-selection
description: "choose the right trading strategy based on market conditions and operator history."
---

# strategy selection

before recommending a strategy, check 2 inputs:

## 1. market conditions

call `zero_get_heat`. if heat returns empty (count=0), fall back: call `zero_evaluate` on BTC, ETH, SOL, AVAX, DOGE individually.

also call `zero_get_approaching` for coins near threshold.

| fear & greed | regime | recommend |
|---|---|---|
| < 20 (extreme fear) + trending | fade or momentum | contrarian or trend |
| < 20 + chaotic | defense | protect capital |
| 20-40 + trending | momentum | default, bread and butter |
| 40-60 (neutral) | momentum or watch | observe if unclear |
| > 80 (extreme greed) | fade or defense | caution, reversals likely |
| no clear trends | defense or watch | wait for setup |

## 2. operator history

call `zero_session_history`.
- which strategies performed best for this operator?
- win rate by strategy?
- favor what works for THEM, not what's theoretically optimal.
- if no history: recommend momentum (default, most forgiving).

## handling errors

- if `zero_get_heat` returns `count: 0`: evaluate BTC, ETH, SOL, AVAX, DOGE individually. use those results.
- if `zero_session_history` returns `count: 0`: no history yet. recommend momentum.
- if any tool returns an error: tell the operator what failed. do not guess or hallucinate data.

## plan gating

not all strategies are available on all plans:

| plan | strategies |
|---|---|
| free | momentum, defense, watch |
| pro | + degen, scout, funding |
| scale | + sniper, fade, apex (all 9) |

if the operator asks for a locked strategy: "Degen needs Pro plan. try Momentum (free) or upgrade."

## recommendation format

always give: recommendation + reasoning + alternative.

"market favors momentum. 6 coins trending short, funding shorts pay.
your momentum win rate: 72%.
alternative: defense if you want to protect capital."

never just say "use momentum." explain WHY for this operator, this market, right now.

## the 9 strategies

| strategy | risk | stops | positions | best when |
|---|---|---|---|---|
| watch | none | — | 0 | uncertain, learning |
| defense | low | 2% | 3 max | protecting capital |
| funding | low | 2% | 4 max | funding rates are paying |
| momentum | medium | 3% | 5 max | clear trends, default choice |
| scout | medium | 3% | 5 max | wide scan, patient |
| fade | medium | 3% | 4 max | mean reversion, contrarian |
| sniper | high | 4% | 3 max | perfect setups only (7/7) |
| degen | high | 6% | 4 max | fast moves, short hold |
| apex | extreme | 8% | 4 max | maximum conviction, expert only |

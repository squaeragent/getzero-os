---
name: zero-strategy-selection
description: "choose the right trading strategy based on market conditions, operator history, energy, and score."
---

# strategy selection

before recommending a strategy, check 4 inputs:

## 1. market conditions

call `zero_get_heat` + `zero_get_approaching`.

| fear & greed | regime | recommend |
|---|---|---|
| < 20 (extreme fear) + trending | fade or momentum | contrarian or trend |
| < 20 + chaotic | defense | protect capital |
| 20-40 + trending | momentum | default, bread and butter |
| 40-60 (neutral) | momentum or watch | observe if unclear |
| > 80 (extreme greed) | fade or defense | caution, reversals likely |
| no clear trends | defense or watch | wait for setup |

## 2. operator history

call `zero_session_history` + `zero_get_score`.
- which strategies performed best for this operator?
- win rate by strategy?
- favor what works for THEM, not what's theoretically optimal.

## 3. energy

call `zero_get_energy`.
- above 60%: any strategy
- 30-60%: moderate only (momentum, defense, scout)
- below 30%: rest or defense (recovery mode)

## 4. score and unlocks

call `zero_get_score`.
- below 4.0: only momentum, defense, watch available
- 4.0-5.0: +scout unlocked
- 5.0-6.0: +sniper, +funding unlocked
- 6.0-7.0: +fade, +degen unlocked
- 7.0+: +apex unlocked (full access)

## recommendation format

always give: recommendation + reasoning + alternative.

"market favors momentum. fear 18, 6 coins trending, shorts paying.
your momentum win rate: 72%. energy 78%.
alternative: fade if you want contrarian exposure."

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

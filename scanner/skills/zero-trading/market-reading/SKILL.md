---
name: zero-market-reading
description: "interpret 7-layer evaluations, heat maps, and approaching signals. understand what the engine sees."
---

# market reading

## evaluating a coin

call `zero_evaluate("SOL")`. you get:

```
coin: SOL
consensus: 5/7
conviction: 0.71
direction: SHORT
regime: strong_trend
layers:
  regime: ✅ trending
  technical: ✅ 3/4 indicators agree
  funding: ✅ shorts getting paid
  book: ❌ thin liquidity
  OI: ✅ open interest confirms
  macro: ❌ extreme fear blocks shorts
  collective: ✅ network agrees
```

## interpreting layers

**regime** — is the market trending, stable, reverting, or chaotic?
trending = momentum works. reverting = fade works. chaotic = defense.

**technical** — do RSI, MACD, EMA, Bollinger agree on direction?
needs 2/4 minimum. RSI only votes at extremes (<30 or >70).

**funding** — would you get paid to hold this position?
positive funding + short = good. negative funding + long = good.

**book** — is there enough liquidity to enter and exit?
thin books = slippage risk. engine correctly blocks thin markets.

**OI** — does open interest confirm the move?
rising OI + price direction = conviction. declining OI = caution.

**macro** — what does fear & greed say?
extreme fear blocks longs (by design). extreme greed blocks shorts.
this is the contrarian filter.

**collective** — does the network agree?
V1: defaults to pass (no network data yet).

## reading the heat map

call `zero_get_heat`. coins sorted by conviction, highest first.
- conviction > 0.7: strong setup forming
- conviction 0.5-0.7: moderate interest
- conviction < 0.5: engine is not interested

report top 3-5 to operator. don't dump the full list.

## reading approaching signals

call `zero_get_approaching`. these are the INTERESTING coins.
they're close to threshold but not there yet.

"SOL forming. 4/7. book depth is the bottleneck.
if liquidity improves, engine enters."

THIS is what makes the agent feel alive between trades.
narrate anticipation. don't go silent for hours.

the bottleneck tells you WHAT needs to change.
- regime bottleneck: market structure needs to shift
- technical bottleneck: indicators need to align
- book bottleneck: liquidity needs to improve
- macro bottleneck: sentiment needs to change

## the pulse

call `zero_get_pulse`. recent events: entries, exits, rejections.
use for "what happened while I was away?" questions.

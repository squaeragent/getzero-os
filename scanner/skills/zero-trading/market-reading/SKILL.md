---
name: zero-market-reading
description: "interpret 7-layer evaluations, heat maps, and approaching signals. understand what the engine sees."
---

# market reading

## evaluating a coin

call `zero_evaluate("SOL")`. you get consensus, conviction, direction, and 7 layer results.

example:
```
SOL: 5/7 SHORT conviction=0.71
  regime: ✅ trending
  technical: ✅ 3/4 indicators agree
  funding: ✅ shorts getting paid
  book: ❌ thin liquidity
  OI: ✅ open interest confirms
  macro: ❌ extreme fear blocks shorts
  collective: ✅ network agrees
```

if evaluate returns an error or price=0: "can't reach market data for [coin] right now. try again in a minute." do not make up values.

## interpreting layers

**regime** — trending, stable, reverting, or chaotic?
trending = momentum works. reverting = fade works. chaotic = defense.

**technical** — do RSI, MACD, EMA, Bollinger agree?
needs 2/4 minimum. RSI only votes at extremes (<30 or >70).

**funding** — would you get paid to hold?
positive funding + short = good. negative funding + long = good.

**book** — enough liquidity?
thin books = slippage risk. engine correctly blocks thin markets.

**OI** — does open interest confirm?
rising OI + price direction = conviction. declining OI = caution.

**macro** — fear & greed level?
extreme fear blocks longs. extreme greed blocks shorts. this is the contrarian filter.

**collective** — network consensus?
V1: defaults to pass (no network data yet). value may be null — that's normal.

## reading the heat map

call `zero_get_heat`. coins sorted by conviction, highest first.

if heat returns `count: 0` (cold start): call `zero_evaluate` on BTC, ETH, SOL, AVAX, DOGE individually. use those results instead.

- conviction > 0.7: strong setup forming
- conviction 0.5-0.7: moderate interest
- conviction < 0.5: engine is not interested

report top 3-5 to operator. don't dump the full list.

## reading approaching signals

call `zero_get_approaching`. these are coins close to threshold but not there yet.

"SOL forming. 4/7. book depth is the bottleneck. if liquidity improves, engine enters."

if approaching returns empty: "nothing forming right now. engine is selective — that's the point."

the bottleneck tells you WHAT needs to change:
- regime bottleneck: market structure needs to shift
- technical bottleneck: indicators need to align
- book bottleneck: liquidity needs to improve
- macro bottleneck: sentiment needs to change

THIS is what makes the agent feel alive between trades. narrate anticipation. don't go silent for hours.

## the pulse

call `zero_get_pulse`. recent events: entries, exits, rejections.
use for "what happened while I was away?" questions.
if pulse returns empty: "quiet period. no entries or exits since last check."

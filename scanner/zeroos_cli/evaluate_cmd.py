"""zeroos evaluate — see the reasoning engine think."""

import json
import os
import sys
import time

import click

from scanner.zeroos_cli.style import Z

SCANNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V6_DIR = os.path.join(SCANNER_ROOT, "v6")


def _evaluate_coin(coin: str, verbose: bool = False) -> dict:
    if V6_DIR not in sys.path:
        sys.path.insert(0, V6_DIR)
    try:
        from smart_provider import SmartProvider
    except ImportError:
        print(f'  {Z.fail("SmartProvider not found. is the scanner installed?")}')
        raise SystemExit(1)

    provider = SmartProvider()
    return provider.evaluate_coin(coin.upper())


def _regime_label(regime: str) -> str:
    """Qualitative regime label."""
    r = regime.lower() if regime else "unknown"
    if r in ("trending", "trend"):
        return "trending"
    elif r in ("mean_reverting", "mean-reverting", "ranging", "range-bound"):
        return "range-bound"
    elif r in ("volatile", "chaotic"):
        return "volatile"
    elif r in ("stable", "quiet"):
        return "stable"
    return r


def _confidence_label(conf) -> str:
    """Qualitative confidence label."""
    if conf is None:
        return "—"
    if conf >= 0.8:
        return "high"
    elif conf >= 0.5:
        return "moderate"
    return "low"


def _indicator_label(name: str) -> str:
    """Friendly indicator label (no raw names)."""
    mapping = {
        "ema": "trend",
        "macd": "momentum",
        "rsi": "strength",
        "bollinger": "bands",
        "obv": "volume",
        "funding": "funding",
        "atr": "volatility",
        "vwap": "flow",
        "stoch": "oscillator",
        "adx": "direction",
        "cci": "cycles",
    }
    key = name.lower().split("_")[0]
    return mapping.get(key, name.lower()[:12])


def _vote_description(vote: str, name: str) -> str:
    """Short qualitative description for each vote."""
    v = vote.lower() if vote else "neutral"
    descs = {
        "long": {
            "ema": "agrees", "macd": "expanding", "rsi": "room to run",
            "bollinger": "upper band", "obv": "volume confirms",
        },
        "short": {
            "ema": "disagrees", "macd": "contracting", "rsi": "overbought",
            "bollinger": "lower band", "obv": "volume fading",
        },
    }
    key = name.lower().split("_")[0]
    if v in descs and key in descs[v]:
        return descs[v][key]
    if v == "long":
        return "agrees"
    elif v == "short":
        return "disagrees"
    return "neutral"


@click.command("evaluate")
@click.argument("coin")
@click.option("--verbose", "-v", is_flag=True, help="Show all indicator values.")
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON.")
def evaluate(coin, verbose, json_output):
    """Evaluate a coin using the reasoning engine."""
    coin = coin.upper()

    if json_output:
        try:
            result = _evaluate_coin(coin, verbose)
        except SystemExit:
            raise
        except Exception as e:
            print(f'  {Z.fail(f"evaluation failed: {e}")}')
            raise SystemExit(1)
        print(json.dumps(result, indent=2, default=str))
        return

    print()
    print(f'  {Z.lime(f"◆ evaluating {coin}")}')
    print()
    print(f'  {Z.rule()}')
    print()

    t0 = time.time()

    try:
        result = _evaluate_coin(coin, verbose)
    except SystemExit:
        raise
    except Exception as e:
        print(f'  {Z.fail(f"evaluation failed: {e}")}')
        raise SystemExit(1)

    elapsed_ms = int((time.time() - t0) * 1000)

    # REGIME section
    regime = result.get("regime", "unknown")
    confidence = result.get("confidence")
    regime_ctx = result.get("regime_context", "")

    print(f'  {Z.header("REGIME")}')
    print(f'  {Z.dots("classification", _regime_label(regime))}')
    print(f'  {Z.dots("confidence", _confidence_label(confidence))}')

    # Regime age from context if available
    regime_age = "—"
    if regime_ctx:
        # Try to extract age from context
        import re
        age_match = re.search(r'(\d+)\s*(hour|day|hr)', regime_ctx.lower())
        if age_match:
            num = age_match.group(1)
            unit = age_match.group(2)
            if "day" in unit:
                regime_age = f"{num} days"
            else:
                regime_age = f"{num} hours"
    print(f'  {Z.dots("age", regime_age)}')

    print()
    print(f'  {Z.rule()}')
    print()

    # INDICATORS section
    print(f'  {Z.header("INDICATORS")}')

    votes = result.get("indicator_votes", {})
    direction = result.get("direction", "NEUTRAL")
    quality = result.get("quality", 0)

    long_count = 0
    total_count = 0

    if votes:
        for name, vote in votes.items():
            total_count += 1
            v = vote.lower() if vote else "neutral"

            # Direction arrow
            if v == "long":
                arrow = f'{Z.LIME}↗{Z.RESET}'
                dir_label = f'{Z.LIME}long{Z.RESET}'
                long_count += 1
            elif v == "short":
                arrow = f'{Z.RED}↘{Z.RESET}'
                dir_label = f'{Z.RED}short{Z.RESET}'
            else:
                arrow = f'{Z.DIM}—{Z.RESET}'
                dir_label = f'{Z.DIM}neutral{Z.RESET}'

            label = _indicator_label(name)
            desc = _vote_description(vote, name)
            print(f'  {Z.dots(label, f"{arrow} {dir_label}")}   {Z.dim(desc)}')

    # Funding & volatility (separate from votes)
    funding = result.get("funding_rate")
    if funding is not None:
        f_label = "neutral" if abs(funding) < 0.0001 else ("elevated" if funding > 0 else "negative")
        print(f'  {Z.dots("funding", f"{Z.DIM}— {f_label}{Z.RESET}")}')

    atr_pct = result.get("atr_pct")
    if atr_pct is not None:
        v_label = "normal" if atr_pct < 0.03 else ("elevated" if atr_pct < 0.06 else "high")
        print(f'  {Z.dots("volatility", f"{Z.DIM}— {v_label}{Z.RESET}")}')

    # Consensus
    if total_count > 0:
        consensus_str = "strong" if long_count >= total_count * 0.7 else "moderate" if long_count >= total_count * 0.4 else "weak"
        print(f'  {Z.dots("consensus", f"{consensus_str} ({long_count} of {total_count})")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # VERDICT section
    if direction in ("LONG",):
        verdict = "would consider entry."
        reasons = result.get("reasons", [])
        detail = f"strong consensus in a {_regime_label(regime)} regime." if consensus_str == "strong" else f"moderate signal in a {_regime_label(regime)} regime."
        print(f'  {Z.lime("VERDICT")}')
        print(f'  {Z.lime(verdict)}')
        print(f'  {Z.dim(detail)}')
    elif direction in ("SHORT",):
        verdict = "would consider short entry."
        detail = f"bearish consensus in a {_regime_label(regime)} regime."
        print(f'  {Z.lime("VERDICT")}')
        print(f'  {Z.lime(verdict)}')
        print(f'  {Z.dim(detail)}')
    else:
        verdict = "no actionable signal."
        detail = f"insufficient consensus in a {_regime_label(regime)} regime."
        print(f'  {Z.header("VERDICT")}')
        print(f'  {Z.mid(verdict)}')
        print(f'  {Z.dim(detail)}')

    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.dim("this is what the reasoning engine sees right now.")}')
    print(f'  {Z.dim("all signals computed locally from on-chain data.")}')
    print()

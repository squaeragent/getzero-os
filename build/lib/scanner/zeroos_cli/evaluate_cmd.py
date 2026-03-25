"""zeroos evaluate — see the reasoning engine think."""

import json
import time

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, success, direction_icon,
)


@click.command("evaluate")
@click.argument("coin")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def evaluate(coin, as_json):
    """Evaluate a coin using the zero reasoning engine."""
    from scanner.zeroos_cli.config_utils import load_token
    from scanner.zeroos_cli.api_client import ZeroAPIClient

    coin = coin.upper()
    token = load_token()
    api = ZeroAPIClient(token)

    t0 = time.time()

    try:
        result = api.evaluate(coin)
    except Exception as e:
        fail(str(e))
        raise SystemExit(1)

    elapsed_ms = int((time.time() - t0) * 1000)

    if as_json:
        print(json.dumps({
            "coin": result.coin,
            "regime": result.regime,
            "regime_confidence": result.confidence,
            "direction": result.direction,
            "consensus": result.consensus_label,
            "consensus_value": result.consensus_value,
            "conviction": result.conviction_level,
            "verdict": result.verdict,
            "reasoning": result.reasoning,
        }, indent=2))
        return

    spacer()
    logo()
    spacer()
    rule()
    spacer()

    section(f"EVALUATE {coin}")
    spacer()

    dots("regime", result.regime)
    dots("confidence", result.confidence)
    dots("direction", f"{direction_icon(result.direction)} {result.direction}")
    dots("consensus", f"{result.consensus_label} ({result.consensus_value:.0%})")
    dots("conviction", result.conviction_level)

    spacer()
    rule()
    spacer()

    # Verdict
    section("VERDICT")

    if result.verdict == "would_enter":
        console.print(f"  [success]▸ {result.verdict}[/success]")
    elif result.verdict == "would_reject":
        console.print(f"  [error]▸ {result.verdict}[/error]")
    else:
        console.print(f"  [dim]▸ {result.verdict}[/dim]")

    if result.reasoning:
        console.print(f"  [dim]{result.reasoning}[/dim]")

    spacer()

    if result.verdict == "would_enter":
        dots("entry", f"${result.entry_price:,.2f}")
        dots("stop", f"${result.stop_price:,.2f}")
        dots("size", f"{result.position_size_pct:.0%}")
        spacer()

    rule()
    spacer()
    console.print(f"  [dim]evaluated in {elapsed_ms}ms via zero reasoning engine.[/dim]")
    spacer()

"""zeroos evaluate — Run SmartProvider evaluation on a coin."""

import json
import os
import sys
import time

import click

# Resolve paths relative to scanner package
SCANNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V6_DIR = os.path.join(SCANNER_ROOT, "v6")


def _evaluate_coin(coin: str, verbose: bool = False) -> dict:
    """Run SmartProvider evaluation for a single coin. Returns result dict."""
    # Add v6 to path so we can import SmartProvider
    if V6_DIR not in sys.path:
        sys.path.insert(0, V6_DIR)

    try:
        from smart_provider import SmartProvider
    except ImportError:
        click.echo("  ✗ SmartProvider not found. Is the scanner installed?")
        raise SystemExit(1)

    provider = SmartProvider()
    result = provider.evaluate_coin(coin.upper())
    return result


@click.command("evaluate")
@click.argument("coin")
@click.option("--verbose", "-v", is_flag=True, help="Show all indicator values.")
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON.")
def evaluate(coin, verbose, json_output):
    """Evaluate a coin using the SmartProvider signal engine.

    Examples:
        zeroos evaluate SOL
        zeroos evaluate BTC --verbose
        zeroos evaluate ETH --json
    """
    coin = coin.upper()

    if not json_output:
        click.echo()
        click.echo(f"  evaluating {coin}...")
        click.echo()

    t0 = time.time()

    try:
        result = _evaluate_coin(coin, verbose)
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"  ✗ evaluation failed: {e}")
        raise SystemExit(1)

    elapsed_ms = int((time.time() - t0) * 1000)

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    # Format output
    regime = result.get("regime", "unknown")
    hurst = result.get("hurst")
    dfa = result.get("dfa")
    direction = result.get("direction", "NEUTRAL")
    quality = result.get("quality", 0)
    confidence = result.get("confidence")
    atr_pct = result.get("atr_pct")
    funding = result.get("funding_rate")
    price = result.get("indicators", {}).get("CLOSE_PRICE")

    # Color the direction
    if direction in ("LONG",):
        dir_str = click.style("▸ LONG", fg="green")
    elif direction in ("SHORT",):
        dir_str = click.style("▸ SHORT", fg="red")
    else:
        dir_str = click.style("▸ NEUTRAL", fg="yellow")

    click.echo(f"  {coin}")
    if price:
        click.echo(f"  price ............ ${price:,.2f}")
    click.echo(f"  regime ........... {regime}")
    if hurst is not None:
        click.echo(f"  hurst ............ {hurst:.4f}")
    if dfa is not None:
        click.echo(f"  dfa .............. {dfa:.4f}")
    click.echo(f"  direction ........ {dir_str}")
    click.echo(f"  quality .......... {quality}/8")
    if confidence is not None:
        click.echo(f"  confidence ....... {confidence:.1%}")
    if atr_pct is not None:
        click.echo(f"  volatility ....... {atr_pct*100:.2f}%")
    if funding is not None:
        click.echo(f"  funding .......... {funding:.4%}")
    click.echo(f"  latency .......... {elapsed_ms}ms")
    click.echo()

    # Regime memory context
    regime_ctx = result.get("regime_context", "")
    if regime_ctx and verbose:
        click.echo(f"  regime memory:")
        for line in regime_ctx.split(". "):
            if line.strip():
                click.echo(f"    {line.strip()}.")
        click.echo()

    # Reasons
    reasons = result.get("reasons", [])
    if reasons:
        click.echo(f"  reasons: {', '.join(reasons)}")
        click.echo()

    # Indicator votes
    votes = result.get("indicator_votes", {})
    if votes and verbose:
        click.echo("  indicator votes:")
        for name, vote in votes.items():
            if vote == "long":
                v = click.style("LONG", fg="green")
            elif vote == "short":
                v = click.style("SHORT", fg="red")
            else:
                v = click.style("neutral", fg="yellow")
            click.echo(f"    {name:.<20s} {v}")
        click.echo()

    if verbose and result.get("indicators"):
        click.echo("  raw indicators:")
        for name, val in result["indicators"].items():
            if isinstance(val, float):
                click.echo(f"    {name:.<30s} {val:.4f}")
            else:
                click.echo(f"    {name:.<30s} {val}")
        click.echo()

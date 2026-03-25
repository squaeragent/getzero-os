"""zeroos think [coin] — live reasoning stream."""

import sys
import time
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, spacer, rule, info,
)


@click.command()
@click.argument("coin", default="SOL")
def think(coin):
    """Watch the reasoning engine think about a coin."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    coin = coin.upper()

    from visible_intelligence import think as think_stream

    stages = think_stream(coin)

    spacer()
    console.print(f"  [lime]◆ thinking about {coin}[/lime]")
    spacer()
    rule()

    for stage in stages:
        spacer()
        label = stage["label"]
        console.print(f"  [dim]▸ {label}...[/dim] ", end="")
        time.sleep(0.3)
        console.print("[success]done[/success]")

        for step in stage["steps"]:
            console.print(f"    [dim]{step}[/dim]")
            time.sleep(0.12)

        result = stage["result"]
        is_verdict = stage["stage"] == "verdict"
        if is_verdict:
            has_entry = "consider" in result.lower()
            if has_entry:
                console.print(f"\n  [lime]{result}[/lime]")
            else:
                console.print(f"\n  [mid]{result}[/mid]")
        else:
            console.print(f"    [dim]→[/dim] [mid]{result}[/mid]")

    spacer()

"""zeroos think [coin] — live reasoning stream."""

import sys
import time
import click
from pathlib import Path

from scanner.zeroos_cli.style import Z


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

    print()
    print(f'  {Z.lime(f"◆ thinking about {coin}")}')
    print()
    print(f'  {Z.rule()}')

    for stage in stages:
        print()
        label = stage["label"]
        print(f'  {Z.info(f"{label}...")}', end='', flush=True)
        time.sleep(0.3)
        print(f' {Z.green("done")}')

        for step in stage["steps"]:
            print(f'    {Z.dim(step)}')
            time.sleep(0.12)

        result = stage["result"]
        is_verdict = stage["stage"] == "verdict"
        if is_verdict:
            has_entry = "consider" in result.lower()
            if has_entry:
                print(f'\n  {Z.lime(result)}')
            else:
                print(f'\n  {Z.mid(result)}')
        else:
            print(f'    {Z.dim("→")} {Z.mid(result)}')

    print()

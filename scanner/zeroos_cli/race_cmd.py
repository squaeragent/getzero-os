"""zeroos race — watch two presets compete."""

import sys
import json
import time
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, score_bar,
)


@click.command()
@click.option("--preset-a", "-a", default="balanced", help="First preset")
@click.option("--preset-b", "-b", default="degen", help="Second preset")
@click.option("--equity", default=10000, type=float, help="Starting equity")
def race(preset_a, preset_b, equity):
    """Race two agent presets against historical data."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from visible_intelligence import race_presets

    trades_file = Path(v6) / "bus" / "trades.jsonl"
    trades = []
    if trades_file.exists():
        for line in trades_file.read_text().strip().split("\n"):
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except Exception:
                    pass

    spacer()
    logo()
    spacer()

    if len(trades) < 5:
        console.print("  [dim]not enough trade data. agent needs to run first.[/dim]")
        console.print("  [lime]$ zeroos start[/lime]")
        spacer()
        return

    rule()
    spacer()
    section(f"RACE: {preset_a} vs {preset_b}")
    console.print(f"  [dim]equity: ${equity:,.0f} · {len(trades)} trades[/dim]")
    spacer()
    console.print("  [dim]simulating...[/dim]", end="")
    time.sleep(0.5)

    result = race_presets(trades, preset_a, preset_b, equity)

    console.print(" [success]done[/success]")
    spacer()

    ra = result[preset_a]
    rb = result[preset_b]
    winner = result["winner"]

    bar_a = score_bar(max(0, ra["total_return_pct"]), 50.0, 20)
    bar_b = score_bar(max(0, rb["total_return_pct"]), 50.0, 20)

    a_tag = "lime" if winner == preset_a else "mid"
    b_tag = "lime" if winner == preset_b else "mid"

    console.print(f"  [{a_tag}]{preset_a:14s}[/{a_tag}] {bar_a} [bright]{ra['total_return_pct']:+.1f}%[/bright]  [dim]{ra['trades']} trades  {ra['win_rate']:.0%} WR[/dim]")
    console.print(f"  [{b_tag}]{preset_b:14s}[/{b_tag}] {bar_b} [bright]{rb['total_return_pct']:+.1f}%[/bright]  [dim]{rb['trades']} trades  {rb['win_rate']:.0%} WR[/dim]")
    spacer()
    console.print(f"  [dim]{result['insight']}[/dim]")
    spacer()

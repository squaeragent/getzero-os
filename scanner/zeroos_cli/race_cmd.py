"""zeroos race — watch two presets compete."""

import sys
import json
import time
import click
from pathlib import Path

from scanner.zeroos_cli.style import Z


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

    print()
    print(f'  {Z.logo()}')
    print()

    if len(trades) < 5:
        print(f'  {Z.dim("not enough trade data. agent needs to run first.")}')
        print(f'  {Z.lime("$ zeroos start")}')
        print()
        return

    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.header(f"RACE: {preset_a} vs {preset_b}")}')
    print(f'  {Z.dim(f"equity: ${equity:,.0f} · {len(trades)} trades")}')
    print()
    print(f'  {Z.dim("simulating...")}', end='', flush=True)
    time.sleep(0.5)

    result = race_presets(trades, preset_a, preset_b, equity)

    print(f' {Z.green("done")}')
    print()

    ra = result[preset_a]
    rb = result[preset_b]
    winner = result["winner"]

    bar_a = Z.bar_small(max(0, ra["total_return_pct"]), 50.0, 20)
    bar_b = Z.bar_small(max(0, rb["total_return_pct"]), 50.0, 20)

    a_color = Z.LIME if winner == preset_a else ""
    b_color = Z.LIME if winner == preset_b else ""
    a_reset = Z.RESET if winner == preset_a else ""
    b_reset = Z.RESET if winner == preset_b else ""

    print(f'  {a_color}{preset_a:14s}{a_reset} {bar_a} {Z.bright(f"{ra['total_return_pct']:+.1f}%")}  {Z.dim(f"{ra['trades']} trades  {ra['win_rate']:.0%} WR")}')
    print(f'  {b_color}{preset_b:14s}{b_reset} {bar_b} {Z.bright(f"{rb['total_return_pct']:+.1f}%")}  {Z.dim(f"{rb['trades']} trades  {rb['win_rate']:.0%} WR")}')
    print()
    print(f'  {Z.dim(result["insight"])}')
    print()

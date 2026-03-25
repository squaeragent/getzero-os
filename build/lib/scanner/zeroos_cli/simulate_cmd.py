"""zeroos simulate — test strategies against historical data."""

import sys
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots,
)


@click.command()
@click.option("--preset", default="balanced", help="Agent preset")
@click.option("--equity", default=5000, type=float, help="Starting equity")
@click.option("--threshold", default=0.60, type=float, help="Conviction threshold")
def simulate(preset, equity, threshold):
    """Run a strategy simulation against historical trades."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)

    import json
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

    if not trades:
        console.print("  [dim]no trade data found. agent needs to run first.[/dim]")
        console.print("  [lime]$ zeroos start[/lime]")
        spacer()
        return

    from intelligence_expansions import run_simulation
    result = run_simulation(trades, equity, preset, threshold)

    rule()
    spacer()
    section(f"SIMULATION: {preset} preset")
    dots("starting equity", f"${result['starting_equity']:,.0f}")
    dots("final equity", f"${result['final_equity']:,.2f}")
    dots("total return", f"{result['total_return_pct']:+.1f}%")
    dots("trades", result["trade_count"])
    dots("win rate", f"{result['win_rate']:.0%}")
    dots("max drawdown", f"{result['max_drawdown_pct']:.1f}%")
    spacer()

    rp = result.get("regime_performance", {})
    if rp:
        section("PER REGIME")
        for regime, stats in rp.items():
            dots(regime[:20], f"{stats['pnl_pct']:+.1f}%  {stats['trades']} trades  {stats['win_rate']:.0%} WR")

    spacer()

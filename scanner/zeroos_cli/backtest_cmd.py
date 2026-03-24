"""zeroos backtest — test reasoning engine against rare/unseen regimes."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, success, warn,
)


@click.command()
@click.option("--days", default=90, help="Look for regimes not seen in N days.")
def backtest(days):
    """Synthetic backtest against rare regime conditions."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    try:
        from compounding_upgrades import find_rare_regimes, backtest_rare
    except ImportError:
        fail("compounding_upgrades module not found.")
        raise SystemExit(1)

    spacer()
    logo()
    spacer()
    rule()
    spacer()
    section("SYNTHETIC BACKTEST: RARE REGIMES")
    console.print(f"  [dim]testing against regimes not seen in {days} days...[/dim]")
    spacer()

    rare = find_rare_regimes(days)
    if not rare:
        console.print("  [dim]no rare regimes found. all conditions seen recently.[/dim]")
        spacer()
        return

    warnings = 0
    for r in rare:
        result = backtest_rare(r["coin"], r["regime"])
        status = result.get("verdict", "?")

        if status == "ok":
            icon = "[success]✓[/success]"
        elif status == "weak":
            icon = "[warning]⚠[/warning]"
            warnings += 1
        else:
            icon = "[dim]?[/dim]"

        console.print(f"  [bright]{r['coin']}[/bright] [dim]·[/dim] [mid]{r['regime']}[/mid] [dim]· last seen {r['days_since']} days ago[/dim]")

        if result.get("periods"):
            console.print(f"    [dim]periods: {result['periods']}  avg pnl: {result['avg_pnl_pct']:+.1f}%  WR: {result['win_rate']:.0%}[/dim]")

        if result.get("warning"):
            console.print(f"    {icon} [mid]{result['warning']}[/mid]")
        else:
            console.print(f"    {icon} [dim]current weights handle this regime well.[/dim]")
        spacer()

    rule()
    if warnings:
        warn(f"{warnings} warning(s). reasoning engine may underperform.")
    else:
        success("all clear. weights handle known rare regimes.")
    spacer()

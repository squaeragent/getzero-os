"""zeroos backtest — test reasoning engine against rare/unseen regimes."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.style import Z


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
        print(f'  {Z.fail("compounding_upgrades module not found.")}')
        raise SystemExit(1)

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.header("SYNTHETIC BACKTEST: RARE REGIMES")}')
    print(f'  {Z.dim(f"testing against regimes not seen in {days} days...")}')
    print()

    rare = find_rare_regimes(days)
    if not rare:
        print(f'  {Z.dim("no rare regimes found. all conditions seen recently.")}')
        print()
        return

    warnings = 0
    for r in rare:
        result = backtest_rare(r["coin"], r["regime"])
        status = result.get("verdict", "?")

        if status == "ok":
            icon = f'{Z.GREEN}✓{Z.RESET}'
        elif status == "weak":
            icon = f'{Z.YELLOW}⚠{Z.RESET}'
            warnings += 1
        else:
            icon = f'{Z.DIM}?{Z.RESET}'

        print(f'  {Z.bright(r["coin"])} {Z.dim("·")} {Z.mid(r["regime"])} {Z.dim(f"· last seen {r['days_since']} days ago")}')

        if result.get("periods"):
            print(f'    {Z.dots("periods", result["periods"])}  {Z.dots("avg pnl", f"{result['avg_pnl_pct']:+.1f}%")}  {Z.dots("WR", f"{result['win_rate']:.0%}")}')

        if result.get("warning"):
            print(f'    {icon} {Z.mid(result["warning"])}')
        else:
            print(f'    {icon} {Z.dim("current weights handle this regime well.")}')
        print()

    print(f'  {Z.rule()}')
    if warnings:
        print(f'  {Z.warn(f"{warnings} warning(s). reasoning engine may underperform.")}')
    else:
        print(f'  {Z.success("all clear. weights handle known rare regimes.")}')
    print()

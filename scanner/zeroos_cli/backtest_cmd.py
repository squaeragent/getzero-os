"""zeroos backtest — Test reasoning engine against rare/unseen regimes."""

import sys
from pathlib import Path

import click


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
        click.echo("  error: compounding_upgrades module not found.")
        raise SystemExit(1)

    click.echo()
    click.echo("  SYNTHETIC BACKTEST: RARE REGIMES")
    click.echo("  ────────────────────────────────────────")
    click.echo(f"  testing against regimes not seen in {days} days...")
    click.echo()

    rare = find_rare_regimes(days)
    if not rare:
        click.echo("  no rare regimes found. all conditions seen recently.")
        click.echo()
        return

    warnings = 0
    for r in rare:
        result = backtest_rare(r["coin"], r["regime"])
        status = result.get("verdict", "?")
        icon = "✓" if status == "ok" else "⚠" if status == "weak" else "?"

        click.echo(f"  {r['coin']} · {r['regime']} · last seen {r['days_since']} days ago")

        if result.get("periods"):
            click.echo(f"    {result['periods']} periods · avg P&L: {result['avg_pnl_pct']:+.1f}% · WR: {result['win_rate']:.0%}")
        
        if result.get("warning"):
            click.echo(f"    {icon} {result['warning']}")
            warnings += 1
        else:
            click.echo(f"    {icon} current weights handle this regime well.")
        click.echo()

    click.echo(f"  ────────────────────────────────────────")
    if warnings:
        click.echo(f"  {warnings} warning(s). reasoning engine may underperform.")
    else:
        click.echo(f"  all clear. weights handle known rare regimes.")
    click.echo()

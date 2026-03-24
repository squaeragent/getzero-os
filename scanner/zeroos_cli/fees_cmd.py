"""zeroos fees — Display fee summary and history."""

import click


@click.command()
def fees():
    """Show performance fee summary."""
    try:
        import sys
        from pathlib import Path
        v6_dir = str(Path(__file__).parent.parent / "v6")
        if v6_dir not in sys.path:
            sys.path.insert(0, v6_dir)
        from performance_fee import get_fee_summary
        s = get_fee_summary()
    except Exception as e:
        click.echo(f"  ✗ could not load fee data: {e}")
        raise SystemExit(1)

    click.echo()
    click.echo("  FEE SUMMARY")
    click.echo("  ────────────────────────────────────────")
    click.echo(f"  rate .................. {s.get('fee_rate', '10%')} of net profit")
    click.echo(f"  high-water mark ....... ${s.get('hwm', 0):,.2f}")
    click.echo(f"  cumulative P&L ........ ${s.get('cumulative_pnl', 0):,.2f}")
    click.echo()
    click.echo(f"  total fees paid ....... ${s.get('total_fees_paid', 0):,.2f}")
    click.echo(f"  pending ............... ${s.get('pending', 0):,.2f}")
    click.echo(f"  today ................. ${s.get('daily_total', 0):,.2f}")
    click.echo(f"  trades with fees ...... {s.get('trade_count', 0)}")
    click.echo()
    click.echo(f"  fee wallet: {s.get('fee_wallet', '?')}")
    click.echo("  verify: all fees visible on HL explorer")
    click.echo()

"""zeroos fees — view fee history."""

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail,
)


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
        fail(f"could not load fee data: {e}")
        raise SystemExit(1)

    spacer()
    logo()
    spacer()
    rule()
    spacer()
    section("FEE SUMMARY")
    dots("rate", f"{s.get('fee_rate', '10%')} of net profit")
    dots("high-water mark", f"${s.get('hwm', 0):,.2f}")
    dots("cumulative pnl", f"${s.get('cumulative_pnl', 0):,.2f}")
    spacer()
    dots("total fees paid", f"${s.get('total_fees_paid', 0):,.2f}")
    dots("pending", f"${s.get('pending', 0):,.2f}")
    dots("today", f"${s.get('daily_total', 0):,.2f}")
    dots("trades with fees", s.get("trade_count", 0))
    spacer()
    rule()
    spacer()
    dots("fee wallet", s.get("fee_wallet", "?"))
    console.print("  [dim]verify: all fees visible on HL explorer.[/dim]")
    spacer()

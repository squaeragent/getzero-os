"""zeroos fees — view fee history."""

import click

from scanner.zeroos_cli.style import Z


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
        print(f'  {Z.fail(f"could not load fee data: {e}")}')
        raise SystemExit(1)

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.header("FEE SUMMARY")}')
    print(f'  {Z.dots("rate", f"{s.get('fee_rate', '10%')} of net profit")}')
    print(f'  {Z.dots("high-water mark", f"${s.get('hwm', 0):,.2f}")}')
    print(f'  {Z.dots("cumulative pnl", f"${s.get('cumulative_pnl', 0):,.2f}")}')
    print()
    print(f'  {Z.dots("total fees paid", f"${s.get('total_fees_paid', 0):,.2f}")}')
    print(f'  {Z.dots("pending", f"${s.get('pending', 0):,.2f}")}')
    print(f'  {Z.dots("today", f"${s.get('daily_total', 0):,.2f}")}')
    print(f'  {Z.dots("trades with fees", s.get("trade_count", 0))}')
    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.dots("fee wallet", s.get("fee_wallet", "?"))}')
    print(f'  {Z.dim("verify: all fees visible on HL explorer.")}')
    print()

"""zeroos simulate — Test strategies against historical data."""
import sys, click
from pathlib import Path

@click.command()
@click.option("--preset", default="balanced", help="Agent preset")
@click.option("--equity", default=5000, type=float, help="Starting equity")
@click.option("--threshold", default=0.60, type=float, help="Conviction threshold")
def simulate(preset, equity, threshold):
    """Run a strategy simulation against historical trades."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)

    import json
    trades_file = Path(v6) / "bus" / "trades.jsonl"
    trades = []
    if trades_file.exists():
        for line in trades_file.read_text().strip().split("\n"):
            if line.strip():
                try: trades.append(json.loads(line))
                except: pass

    if not trades:
        click.echo("\n  no trade data found. agent needs to run first.\n")
        return

    from intelligence_expansions import run_simulation
    result = run_simulation(trades, equity, preset, threshold)

    click.echo(f"\n  SIMULATION: {preset} preset")
    click.echo(f"  ────────────────────────────────────────")
    click.echo(f"  starting equity .... ${result['starting_equity']:,.0f}")
    click.echo(f"  final equity ....... ${result['final_equity']:,.2f}")
    click.echo(f"  total return ....... {result['total_return_pct']:+.1f}%")
    click.echo(f"  trades ............. {result['trade_count']}")
    click.echo(f"  win rate ........... {result['win_rate']:.0%}")
    click.echo(f"  max drawdown ....... {result['max_drawdown_pct']:.1f}%")
    click.echo()
    rp = result.get("regime_performance", {})
    if rp:
        click.echo(f"  PER REGIME:")
        for regime, stats in rp.items():
            click.echo(f"    {regime:20s} {stats['pnl_pct']:+.1f}%  {stats['trades']} trades  {stats['win_rate']:.0%} WR")
    click.echo()

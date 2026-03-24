"""zeroos race — Watch two presets compete."""
import sys, json, time, click
from pathlib import Path

@click.command()
@click.option("--preset-a", "-a", default="balanced", help="First preset")
@click.option("--preset-b", "-b", default="degen", help="Second preset")
@click.option("--equity", default=10000, type=float, help="Starting equity")
def race(preset_a, preset_b, equity):
    """Race two agent presets against historical data."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)
    from visible_intelligence import race_presets

    trades_file = Path(v6) / "bus" / "trades.jsonl"
    trades = []
    if trades_file.exists():
        for line in trades_file.read_text().strip().split("\n"):
            if line.strip():
                try: trades.append(json.loads(line))
                except: pass

    if len(trades) < 5:
        click.echo("\n  not enough trade data. agent needs to run first.\n")
        return

    click.echo(f"\n  RACE: {preset_a} vs {preset_b}")
    click.echo(f"  ════════════════════════════════════════")
    click.echo(f"  equity: ${equity:,.0f} · {len(trades)} trades")
    click.echo()
    click.echo(f"  simulating...", nl=False)
    time.sleep(0.5)

    result = race_presets(trades, preset_a, preset_b, equity)

    click.echo(" done\n")

    ra = result[preset_a]
    rb = result[preset_b]

    bar_a = "█" * max(1, int(max(0, ra["total_return_pct"]) / 2)) + "░" * max(0, 20 - int(max(0, ra["total_return_pct"]) / 2))
    bar_b = "█" * max(1, int(max(0, rb["total_return_pct"]) / 2)) + "░" * max(0, 20 - int(max(0, rb["total_return_pct"]) / 2))

    winner = result["winner"]
    click.echo(click.style(f"  {preset_a:14s} {bar_a} {ra['total_return_pct']:+.1f}%  {ra['trades']} trades  {ra['win_rate']:.0%} WR",
               fg="green" if winner == preset_a else None))
    click.echo(click.style(f"  {preset_b:14s} {bar_b} {rb['total_return_pct']:+.1f}%  {rb['trades']} trades  {rb['win_rate']:.0%} WR",
               fg="green" if winner == preset_b else None))
    click.echo()
    click.echo(click.style(f"  {result['insight']}", fg="cyan"))
    click.echo()

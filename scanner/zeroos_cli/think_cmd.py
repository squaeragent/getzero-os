"""zeroos think [coin] — Live reasoning stream."""
import sys, time, click
from pathlib import Path

@click.command()
@click.argument("coin", default="SOL")
def think(coin):
    """Watch the reasoning engine think about a coin."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)
    coin = coin.upper()

    from visible_intelligence import think as think_stream

    stages = think_stream(coin)

    click.echo(f"\n  zeroos think {coin}")
    click.echo(f"  ────────────────────────────────────────")

    for stage in stages:
        click.echo()
        label = stage["label"]
        click.echo(click.style(f"  ▸ {label}...", fg="yellow"), nl=False)
        time.sleep(0.3)
        click.echo(click.style(" done", fg="green"))

        for step in stage["steps"]:
            click.echo(f"    {step}")
            time.sleep(0.12)

        result = stage["result"]
        is_verdict = stage["stage"] == "verdict"
        if is_verdict:
            color = "green" if "consider" in result else "red"
            click.echo(click.style(f"\n  ■ {result}", fg=color, bold=True))
        else:
            click.echo(click.style(f"    → {result}", fg="cyan"))

    click.echo()

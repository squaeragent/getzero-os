"""zeroos conviction — Computed Conviction Index."""
import sys, json, click
from pathlib import Path

@click.command()
@click.option("--history", is_flag=True, help="Show 24h CCI history")
def conviction(history):
    """Display the Computed Conviction Index across all coins."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)
    from emergent_infrastructure import get_cci, get_cci_history

    if history:
        hist = get_cci_history(24)
        click.echo(f"\n  CCI HISTORY (last {len(hist)} snapshots)")
        click.echo(f"  ────────────────────────────────────────")
        for h in hist[-10:]:
            ts = h.get("timestamp", "")[:16]
            coins = h.get("cci", {})
            parts = [f"{c}:{d['value']:+.2f}" for c, d in coins.items()]
            click.echo(f"  {ts}  {' '.join(parts)}")
        click.echo()
        return

    data = get_cci()
    cci = data.get("cci", {})
    agents = data.get("network_agents", 0)

    click.echo(f"\n  COMPUTED CONVICTION INDEX")
    click.echo(f"  {agents} agents")
    click.echo(f"  ────────────────────────────────────────")

    if not cci:
        click.echo(f"  no CCI data yet. agents need to report evaluations.")
        click.echo()
        return

    sorted_coins = sorted(cci.items(), key=lambda x: -x[1].get("value", 0))
    for coin, info in sorted_coins:
        val = info.get("value", 0)
        direction = info.get("direction", "neutral")
        regime = info.get("regime", "?")
        coin_agents = info.get("agents", 0)

        # Bar visualization
        bar_len = 20
        if val >= 0:
            filled = int(val * bar_len)
            bar = "░" * (bar_len - filled) + "█" * filled
        else:
            filled = int(abs(val) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)

        click.echo(f"  {coin:6s} {bar} {val:+.2f} {direction:7s} {regime:12s} {coin_agents} agents")

    click.echo()
    click.echo(f"  the cci is not a price prediction.")
    click.echo(f"  it's what {agents} reasoning engines compute.")
    click.echo()

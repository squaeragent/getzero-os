"""zeroos conviction — computed conviction index."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, direction_icon, score_bar,
)


@click.command()
@click.option("--history", is_flag=True, help="Show 24h CCI history")
def conviction(history):
    """Display the Computed Conviction Index across all coins."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from emergent_infrastructure import get_cci, get_cci_history

    spacer()
    logo()
    spacer()
    rule()
    spacer()

    if history:
        hist = get_cci_history(24)
        section(f"CCI HISTORY (last {len(hist)} snapshots)")
        spacer()
        for h in hist[-10:]:
            ts = h.get("timestamp", "")[:16]
            coins = h.get("cci", {})
            parts = [f"{c}:{d['value']:+.2f}" for c, d in coins.items()]
            console.print(f"  [dim]{ts}[/dim]  [mid]{' '.join(parts)}[/mid]")
        spacer()
        return

    data = get_cci()
    cci = data.get("cci", {})
    agents = data.get("network_agents", 0)

    section("COMPUTED CONVICTION INDEX")
    console.print(f"  [dim]{agents} agents[/dim]")
    spacer()

    if not cci:
        console.print("  [dim]no CCI data yet. agents need to report evaluations.[/dim]")
        spacer()
        return

    sorted_coins = sorted(cci.items(), key=lambda x: -x[1].get("value", 0))
    for coin, info_data in sorted_coins:
        val = info_data.get("value", 0)
        direction = info_data.get("direction", "neutral")
        regime = info_data.get("regime", "?")
        coin_agents = info_data.get("agents", 0)

        arrow = direction_icon(direction)
        b = score_bar(abs(val), 1.0, 20)
        console.print(f"  [bright]{coin:6s}[/bright] {b} [bright]{val:+.2f}[/bright] {arrow} [dim]{regime:12s} {coin_agents} agents[/dim]")

    spacer()
    console.print("  [dim]the cci is not a price prediction.[/dim]")
    console.print(f"  [dim]it's what {agents} reasoning engines compute.[/dim]")
    spacer()

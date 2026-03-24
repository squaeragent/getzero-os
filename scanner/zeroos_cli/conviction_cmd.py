"""zeroos conviction — computed conviction index."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.style import Z


@click.command()
@click.option("--history", is_flag=True, help="Show 24h CCI history")
def conviction(history):
    """Display the Computed Conviction Index across all coins."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from emergent_infrastructure import get_cci, get_cci_history

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()

    if history:
        hist = get_cci_history(24)
        print(f'  {Z.header(f"CCI HISTORY (last {len(hist)} snapshots)")}')
        print()
        for h in hist[-10:]:
            ts = h.get("timestamp", "")[:16]
            coins = h.get("cci", {})
            parts = [f"{c}:{d['value']:+.2f}" for c, d in coins.items()]
            print(f'  {Z.dim(ts)}  {Z.mid(" ".join(parts))}')
        print()
        return

    data = get_cci()
    cci = data.get("cci", {})
    agents = data.get("network_agents", 0)

    print(f'  {Z.header("COMPUTED CONVICTION INDEX")}')
    print(f'  {Z.dim(f"{agents} agents")}')
    print()

    if not cci:
        print(f'  {Z.dim("no CCI data yet. agents need to report evaluations.")}')
        print()
        return

    sorted_coins = sorted(cci.items(), key=lambda x: -x[1].get("value", 0))
    for coin, info in sorted_coins:
        val = info.get("value", 0)
        direction = info.get("direction", "neutral")
        regime = info.get("regime", "?")
        coin_agents = info.get("agents", 0)

        arrow = Z.direction(direction)
        bar = Z.bar_small(abs(val), 1.0, 20)
        print(f'  {Z.bright(f"{coin:6s}")} {bar} {Z.bright(f"{val:+.2f}")} {arrow} {Z.dim(f"{regime:12s} {coin_agents} agents")}')

    print()
    print(f'  {Z.dim(f"the cci is not a price prediction.")}')
    print(f'  {Z.dim(f"it\'s what {agents} reasoning engines compute.")}')
    print()

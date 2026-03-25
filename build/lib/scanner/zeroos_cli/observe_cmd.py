"""zeroos observe — what the network is seeing."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, fail, info,
)


@click.command()
def observe():
    """Show today's market observations."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    try:
        from category_changers import observe_market
    except ImportError:
        fail("category_changers module not found.")
        raise SystemExit(1)

    bus_dir = Path(v6_dir) / "bus"
    import json

    landscape = {}
    landscape_file = bus_dir / "landscape.json"
    if landscape_file.exists():
        try:
            landscape = json.loads(landscape_file.read_text())
        except Exception:
            pass

    coins_data = landscape.get("coins", {})
    market_data = {}
    regime_data = {}

    for coin, data in coins_data.items():
        smart = data.get("smart", data.get("evaluation", {}))
        market_data[coin] = {
            "funding_rate": smart.get("funding_rate", 0),
            "price_change_24h_pct": data.get("price_change_24h_pct", 0),
            "volume_change_24h_pct": data.get("volume_change_24h_pct", 0),
        }
        regime_data[coin] = {
            "current_regime": smart.get("regime", "unknown"),
            "regime": smart.get("regime", "unknown"),
            "hurst": smart.get("hurst", 0.5),
            "hurst_velocity": smart.get("hurst_velocity", 0),
            "dfa": smart.get("dfa", 0.5),
            "regime_duration_hours": data.get("regime_duration_hours", 0),
        }

    spacer()
    logo()
    spacer()
    rule()
    spacer()

    if not market_data:
        console.print("  [dim]no market data available. agent may not be running.[/dim]")
        console.print("  [lime]$ zeroos start[/lime]")
        spacer()
        return

    observations = observe_market(market_data, regime_data)

    if not observations:
        console.print("  [dim]no notable observations right now.[/dim]")
        console.print("  [dim]the agent monitors continuously.[/dim]")
    else:
        section(f"OBSERVATIONS ({len(observations)})")
        spacer()
        for obs in observations:
            sig = obs.get("significance", 0)
            icon = "◆" if sig > 0.85 else "▸" if sig > 0.75 else "·"
            console.print(f"  [bright]{icon}[/bright] [mid]{obs['title']}[/mid]")
            console.print(f"    [dim]{obs['detail']}[/dim]")
            if obs.get("actionable") and obs.get("action_hint"):
                console.print(f"    [dim]→[/dim] [dim]{obs['action_hint']}[/dim]")
            spacer()

    console.print("  [dim]these are observations, not trade signals.[/dim]")
    spacer()

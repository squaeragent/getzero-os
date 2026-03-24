"""zeroos observe — Show market observations from the agent analyst."""

import sys
from pathlib import Path

import click


@click.command()
def observe():
    """Show today's market observations."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    try:
        from category_changers import observe_market
    except ImportError:
        click.echo("  error: category_changers module not found.")
        raise SystemExit(1)

    # Build market/regime data from bus files
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

    if not market_data:
        click.echo("\n  no market data available. agent may not be running.")
        click.echo("  start the agent with: zeroos start\n")
        return

    observations = observe_market(market_data, regime_data)

    click.echo()
    if not observations:
        click.echo("  no notable observations right now.")
        click.echo("  the agent monitors 8 coins × 13 regimes continuously.")
    else:
        click.echo(f"  {len(observations)} observation(s) from today's analysis:")
        click.echo()
        for obs in observations:
            sig = obs.get("significance", 0)
            icon = "◆" if sig > 0.85 else "▸" if sig > 0.75 else "·"
            click.echo(f"  {icon} {obs['title']}")
            click.echo(f"    {obs['detail']}")
            if obs.get("actionable") and obs.get("action_hint"):
                click.echo(f"    → {obs['action_hint']}")
            click.echo()
    click.echo("  these are observations, not trade signals.")
    click.echo()

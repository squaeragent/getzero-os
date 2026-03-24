"""zeroos observe — what the network is seeing."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.style import Z


@click.command()
def observe():
    """Show today's market observations."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    try:
        from category_changers import observe_market
    except ImportError:
        print(f'  {Z.fail("category_changers module not found.")}')
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

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()

    if not market_data:
        print(f'  {Z.dim("no market data available. agent may not be running.")}')
        print(f'  {Z.lime("$ zeroos start")}')
        print()
        return

    observations = observe_market(market_data, regime_data)

    if not observations:
        print(f'  {Z.dim("no notable observations right now.")}')
        print(f'  {Z.dim("the agent monitors continuously.")}')
    else:
        print(f'  {Z.header(f"OBSERVATIONS ({len(observations)})")}')
        print()
        for obs in observations:
            sig = obs.get("significance", 0)
            icon = "◆" if sig > 0.85 else "▸" if sig > 0.75 else "·"
            print(f'  {Z.bright(icon)} {Z.mid(obs["title"])}')
            print(f'    {Z.dim(obs["detail"])}')
            if obs.get("actionable") and obs.get("action_hint"):
                print(f'    {Z.dim("→")} {Z.dim(obs["action_hint"])}')
            print()

    print(f'  {Z.dim("these are observations, not trade signals.")}')
    print()

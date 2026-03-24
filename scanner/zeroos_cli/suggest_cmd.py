"""zeroos suggest — network universe diversification suggestions."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.style import Z


@click.command()
def suggest():
    """Get network coverage suggestions for your coin universe."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from emergent_infrastructure import suggest_universe

    config_file = Path.home() / ".zeroos" / "config.json"
    universe = ["SOL", "ETH", "BTC", "WLD", "AVAX", "TIA", "NEAR", "APT"]
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            universe = cfg.get("coins", universe)
        except Exception:
            pass

    all_coins = universe + ["ONDO", "JUP", "PENDLE", "SEI", "SUI", "ARB", "OP", "LINK"]
    coverage = {c: 47 if c in ("SOL", "ETH", "BTC") else 12 if c in universe else 3 for c in all_coins}

    result = suggest_universe(universe, coverage, 120, all_coins)

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.header("NETWORK COVERAGE SUGGESTIONS")}')
    print(f'  {Z.dots("your universe", " ".join(universe))}')
    print()

    for s in result.get("suggestions", []):
        action = s["action"]
        coin = s["coin"]
        icon = "▸" if action == "consider_adding" else "▾"
        print(f'  {Z.bright(icon)} {Z.mid(action.replace("_", " ") + ": " + coin)}')
        print(f'    {Z.dim(s["reason"])}')
        print()

    bonus = result.get("network_contribution_bonus", 0)
    if bonus > 0:
        print(f'  {Z.dim(f"following suggestions earns +{bonus:.2f} network contribution bonus.")}')

    print()

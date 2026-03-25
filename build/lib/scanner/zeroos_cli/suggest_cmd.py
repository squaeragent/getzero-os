"""zeroos suggest — network universe diversification suggestions."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots,
)


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

    spacer()
    logo()
    spacer()
    rule()
    spacer()
    section("NETWORK COVERAGE SUGGESTIONS")
    dots("your universe", " ".join(universe))
    spacer()

    for s in result.get("suggestions", []):
        action = s["action"]
        coin = s["coin"]
        icon = "▸" if action == "consider_adding" else "▾"
        console.print(f"  [bright]{icon}[/bright] [mid]{action.replace('_', ' ')}: {coin}[/mid]")
        console.print(f"    [dim]{s['reason']}[/dim]")
        spacer()

    bonus = result.get("network_contribution_bonus", 0)
    if bonus > 0:
        console.print(f"  [dim]following suggestions earns +{bonus:.2f} network contribution bonus.[/dim]")

    spacer()

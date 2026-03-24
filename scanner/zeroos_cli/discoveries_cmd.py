"""zeroos discoveries — show strategy patterns discovered by the network."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots,
)


@click.command()
def discoveries():
    """Show discovered strategy patterns from collective data."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    state_file = Path.home() / ".zeroos" / "state" / "discoveries.json"
    import json

    rules = []
    if state_file.exists():
        try:
            rules = json.loads(state_file.read_text())
        except Exception:
            pass

    spacer()
    logo()
    spacer()
    rule()
    spacer()

    if not rules:
        console.print("  [dim]no discovered patterns yet.[/dim]")
        console.print("  [dim]the network needs 100+ collective trades to start finding patterns.[/dim]")
        console.print("  [dim]discoveries run monthly from the collective intelligence.[/dim]")
    else:
        section(f"DISCOVERED PATTERNS ({len(rules)} rules)")
        spacer()
        for i, r in enumerate(rules, 1):
            direction = r.get("direction", "?")
            icon = "[success]✓[/success]" if direction == "positive" else "[warning]⚠[/warning]"
            console.print(f"  {icon} [bright]rule #{i}[/bright] [dim](sample: {r['sample_size']} trades)[/dim]")
            console.print(f"    [mid]{r['description']}[/mid]")
            console.print(f"    [dim]win rate: {r['win_rate']:.0%} vs baseline {r['baseline_wr']:.0%} ({r['improvement']:+.0%})[/dim]")
            if direction == "negative":
                console.print("    [dim]→ your agent AVOIDS this combination.[/dim]")
            else:
                console.print("    [dim]→ your agent BOOSTS conviction for this pattern.[/dim]")
            spacer()

    spacer()

"""zeroos discoveries — Show strategy patterns discovered by the network."""

import sys
from pathlib import Path

import click


@click.command()
def discoveries():
    """Show discovered strategy patterns from collective data."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    # Load discovered rules from state
    state_file = Path.home() / ".zeroos" / "state" / "discoveries.json"
    import json

    rules = []
    if state_file.exists():
        try:
            rules = json.loads(state_file.read_text())
        except Exception:
            pass

    click.echo()
    if not rules:
        click.echo("  no discovered patterns yet.")
        click.echo("  the network needs 100+ collective trades to start finding patterns.")
        click.echo("  discoveries run monthly from the collective intelligence.")
    else:
        click.echo(f"  DISCOVERED PATTERNS ({len(rules)} rules)")
        click.echo("  ────────────────────────────────────────")
        for i, rule in enumerate(rules, 1):
            direction = rule.get("direction", "?")
            icon = "✓" if direction == "positive" else "⚠"
            click.echo(f"\n  {icon} RULE #{i} (sample: {rule['sample_size']} trades)")
            click.echo(f"    {rule['description']}")
            click.echo(f"    WR: {rule['win_rate']:.0%} vs baseline {rule['baseline_wr']:.0%} "
                       f"(improvement: {rule['improvement']:+.0%})")
            if direction == "negative":
                click.echo(f"    → your agent AVOIDS this combination.")
            else:
                click.echo(f"    → your agent BOOSTS conviction for this pattern.")
    click.echo()

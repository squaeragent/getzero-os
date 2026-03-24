"""zeroos discoveries — show strategy patterns discovered by the network."""

import sys
from pathlib import Path

import click

from scanner.zeroos_cli.style import Z


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

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()

    if not rules:
        print(f'  {Z.dim("no discovered patterns yet.")}')
        print(f'  {Z.dim("the network needs 100+ collective trades to start finding patterns.")}')
        print(f'  {Z.dim("discoveries run monthly from the collective intelligence.")}')
    else:
        print(f'  {Z.header(f"DISCOVERED PATTERNS ({len(rules)} rules)")}')
        print()
        for i, rule in enumerate(rules, 1):
            direction = rule.get("direction", "?")
            icon = f'{Z.GREEN}✓{Z.RESET}' if direction == "positive" else f'{Z.YELLOW}⚠{Z.RESET}'
            print(f'  {icon} {Z.bright(f"rule #{i}")} {Z.dim(f"(sample: {rule['sample_size']} trades)")}')
            print(f'    {Z.mid(rule["description"])}')
            print(f'    {Z.dots("win rate", f"{rule['win_rate']:.0%} vs baseline {rule['baseline_wr']:.0%} ({rule['improvement']:+.0%})")}')
            if direction == "negative":
                print(f'    {Z.dim("→ your agent AVOIDS this combination.")}')
            else:
                print(f'    {Z.dim("→ your agent BOOSTS conviction for this pattern.")}')
            print()

    print()

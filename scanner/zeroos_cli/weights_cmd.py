"""zeroos weights — view current collective weights."""

import json
import sys
from pathlib import Path

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, score_bar,
)


@click.command()
def weights():
    """Show reasoning weights per regime."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    cache_dir = Path(v6_dir) / "cache"
    weights_file = cache_dir / "smart_weights.json"
    collective = {}
    trades_count = 0
    version = "?"
    if weights_file.exists():
        try:
            data = json.loads(weights_file.read_text())
            collective = data.get("weights", {})
            trades_count = data.get("trades_count", 0)
            version = data.get("version", "?")
        except Exception:
            pass

    personal_file = Path.home() / ".zeroos" / "state" / "personal_weights.json"
    personal = {}
    if personal_file.exists():
        try:
            personal = json.loads(personal_file.read_text())
        except Exception:
            pass

    spacer()
    logo()
    spacer()
    rule()
    spacer()

    if not collective and not personal:
        console.print("  [dim]no weights available yet.[/dim]")
        console.print("  [dim]collective: need 200+ network trades.[/dim]")
        console.print("  [dim]personal: need 50+ local trades.[/dim]")
        spacer()
        return

    try:
        from reasoning_upgrades import blend_weights
        has_blend = True
    except ImportError:
        has_blend = False

    source_parts = []
    if collective:
        source_parts.append(f"collective (v{version}, {trades_count} trades)")
    if personal:
        p_regimes = len(personal)
        source_parts.append(f"personal ({p_regimes} regimes)")

    section("REASONING WEIGHTS")
    if source_parts:
        blend_desc = "60% collective + 40% personal" if collective and personal else "100% " + ("collective" if collective else "personal")
        dots("source", blend_desc)
        console.print(f"  [dim]{' · '.join(source_parts)}[/dim]")
    spacer()

    all_regimes = set(list(collective.keys()) + list(personal.keys()))

    for regime in sorted(all_regimes):
        section(regime.upper())
        if has_blend and collective:
            blended = blend_weights(collective, regime)
        elif collective and regime in collective:
            blended = collective[regime]
        elif personal and regime in personal:
            blended = personal[regime]
        else:
            blended = {}

        for ind, val in sorted(blended.items(), key=lambda x: -x[1]):
            dots(ind[:16], f"{val:.2f}")
            console.print(f"      {score_bar(val, 1.0, 20)}")
        spacer()

    spacer()

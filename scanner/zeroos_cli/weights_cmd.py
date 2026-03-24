"""zeroos weights — view current collective weights."""

import json
import sys
from pathlib import Path

import click

from scanner.zeroos_cli.style import Z


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

    print()
    print(f'  {Z.logo()}')
    print()
    print(f'  {Z.rule()}')
    print()

    if not collective and not personal:
        print(f'  {Z.dim("no weights available yet.")}')
        print(f'  {Z.dim("collective: need 200+ network trades.")}')
        print(f'  {Z.dim("personal: need 50+ local trades.")}')
        print()
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

    print(f'  {Z.header("REASONING WEIGHTS")}')
    if source_parts:
        blend_desc = "60% collective + 40% personal" if collective and personal else "100% " + ("collective" if collective else "personal")
        print(f'  {Z.dots("source", blend_desc)}')
        print(f'  {Z.dim(" · ".join(source_parts))}')
    print()

    all_regimes = set(list(collective.keys()) + list(personal.keys()))

    for regime in sorted(all_regimes):
        print(f'  {Z.header(regime.upper())}')
        if has_blend and collective:
            blended = blend_weights(collective, regime)
        elif collective and regime in collective:
            blended = collective[regime]
        elif personal and regime in personal:
            blended = personal[regime]
        else:
            blended = {}

        for ind, val in sorted(blended.items(), key=lambda x: -x[1]):
            print(f'  {Z.dots(ind[:16], f"{val:.2f}")}  {Z.bar_small(val, 1.0, 20)}')
        print()

    print()

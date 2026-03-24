"""zeroos weights — Display reasoning weights (collective + personal)."""

import json
import sys
from pathlib import Path

import click


@click.command()
def weights():
    """Show reasoning weights per regime."""
    v6_dir = str(Path(__file__).parent.parent / "v6")
    if v6_dir not in sys.path:
        sys.path.insert(0, v6_dir)

    # Load collective weights
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

    # Load personal weights
    personal_file = Path.home() / ".zeroos" / "state" / "personal_weights.json"
    personal = {}
    if personal_file.exists():
        try:
            personal = json.loads(personal_file.read_text())
        except Exception:
            pass

    click.echo()

    if not collective and not personal:
        click.echo("  no weights available yet.")
        click.echo("  collective: need 200+ network trades.")
        click.echo("  personal: need 50+ local trades.")
        click.echo()
        return

    # Blend
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

    click.echo(f"  REASONING WEIGHTS")
    click.echo(f"  ────────────────────────────────────────")
    if source_parts:
        blend_desc = "60% collective + 40% personal" if collective and personal else "100% " + ("collective" if collective else "personal")
        click.echo(f"  source: {blend_desc}")
        click.echo(f"  {' · '.join(source_parts)}")
    click.echo()

    all_regimes = set(list(collective.keys()) + list(personal.keys()))

    for regime in sorted(all_regimes):
        click.echo(f"  {regime.upper()}:")
        if has_blend and collective:
            blended = blend_weights(collective, regime)
        elif collective and regime in collective:
            blended = collective[regime]
        elif personal and regime in personal:
            blended = personal[regime]
        else:
            blended = {}

        c_regime = collective.get(regime, {})
        p_regime = personal.get(regime, {})

        for ind, val in sorted(blended.items(), key=lambda x: -x[1]):
            bar_len = int(val * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            c_val = c_regime.get(ind, "—")
            p_val = p_regime.get(ind, "—")
            c_str = f"{c_val:.2f}" if isinstance(c_val, (int, float)) else c_val
            p_str = f"{p_val:.2f}" if isinstance(p_val, (int, float)) else p_val
            click.echo(f"    {ind:16s} {val:.2f}  {bar}  c:{c_str} p:{p_str}")
        click.echo()

    click.echo()

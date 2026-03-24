"""zeroos replay [trade_id] — Re-live any trade."""
import sys, json, click
from pathlib import Path

@click.command()
@click.argument("trade_id", default="latest")
def replay(trade_id):
    """Re-live a historical trade with full context."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path: sys.path.insert(0, v6)
    from visible_intelligence import replay_trade

    # Load trade data
    trades_file = Path(v6) / "bus" / "trades.jsonl"
    trade = None
    if trades_file.exists():
        lines = [l.strip() for l in trades_file.read_text().strip().split("\n") if l.strip()]
        if trade_id == "latest" and lines:
            trade = json.loads(lines[-1])
        else:
            for line in lines:
                t = json.loads(line)
                if str(t.get("id", t.get("trade_id", ""))) == trade_id:
                    trade = t
                    break

    if not trade:
        click.echo(f"\n  trade {trade_id} not found.\n")
        return

    result = replay_trade(trade)

    click.echo(f"\n  REPLAY: {result['coin']} {result['direction']} {'+'if result['pnl']>=0 else ''}{result['pnl']:.2f}")
    click.echo(f"  ────────────────────────────────────────")

    # Entry
    click.echo(f"\n  ENTRY:")
    e = result["entry"]
    click.echo(f"    regime: {e['regime']} (H {e['hurst']:.2f})")
    click.echo(f"    consensus: {e['consensus']:.0%}")
    click.echo(f"    mode: {e['signal_mode']}")

    # Chart
    click.echo(f"\n  HOLD ({result['hold_hours']:.1f}h):")
    click.echo(f"    {result['chart']}")
    click.echo(f"    ■ entry  ▼ MAE ({result['mae_pct']:.1%})  ▲ MFE ({result['mfe_pct']:.1%})  ■ exit")

    # Exit
    click.echo(f"\n  EXIT:")
    x = result["exit"]
    click.echo(f"    reason: {x['reason']}")
    if x["regime_changed"]:
        click.echo(f"    regime shifted: {result['entry']['regime']} → {x['regime_at_exit']}")
    click.echo(f"    capture rate: {result['capture_rate']:.0%}")

    # Lessons
    if result["lessons"]:
        click.echo(f"\n  LESSONS:")
        for lesson in result["lessons"]:
            icon = "✓" if lesson["type"] == "positive" else "→" if lesson["type"] == "improve" else "·"
            click.echo(f"    {icon} {lesson['text']}")

    click.echo()

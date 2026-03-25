"""zeroos replay [trade_id] — re-live any trade."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, success, info, pnl, pnl_pct,
)


@click.command()
@click.argument("trade_id", default="latest")
def replay(trade_id):
    """Re-live a historical trade with full context."""
    v6 = str(Path(__file__).parent.parent / "v6")
    if v6 not in sys.path:
        sys.path.insert(0, v6)
    from visible_intelligence import replay_trade

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

    spacer()
    logo()
    spacer()

    if not trade:
        console.print(f"  [dim]trade {trade_id} not found.[/dim]")
        spacer()
        return

    result = replay_trade(trade)

    pnl_val = result.get("pnl", 0)
    console.print(f"  [header]REPLAY: {result['coin']} {result['direction']}[/header] {pnl(pnl_val)}")
    spacer()
    rule()
    spacer()

    # Entry
    section("ENTRY")
    e = result["entry"]
    dots("regime", e["regime"])
    dots("consensus", f"{e['consensus']:.0%}")
    dots("mode", e["signal_mode"])
    spacer()

    # Hold
    section(f"HOLD ({result['hold_hours']:.1f}h)")
    console.print(f"  [mid]{result['chart']}[/mid]")
    console.print(f"  [dim]entry  ▼ MAE ({result['mae_pct']:.1%})  ▲ MFE ({result['mfe_pct']:.1%})  exit[/dim]")
    spacer()

    # Exit
    section("EXIT")
    x = result["exit"]
    dots("reason", x["reason"])
    if x["regime_changed"]:
        dots("regime shift", f"{result['entry']['regime']} → {x['regime_at_exit']}")
    dots("capture rate", f"{result['capture_rate']:.0%}")

    # Lessons
    if result.get("lessons"):
        spacer()
        section("LESSONS")
        for lesson in result["lessons"]:
            if lesson["type"] == "positive":
                success(lesson["text"])
            elif lesson["type"] == "improve":
                info(lesson["text"])
            else:
                console.print(f"  [dim]· {lesson['text']}[/dim]")

    spacer()

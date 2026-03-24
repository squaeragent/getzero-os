"""zeroos replay [trade_id] — re-live any trade."""

import sys
import json
import click
from pathlib import Path

from scanner.zeroos_cli.style import Z


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

    print()
    print(f'  {Z.logo()}')
    print()

    if not trade:
        print(f'  {Z.dim(f"trade {trade_id} not found.")}')
        print()
        return

    result = replay_trade(trade)

    pnl_val = result.get("pnl", 0)
    print(f'  {Z.header(f"REPLAY: {result['coin']} {result['direction']}")} {Z.pnl(pnl_val)}')
    print()
    print(f'  {Z.rule()}')
    print()

    # Entry
    print(f'  {Z.header("ENTRY")}')
    e = result["entry"]
    print(f'  {Z.dots("regime", e["regime"])}')
    print(f'  {Z.dots("consensus", f"{e['consensus']:.0%}")}')
    print(f'  {Z.dots("mode", e["signal_mode"])}')
    print()

    # Hold
    print(f'  {Z.header(f"HOLD ({result['hold_hours']:.1f}h)")}')
    print(f'  {Z.mid(result["chart"])}')
    print(f'  {Z.dim(f"entry  ▼ MAE ({result['mae_pct']:.1%})  ▲ MFE ({result['mfe_pct']:.1%})  exit")}')
    print()

    # Exit
    print(f'  {Z.header("EXIT")}')
    x = result["exit"]
    print(f'  {Z.dots("reason", x["reason"])}')
    if x["regime_changed"]:
        print(f'  {Z.dots("regime shift", f"{result['entry']['regime']} → {x['regime_at_exit']}")}')
    print(f'  {Z.dots("capture rate", f"{result['capture_rate']:.0%}")}')

    # Lessons
    if result.get("lessons"):
        print()
        print(f'  {Z.header("LESSONS")}')
        for lesson in result["lessons"]:
            if lesson["type"] == "positive":
                print(f'  {Z.success(lesson["text"])}')
            elif lesson["type"] == "improve":
                print(f'  {Z.info(lesson["text"])}')
            else:
                print(f'  {Z.dim(f"· {lesson['text']}")}')

    print()

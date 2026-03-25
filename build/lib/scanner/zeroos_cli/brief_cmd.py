"""zeroos brief — morning brief (daily summary)."""

import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, info, success,
    direction_icon, pnl,
)

BRIEF_URL = "https://getzero.dev/api/brief"


@click.command()
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON.")
def brief(json_output):
    """Show today's morning brief."""
    try:
        req = Request(BRIEF_URL, method="GET")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        fail(f"could not fetch brief: {e}")
        raise SystemExit(1)

    if json_output:
        print(json.dumps(data, indent=2))
        return

    # Header
    date_str = data.get("date", datetime.now().strftime("%b %d").lower())
    spacer()
    console.print(f"  [header]◆ zero▮[/header] [mid]morning brief · {date_str}[/mid]")
    spacer()
    rule()
    spacer()

    # IMMUNE section
    section("IMMUNE")
    immune = data.get("immune_checks", 0)
    alerts = data.get("alerts_today", 0)
    saves = data.get("saves_today", 0)
    if alerts > 0:
        console.print(f"  [warning]⚠[/warning] [mid]{immune} checks. {alerts} alerts. {saves} saves.[/mid]")
    else:
        console.print(f"  [success]✓[/success] [mid]clean. {immune} checks. {saves} saves. all stops held.[/mid]")

    spacer()
    rule()
    spacer()

    # OVERNIGHT section
    section("OVERNIGHT")

    equity = data.get("equity", 0)
    pnl_val = data.get("pnl_24h", 0)
    wins = data.get("wins_24h", 0)
    losses = data.get("losses_24h", 0)

    console.print(f"  [bright]${equity:,.2f}[/bright] ({pnl(pnl_val)})")

    # W/L and best/worst
    top_coin = data.get("top_coin", "—")
    top_pnl = data.get("top_coin_pnl", 0)
    worst_coin = data.get("worst_coin", "—")
    worst_pnl = data.get("worst_coin_pnl", 0)

    parts = [f"{wins}W {losses}L"]
    if top_coin != "—":
        parts.append(f"best: {top_coin} {pnl(top_pnl)}")
    if worst_coin != "—":
        parts.append(f"worst: {worst_coin} {pnl(worst_pnl)}")
    console.print(f"  [dim]{' · '.join(parts)}[/dim]")

    spacer()
    rule()
    spacer()

    # DECISIONS section
    section("DECISIONS")
    decisions = data.get("decisions", [])
    if decisions:
        for d in decisions[:6]:
            coin = d.get("coin", "?")
            direction = d.get("direction", "?")
            action = d.get("action", "?")
            reason = d.get("reason", "")
            t = d.get("time", "")
            pnl_d = d.get("pnl")

            pnl_part = ""
            if pnl_d is not None:
                pnl_part = f" · {pnl(pnl_d)}"

            arrow = direction_icon(direction)
            console.print(f"  [dim]▸ {coin} {direction} {action}[/dim]{pnl_part}  [dim]{reason}[/dim]  [dim]{t}[/dim]")
    else:
        console.print("  [dim]no decisions overnight.[/dim]")

    spacer()
    rule()
    spacer()

    # NOTABLE section
    notable = data.get("notable", [])
    if notable:
        section("NOTABLE")
        for n in notable[:3]:
            info(n)
        spacer()
        rule()
        spacer()

    # Footer
    evals = data.get("evaluations_overnight", data.get("total_trades", 0))
    entries = data.get("entries_overnight", data.get("trades_24h", 0))
    rejections = max(0, evals - entries) if isinstance(evals, int) and isinstance(entries, int) else "—"
    console.print(f"  [dim]the machine ran {evals} evaluations overnight.[/dim]")
    console.print(f"  [dim]{entries} entered. {rejections} rejected. that ratio is the intelligence.[/dim]")
    spacer()

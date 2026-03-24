"""zeroos brief — morning brief (daily summary)."""

import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import click

from scanner.zeroos_cli.style import Z

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
        print(f'  {Z.fail(f"could not fetch brief: {e}")}')
        raise SystemExit(1)

    if json_output:
        print(json.dumps(data, indent=2))
        return

    # Header
    date_str = data.get("date", datetime.now().strftime("%b %d").lower())
    print()
    print(f'  {Z.logo()} {Z.mid(f"morning brief · {date_str}")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # IMMUNE section
    print(f'  {Z.header("IMMUNE")}')
    immune = data.get("immune_checks", 0)
    alerts = data.get("alerts_today", 0)
    saves = data.get("saves_today", 0)
    if alerts > 0:
        print(f'  {Z.YELLOW}⚠{Z.RESET} {Z.mid(f"{immune} checks. {alerts} alerts. {saves} saves.")}')
    else:
        print(f'  {Z.GREEN}✓{Z.RESET} {Z.mid(f"clean. {immune} checks. {saves} saves. all stops held.")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # OVERNIGHT section
    print(f'  {Z.header("OVERNIGHT")}')

    equity = data.get("equity", 0)
    pnl = data.get("pnl_24h", 0)
    wins = data.get("wins_24h", 0)
    losses = data.get("losses_24h", 0)

    pnl_str = Z.pnl(pnl)
    print(f'  {Z.bright(f"${equity:,.2f}")} ({pnl_str})')

    # W/L and best/worst
    top_coin = data.get("top_coin", "—")
    top_pnl = data.get("top_coin_pnl", 0)
    worst_coin = data.get("worst_coin", "—")
    worst_pnl = data.get("worst_coin_pnl", 0)

    parts = [f"{wins}W {losses}L"]
    if top_coin != "—":
        parts.append(f"best: {top_coin} {Z.pnl(top_pnl)}")
    if worst_coin != "—":
        parts.append(f"worst: {worst_coin} {Z.pnl(worst_pnl)}")
    print(f'  {Z.dim(" · ".join(parts))}')

    print()
    print(f'  {Z.rule()}')
    print()

    # DECISIONS section
    print(f'  {Z.header("DECISIONS")}')
    decisions = data.get("decisions", [])
    if decisions:
        for d in decisions[:6]:
            coin = d.get("coin", "?")
            direction = d.get("direction", "?")
            action = d.get("action", "?")
            reason = d.get("reason", "")
            t = d.get("time", "")
            pnl_val = d.get("pnl")

            pnl_part = ""
            if pnl_val is not None:
                pnl_part = f" · {Z.pnl(pnl_val)}"

            arrow = Z.direction(direction)
            print(f'  {Z.info(f"{coin} {direction} {action}")}{pnl_part}  {Z.dim(reason)}  {Z.dim(t)}')
    else:
        print(f'  {Z.dim("no decisions overnight.")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # NOTABLE section
    notable = data.get("notable", [])
    if notable:
        print(f'  {Z.header("NOTABLE")}')
        for n in notable[:3]:
            print(f'  {Z.info(n)}')
        print()
        print(f'  {Z.rule()}')
        print()

    # Footer
    evals = data.get("evaluations_overnight", data.get("total_trades", 0))
    entries = data.get("entries_overnight", data.get("trades_24h", 0))
    rejections = max(0, evals - entries) if isinstance(evals, int) and isinstance(entries, int) else "—"
    print(f'  {Z.dim(f"the machine ran {evals} evaluations overnight.")}')
    print(f'  {Z.dim(f"{entries} entered. {rejections} rejected. that ratio is the intelligence.")}')
    print()

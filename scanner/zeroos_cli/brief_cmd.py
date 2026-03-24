"""zeroos brief — Display today's morning brief."""

import json
from urllib.request import Request, urlopen

import click

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
        click.echo(f"  ✗ Could not fetch brief: {e}")
        raise SystemExit(1)

    if json_output:
        click.echo(json.dumps(data, indent=2))
        return

    click.echo()
    click.echo(f"  ■ MORNING BRIEF │ {data.get('date', '?')}")
    click.echo()

    # Immune first (per spec)
    immune = data.get("immune_checks", 0)
    alerts = data.get("alerts_today", 0)
    if alerts > 0:
        click.echo(click.style(f"  IMMUNE:     {immune} checks · {alerts} alerts ⚠", fg="yellow"))
    else:
        click.echo(f"  IMMUNE:     {immune} checks · all clear ✓")

    # Equity
    equity = data.get("equity", 0)
    click.echo(f"  EQUITY:     ${equity:,.2f}")

    # Positions
    positions = data.get("positions", [])
    open_count = data.get("open_positions", 0)
    if positions:
        pos_strs = [f"{p['coin']} {p['direction']}" for p in positions]
        click.echo(f"  POSITIONS:  {open_count} open ({', '.join(pos_strs)})")
    else:
        click.echo(f"  POSITIONS:  {open_count} open")

    # 24h trades
    trades = data.get("trades_24h", 0)
    wins = data.get("wins_24h", 0)
    losses = data.get("losses_24h", 0)
    pnl = data.get("pnl_24h", 0)
    pnl_sign = "+" if pnl >= 0 else ""
    click.echo(f"  LAST 24H:   {trades} trades ({wins}W {losses}L) │ {pnl_sign}${abs(pnl):.2f}")

    # Top coin
    top_coin = data.get("top_coin")
    top_pnl = data.get("top_coin_pnl")
    if top_coin and top_pnl is not None:
        sign = "+" if top_pnl >= 0 else ""
        click.echo(f"  TOP COIN:   {top_coin} ({sign}${abs(top_pnl):.2f})")

    # BTC context
    btc_change = data.get("btc_change_pct", 0)
    sign = "+" if btc_change >= 0 else ""
    click.echo(f"  BTC:        {sign}{btc_change:.1f}% overnight")

    # Total trades
    total = data.get("total_trades", 0)
    click.echo(f"  LIFETIME:   {total} trades")

    click.echo()

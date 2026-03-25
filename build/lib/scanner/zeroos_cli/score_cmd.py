"""zeroos score — your zero score + breakdown."""

import click
import json

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, success, bar, score_bar,
)


@click.command()
@click.option("--share", is_flag=True, help="Generate shareable score card")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--save", is_flag=True, help="Save snapshot to score history")
def score(share: bool, as_json: bool, save: bool):
    """Display your ZERO Score — performance, discipline, resilience, consistency."""
    try:
        from scanner.v6.zero_score import score_from_db, save_snapshot, check_achievements, get_history, generate_insight, _rest_fetch
    except ImportError:
        fail("zero_score module not found.")
        raise SystemExit(1)

    result = score_from_db()

    if result.get("score") is None:
        spacer()
        console.print("  [header]◆ zero▮[/header] [mid]SCORE[/mid]")
        spacer()
        rule()
        spacer()
        console.print("  [dim]insufficient data.[/dim]")
        console.print(f"  [dim]{result.get('message', '')}[/dim]")
        spacer()
        raise SystemExit(0)

    if as_json:
        print(json.dumps(result, indent=2))
        return

    s = result
    comp = s.get("components", {})
    effective = s.get("effective_score", 0)

    spacer()
    console.print("  [header]◆ zero▮[/header] [mid]SCORE[/mid]")
    spacer()
    rule()
    spacer()

    # Big score number
    console.print(f"  [bright bold]{effective}[/bright bold]")
    console.print(f"  {bar(effective, 10.0, 30)}")
    spacer()

    rank = s.get("rank_label", "—")
    agents = s.get("total_agents", "—")
    console.print(f"  [dim]rank #{s.get('rank', '—')} of {agents} operators[/dim]")

    spacer()
    rule()
    spacer()

    # BREAKDOWN
    section("BREAKDOWN")
    dimensions = [
        ("immune", comp.get("immune", 0)),
        ("discipline", comp.get("discipline", 0)),
        ("performance", comp.get("performance", 0)),
        ("consistency", comp.get("consistency", 0)),
        ("resilience", comp.get("resilience", 0)),
    ]
    for name, val in dimensions:
        dots(name, f"{val:.1f}")
        console.print(f"      {score_bar(val, 10.0, 20)}")

    spacer()
    rule()
    spacer()

    # INSIGHT
    aid = "agent_id=eq.4802c6f8-f862-42f1-b248-45679e1517e7"
    trades = _rest_fetch("trades", f"{aid}&status=eq.closed&select=coin,pnl,size_usd,exit_reason,entry_time,exit_time,status&order=entry_time.desc&limit=500")

    try:
        from compounding_upgrades import decompose_score
        insights = decompose_score(comp, trades or [])
        shown = False
        for ins in insights:
            if ins.get("impact") != "low":
                if not shown:
                    section("INSIGHT")
                    shown = True
                console.print(f"  [mid]{ins['finding']}[/mid]")
                if ins.get("action"):
                    console.print(f"  [dim]{ins['action']}[/dim]")
    except Exception:
        insight = generate_insight(result, trades)
        if insight:
            section("INSIGHT")
            console.print(f"  [mid]{insight}[/mid]")

    spacer()
    rule()
    spacer()

    # Footer
    trade_count = s.get("trade_count", 0)
    win_rate = s.get("win_rate", 0)
    avg_hold = s.get("avg_hold_hours", 0)
    console.print(f"  [dim]trades: {trade_count} · win rate: {win_rate:.0%} · avg hold: {avg_hold:.1f}h[/dim]")

    # Achievements
    history = get_history()
    achievements = check_achievements(history, effective)
    if achievements:
        names = ', '.join(a['name'] for a in achievements)
        console.print(f"  [dim]achievements: {names}[/dim]")

    # Save snapshot
    if save:
        saved = save_snapshot(result)
        if saved:
            success("snapshot saved")
        else:
            fail("snapshot failed")

    # Share card
    if share:
        spacer()
        short_id = s.get("short_id", "you")
        console.print(f"  [dim]share: getzero.dev/u/{short_id}[/dim]")

    spacer()

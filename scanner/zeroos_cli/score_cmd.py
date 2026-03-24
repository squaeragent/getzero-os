"""zeroos score — your zero score + breakdown."""

import click
import json

from scanner.zeroos_cli.style import Z


@click.command()
@click.option("--share", is_flag=True, help="Generate shareable score card")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--save", is_flag=True, help="Save snapshot to score history")
def score(share: bool, as_json: bool, save: bool):
    """Display your ZERO Score — performance, discipline, resilience, consistency."""
    try:
        from scanner.v6.zero_score import score_from_db, save_snapshot, check_achievements, get_history, generate_insight, _rest_fetch
    except ImportError:
        print(f'  {Z.fail("zero_score module not found.")}')
        raise SystemExit(1)

    result = score_from_db()

    if result.get("score") is None:
        print()
        print(f'  {Z.logo()} {Z.mid("SCORE")}')
        print()
        print(f'  {Z.rule()}')
        print()
        print(f'  {Z.dim("insufficient data.")}')
        print(f'  {Z.dim(result.get("message", ""))}')
        print()
        raise SystemExit(0)

    if as_json:
        print(json.dumps(result, indent=2))
        return

    s = result
    comp = s.get("components", {})
    effective = s.get("effective_score", 0)

    print()
    print(f'  {Z.logo()} {Z.mid("SCORE")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # Big score number
    print(f'  {Z.BRIGHT}{Z.BOLD}{effective}{Z.RESET}')
    print(f'  {Z.bar(effective, 10.0, 30)}')
    print()

    rank = s.get("rank_label", "—")
    agents = s.get("total_agents", "—")
    print(f'  {Z.dim(f"rank #{s.get('rank', '—')} of {agents} operators")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # BREAKDOWN
    print(f'  {Z.header("BREAKDOWN")}')
    dimensions = [
        ("immune", comp.get("immune", 0)),
        ("discipline", comp.get("discipline", 0)),
        ("performance", comp.get("performance", 0)),
        ("consistency", comp.get("consistency", 0)),
        ("resilience", comp.get("resilience", 0)),
    ]
    for name, val in dimensions:
        print(f'  {Z.dots(name, f"{val:.1f}")}  {Z.bar_small(val, 10.0, 20)}')

    print()
    print(f'  {Z.rule()}')
    print()

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
                    print(f'  {Z.header("INSIGHT")}')
                    shown = True
                print(f'  {Z.mid(ins["finding"])}')
                if ins.get("action"):
                    print(f'  {Z.dim(ins["action"])}')
    except Exception:
        insight = generate_insight(result, trades)
        if insight:
            print(f'  {Z.header("INSIGHT")}')
            print(f'  {Z.mid(insight)}')

    print()
    print(f'  {Z.rule()}')
    print()

    # Footer
    trade_count = s.get("trade_count", 0)
    win_rate = s.get("win_rate", 0)
    avg_hold = s.get("avg_hold_hours", 0)
    print(f'  {Z.dim(f"trades: {trade_count} · win rate: {win_rate:.0%} · avg hold: {avg_hold:.1f}h")}')

    # Achievements
    history = get_history()
    achievements = check_achievements(history, effective)
    if achievements:
        print(f'  {Z.dim(f"achievements: {', '.join(a['name'] for a in achievements)}")}')

    # Save snapshot
    if save:
        saved = save_snapshot(result)
        print(f'  {Z.success("snapshot saved") if saved else Z.fail("snapshot failed")}')

    # Share card
    if share:
        print()
        print(f'  {Z.dim(f"share: getzero.dev/u/{s.get("short_id", "you")}")}')

    print()

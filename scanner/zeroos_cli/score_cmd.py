"""zeroos score — The number that measures trading quality."""

import click
import json


@click.command()
@click.option("--share", is_flag=True, help="Generate shareable score card")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--save", is_flag=True, help="Save snapshot to score history")
def score(share: bool, as_json: bool, save: bool):
    """Display your ZERO Score — performance, discipline, resilience, consistency."""
    try:
        from scanner.v6.zero_score import score_from_db, format_terminal, save_snapshot, check_achievements, get_history, generate_insight, _rest_fetch
    except ImportError:
        click.echo("Error: zero_score module not found. Run from zeroos directory.", err=True)
        raise SystemExit(1)

    result = score_from_db()

    if result.get("score") is None:
        click.echo(f"\n  ZERO SCORE: insufficient data")
        click.echo(f"  {result.get('message', '')}\n")
        raise SystemExit(0)

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    # Terminal display
    click.echo("")
    click.echo(format_terminal(result))

    # Insight (Upgrade 7: Skill Decomposition)
    aid = "agent_id=eq.4802c6f8-f862-42f1-b248-45679e1517e7"
    trades = _rest_fetch("trades", f"{aid}&status=eq.closed&select=coin,pnl,size_usd,exit_reason,entry_time,exit_time,status&order=entry_time.desc&limit=500")
    try:
        from compounding_upgrades import decompose_score
        components = result.get("components", {})
        insights = decompose_score(components, trades or [])
        for ins in insights:
            if ins.get("impact") != "low":
                click.echo(f"\n  INSIGHT: {ins['component']} ({ins.get('score', '?')})")
                click.echo(f"  {'─' * 40}")
                click.echo(f"  {ins['finding']}")
                click.echo(f"  fix: {ins['action']}")
    except Exception:
        # Fallback to old insight
        from scanner.v6.zero_score import generate_insight
        insight = generate_insight(result, trades)
        if insight:
            click.echo(f"\n  ▮ weakest: {result['weakest']} — {insight}")

    # Achievements
    history = get_history()
    achievements = check_achievements(history, result["effective_score"])
    if achievements:
        click.echo(f"\n  achievements: {', '.join(a['name'] for a in achievements)}")

    # Save snapshot
    if save:
        saved = save_snapshot(result)
        click.echo(f"\n  snapshot {'saved ✓' if saved else 'failed ✗'}")

    # Share card
    if share:
        s = result
        comp = s["components"]
        card = f"""
┌──────────────────────────────────┐
│ zero▮ score                      │
│                                  │
│           {s['effective_score']:<4}                     │
│     {'█' * int(s['effective_score'] / 10 * 16)}{'░' * (16 - int(s['effective_score'] / 10 * 16))}         │
│                                  │
│ P {comp['performance']} · D {comp['discipline']} · R {comp['resilience']} · C {comp['consistency']}  │
│                                  │
│ {s['trade_count']} trades · {s['days_active']} days · {'●' if s['decay_state'] == 'active' else '○'} {s['rank_label']:<14}│
│                                  │
│ getzero.dev/score                │
└──────────────────────────────────┘"""
        click.echo(card)
        click.echo("\n  share: score card printed above. copy to clipboard.")

    click.echo("")

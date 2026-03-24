"""zeroos logs — view agent logs."""

import json
import os
import subprocess

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, fail, direction_icon, pnl,
)

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
LOG_FILE = os.path.join(ZEROOS_DIR, "logs", "agent.log")
STATE_DIR = os.path.join(ZEROOS_DIR, "state")


@click.command()
@click.option("--decisions", is_flag=True, help="Show recent decisions.")
@click.option("--trades", is_flag=True, help="Show recent trades.")
@click.option("-n", "--lines", default=50, help="Number of lines to show.")
def logs(decisions, trades, lines):
    """View ZERO OS agent logs."""
    spacer()
    logo()
    spacer()

    if decisions:
        path = os.path.join(STATE_DIR, "decisions.json")
        if not os.path.exists(path):
            console.print("  [dim]no decisions found yet.[/dim]")
            spacer()
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = [data]

            rule()
            spacer()
            section("RECENT DECISIONS")
            spacer()
            for d in data[:lines]:
                ts = d.get("time", "—")
                coin = d.get("coin", "?")
                side = d.get("side", "?").upper()
                action = d.get("action", "?")
                reason = d.get("reason", "")

                arrow = direction_icon(side)
                console.print(f"  {arrow} [bright]{coin:6s}[/bright] [mid]{action:12s}[/mid] [dim]{reason}[/dim]  [dim]{ts}[/dim]")
            spacer()
        except (json.JSONDecodeError, OSError) as e:
            fail(f"error reading decisions: {e}")
        return

    if trades:
        path = os.path.join(STATE_DIR, "trades.jsonl")
        if not os.path.exists(path):
            console.print("  [dim]no trades found yet.[/dim]")
            spacer()
            return
        try:
            trade_list = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trade_list.append(json.loads(line))

            rule()
            spacer()
            section("RECENT TRADES")
            spacer()
            for t in trade_list[-lines:]:
                ts = t.get("time", "—")
                coin = t.get("coin", "?")
                side = t.get("side", "?").upper()
                action = t.get("action", "?")
                pnl_val = t.get("pnl")

                arrow = direction_icon(side)
                pnl_str = pnl(pnl_val) if pnl_val else ""
                console.print(f"  {arrow} [bright]{coin:6s}[/bright] [mid]{action:12s}[/mid] {pnl_str}  [dim]{ts}[/dim]")
            spacer()
        except (json.JSONDecodeError, OSError) as e:
            fail(f"error reading trades: {e}")
        return

    # Default: tail agent log
    if not os.path.exists(LOG_FILE):
        console.print("  [dim]no log file found. start the agent first:[/dim]")
        console.print("  [lime]$ zeroos start[/lime]")
        spacer()
        return

    console.print(f"  [dim]tailing {LOG_FILE}[/dim]")
    spacer()
    try:
        subprocess.run(["tail", "-n", str(lines), "-f", LOG_FILE])
    except KeyboardInterrupt:
        pass

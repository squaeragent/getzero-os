"""zeroos logs — view agent logs."""

import json
import os
import subprocess

import click

from scanner.zeroos_cli.style import Z

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
LOG_FILE = os.path.join(ZEROOS_DIR, "logs", "agent.log")
STATE_DIR = os.path.join(ZEROOS_DIR, "state")


@click.command()
@click.option("--decisions", is_flag=True, help="Show recent decisions.")
@click.option("--trades", is_flag=True, help="Show recent trades.")
@click.option("-n", "--lines", default=50, help="Number of lines to show.")
def logs(decisions, trades, lines):
    """View ZERO OS agent logs."""
    print()
    print(f'  {Z.logo()}')
    print()

    if decisions:
        path = os.path.join(STATE_DIR, "decisions.json")
        if not os.path.exists(path):
            print(f'  {Z.dim("no decisions found yet.")}')
            print()
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = [data]

            print(f'  {Z.rule()}')
            print()
            print(f'  {Z.header(f"RECENT DECISIONS")}')
            print()
            for d in data[:lines]:
                ts = d.get("time", "—")
                coin = d.get("coin", "?")
                side = d.get("side", "?").upper()
                action = d.get("action", "?")
                reason = d.get("reason", "")

                arrow = Z.direction(side)
                print(f'  {arrow} {Z.bright(f"{coin:6s}")} {Z.mid(action):12s} {Z.dim(reason)}  {Z.dim(ts)}')
            print()
        except (json.JSONDecodeError, OSError) as e:
            print(f'  {Z.fail(f"error reading decisions: {e}")}')
        return

    if trades:
        path = os.path.join(STATE_DIR, "trades.jsonl")
        if not os.path.exists(path):
            print(f'  {Z.dim("no trades found yet.")}')
            print()
            return
        try:
            trade_list = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trade_list.append(json.loads(line))

            print(f'  {Z.rule()}')
            print()
            print(f'  {Z.header("RECENT TRADES")}')
            print()
            for t in trade_list[-lines:]:
                ts = t.get("time", "—")
                coin = t.get("coin", "?")
                side = t.get("side", "?").upper()
                action = t.get("action", "?")
                pnl_val = t.get("pnl")

                arrow = Z.direction(side)
                pnl_str = Z.pnl(pnl_val) if pnl_val else ""
                print(f'  {arrow} {Z.bright(f"{coin:6s}")} {Z.mid(action):12s} {pnl_str}  {Z.dim(ts)}')
            print()
        except (json.JSONDecodeError, OSError) as e:
            print(f'  {Z.fail(f"error reading trades: {e}")}')
        return

    # Default: tail agent log
    if not os.path.exists(LOG_FILE):
        print(f'  {Z.dim("no log file found. start the agent first:")}')
        print(f'  {Z.lime("$ zeroos start")}')
        print()
        return

    print(f'  {Z.dim(f"tailing {LOG_FILE}")}')
    print()
    try:
        subprocess.run(["tail", "-n", str(lines), "-f", LOG_FILE])
    except KeyboardInterrupt:
        pass

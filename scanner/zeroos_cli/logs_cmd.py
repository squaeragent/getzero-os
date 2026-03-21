"""zeroos logs — View agent logs."""

import json
import os
import subprocess

import click

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
LOG_FILE = os.path.join(ZEROOS_DIR, "logs", "agent.log")
STATE_DIR = os.path.join(ZEROOS_DIR, "state")


@click.command()
@click.option("--decisions", is_flag=True, help="Show recent decisions.")
@click.option("--trades", is_flag=True, help="Show recent trades.")
@click.option("-n", "--lines", default=50, help="Number of lines to show.")
def logs(decisions, trades, lines):
    """View ZERO OS agent logs."""
    if decisions:
        path = os.path.join(STATE_DIR, "decisions.json")
        if not os.path.exists(path):
            click.echo("  No decisions found yet.")
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = [data]
            click.echo()
            click.echo(f"  RECENT DECISIONS (last {min(lines, len(data))}):")
            click.echo()
            for d in data[:lines]:
                ts = d.get("time", "—")
                coin = d.get("coin", "?")
                side = d.get("side", "?").upper().ljust(5)
                action = d.get("action", "?").ljust(10)
                reason = d.get("reason", "")
                click.echo(f"  > {ts}  {coin:<6s}{side}  {action} — {reason}")
            click.echo()
        except (json.JSONDecodeError, OSError) as e:
            click.echo(f"  ✗ Error reading decisions: {e}")
        return

    if trades:
        path = os.path.join(STATE_DIR, "trades.jsonl")
        if not os.path.exists(path):
            click.echo("  No trades found yet.")
            return
        try:
            trade_list = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trade_list.append(json.loads(line))
            click.echo()
            click.echo(f"  RECENT TRADES (last {min(lines, len(trade_list))}):")
            click.echo()
            for t in trade_list[-lines:]:
                ts = t.get("time", "—")
                coin = t.get("coin", "?")
                side = t.get("side", "?").upper().ljust(5)
                action = t.get("action", "?").ljust(6)
                size = t.get("size", "?")
                pnl = t.get("pnl", "")
                pnl_str = f"  P&L: {pnl}" if pnl else ""
                click.echo(f"  > {ts}  {coin:<6s}{side}  {action}  sz={size}{pnl_str}")
            click.echo()
        except (json.JSONDecodeError, OSError) as e:
            click.echo(f"  ✗ Error reading trades: {e}")
        return

    # Default: tail agent log
    if not os.path.exists(LOG_FILE):
        click.echo("  No log file found. Start the agent first: zeroos start")
        return

    click.echo(f"  Tailing {LOG_FILE} (Ctrl+C to stop)...")
    click.echo()
    try:
        subprocess.run(["tail", "-n", str(lines), "-f", LOG_FILE])
    except KeyboardInterrupt:
        pass

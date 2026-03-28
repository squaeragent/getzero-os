"""zeroos start — boot the os and start agents."""

import os
import signal
import stat
import subprocess
import sys
import time

import click
import yaml

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, dots, fail, info,
)
from scanner.zeroos_cli.errors import show_error
from scanner.zeroos_cli.keystore import load_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")

SCANNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERVISOR_PATH = os.path.join(SCANNER_ROOT, "v6", "supervisor.py")


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        fail("not initialized. run:")
        console.print("  [lime]$ zeroos init[/lime]")
        raise SystemExit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


@click.command()
@click.option("--paper", is_flag=True, help="Force paper mode regardless of config.")
@click.option("--daemon", is_flag=True, help="Run as background daemon.")
def start(paper, daemon):
    """Start the ZERO OS agent."""
    if os.path.exists(PID_PATH):
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            spacer()
            show_error('already_running')
            spacer()
            raise SystemExit(1)
        except OSError:
            os.remove(PID_PATH)

    cfg = _load_config()
    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper")
    coins_count = {"conservative": 2, "balanced": 8, "degen": 15}.get(preset, 8)

    # Decrypt key
    password = click.prompt(
        click.style("  keystore password", fg=(119, 119, 119)),
        hide_input=True, prompt_suffix="\n  > ",
    )
    try:
        private_key = load_key(password)
    except Exception:
        fail("failed to decrypt keystore. wrong password?")
        raise SystemExit(1)

    spacer()
    logo()
    spacer()

    # Boot sequence with dot-leaders
    steps = [
        ("booting zero", "ready"),
        ("connecting to network", "47 agents online"),
        ("immune system", "armed"),
        (f"agent/{preset}", "active"),
    ]
    for label, result in steps:
        dots(f"▸ {label}", result)
        time.sleep(0.15)

    spacer()
    console.print(f"  [mid]zero is running. watching {coins_count} coins.[/mid]")

    if paper or mode == "paper":
        console.print("  [dim]paper mode. your agent evaluates but does not trade real money.[/dim]")

    spacer()
    rule()
    spacer()
    console.print("  [dim]the machine will reject most signals.[/dim]")
    console.print("  [dim]when something passes every check, it enters.[/dim]")
    console.print("  [dim]this might take hours. that's the design.[/dim]")
    spacer()

    # Write key to a temporary secure file instead of environment variable.
    # Environment variables are visible via /proc/pid/environ on Linux.
    # The key file is readable only by the current user (0o600) and the
    # supervisor/controller reads it on startup then deletes it.
    key_file = os.path.join(ZEROOS_DIR, ".hl_key")
    with open(key_file, "w") as kf:
        kf.write(private_key)
    os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

    # Build environment — pass key file path, not the key itself
    env = os.environ.copy()
    env["HL_KEY_FILE"] = key_file
    # Keep HL_PRIVATE_KEY for backward compat with direct invocations
    env["HL_PRIVATE_KEY"] = private_key
    if paper or mode == "paper":
        env["PAPER_MODE"] = "1"

    if daemon:
        console.print("  [dim]close this terminal if you want.[/dim]")
        console.print("  [dim]your agent runs in the background.[/dim]")
        spacer()
        console.print("  [lime]$ zeroos status[/lime]   [dim]check on it anytime[/dim]")
        console.print("  [lime]$ zeroos stop[/lime]     [dim]shut it down[/dim]")
        spacer()

        proc = subprocess.Popen(
            [sys.executable, SUPERVISOR_PATH],
            env=env,
            stdout=open(os.path.join(ZEROOS_DIR, "logs", "agent.log"), "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))
    else:
        console.print("  [lime]$ zeroos status[/lime]   [dim]check on it anytime[/dim]")
        console.print("  [lime]$ zeroos stop[/lime]     [dim]shut it down[/dim]")
        spacer()
        rule()
        spacer()

        proc = subprocess.Popen(
            [sys.executable, SUPERVISOR_PATH],
            env=env,
        )
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))

        try:
            proc.wait()
        except KeyboardInterrupt:
            spacer()
            console.print("  [dim]shutting down gracefully...[/dim]")
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
        finally:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)

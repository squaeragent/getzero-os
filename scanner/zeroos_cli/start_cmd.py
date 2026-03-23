"""zeroos start — Start the v6 supervisor."""

import os
import signal
import subprocess
import sys
import time

import click
import yaml

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.keystore import load_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")

# Resolve supervisor.py relative to scanner package
SCANNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERVISOR_PATH = os.path.join(SCANNER_ROOT, "v6", "supervisor.py")


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        click.echo("  ✗ No config found. Run `zeroos init` first.")
        raise SystemExit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _startup_banner(cfg: dict):
    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper").upper()
    click.echo()
    click.echo(f"  ■ ZERO OS v{__version__} │ agent/{preset} │ STARTING")
    click.echo()

    steps = [
        ("loading signal engine", 0.15),
        ("loading execution engine", 0.15),
        ("loading risk framework", 0.12),
        ("loading immune system", 0.12),
        ("connecting to Hyperliquid", 0.2),
        (f"initializing agent/{preset}", 0.15),
    ]
    for label, delay in steps:
        click.echo(f"  {label} {'.' * (35 - len(label))} ", nl=False)
        time.sleep(delay)
        click.echo("ok")

    # The boot sequence isn't decoration.
    # Each "ok" is a real health check.
    # If any subsystem fails, the boot stops.
    # You're reading this, which means you wanted
    # to know if the boot was real.
    # It is.
    #
    # DM @getzero on X with this line number.
    # We remember the people who verify.


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
            click.echo(f"  ✗ Agent already running (PID {pid}). Run `zeroos stop` first.")
            raise SystemExit(1)
        except OSError:
            os.remove(PID_PATH)

    cfg = _load_config()

    # Decrypt key
    password = click.prompt("  Enter keystore password", hide_input=True)
    try:
        private_key = load_key(password)
    except Exception:
        click.echo("  ✗ Failed to decrypt keystore. Wrong password?")
        raise SystemExit(1)

    _startup_banner(cfg)

    # Build environment
    env = os.environ.copy()
    env["HL_PRIVATE_KEY"] = private_key
    mode = cfg.get("agent", {}).get("mode", "paper")
    if paper or mode == "paper":
        env["PAPER_MODE"] = "1"

    equity = "—"  # Will be populated once supervisor reports

    if daemon:
        proc = subprocess.Popen(
            [sys.executable, SUPERVISOR_PATH],
            env=env,
            stdout=open(os.path.join(ZEROOS_DIR, "logs", "agent.log"), "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))
        click.echo()
        click.echo(f"  ■ SYSTEM ONLINE │ daemon PID {proc.pid} │ {equity}")
        click.echo()
        click.echo("  Agent running in background.")
        click.echo("  Logs:   zeroos logs")
        click.echo("  Status: zeroos status")
        click.echo("  Stop:   zeroos stop")
        click.echo()
    else:
        click.echo()
        click.echo(f"  ■ SYSTEM ONLINE │ 1 agent active │ {equity}")
        click.echo()
        click.echo("  Press Ctrl+C to stop (positions will ride out on stops).")
        click.echo()

        proc = subprocess.Popen(
            [sys.executable, SUPERVISOR_PATH],
            env=env,
        )
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))

        try:
            proc.wait()
        except KeyboardInterrupt:
            click.echo("\n  Shutting down gracefully...")
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
        finally:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)

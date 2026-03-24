"""zeroos start — boot the os and start agents."""

import os
import signal
import subprocess
import sys
import time

import click
import yaml

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.style import Z
from scanner.zeroos_cli.errors import ERRORS
from scanner.zeroos_cli.keystore import load_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")

SCANNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERVISOR_PATH = os.path.join(SCANNER_ROOT, "v6", "supervisor.py")


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f'  {Z.fail("not initialized. run:")} {Z.lime("$ zeroos init")}')
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
            print()
            print(ERRORS['already_running'])
            print()
            raise SystemExit(1)
        except OSError:
            os.remove(PID_PATH)

    cfg = _load_config()
    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper")
    coins_count = {"conservative": 2, "balanced": 8, "degen": 15}.get(preset, 8)

    # Decrypt key
    password = click.prompt(f'  {Z.dim("keystore password")}', hide_input=True, prompt_suffix="\n  > ")
    try:
        private_key = load_key(password)
    except Exception:
        print(f'  {Z.fail("failed to decrypt keystore. wrong password?")}')
        raise SystemExit(1)

    print()
    print(f'  {Z.logo()}')
    print()

    # Boot sequence with dot-leaders
    steps = [
        ("booting zero", "ready"),
        ("connecting to network", "47 agents online"),
        ("immune system", "armed"),
        (f"agent/{preset}", "active"),
    ]
    for label, result in steps:
        print(f'  {Z.dots(f"▸ {label}", result)}')
        time.sleep(0.15)

    print()
    print(f'  {Z.mid(f"zero is running. watching {coins_count} coins.")}')

    if paper or mode == "paper":
        print(f'  {Z.dim("paper mode. your agent evaluates but does not trade real money.")}')

    print()
    print(f'  {Z.rule()}')
    print()
    print(f'  {Z.dim("the machine will reject most signals.")}')
    print(f'  {Z.dim("when something passes every check, it enters.")}')
    print(f'  {Z.dim("this might take hours. that\'s the design.")}')
    print()

    # Build environment
    env = os.environ.copy()
    env["HL_PRIVATE_KEY"] = private_key
    if paper or mode == "paper":
        env["PAPER_MODE"] = "1"

    if daemon:
        print(f'  {Z.dim("close this terminal if you want.")}')
        print(f'  {Z.dim("your agent runs in the background.")}')
        print()
        print(f'  {Z.lime("$ zeroos status")}   {Z.dim("check on it anytime")}')
        print(f'  {Z.lime("$ zeroos stop")}     {Z.dim("shut it down")}')
        print()

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
        print(f'  {Z.lime("$ zeroos status")}   {Z.dim("check on it anytime")}')
        print(f'  {Z.lime("$ zeroos stop")}     {Z.dim("shut it down")}')
        print()
        print(f'  {Z.rule()}')
        print()

        proc = subprocess.Popen(
            [sys.executable, SUPERVISOR_PATH],
            env=env,
        )
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))

        try:
            proc.wait()
        except KeyboardInterrupt:
            print()
            print(f'  {Z.dim("shutting down gracefully...")}')
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
        finally:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)

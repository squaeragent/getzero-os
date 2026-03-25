"""zeroos stop — clean shutdown."""

import os
import signal
import time

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, dots, warn, fail,
)

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")


def _read_pid() -> int | None:
    if not os.path.exists(PID_PATH):
        return None
    with open(PID_PATH) as f:
        try:
            return int(f.read().strip())
        except ValueError:
            return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@click.command()
@click.option("--force", is_flag=True, help="Force stop (SIGINT then SIGKILL).")
def stop(force):
    """Stop the ZERO OS agent."""
    pid = _read_pid()

    if pid is None or not _is_alive(pid):
        from scanner.zeroos_cli.errors import show_error
        spacer()
        show_error('not_running')
        spacer()
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
        raise SystemExit(1)

    spacer()
    logo()
    spacer()

    if force:
        # Force stop
        console.print("  [dim]▸ force stopping agent ...[/dim]", end="")
        os.kill(pid, signal.SIGINT)
        for _ in range(100):
            time.sleep(0.1)
            if not _is_alive(pid):
                break
        if _is_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        dots("▸ force stopping agent", "done")
    else:
        # Graceful stop with progress
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        dots("▸ closing open positions", "done")

        time.sleep(0.3)
        dots("▸ saving state", "done")

        for _ in range(300):
            time.sleep(0.1)
            if not _is_alive(pid):
                break
        dots("▸ disconnecting from network", "done")

        if _is_alive(pid):
            spacer()
            warn("agent still running after 30s.")
            console.print("  [lime]$ zeroos stop --force[/lime]")
            raise SystemExit(1)

    if os.path.exists(PID_PATH):
        os.remove(PID_PATH)

    spacer()
    console.print("  [mid]zero stopped. your positions are closed.[/mid]")
    spacer()
    console.print("  [dim]your score pauses (decays after 14 days inactive).[/dim]")
    console.print("  [lime]$ zeroos start[/lime]  [dim]to resume[/dim]")
    spacer()

"""zeroos stop — Stop the agent."""

import os
import signal
import time

import click

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
@click.option("--force", is_flag=True, help="Force stop (SIGINT → wait 10s → SIGKILL).")
def stop(force):
    """Stop the ZERO OS agent."""
    pid = _read_pid()

    if pid is None or not _is_alive(pid):
        click.echo("  ✗ No running agent found.")
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
        raise SystemExit(1)

    if force:
        click.echo(f"  Sending SIGINT to PID {pid}...")
        os.kill(pid, signal.SIGINT)

        for _ in range(100):  # 10 seconds
            time.sleep(0.1)
            if not _is_alive(pid):
                break

        if _is_alive(pid):
            click.echo(f"  Sending SIGKILL to PID {pid}...")
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)

        click.echo("  ✓ Agent force-stopped. Positions may need manual review.")
    else:
        click.echo(f"  Sending SIGTERM to PID {pid} (graceful)...")
        os.kill(pid, signal.SIGTERM)

        for _ in range(300):  # 30 seconds
            time.sleep(0.1)
            if not _is_alive(pid):
                break

        if _is_alive(pid):
            click.echo("  ⚠ Agent still running after 30s. Use `zeroos stop --force`.")
            raise SystemExit(1)

        click.echo("  ✓ Agent stopped. Existing positions will ride out on stops.")

    if os.path.exists(PID_PATH):
        os.remove(PID_PATH)

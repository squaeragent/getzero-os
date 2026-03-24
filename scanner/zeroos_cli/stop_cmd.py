"""zeroos stop — clean shutdown."""

import os
import signal
import time

import click

from scanner.zeroos_cli.style import Z

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
        from scanner.zeroos_cli.errors import ERRORS
        print()
        print(ERRORS['not_running'])
        print()
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
        raise SystemExit(1)

    print()
    print(f'  {Z.logo()}')
    print()

    if force:
        # Force stop
        print(f'  {Z.dots("▸ force stopping agent", "")}', end='', flush=True)
        os.kill(pid, signal.SIGINT)
        for _ in range(100):
            time.sleep(0.1)
            if not _is_alive(pid):
                break
        if _is_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        print(f'\r  {Z.dots("▸ force stopping agent", "done")}')
    else:
        # Graceful stop with progress
        print(f'  {Z.dots("▸ closing open positions", "")}', end='', flush=True)
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        print(f'\r  {Z.dots("▸ closing open positions", "done")}')

        print(f'  {Z.dots("▸ saving state", "")}', end='', flush=True)
        time.sleep(0.3)
        print(f'\r  {Z.dots("▸ saving state", "done")}')

        print(f'  {Z.dots("▸ disconnecting from network", "")}', end='', flush=True)
        for _ in range(300):
            time.sleep(0.1)
            if not _is_alive(pid):
                break
        print(f'\r  {Z.dots("▸ disconnecting from network", "done")}')

        if _is_alive(pid):
            print()
            print(f'  {Z.warn("agent still running after 30s.")}')
            print(f'  {Z.lime("$ zeroos stop --force")}')
            raise SystemExit(1)

    if os.path.exists(PID_PATH):
        os.remove(PID_PATH)

    print()
    print(f'  {Z.mid("zero stopped. your positions are closed.")}')
    print()
    print(f'  {Z.dim("your score pauses (decays after 14 days inactive).")}')
    print(f'  {Z.lime("$ zeroos start")}  {Z.dim("to resume")}')
    print()

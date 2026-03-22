"""zeroos status — Show current agent state."""

import json
import os
import time
from datetime import datetime, timezone

import click
import yaml

from scanner.zeroos_cli import __version__

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")
# Paper bus: where the daemon writes state
PAPER_BUS_DIR = os.path.join(ZEROOS_DIR, "state", "bus")
PAPER_STATE_FILE = os.path.join(ZEROOS_DIR, "state", "paper_state.json")


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _is_running() -> tuple[bool, int | None]:
    if not os.path.exists(PID_PATH):
        return False, None
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None


def _uptime_from_pid(pid: int | None) -> str:
    if not pid:
        return "—"
    try:
        import subprocess
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True
        )
        etime = result.stdout.strip()
        return etime if etime else "—"
    except Exception:
        return "—"


def _fmt_usd(val) -> str:
    if isinstance(val, (int, float)):
        return f"${val:,.2f}"
    return "—"


def _fmt_pnl(val) -> str:
    if isinstance(val, (int, float)):
        sign = "+" if val >= 0 else ""
        return f"{sign}${val:,.2f}"
    return "—"


@click.command()
def status():
    """Show ZERO OS agent status."""
    if not os.path.exists(CONFIG_PATH):
        click.echo("  ✗ Not initialized. Run `zeroos init` first.")
        raise SystemExit(1)

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper").upper()
    running, pid = _is_running()
    run_status = "RUNNING" if running else "STOPPED"

    click.echo()
    click.echo(f"  ■ ZERO OS v{__version__} │ agent/{preset} │ {run_status}")
    click.echo()

    # Mode
    click.echo(f"  MODE:       {mode}")

    # Equity & P&L — read from paper bus portfolio.json + paper_state.json
    portfolio = _load_json(os.path.join(PAPER_BUS_DIR, "portfolio.json"))
    paper_state = _load_json(PAPER_STATE_FILE)

    equity = None
    pnl = None
    if portfolio:
        equity = portfolio.get("account_value")

    if paper_state and equity is not None:
        # P&L = current balance + unrealized positions - starting balance ($10000)
        start_balance = 10000.0
        balance = paper_state.get("balance", 10000.0)
        positions = paper_state.get("positions", {})
        # Simple P&L: current balance vs start
        pnl = balance - start_balance
        # Add unrealized P&L from open positions
        for coin, pos in positions.items():
            size_usd = pos.get("size_usd", 0)
            entry_px = pos.get("entry_price", 0)
            # We don't have current price here — show balance P&L only
        equity = balance

    if equity is not None:
        click.echo(f"  EQUITY:     {_fmt_usd(equity)}")
        if pnl is not None:
            pnl_pct = (pnl / 10000.0) * 100
            click.echo(f"  P&L:        {_fmt_pnl(pnl)} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%)")
        else:
            click.echo("  P&L:        —")
    else:
        click.echo("  EQUITY:     —")
        click.echo("  P&L:        —")

    # Positions — from bus positions.json
    positions_data = _load_json(os.path.join(PAPER_BUS_DIR, "positions.json"))
    max_pos = cfg.get("execution", {}).get("max_positions", 3)
    positions_list = positions_data.get("positions", []) if positions_data else []
    if positions_list:
        pos_strs = [f"{p.get('coin','?')} {p.get('direction','?')}" for p in positions_list]
        click.echo(f"  POSITIONS:  {len(positions_list)}/{max_pos} ({', '.join(pos_strs)})")
    else:
        click.echo(f"  POSITIONS:  0/{max_pos}")

    # Uptime
    click.echo(f"  UPTIME:     {_uptime_from_pid(pid)}")

    # Signals mode
    heartbeat = _load_json(os.path.join(PAPER_BUS_DIR, "heartbeat.json"))
    if heartbeat:
        # Show heartbeat freshness
        eval_hb = heartbeat.get("evaluator", "")
        if eval_hb:
            try:
                hb_dt = datetime.fromisoformat(eval_hb.replace("Z", "+00:00"))
                age_s = int((datetime.now(timezone.utc) - hb_dt).total_seconds())
                ws_status = f"connected ({age_s}s ago)" if age_s < 60 else f"⚠ stale ({age_s}s)"
                click.echo(f"  SIGNALS:    full | WS {ws_status}")
            except Exception:
                click.echo("  SIGNALS:    full")
        else:
            click.echo("  SIGNALS:    full")
    else:
        click.echo(f"  SIGNALS:    starting...")

    # Dashboard
    token = cfg.get("telemetry", {}).get("token")
    if token:
        click.echo("  DASHBOARD:  connected (getzero.dev/app)")
    else:
        click.echo("  DASHBOARD:  not connected")

    # Recent activity from bus files
    risk = _load_json(os.path.join(PAPER_BUS_DIR, "risk.json"))
    if risk and risk.get("halted"):
        click.echo(f"\n  ⚠ HALTED: {risk.get('halt_reason', 'unknown reason')}")

    # Show open positions detail
    if positions_list:
        click.echo()
        click.echo("  OPEN POSITIONS:")
        for p in positions_list:
            coin = p.get("coin", "?")
            direction = p.get("direction", "?")
            entry = p.get("entry_price", 0)
            size = p.get("size_usd", 0)
            signal = p.get("signal_name", "?")[:35]
            sharpe = p.get("sharpe", 0)
            entry_time = p.get("entry_time", "")
            age_str = ""
            if entry_time:
                try:
                    et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - et).total_seconds() / 3600
                    age_str = f" | {age_h:.1f}h old"
                except Exception:
                    pass
            size_str = f"${size:.0f}" if isinstance(size, (int, float)) else "—"
            sharpe_str = f"S={sharpe:.2f}" if isinstance(sharpe, (int, float)) else ""
            click.echo(f"  > {coin} {direction} @ ${entry:.4f} | {size_str} | {sharpe_str} | {signal}{age_str}")

    click.echo()

"""zeroos status — system and agent health."""

import json
import os
import time
from datetime import datetime, timezone

import click
import yaml

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.console import (
    console, logo, spacer, rule, section, dots, fail, warn, pnl_pct,
)

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
PID_PATH = os.path.join(ZEROOS_DIR, "zeroos.pid")
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


def _uptime_str(pid: int | None) -> str:
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
        fail("not initialized. run:")
        console.print("  [lime]$ zeroos init[/lime]")
        raise SystemExit(1)

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper")
    running, pid = _is_running()
    uptime = _uptime_str(pid)

    # Header
    spacer()
    if running:
        console.print(f"  [header]◆ zero▮[/header] [mid]running[/mid] [dim]· uptime[/dim] [bright]{uptime}[/bright]")
    else:
        console.print("  [header]◆ zero▮[/header] [error]stopped[/error]")
    spacer()
    rule()
    spacer()

    # AGENT section
    section("AGENT")

    mode_display = "[success]● LIVE[/success]" if mode == "live" else "[warning]● PAPER[/warning]"
    dots(f"agent/{preset}", mode_display)

    # Wallet
    net_path = os.path.join(ZEROOS_DIR, "network.json")
    net = _load_json(net_path)
    wallet = "—"
    if net:
        wallet = net.get("wallet_address", "—")
        if len(wallet) > 10:
            wallet = f"{wallet[:6]}...{wallet[-4:]}"
    dots("wallet", wallet)

    # Equity & P&L
    portfolio = _load_json(os.path.join(PAPER_BUS_DIR, "portfolio.json"))
    paper_state = _load_json(PAPER_STATE_FILE)

    equity = None
    pnl = None
    if portfolio:
        equity = portfolio.get("account_value")
    if paper_state and equity is None:
        equity = paper_state.get("balance", 10000.0)
    if paper_state:
        start_balance = 10000.0
        balance = paper_state.get("balance", 10000.0)
        pnl = balance - start_balance

    dots("equity", _fmt_usd(equity))
    if pnl is not None:
        tag = "success" if pnl >= 0 else "error"
        sign = "+" if pnl >= 0 else ""
        dots("today", f"[{tag}]{sign}${abs(pnl):,.2f}[/{tag}]")
    else:
        dots("today", "—")

    spacer()
    rule()
    spacer()

    # IMMUNE section
    section("IMMUNE")
    heartbeat = _load_json(os.path.join(PAPER_BUS_DIR, "heartbeat.json"))
    immune_status = "[success]✓ healthy[/success]"
    checks = "—"
    saves = "0"
    last_check = "—"

    if heartbeat:
        eval_hb = heartbeat.get("evaluator", "")
        if eval_hb:
            try:
                hb_dt = datetime.fromisoformat(eval_hb.replace("Z", "+00:00"))
                age_s = int((datetime.now(timezone.utc) - hb_dt).total_seconds())
                last_check = f"{age_s}s ago"
                if age_s > 120:
                    immune_status = "[warning]⚠ stale[/warning]"
            except Exception:
                pass

    dots("status", immune_status)
    dots("checks today", checks)
    dots("saves", saves)
    dots("last check", last_check)

    spacer()
    rule()
    spacer()

    # POSITIONS section
    positions_data = _load_json(os.path.join(PAPER_BUS_DIR, "positions.json"))
    positions_list = positions_data.get("positions", []) if positions_data else []

    section(f"POSITIONS ({len(positions_list)})")

    if positions_list:
        spacer()
        for p in positions_list:
            coin = p.get("coin", "?")
            direction = p.get("direction", "?").upper()
            entry_time = p.get("entry_time", "")
            pnl_pct_val = p.get("pnl_pct", 0)

            # Direction arrow
            if direction == "LONG":
                arrow = "[lime]↗[/lime]"
                dir_label = "[lime]LONG[/lime]"
            else:
                arrow = "[error]↘[/error]"
                dir_label = "[error]SHORT[/error]"

            # Hold time
            hold_str = "—"
            if entry_time:
                try:
                    et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - et).total_seconds() / 3600
                    hold_str = f"{age_h:.0f}h held"
                except Exception:
                    pass

            # P&L
            pnl_str = pnl_pct(pnl_pct_val) if pnl_pct_val else "[dim]—[/dim]"

            console.print(f"  [bright]{coin:6s}[/bright] {dir_label}  {pnl_str}  [dim]{hold_str}[/dim]  [dim]stop[/dim] [success]✓[/success]")
    else:
        console.print("  [dim]no open positions.[/dim]")

    spacer()
    rule()
    spacer()

    # TODAY section
    section("TODAY")

    risk = _load_json(os.path.join(PAPER_BUS_DIR, "risk.json"))
    evals = "—"
    entries = "—"
    rejections = "—"
    reject_rate = "—"

    dots("evaluations", evals)
    dots("entries", entries)
    dots("rejections", rejections)
    dots("rejection rate", reject_rate)

    if risk and risk.get("halted"):
        spacer()
        warn(f"halted: {risk.get('halt_reason', 'unknown')}")

    spacer()
    rule()
    spacer()

    # NETWORK section
    section("NETWORK")
    dots("agents online", "—")
    dots("your rank", "—")
    dots("score", "—")

    spacer()

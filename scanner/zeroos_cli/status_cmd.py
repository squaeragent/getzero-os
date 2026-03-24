"""zeroos status — system and agent health."""

import json
import os
import time
from datetime import datetime, timezone

import click
import yaml

from scanner.zeroos_cli import __version__
from scanner.zeroos_cli.style import Z

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
        print(f'  {Z.fail("not initialized. run:")} {Z.lime("$ zeroos init")}')
        raise SystemExit(1)

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    preset = cfg.get("agent", {}).get("preset", "balanced")
    mode = cfg.get("agent", {}).get("mode", "paper")
    running, pid = _is_running()
    uptime = _uptime_str(pid)

    # Header
    print()
    if running:
        print(f'  {Z.logo()} {Z.mid("running")} {Z.dim("· uptime")} {Z.bright(uptime)}')
    else:
        print(f'  {Z.logo()} {Z.red("stopped")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # AGENT section
    print(f'  {Z.header("AGENT")}')

    mode_display = f'{Z.GREEN}● LIVE{Z.RESET}' if mode == "live" else f'{Z.YELLOW}● PAPER{Z.RESET}'
    print(f'  {Z.dots(f"agent/{preset}", mode_display)}')

    # Wallet
    net_path = os.path.join(ZEROOS_DIR, "network.json")
    net = _load_json(net_path)
    wallet = "—"
    if net:
        wallet = net.get("wallet_address", "—")
        if len(wallet) > 10:
            wallet = f"{wallet[:6]}...{wallet[-4:]}"
    print(f'  {Z.dots("wallet", wallet)}')

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

    print(f'  {Z.dots("equity", _fmt_usd(equity))}')
    if pnl is not None:
        pnl_color = Z.GREEN if pnl >= 0 else Z.RED
        sign = "+" if pnl >= 0 else ""
        print(f'  {Z.dots("today", f"{pnl_color}{sign}${abs(pnl):,.2f}{Z.RESET}")}')
    else:
        print(f'  {Z.dots("today", "—")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # IMMUNE section
    print(f'  {Z.header("IMMUNE")}')
    heartbeat = _load_json(os.path.join(PAPER_BUS_DIR, "heartbeat.json"))
    immune_status = f'{Z.GREEN}✓ healthy{Z.RESET}'
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
                    immune_status = f'{Z.YELLOW}⚠ stale{Z.RESET}'
            except Exception:
                pass

    print(f'  {Z.dots("status", immune_status)}')
    print(f'  {Z.dots("checks today", checks)}')
    print(f'  {Z.dots("saves", saves)}')
    print(f'  {Z.dots("last check", last_check)}')

    print()
    print(f'  {Z.rule()}')
    print()

    # POSITIONS section
    positions_data = _load_json(os.path.join(PAPER_BUS_DIR, "positions.json"))
    positions_list = positions_data.get("positions", []) if positions_data else []

    print(f'  {Z.header(f"POSITIONS ({len(positions_list)})")}')

    if positions_list:
        print()
        for p in positions_list:
            coin = p.get("coin", "?")
            direction = p.get("direction", "?").upper()
            entry_time = p.get("entry_time", "")
            pnl_pct = p.get("pnl_pct", 0)

            # Direction arrow
            arrow = f'{Z.LIME}↗{Z.RESET}' if direction == "LONG" else f'{Z.RED}↘{Z.RESET}'
            dir_label = f'{Z.LIME}LONG{Z.RESET}' if direction == "LONG" else f'{Z.RED}SHORT{Z.RESET}'

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
            pnl_str = Z.pnl_pct(pnl_pct) if pnl_pct else f'{Z.DIM}—{Z.RESET}'

            print(f'  {Z.bright(f"{coin:6s}")} {dir_label:>20s}  {pnl_str:>16s}  {Z.dim(hold_str):>16s}  {Z.dim("stop")} {Z.GREEN}✓{Z.RESET}')
    else:
        print(f'  {Z.dim("no open positions.")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # TODAY section
    print(f'  {Z.header("TODAY")}')

    risk = _load_json(os.path.join(PAPER_BUS_DIR, "risk.json"))
    evals = "—"
    entries = "—"
    rejections = "—"
    reject_rate = "—"

    print(f'  {Z.dots("evaluations", evals)}')
    print(f'  {Z.dots("entries", entries)}')
    print(f'  {Z.dots("rejections", rejections)}')
    print(f'  {Z.dots("rejection rate", reject_rate)}')

    if risk and risk.get("halted"):
        print()
        print(f'  {Z.warn(f"halted: {risk.get("halt_reason", "unknown")}")}')

    print()
    print(f'  {Z.rule()}')
    print()

    # NETWORK section
    print(f'  {Z.header("NETWORK")}')
    print(f'  {Z.dots("agents online", "—")}')
    print(f'  {Z.dots("your rank", "—")}')
    print(f'  {Z.dots("score", "—")}')

    print()

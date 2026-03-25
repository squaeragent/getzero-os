#!/usr/bin/env python3
"""
V6 Self-Audit — runs weekly, checks system health, posts findings to Telegram.
Scheduled via cron or called by immune system on Sundays.
"""

import json
import sys
import os
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.config import (
    BUS_DIR, POSITIONS_FILE, RISK_FILE, EQUITY_HISTORY_FILE,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN_ENV, get_env,
    get_dynamic_limits, FEE_RATE, CAPITAL,
)
from scanner.v6.bus_io import load_json, load_json_locked


def send_telegram(message: str):
    import urllib.request
    token = get_env(TELEGRAM_BOT_TOKEN_ENV)
    if not token:
        print(f"No telegram token — printing instead:\n{message}")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram failed: {e}")


def audit() -> list[str]:
    """Run all self-audit checks. Returns list of findings."""
    findings = []

    # 1. Check all processes are running
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        running = result.stdout
        for component in ["supervisor", "evaluator", "immune"]:
            if f"v6/{component}" not in running:
                findings.append(f"🔴 PROCESS DOWN: {component} is not running")
    except Exception:
        findings.append("🟡 Could not check process status")

    # 2. Check equity vs HL
    try:
        import urllib.request
        main = "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e"
        spot = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "spotClearinghouseState", "user": main}).encode(),
            headers={"Content-Type": "application/json"}
        ), timeout=10).read().decode())
        hl_usdc = 0
        for b in spot.get("balances", []):
            if b.get("coin") == "USDC":
                hl_usdc = float(b.get("total", 0))

        local_equity = load_json(BUS_DIR / "portfolio.json", {}).get("account_value", 0)
        if abs(hl_usdc - local_equity) > 5:
            findings.append(f"🔴 EQUITY MISMATCH: HL=${hl_usdc:.2f} vs local=${local_equity:.2f}")
    except Exception as e:
        findings.append(f"🟡 Equity check failed: {e}")

    # 3. Check for stale heartbeats
    heartbeats = load_json(BUS_DIR / "heartbeat.json", {})
    now = datetime.now(timezone.utc)
    for component in ["risk_guard", "executor", "evaluator"]:
        ts_str = heartbeats.get(component, "")
        if ts_str:
            try:
                last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = (now - last).total_seconds()
                if age > 300:
                    findings.append(f"🟡 STALE HEARTBEAT: {component} last seen {age:.0f}s ago")
            except (ValueError, TypeError):
                pass
        else:
            findings.append(f"🟡 NO HEARTBEAT: {component}")

    # 4. Check stop orders for all positions
    try:
        import urllib.request
        positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
        if positions:
            orders = json.loads(urllib.request.urlopen(urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "openOrders", "user": main}).encode(),
                headers={"Content-Type": "application/json"}
            ), timeout=10).read().decode())
            order_coins = set(o.get("coin") for o in orders)
            for pos in positions:
                coin = pos.get("coin")
                if coin and coin not in order_coins:
                    findings.append(f"🔴 NAKED POSITION: {coin} has no stop order")
    except Exception as e:
        findings.append(f"🟡 Stop check failed: {e}")

    # 5. Check equity history is being recorded
    try:
        if EQUITY_HISTORY_FILE.exists():
            lines = EQUITY_HISTORY_FILE.read_text().strip().split("\n")
            if lines:
                last_line = json.loads(lines[-1])
                last_ts = last_line.get("timestamp", "")
                if last_ts:
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    age = (now - last_dt).total_seconds()
                    if age > 300:
                        findings.append(f"🟡 EQUITY RECORDING STALE: last entry {age:.0f}s ago")
            n_entries = len(lines)
            if n_entries > 100000:
                findings.append(f"🟡 EQUITY HISTORY LARGE: {n_entries} entries — consider rotation")
    except Exception:
        pass

    # 6. Check analytics
    try:
        from scanner.v6.analytics import full_report
        report = full_report()
        sharpe = report.get("sharpe_all", 0)
        wr = report.get("win_rate", 0)
        total_pnl = report.get("total_pnl", 0)
        n_trades = report.get("total_trades", 0)

        if n_trades > 20 and wr < 35:
            findings.append(f"🟡 LOW WIN RATE: {wr}% over {n_trades} trades")
        if n_trades > 20 and total_pnl < 0:
            findings.append(f"🔴 NEGATIVE P&L: ${total_pnl:.2f} over {n_trades} trades")

        # Check worst signals
        signals = report.get("per_signal", {})
        for sig, data in signals.items():
            if data["count"] >= 3 and data["our_wr"] == 0:
                findings.append(f"🔴 DEAD SIGNAL: {sig[:40]} — 0% WR over {data['count']} trades")
    except Exception as e:
        findings.append(f"🟡 Analytics check failed: {e}")

    # 7. Check bare excepts in code
    try:
        result = subprocess.run(
            ["grep", "-rn", "except.*:", "--include=*.py"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent)
        )
        bare = [l for l in result.stdout.splitlines() if " as " not in l and "#" not in l and "__pycache__" not in l]
        if bare:
            findings.append(f"🟡 CODE: {len(bare)} bare except clauses in V6")
    except Exception:
        pass

    if not findings:
        findings.append("✅ All checks passed — no issues found")

    return findings


def main():
    findings = audit()
    
    # Build message
    n_red = sum(1 for f in findings if "🔴" in f)
    n_yellow = sum(1 for f in findings if "🟡" in f)
    n_green = sum(1 for f in findings if "✅" in f)

    header = f"🔍 <b>WEEKLY SELF-AUDIT</b> — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
    header += f"Score: {n_green}✅ {n_yellow}🟡 {n_red}🔴\n\n"

    body = "\n".join(findings)
    message = header + body

    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()

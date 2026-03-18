#!/usr/bin/env python3
"""
ZERO OS — Agent 5: Risk Agent
Portfolio-level risk management, kill switch, drawdown detection,
strategy health monitoring.

Monitors:
  - Live positions, closed trades, portfolio state
  - Hyperliquid account state (real-time equity, margin, unrealized PnL)
  - Rolling win rate, streak detection, drawdown from peak

Outputs:
  scanner/bus/risk.json            — current risk state
  scanner/bus/equity_history.jsonl — equity curve log
  scanner/bus/heartbeat.json       — last-alive timestamp

Usage:
  python3 scanner/agents/risk_agent.py           # single run
  python3 scanner/agents/risk_agent.py --loop    # continuous 2-min cycle
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"

RISK_FILE = BUS_DIR / "risk.json"
EQUITY_HISTORY_FILE = BUS_DIR / "equity_history.jsonl"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

POSITIONS_FILE = LIVE_DIR / "positions.json"
PORTFOLIO_FILE = LIVE_DIR / "portfolio.json"
# closed trades: check both possible locations
CLOSED_FILE_LIVE = LIVE_DIR / "closed.jsonl"
CLOSED_FILE_DATA = DATA_DIR / "closed.jsonl"

# ─── CONFIG ───
HL_API_URL = "https://api.hyperliquid.xyz/info"
CYCLE_SECONDS = 120  # 2 minutes
ROLLING_WINDOW = 20  # trades for strategy health
HEARTBEAT_STALE_MINUTES = 10

# ─── RISK THRESHOLDS ───
DRAWDOWN_YELLOW = 3.0
DRAWDOWN_ORANGE = 7.0
DRAWDOWN_RED = 12.0
DAILY_LOSS_RED = 15.0  # dollars
LOSE_STREAK_YELLOW = 3
LOSE_STREAK_ORANGE = 5
WIN_RATE_FLOOR = 50.0
WIN_LOSS_RATIO_FLOOR = 1.0


# ─── ENV ───
def load_main_address():
    """Load HYPERLIQUID_MAIN_ADDRESS from ~/.config/openclaw/.env"""
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("HYPERLIQUID_MAIN_ADDRESS="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    raise RuntimeError("HYPERLIQUID_MAIN_ADDRESS not found in ~/.config/openclaw/.env")


# ─── HYPERLIQUID API ───
def fetch_hl_account(main_address):
    """Query Hyperliquid clearinghouse state for account equity and positions."""
    payload = json.dumps({"type": "clearinghouseState", "user": main_address}).encode()
    req = urllib.request.Request(
        HL_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        print(f"  [warn] HL API error: {e}")
        return None


STARTING_EQUITY = 115.0  # USDC deposited to HL (spot as cross-margin collateral)

def parse_hl_state(hl_data):
    """Extract account value, unrealized PnL, and margin from HL response."""
    if not hl_data:
        return None
    try:
        margin_summary = hl_data.get("marginSummary", {})
        margin_account_value = float(margin_summary.get("accountValue", 0))
        total_margin = float(margin_summary.get("totalMarginUsed", 0))
        total_ntl = float(margin_summary.get("totalNtlPos", 0))

        unrealized_pnl = 0.0
        positions = hl_data.get("assetPositions", [])
        for pos in positions:
            p = pos.get("position", {})
            unrealized_pnl += float(p.get("unrealizedPnl", 0))

        # True equity = starting capital + unrealized P&L
        # HL margin_account_value only shows margin portion, not full capital
        account_value = STARTING_EQUITY + unrealized_pnl

        return {
            "account_value": account_value,
            "margin_account_value": margin_account_value,
            "total_margin": total_margin,
            "total_ntl_pos": total_ntl,
            "unrealized_pnl": unrealized_pnl,
            "n_positions": len([p for p in positions if float(p.get("position", {}).get("szi", 0)) != 0]),
        }
    except (KeyError, ValueError, TypeError) as e:
        print(f"  [warn] Failed to parse HL state: {e}")
        return None


# ─── LOCAL DATA ───
def load_json(path):
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def load_jsonl(path, max_lines=200):
    """Load last N lines from a jsonl file."""
    if not path.exists():
        return []
    lines = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return lines[-max_lines:]


def load_positions():
    data = load_json(POSITIONS_FILE)
    if isinstance(data, list):
        return data
    return []


def load_portfolio():
    return load_json(PORTFOLIO_FILE) or {}


def load_closed_trades():
    """Load closed trades, checking both possible file locations."""
    trades = load_jsonl(CLOSED_FILE_LIVE)
    if not trades:
        trades = load_jsonl(CLOSED_FILE_DATA)
    return trades


# ─── EQUITY TRACKING ───
def load_equity_history():
    return load_jsonl(EQUITY_HISTORY_FILE, max_lines=500)


def append_equity(entry):
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EQUITY_HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def compute_peak_equity(equity_history, current_value):
    """Find peak account value from equity history."""
    peak = current_value
    for e in equity_history:
        val = e.get("account_value", 0)
        if val > peak:
            peak = val
    return peak


def compute_drawdown(peak, current):
    if peak <= 0:
        return 0.0
    return (peak - current) / peak * 100


# ─── STREAK & WIN RATE ───
def compute_streaks(closed_trades):
    """Compute current win/lose streak from recent trades."""
    if not closed_trades:
        return 0, 0
    win_streak = 0
    lose_streak = 0
    # Walk backwards from most recent
    for t in reversed(closed_trades):
        pnl = t.get("pnl_dollars", 0)
        if pnl > 0:
            if lose_streak > 0:
                break
            win_streak += 1
        elif pnl < 0:
            if win_streak > 0:
                break
            lose_streak += 1
        # pnl == 0: skip, doesn't break streak
    return win_streak, lose_streak


def compute_rolling_stats(closed_trades, window=ROLLING_WINDOW):
    """Rolling win rate, avg win/loss ratio, and simple Sharpe over last N trades."""
    recent = closed_trades[-window:] if len(closed_trades) >= window else closed_trades
    if not recent:
        return {"win_rate": None, "win_loss_ratio": None, "sharpe": None, "n_trades": 0}

    wins = [t for t in recent if t.get("pnl_dollars", 0) > 0]
    losses = [t for t in recent if t.get("pnl_dollars", 0) < 0]
    n = len(recent)

    win_rate = (len(wins) / n * 100) if n > 0 else 0

    avg_win = sum(t["pnl_dollars"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl_dollars"] for t in losses) / len(losses)) if losses else 0.001
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Simple Sharpe: mean / stdev of pnl
    pnls = [t.get("pnl_dollars", 0) for t in recent]
    mean_pnl = sum(pnls) / len(pnls) if pnls else 0
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        stdev = variance ** 0.5
        sharpe = mean_pnl / stdev if stdev > 0 else 0
    else:
        sharpe = 0

    return {
        "win_rate": round(win_rate, 1),
        "win_loss_ratio": round(win_loss_ratio, 2),
        "sharpe": round(sharpe, 3),
        "n_trades": n,
    }


def compute_daily_pnl(closed_trades):
    """Sum realized PnL for today (UTC)."""
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    for t in reversed(closed_trades):
        exit_time = t.get("exit_time", "")
        if exit_time[:10] == today:
            total += t.get("pnl_dollars", 0)
        elif exit_time[:10] < today:
            break  # trades are chronological, stop early
    return total


# ─── RISK CLASSIFICATION ───
def classify_risk(drawdown_pct, daily_pnl, lose_streak, rolling_stats):
    """
    Determine risk level and throttle.
    Returns (level, throttle, kill_all, alerts).
    """
    alerts = []
    level = "green"
    throttle = 1.0
    kill_all = False

    # Check from most severe to least
    # RED: kill switch
    if drawdown_pct > DRAWDOWN_RED:
        level = "red"
        throttle = 0.0
        kill_all = True
        alerts.append(f"KILL: drawdown {drawdown_pct:.1f}% exceeds {DRAWDOWN_RED}%")
    elif daily_pnl < -DAILY_LOSS_RED:
        level = "red"
        throttle = 0.0
        kill_all = True
        alerts.append(f"KILL: daily loss ${abs(daily_pnl):.2f} exceeds ${DAILY_LOSS_RED}")
    elif lose_streak >= LOSE_STREAK_ORANGE and drawdown_pct > DRAWDOWN_YELLOW:
        level = "red"
        throttle = 0.0
        kill_all = True
        alerts.append(f"KILL: {lose_streak} consecutive losses + {drawdown_pct:.1f}% drawdown")

    # ORANGE: close-only
    elif drawdown_pct > DRAWDOWN_ORANGE:
        level = "orange"
        throttle = 0.0
        alerts.append(f"CLOSE-ONLY: drawdown {drawdown_pct:.1f}% exceeds {DRAWDOWN_ORANGE}%")
    elif lose_streak >= LOSE_STREAK_ORANGE:
        level = "orange"
        throttle = 0.0
        alerts.append(f"CLOSE-ONLY: {lose_streak} consecutive losses")

    # YELLOW: reduce size
    elif drawdown_pct > DRAWDOWN_YELLOW:
        level = "yellow"
        throttle = 0.5
        alerts.append(f"CAUTION: drawdown {drawdown_pct:.1f}%")
    elif lose_streak >= LOSE_STREAK_YELLOW:
        level = "yellow"
        throttle = 0.5
        alerts.append(f"CAUTION: {lose_streak} consecutive losses")

    # Strategy health degradation
    if rolling_stats.get("n_trades", 0) >= 10:
        wr = rolling_stats.get("win_rate")
        wlr = rolling_stats.get("win_loss_ratio")
        if wr is not None and wr < WIN_RATE_FLOOR:
            if level in ("green", "yellow"):
                level = "yellow"
                throttle = min(throttle, 0.5)
            alerts.append(f"Win rate {wr:.1f}% below {WIN_RATE_FLOOR}%")
        if wlr is not None and wlr < WIN_LOSS_RATIO_FLOOR:
            if level in ("green", "yellow"):
                level = "yellow"
                throttle = min(throttle, 0.5)
            alerts.append(f"Win/loss ratio {wlr:.2f} below {WIN_LOSS_RATIO_FLOOR}")

    return level, throttle, kill_all, alerts


# ─── HEARTBEAT ───
def check_agent_heartbeats():
    """Check if any agent heartbeat is stale (>10 min)."""
    stale = []
    if not HEARTBEAT_FILE.exists():
        return stale
    heartbeat = load_json(HEARTBEAT_FILE) or {}
    now = datetime.now(timezone.utc)
    for agent, ts_str in heartbeat.items():
        if agent == "risk":
            continue  # don't check ourselves
        try:
            ts = datetime.fromisoformat(ts_str)
            age_min = (now - ts).total_seconds() / 60
            if age_min > HEARTBEAT_STALE_MINUTES:
                stale.append(f"{agent} last seen {age_min:.0f}m ago")
        except (ValueError, TypeError):
            stale.append(f"{agent} has invalid timestamp")
    return stale


def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = load_json(HEARTBEAT_FILE) or {}
    heartbeat["risk"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── OUTPUT ───
def write_risk(state):
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RISK_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── MAIN CYCLE ───
def run_cycle(main_address):
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Risk Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # 1. Fetch Hyperliquid account state
    print("  Querying Hyperliquid account state...")
    hl_raw = fetch_hl_account(main_address)
    hl = parse_hl_state(hl_raw)

    # 2. Load local data
    positions = load_positions()
    portfolio = load_portfolio()
    closed_trades = load_closed_trades()

    # 3. Determine account value — prefer HL (real-time), fallback to local
    if hl:
        account_value = hl["account_value"]
        unrealized_pnl = hl["unrealized_pnl"]
        print(f"  HL account: ${account_value:.2f} (unrealized: ${unrealized_pnl:+.2f})")
    else:
        account_value = portfolio.get("capital", 0)
        unrealized_pnl = 0.0
        print(f"  HL unavailable, using local capital: ${account_value:.2f}")

    # 4. Equity tracking
    # When no positions are open, HL returns account_value=0 because capital
    # sits on spot (not cross-margin). This is NOT a drawdown — skip equity tracking.
    equity_history = load_equity_history()
    if account_value <= 0 and len(positions) == 0:
        # Idle state — use last known peak, drawdown is 0
        peak_equity = compute_peak_equity(equity_history, 0)
        drawdown_pct = 0.0
        print("  Idle (no positions, capital on spot) — drawdown N/A")
    else:
        peak_equity = compute_peak_equity(equity_history, account_value)
        drawdown_pct = compute_drawdown(peak_equity, account_value)

    # Append to equity curve (only when positions are open — idle $0 pollutes peak tracking)
    if len(positions) > 0 or account_value > 0:
        equity_entry = {
            "timestamp": ts_iso,
            "account_value": round(account_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "n_positions": len(positions),
        }
        append_equity(equity_entry)

    # 5. Trade statistics
    win_streak, lose_streak = compute_streaks(closed_trades)
    rolling = compute_rolling_stats(closed_trades)
    daily_pnl = compute_daily_pnl(closed_trades)

    print(f"  Peak equity: ${peak_equity:.2f} | Drawdown: {drawdown_pct:.1f}%")
    print(f"  Streaks: W{win_streak} / L{lose_streak} | Daily PnL: ${daily_pnl:+.2f}")
    print(f"  Rolling({rolling['n_trades']}): WR={rolling['win_rate']}% WL={rolling['win_loss_ratio']} Sharpe={rolling['sharpe']}")

    # 6. Risk classification
    level, throttle, kill_all, alerts = classify_risk(
        drawdown_pct, daily_pnl, lose_streak, rolling
    )

    # 7. Check heartbeats for stale agents
    stale_agents = check_agent_heartbeats()
    if stale_agents:
        for s in stale_agents:
            alerts.append(f"STALE: {s}")

    # 8. Compute max drawdown from history
    max_dd = drawdown_pct
    if equity_history:
        running_peak = 0
        for e in equity_history:
            v = e.get("account_value", 0)
            if v > running_peak:
                running_peak = v
            dd = compute_drawdown(running_peak, v)
            if dd > max_dd:
                max_dd = dd

    # 9. Build output
    risk_state = {
        "timestamp": ts_iso,
        "status": level,
        "account_value": round(account_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "realized_pnl_today": round(daily_pnl, 2),
        "drawdown_pct": round(drawdown_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "peak_equity": round(peak_equity, 2),
        "win_streak": win_streak,
        "lose_streak": lose_streak,
        "rolling_stats": rolling,
        "strategy_health": level,
        "throttle": throttle,
        "kill_all": kill_all,
        "blocked_coins": [],
        "open_positions": len(positions),
        "alerts": alerts,
    }

    write_risk(risk_state)
    write_heartbeat()

    # 10. Print summary
    status_icon = {"green": "OK", "yellow": "WARN", "orange": "DANGER", "red": "KILL"}
    print(f"\n  Status: [{status_icon.get(level, '?')}] {level.upper()} | Throttle: {throttle}")
    if kill_all:
        print(f"  *** KILL SWITCH ACTIVE — all positions should be closed ***")
    if alerts:
        for a in alerts:
            print(f"  >> {a}")
    print(f"  Written to {RISK_FILE}")
    print(f"{'='*60}\n")

    return risk_state


def main():
    main_address = load_main_address()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Risk Agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle(main_address)
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(main_address)


if __name__ == "__main__":
    main()

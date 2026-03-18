#!/usr/bin/env python3
"""
ZERO OS — Portfolio Exporter
Reads from the 5-agent bus + live data, exports portfolio.json for the website.
Pushes to git → Vercel auto-deploys.

Run after each execution cycle or on its own schedule.
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent
REPO_DIR = SCANNER_DIR.parent
DATA_DIR = SCANNER_DIR / "data"
BUS_DIR = SCANNER_DIR / "bus"
LIVE_DIR = DATA_DIR / "live"
OUTPUT = REPO_DIR / "public" / "api" / "portfolio.json"


def load_json(path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return default
    return default


def load_jsonl(path, limit=None):
    records = []
    if path.exists():
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
    if limit:
        return records[-limit:]
    return records


def export():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # ─── PAPER TRADING ───
    paper_positions = load_json(DATA_DIR / "positions.json", [])
    paper_portfolio = load_json(DATA_DIR / "portfolio.json", {
        "capital": 10000, "trades": 0, "wins": 0,
        "started": now_iso
    })
    paper_closed = load_jsonl(DATA_DIR / "closed.jsonl", limit=100)
    paper_fires = load_jsonl(DATA_DIR / "fires.jsonl", limit=30)

    total_in_positions = sum(p.get("size", 0) for p in paper_positions)
    total_value = paper_portfolio.get("capital", 10000) + total_in_positions
    started = paper_portfolio.get("started", now_iso)
    try:
        days = max(1, int((time.time() - datetime.fromisoformat(started).timestamp()) / 86400) + 1)
    except (ValueError, TypeError):
        days = 1
    trades = paper_portfolio.get("trades", 0)
    wins = paper_portfolio.get("wins", 0)

    # ─── LIVE TRADING ───
    live_positions = load_json(LIVE_DIR / "positions.json", [])
    live_portfolio = load_json(LIVE_DIR / "portfolio.json", {})
    live_closed = load_jsonl(LIVE_DIR / "closed.jsonl", limit=50)

    # ─── AGENT BUS STATE ───
    regimes = load_json(BUS_DIR / "regimes.json", {})
    risk = load_json(BUS_DIR / "risk.json", {})
    heartbeat = load_json(BUS_DIR / "heartbeat.json", {})
    candidates = load_json(BUS_DIR / "candidates.json", {})
    approved = load_json(BUS_DIR / "approved.json", {})
    equity_history = load_jsonl(BUS_DIR / "equity_history.jsonl", limit=500)

    # ─── REGIME MAP (coin → regime) ───
    regime_map = {}
    for coin, data in regimes.get("coins", {}).items():
        regime_map[coin] = {
            "regime": data.get("regime", "unknown"),
            "confidence": data.get("confidence", 0),
            "transition": data.get("transition", False),
        }

    # ─── REGIME SUMMARY ───
    regime_counts = {}
    for coin, data in regimes.get("coins", {}).items():
        r = data.get("regime", "unknown")
        regime_counts[r] = regime_counts.get(r, 0) + 1

    # ─── CLOSED TRADE STATS ───
    def compute_stats(closed_list):
        if not closed_list:
            return {}
        total = len(closed_list)
        wins_count = sum(1 for c in closed_list if c.get("pnl_dollars", c.get("pnl_usd", 0)) > 0)
        losses = total - wins_count
        pnl_values = [c.get("pnl_dollars", c.get("pnl_usd", 0)) for c in closed_list]
        hold_times = [c.get("hours_held", 0) for c in closed_list if c.get("hours_held")]
        by_coin = {}
        for c in closed_list:
            coin = c.get("coin", "?")
            if coin not in by_coin:
                by_coin[coin] = {"trades": 0, "wins": 0, "pnl": 0}
            by_coin[coin]["trades"] += 1
            pnl = c.get("pnl_dollars", c.get("pnl_usd", 0))
            by_coin[coin]["pnl"] += pnl
            if pnl > 0:
                by_coin[coin]["wins"] += 1

        best = max(closed_list, key=lambda c: c.get("pnl_dollars", c.get("pnl_usd", 0)))
        worst = min(closed_list, key=lambda c: c.get("pnl_dollars", c.get("pnl_usd", 0)))

        long_trades = [c for c in closed_list if c.get("direction") == "LONG"]
        short_trades = [c for c in closed_list if c.get("direction") == "SHORT"]

        return {
            "total": total,
            "wins": wins_count,
            "losses": losses,
            "winRate": round(wins_count / total * 100, 1) if total > 0 else 0,
            "totalPnl": round(sum(pnl_values), 2),
            "avgPnl": round(sum(pnl_values) / total, 2) if total > 0 else 0,
            "bestTrade": {
                "coin": best.get("coin"), "direction": best.get("direction"),
                "pnl": round(best.get("pnl_dollars", best.get("pnl_usd", 0)), 2),
                "pnlPct": best.get("pnl_pct", 0),
            },
            "worstTrade": {
                "coin": worst.get("coin"), "direction": worst.get("direction"),
                "pnl": round(worst.get("pnl_dollars", worst.get("pnl_usd", 0)), 2),
                "pnlPct": worst.get("pnl_pct", 0),
            },
            "avgHoldHours": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0,
            "byCoin": {
                coin: {
                    "trades": d["trades"],
                    "winRate": round(d["wins"] / d["trades"] * 100, 0) if d["trades"] > 0 else 0,
                    "pnl": round(d["pnl"], 2),
                }
                for coin, d in sorted(by_coin.items(), key=lambda x: x[1]["pnl"], reverse=True)
            },
            "longWinRate": round(sum(1 for c in long_trades if c.get("pnl_dollars", c.get("pnl_usd", 0)) > 0) / len(long_trades) * 100, 1) if long_trades else 0,
            "shortWinRate": round(sum(1 for c in short_trades if c.get("pnl_dollars", c.get("pnl_usd", 0)) > 0) / len(short_trades) * 100, 1) if short_trades else 0,
            "longCount": len(long_trades),
            "shortCount": len(short_trades),
        }

    paper_stats = compute_stats(paper_closed)
    live_stats = compute_stats(live_closed)

    # ─── PAPER EQUITY CURVE (from closed trades) ───
    paper_equity_curve = []
    running_value = 10000.0
    for trade in paper_closed:
        pnl = trade.get("pnl_dollars", trade.get("pnl_usd", 0))
        running_value += pnl
        t = trade.get("exit_time", trade.get("closed_at", ""))
        paper_equity_curve.append({"t": t, "v": round(running_value, 2)})

    # ─── LIVE EQUITY CURVE (from risk agent) ───
    live_equity_curve = [
        {"t": e.get("timestamp", ""), "v": e.get("account_value", 0)}
        for e in equity_history
    ]

    # ─── BUILD SNAPSHOT ───
    snapshot = {
        "live": True,
        "updated": now_iso,
        "summary": {
            "startingCapital": 10000,
            "currentValue": round(total_value, 2),
            "cash": round(paper_portfolio.get("capital", 10000), 2),
            "pnl": round(total_value - 10000, 2),
            "pnlPct": round((total_value - 10000) / 100, 2),
            "totalTrades": trades,
            "winRate": round(wins / trades * 100, 1) if trades > 0 else None,
            "openPositions": len(paper_positions),
            "daysRunning": days,
            "started": started,
        },
        "liveTrading": {
            "enabled": True,
            "capital": live_portfolio.get("capital", 115),
            "positions": live_positions,
            "trades": live_portfolio.get("trades", 0),
            "wins": live_portfolio.get("wins", 0),
            "dailyLoss": live_portfolio.get("daily_loss", 0),
            "closed": live_closed[-20:],
            "started": live_portfolio.get("started", now_iso),
            "stats": live_stats,
        },
        "agents": {
            "heartbeat": heartbeat,
            "risk": {
                "status": risk.get("status", "unknown"),
                "throttle": risk.get("throttle", 1.0),
                "killAll": risk.get("kill_all", False),
                "drawdownPct": risk.get("drawdown_pct", 0),
                "peakEquity": risk.get("peak_equity", 0),
                "winStreak": risk.get("win_streak", 0),
                "loseStreak": risk.get("lose_streak", 0),
                "rollingStats": risk.get("rolling_stats", {}),
                "alerts": risk.get("alerts", []),
            },
            "regimes": {
                "summary": regime_counts,
                "coins": regime_map,
                "updated": regimes.get("timestamp", ""),
            },
            "candidates": len(candidates.get("candidates", [])),
            "approved": len(approved.get("approved", [])),
            "blocked": len(approved.get("blocked", [])),
        },
        "equityCurve": paper_equity_curve,
        "liveEquityCurve": live_equity_curve,
        "positions": paper_positions,
        "closed": paper_closed[-50:],
        "recentFires": paper_fires[-20:],
        "stats": paper_stats,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(f"[{now.strftime('%H:%M:%S UTC')}] Exported portfolio.json ({len(json.dumps(snapshot))} bytes)")


def push():
    try:
        subprocess.run(["git", "add", "public/api/portfolio.json"],
                       cwd=REPO_DIR, capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "commit", "-m", "portfolio: update snapshot"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            subprocess.run(["git", "push", "origin", "main"],
                           cwd=REPO_DIR, capture_output=True, timeout=30)
            print("  Pushed to repo → Vercel auto-deploy")
        else:
            print("  No changes to push")
    except Exception as e:
        print(f"  Git push failed: {e}")


if __name__ == "__main__":
    export()
    push()

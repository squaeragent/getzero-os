#!/usr/bin/env python3
"""
V6 Analytics — computes real performance metrics from trade history.
No ENVY numbers. Only our actual outcomes.
"""

import json
import math
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

V5_TRADES = Path("scanner/data/live/closed.jsonl")
V6_TRADES = Path("scanner/v6/data/trades.jsonl")


def load_all_trades() -> list[dict]:
    """Load all closed trades from V5 and V6."""
    trades = []
    for path in [V5_TRADES, V6_TRADES]:
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trades.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
    return trades


def get_pnl(trade: dict) -> float:
    """Extract P&L from trade (handles both V5 and V6 field names)."""
    return trade.get("pnl_usd") or trade.get("pnl_dollars") or 0.0


def get_pnl_pct(trade: dict) -> float:
    """Extract P&L % from trade."""
    return trade.get("pnl_pct") or 0.0


def compute_sharpe(trades: list[dict], annualize: bool = True) -> float:
    """
    Compute Sharpe ratio from actual trade returns.
    Uses per-trade P&L percentage, not dollar amounts (scale-independent).
    Annualized assuming 365 trading days.
    """
    returns = [get_pnl_pct(t) for t in trades]
    if len(returns) < 2:
        return 0.0

    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0001

    if std_r == 0:
        return 0.0

    sharpe = mean_r / std_r

    if annualize:
        # Estimate trades per year from actual trading frequency
        if len(trades) >= 2:
            first_exit = trades[0].get("exit_time", "")
            last_exit = trades[-1].get("exit_time", "")
            try:
                dt_first = datetime.fromisoformat(first_exit.replace("Z", "+00:00"))
                dt_last = datetime.fromisoformat(last_exit.replace("Z", "+00:00"))
                days_span = max((dt_last - dt_first).total_seconds() / 86400, 1)
                trades_per_year = len(trades) / days_span * 365
            except (ValueError, TypeError):
                trades_per_year = 365  # fallback: 1 trade/day
        else:
            trades_per_year = 365

        sharpe *= math.sqrt(trades_per_year)

    return round(sharpe, 3)


def compute_max_drawdown(trades: list[dict]) -> tuple[float, float]:
    """Compute max drawdown from sequential P&L. Returns (max_dd_usd, max_dd_pct)."""
    if not trades:
        return 0.0, 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for t in sorted(trades, key=lambda x: x.get("exit_time", "")):
        cumulative += get_pnl(t)
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    max_dd_pct = max_dd / max(peak, 1) * 100 if peak > 0 else 0
    return round(max_dd, 2), round(max_dd_pct, 2)


def per_signal_stats(trades: list[dict]) -> dict:
    """Per-signal performance: WR, P&L, count, Sharpe, expected vs actual."""
    stats = defaultdict(lambda: {
        "count": 0, "wins": 0, "losses": 0,
        "pnl_total": 0.0, "returns": [],
        "envy_sharpe": 0.0, "envy_wr": 0.0,
        "avg_hold_hours": 0.0, "hold_hours_list": [],
    })

    for t in trades:
        sig = t.get("signal_name") or t.get("signal") or "unknown"
        pnl = get_pnl(t)
        pnl_pct = get_pnl_pct(t)
        s = stats[sig]
        s["count"] += 1
        s["pnl_total"] += pnl
        s["returns"].append(pnl_pct)
        if pnl > 0:
            s["wins"] += 1
        elif pnl < 0:
            s["losses"] += 1

        # Track ENVY's claimed stats for comparison
        envy_sharpe = t.get("sharpe") or 0
        envy_wr = t.get("win_rate") or 0
        if envy_sharpe:
            s["envy_sharpe"] = envy_sharpe
        if envy_wr:
            s["envy_wr"] = envy_wr

        # Hold time
        entry_t = t.get("entry_time", "")
        exit_t = t.get("exit_time", "")
        if entry_t and exit_t:
            try:
                dt_entry = datetime.fromisoformat(entry_t.replace("Z", "+00:00"))
                dt_exit = datetime.fromisoformat(exit_t.replace("Z", "+00:00"))
                hours = (dt_exit - dt_entry).total_seconds() / 3600
                s["hold_hours_list"].append(hours)
            except (ValueError, TypeError):
                pass

    # Compute derived stats
    result = {}
    for sig, s in stats.items():
        our_wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
        our_sharpe = 0.0
        if len(s["returns"]) >= 2:
            mean_r = sum(s["returns"]) / len(s["returns"])
            var_r = sum((r - mean_r) ** 2 for r in s["returns"]) / (len(s["returns"]) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.0001
            our_sharpe = round(mean_r / std_r, 3) if std_r > 0 else 0

        avg_hold = sum(s["hold_hours_list"]) / len(s["hold_hours_list"]) if s["hold_hours_list"] else 0

        result[sig] = {
            "count":       s["count"],
            "wins":        s["wins"],
            "losses":      s["losses"],
            "our_wr":      round(our_wr, 1),
            "our_sharpe":  our_sharpe,
            "pnl_total":   round(s["pnl_total"], 4),
            "avg_pnl":     round(s["pnl_total"] / s["count"], 4) if s["count"] > 0 else 0,
            "envy_sharpe": s["envy_sharpe"],
            "envy_wr":     round(s["envy_wr"], 1),
            "wr_drift":    round(our_wr - s["envy_wr"], 1) if s["envy_wr"] > 0 else None,
            "avg_hold_h":  round(avg_hold, 1),
        }

    return result


def rolling_sharpe(trades: list[dict], window: int = 30) -> list[dict]:
    """Compute rolling Sharpe over last N trades."""
    sorted_trades = sorted(trades, key=lambda x: x.get("exit_time", ""))
    results = []
    for i in range(window, len(sorted_trades) + 1):
        chunk = sorted_trades[i - window:i]
        sharpe = compute_sharpe(chunk, annualize=False)
        last_trade = chunk[-1]
        results.append({
            "trade_index": i,
            "exit_time": last_trade.get("exit_time", ""),
            "sharpe_30t": sharpe,
        })
    return results


def full_report() -> dict:
    """Generate complete analytics report."""
    trades = load_all_trades()
    if not trades:
        return {"error": "no trades"}

    sorted_trades = sorted(trades, key=lambda x: x.get("exit_time", ""))
    wins = sum(1 for t in trades if get_pnl(t) > 0)
    losses = sum(1 for t in trades if get_pnl(t) < 0)
    breakeven = sum(1 for t in trades if get_pnl(t) == 0)
    total_pnl = sum(get_pnl(t) for t in trades)
    max_dd_usd, max_dd_pct = compute_max_drawdown(sorted_trades)

    # V6 only stats
    v6_trades = [t for t in trades if t.get("strategy_version") == 6]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(trades),
        "v6_trades": len(v6_trades),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 4) if trades else 0,
        "sharpe_all": compute_sharpe(sorted_trades),
        "sharpe_30t": compute_sharpe(sorted_trades[-30:]) if len(sorted_trades) >= 30 else None,
        "sharpe_v6": compute_sharpe(v6_trades) if len(v6_trades) >= 2 else None,
        "max_drawdown_usd": max_dd_usd,
        "max_drawdown_pct": max_dd_pct,
        "per_signal": per_signal_stats(sorted_trades),
    }


if __name__ == "__main__":
    report = full_report()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        print(f"=== ZERO OS Analytics Report ===")
        print(f"Trades: {report['total_trades']} (V6: {report['v6_trades']})")
        print(f"W/L/B: {report['wins']}/{report['losses']}/{report['breakeven']}")
        print(f"Win Rate: {report['win_rate']}%")
        print(f"Total P&L: ${report['total_pnl']}")
        print(f"Avg P&L: ${report['avg_pnl']}")
        print(f"Sharpe (all): {report['sharpe_all']}")
        print(f"Sharpe (30t): {report['sharpe_30t']}")
        print(f"Sharpe (V6): {report['sharpe_v6']}")
        print(f"Max Drawdown: ${report['max_drawdown_usd']} ({report['max_drawdown_pct']}%)")
        print()
        print("=== Per-Signal Performance (2+ trades) ===")
        signals = report["per_signal"]
        for sig, data in sorted(signals.items(), key=lambda x: x[1]["count"], reverse=True):
            if data["count"] < 2:
                continue
            drift = f" drift={data['wr_drift']:+.0f}pp" if data["wr_drift"] is not None else ""
            print(f"  {sig[:55]}")
            print(f"    {data['count']}t {data['our_wr']}%WR ${data['pnl_total']:+.2f}"
                  f" | ENVY: {data['envy_wr']}%WR Sharpe={data['envy_sharpe']}{drift}")

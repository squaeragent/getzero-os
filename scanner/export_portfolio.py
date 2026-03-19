#!/usr/bin/env python3
"""
ZERO OS — Portfolio Exporter
Reads from the 10-agent bus + live data, exports portfolio.json for the website.
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


def compute_spread(spread_data):
    """Build spread export: summary + per-coin status."""
    if not spread_data:
        return {}
    coins = {}
    raw_coins = spread_data.get("coins", spread_data.get("data", {}))
    for coin, data in raw_coins.items():
        if isinstance(data, dict):
            coins[coin] = {
                "status": data.get("status", "normal"),
                "spread_bps": data.get("spread_bps", data.get("spread", None)),
                "anomaly": data.get("anomaly", False),
            }
        else:
            coins[coin] = {"status": "normal"}
    summary = spread_data.get("summary", {})
    if not summary:
        # build summary from coins
        normal_count = sum(1 for c in coins.values() if c.get("status") == "normal")
        anomaly_count = len(coins) - normal_count
        summary = {"normal": normal_count, "anomalies": anomaly_count}
    return {"summary": summary, "coins": coins}


def compute_funding(funding_data):
    """Build funding export: per-coin velocity_direction + funding_pct."""
    if not funding_data:
        return {}
    coins = {}
    raw_coins = funding_data.get("coins", funding_data.get("data", {}))
    for coin, data in raw_coins.items():
        if isinstance(data, dict):
            coins[coin] = {
                "velocity_direction": data.get("velocity_direction", data.get("direction", "stable")),
                "funding_pct": data.get("funding_pct", data.get("rate", data.get("funding_rate", 0))),
            }
        else:
            coins[coin] = {"velocity_direction": "stable", "funding_pct": 0}
    return {"coins": coins}


def compute_archetypes(regimes_data, timeframe_data):
    """
    Compute proximity scores for 6 archetypes across all coins.
    Returns list of {name, conditions_met, conditions_total, closest_coin}.
    """
    archetypes = []

    # Gather per-coin regime indicators
    coins_data = regimes_data.get("coins", {})

    # Build per-coin indicator map from regimes.json
    coin_indicators = {}
    for coin, data in coins_data.items():
        if not isinstance(data, dict):
            continue
        indicators = data.get("indicators", data)
        coin_indicators[coin] = {
            "HURST_24H": indicators.get("HURST_24H", indicators.get("hurst_24h")),
            "HURST_48H": indicators.get("HURST_48H", indicators.get("hurst_48h")),
            "DFA_24H": indicators.get("DFA_24H", indicators.get("dfa_24h")),
            "DFA_48H": indicators.get("DFA_48H", indicators.get("dfa_48h")),
            "LYAPUNOV_24H": indicators.get("LYAPUNOV_24H", indicators.get("lyapunov_24h")),
            "ADX_3H30M": indicators.get("ADX_3H30M", indicators.get("adx")),
            "XONE_I_NET": indicators.get("XONE_I_NET", indicators.get("xone_i_net")),
            "XONE_AVG_NET": indicators.get("XONE_AVG_NET", indicators.get("xone_avg_net")),
            "RSI_3H30M": indicators.get("RSI_3H30M", indicators.get("rsi")),
            "CMO_3H30M": indicators.get("CMO_3H30M", indicators.get("cmo")),
            "BB_POS_24H": indicators.get("BB_POS_24H", indicators.get("bb_pos")),
            "EMA_N_24H": indicators.get("EMA_N_24H", indicators.get("ema_n")),
            "MACD_N_24H": indicators.get("MACD_N_24H", indicators.get("macd_n")),
            "DOJI_VELOCITY": indicators.get("DOJI_VELOCITY", indicators.get("doji_velocity")),
            "DOJI_SIGNAL": indicators.get("DOJI_SIGNAL", indicators.get("doji_signal")),
            "regime": data.get("regime", "unknown"),
        }

    # Merge timeframe signals if available
    tf_coins = timeframe_data.get("coins", timeframe_data.get("signals", {})) if timeframe_data else {}
    for coin, tf in tf_coins.items():
        if coin not in coin_indicators:
            coin_indicators[coin] = {}
        if isinstance(tf, dict):
            for k, v in tf.items():
                if k not in coin_indicators[coin] or coin_indicators[coin][k] is None:
                    coin_indicators[coin][k] = v

    def safe(val):
        return val is not None

    def best_score_for_archetype(name):
        """Returns (conditions_met, conditions_total, closest_coin) for an archetype."""
        best_met = 0
        best_coin = None
        total = 0

        for coin, ind in coin_indicators.items():
            met = 0
            t = 0

            if name == "CHAOS":
                t = 4
                h24 = ind.get("HURST_24H")
                h48 = ind.get("HURST_48H")
                d24 = ind.get("DFA_24H")
                d48 = ind.get("DFA_48H")
                lya = ind.get("LYAPUNOV_24H")
                adx = ind.get("ADX_3H30M")
                if safe(h24) and safe(h48) and abs(h24 - h48) > 0.05:
                    met += 1
                if safe(d24) and safe(d48) and (d24 - d48) > 0.05:
                    met += 1
                if safe(lya) and lya < 1.90:
                    met += 1
                if safe(adx) and adx >= 25:
                    met += 1

            elif name == "SOCIAL":
                t = 5
                xone_i = ind.get("XONE_I_NET")
                xone_a = ind.get("XONE_AVG_NET")
                rsi = ind.get("RSI_3H30M")
                cmo = ind.get("CMO_3H30M")
                bb = ind.get("BB_POS_24H")
                if safe(xone_i) and xone_i >= 80: met += 1
                if safe(xone_a) and xone_a >= 50: met += 1
                if safe(rsi) and rsi >= 70: met += 1
                if safe(cmo) and cmo >= 30: met += 1
                if safe(bb) and bb >= 0.8: met += 1

            elif name == "TRIPLE":
                t = 5
                regime = ind.get("regime", "")
                h24 = ind.get("HURST_24H")
                ema = ind.get("EMA_N_24H")
                macd = ind.get("MACD_N_24H")
                tf_signal = ind.get("timeframe_signal", ind.get("tf_signal", ""))
                if regime == "trending": met += 1
                if safe(h24) and h24 > 0.55: met += 1
                if safe(ema) and ema > 1.005: met += 1
                if safe(macd) and macd > 0: met += 1
                if tf_signal == "CONFIRMATION_LONG": met += 1

            elif name == "DOJI":
                t = 4
                dv = ind.get("DOJI_VELOCITY")
                ds = ind.get("DOJI_SIGNAL")
                lya = ind.get("LYAPUNOV_24H")
                adx = ind.get("ADX_3H30M")
                if safe(dv) and dv >= 3.0: met += 1
                if safe(ds) and ds >= 1.0: met += 1
                if safe(lya) and lya < 1.80: met += 1
                if safe(adx) and adx < 20: met += 1

            elif name == "VOLUME":
                t = 4
                met = 0  # skip for now

            elif name == "COMPOUND":
                t = 10
                # Use score_long or score_short if present
                score_l = ind.get("score_long", ind.get("score", 0)) or 0
                score_s = ind.get("score_short", 0) or 0
                best_s = max(score_l, score_s)
                # Clamp to t
                met = min(int(round(best_s)), t)

            if met > best_met:
                best_met = met
                best_coin = coin
            total = t

        return best_met, total, best_coin

    for arch_name in ["CHAOS", "SOCIAL", "TRIPLE", "DOJI", "VOLUME", "COMPOUND"]:
        met, total, closest = best_score_for_archetype(arch_name)
        archetypes.append({
            "name": arch_name,
            "conditions_met": met,
            "conditions_total": total,
            "closest_coin": closest,
        })

    return archetypes


def export():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

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

    # ─── NEW: spread, funding, archetypes ───
    spread_raw = load_json(BUS_DIR / "spread.json", {})
    funding_raw = load_json(BUS_DIR / "funding.json", {})
    timeframe_raw = load_json(BUS_DIR / "timeframe_signals.json", {})

    spread_export = compute_spread(spread_raw)
    funding_export = compute_funding(funding_raw)
    archetypes_export = compute_archetypes(regimes, timeframe_raw)

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
        # Compute hold times from entry/exit timestamps (hours_held is never set)
        hold_times = []
        for c in closed_list:
            try:
                from datetime import datetime
                entry = datetime.fromisoformat(c["entry_time"])
                exit_t = datetime.fromisoformat(c["exit_time"])
                hours = (exit_t - entry).total_seconds() / 3600
                if hours > 0:
                    hold_times.append(round(hours, 2))
            except (KeyError, ValueError):
                pass
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

    live_stats = compute_stats(live_closed)
    live_total_trades = len(live_closed)
    live_total_wins = sum(1 for c in live_closed if c.get("pnl_dollars", c.get("pnl_usd", 0)) > 0)

    # ─── LIVE EQUITY CURVE (from risk agent) ───
    live_equity_curve = [
        {"t": e.get("timestamp", ""), "v": e.get("account_value", 0)}
        for e in equity_history
    ]

    # ─── BUILD SNAPSHOT ───
    snapshot = {
        "live": True,
        "updated": now_iso,
        "liveTrading": {
            "enabled": True,
            "capital": 115,
            "positions": live_positions,
            "trades": live_total_trades,
            "wins": live_total_wins,
            "dailyLoss": live_portfolio.get("daily_loss", 0),
            "closed": live_closed,
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
            "spread": spread_export,
            "funding": funding_export,
            "archetypes": archetypes_export,
        },
        "liveEquityCurve": live_equity_curve,
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

#!/usr/bin/env python3
"""
V6 Immune System — proactive monitoring, anomaly detection, daily reports.

Runs as a continuous daemon alongside executor/risk_guard.
Checks every 60s, sends alerts only when anomalies detected.
Posts daily summary at midnight UTC.

Items:
  - Position age monitoring (>24h alert)
  - WS data freshness tracking
  - Error rate tracking (from supervisor log)
  - Anomaly detection (2σ on equity, trade frequency, win rate)
  - Expected vs actual P&L divergence per signal
  - Daily midnight summary to Telegram
"""

import json
import math
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.v6.config import (
    BUS_DIR, POSITIONS_FILE, HEARTBEAT_FILE, RISK_FILE,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN_ENV, get_env,
    EQUITY_HISTORY_FILE,
)
from scanner.v6.bus_io import load_json, load_json_locked

CYCLE_SECONDS = 60  # check every minute
IMMUNE_STATE_FILE = BUS_DIR / "immune_state.json"
SUPERVISOR_LOG = Path(__file__).parent / "supervisor.log"

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [IMMUNE] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_telegram(message: str):
    """Send alert via Telegram."""
    import urllib.request
    token = get_env(TELEGRAM_BOT_TOKEN_ENV)
    if not token:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"Telegram send failed: {e}")


def load_state() -> dict:
    return load_json(IMMUNE_STATE_FILE, {
        "last_daily_summary": "",
        "last_position_alert": {},
        "error_counts": [],
        "equity_history_7d": [],
        "alerts_sent_today": 0,
    })


def save_state(state: dict):
    from scanner.v6.bus_io import save_json_atomic
    save_json_atomic(IMMUNE_STATE_FILE, state)


# ─── CHECK: POSITION AGE ─────────────────────────────────────────────────────

def check_position_age(state: dict) -> list[str]:
    """Alert if any position is open > 24h."""
    alerts = []
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    now = datetime.now(timezone.utc)
    last_alerts = state.get("last_position_alert", {})

    for pos in positions:
        coin = pos.get("coin", "?")
        entry_str = pos.get("entry_time", "")
        if not entry_str:
            continue
        try:
            entry_dt = datetime.fromisoformat(entry_str.replace("Z", "+00:00"))
            age_hours = (now - entry_dt).total_seconds() / 3600

            # Alert at 24h, 48h, 72h thresholds
            for threshold in [24, 48, 72]:
                last_threshold = last_alerts.get(coin, 0)
                if age_hours >= threshold and last_threshold < threshold:
                    direction = pos.get("direction", "?")
                    entry_price = pos.get("entry_price", 0)
                    alerts.append(
                        f"⏰ POSITION AGE: {coin} {direction} @ ${entry_price:.2f}\n"
                        f"Open for {age_hours:.1f}h (>{threshold}h threshold)\n"
                        f"Signal: {pos.get('signal_name', '?')}"
                    )
                    last_alerts[coin] = threshold
        except (ValueError, TypeError):
            continue

    # Clean up alerts for closed positions
    open_coins = {p.get("coin") for p in positions}
    for coin in list(last_alerts.keys()):
        if coin not in open_coins:
            del last_alerts[coin]

    state["last_position_alert"] = last_alerts
    return alerts


# ─── CHECK: WS DATA FRESHNESS ────────────────────────────────────────────────

def check_ws_freshness(state: dict) -> list[str]:
    """Alert if WebSocket data is stale."""
    alerts = []
    heartbeats = load_json(HEARTBEAT_FILE, {})
    evaluator_ts = heartbeats.get("evaluator", "")

    if evaluator_ts:
        try:
            last_tick = datetime.fromisoformat(evaluator_ts.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - last_tick).total_seconds()

            if age_seconds > 120:  # 2 minutes without a tick
                alerts.append(
                    f"📡 WS DATA STALE: evaluator last tick {age_seconds:.0f}s ago\n"
                    f"Expected every 15s. Possible disconnect."
                )
        except (ValueError, TypeError):
            pass
    return alerts


# ─── CHECK: ERROR RATE ────────────────────────────────────────────────────────

def check_error_rate(state: dict) -> list[str]:
    """Count errors in supervisor log, alert on spikes."""
    alerts = []
    error_counts = state.get("error_counts", [])

    # Count errors in last 60 seconds of log
    # Exclude benign patterns: API rate limits (HTTP 429) are handled gracefully
    BENIGN_PATTERNS = {"Rate limit exceeded", "HTTP 429", "retryAfterSeconds"}
    errors_this_cycle = 0
    if SUPERVISOR_LOG.exists():
        try:
            lines = SUPERVISOR_LOG.read_text().splitlines()
            now = time.time()
            for line in lines[-200:]:  # check last 200 lines
                if "ERROR" in line or "Traceback" in line or "FATAL" in line:
                    if not any(bp in line for bp in BENIGN_PATTERNS):
                        errors_this_cycle += 1
        except Exception:
            pass

    # Track rolling window (last 60 readings = 60 minutes)
    error_counts.append({"ts": now_iso(), "count": errors_this_cycle})
    if len(error_counts) > 60:
        error_counts = error_counts[-60:]
    state["error_counts"] = error_counts

    # Alert if error rate spikes (>2σ from mean), with 30-min cooldown
    last_error_alert = state.get("last_error_spike_alert", 0)
    cooldown_ok = time.time() - last_error_alert > 1800  # 30 minutes
    if len(error_counts) >= 10 and cooldown_ok:
        counts = [e["count"] for e in error_counts]
        mean = sum(counts) / len(counts)
        if len(counts) >= 2:
            variance = sum((c - mean) ** 2 for c in counts) / (len(counts) - 1)
            std = math.sqrt(variance) if variance > 0 else 0
            if std > 0 and errors_this_cycle > mean + 2 * std and errors_this_cycle > 5:
                alerts.append(
                    f"📊 ERROR SPIKE: {errors_this_cycle} errors this cycle\n"
                    f"Mean: {mean:.1f}, σ: {std:.1f}, threshold: {mean + 2*std:.0f}"
                )
                state["last_error_spike_alert"] = time.time()

    return alerts


# ─── CHECK: EQUITY ANOMALY ───────────────────────────────────────────────────

def check_equity_anomaly(state: dict) -> list[str]:
    """Detect abnormal equity movements (>2σ from 7d average)."""
    alerts = []

    # Read latest equity
    portfolio = load_json(BUS_DIR / "portfolio.json", {})
    equity = portfolio.get("account_value", 0)
    if not equity:
        return alerts

    # Track 7d history (one reading per check = per minute)
    history = state.get("equity_history_7d", [])
    history.append({"ts": now_iso(), "equity": equity})

    # Keep last 7 days (10080 minutes)
    if len(history) > 10080:
        history = history[-10080:]
    state["equity_history_7d"] = history

    # Need at least 1 hour of data
    if len(history) < 60:
        return alerts

    equities = [e["equity"] for e in history]
    mean = sum(equities) / len(equities)
    if len(equities) >= 2:
        variance = sum((e - mean) ** 2 for e in equities) / (len(equities) - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        if std > 0 and abs(equity - mean) > 2 * std:
            direction = "above" if equity > mean else "below"
            alerts.append(
                f"📈 EQUITY ANOMALY: ${equity:.2f} is {abs(equity-mean)/std:.1f}σ {direction} mean\n"
                f"Mean: ${mean:.2f}, σ: ${std:.2f}"
            )

    return alerts


# ─── CHECK: SIGNAL PERFORMANCE DRIFT ─────────────────────────────────────────

def check_signal_drift(state: dict) -> list[str]:
    """Compare our per-signal WR against ENVY's claimed WR."""
    alerts = []
    try:
        from scanner.v6.analytics import per_signal_stats, load_all_trades
        trades = load_all_trades()
        stats = per_signal_stats(trades)

        for sig, data in stats.items():
            if data["count"] < 5:  # need enough data
                continue
            if data["wr_drift"] is not None and abs(data["wr_drift"]) > 20:
                alerts.append(
                    f"📉 SIGNAL DRIFT: {sig[:40]}\n"
                    f"Our WR: {data['our_wr']}% vs ENVY: {data['envy_wr']}% "
                    f"(drift: {data['wr_drift']:+.0f}pp over {data['count']} trades)"
                )
    except Exception as e:
        log(f"Signal drift check failed: {e}")

    return alerts


# ─── SHARPE GAP TRACKER ──────────────────────────────────────────────────────

SHARPE_GAP_FILE = BUS_DIR / "sharpe_gap.jsonl"

def track_sharpe_gap(state: dict) -> list[str]:
    """Track backtested_sharpe vs realized_sharpe daily.
    
    This is THE metric for the next two weeks. If the gap is large,
    our signals look good on paper but fail in production.
    If the gap is small, the product works.
    """
    alerts = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_sharpe_gap_date = state.get("last_sharpe_gap_date", "")
    
    if last_sharpe_gap_date == today:
        return []  # already recorded today
    
    try:
        from scanner.v6.analytics import per_signal_stats, load_all_trades, compute_sharpe
        
        trades = load_all_trades()
        if len(trades) < 3:
            return []
        
        stats = per_signal_stats(trades)
        
        # Aggregate backtested vs realized
        signals_with_both = []
        for sig, data in stats.items():
            if data["count"] >= 2 and data.get("envy_sharpe", 0) > 0 and data.get("our_sharpe") is not None:
                signals_with_both.append({
                    "signal":          sig,
                    "trades":          data["count"],
                    "backtested_sharpe": data["envy_sharpe"],
                    "realized_sharpe":  data["our_sharpe"],
                    "gap":             data["envy_sharpe"] - data["our_sharpe"],
                    "backtested_wr":   data["envy_wr"],
                    "realized_wr":     data["our_wr"],
                })
        
        if not signals_with_both:
            return []
        
        # Portfolio-level numbers
        total_trades = sum(s["trades"] for s in signals_with_both)
        
        # Trade-weighted averages
        w_bt_sharpe = sum(s["backtested_sharpe"] * s["trades"] for s in signals_with_both) / total_trades
        w_re_sharpe = sum(s["realized_sharpe"] * s["trades"] for s in signals_with_both) / total_trades
        w_bt_wr = sum(s["backtested_wr"] * s["trades"] for s in signals_with_both) / total_trades
        w_re_wr = sum(s["realized_wr"] * s["trades"] for s in signals_with_both) / total_trades
        
        # Overall realized Sharpe from actual P&L series
        overall_realized_sharpe = compute_sharpe(trades, annualize=True)
        
        gap = w_bt_sharpe - w_re_sharpe
        gap_pct = (gap / w_bt_sharpe * 100) if w_bt_sharpe > 0 else 0
        
        # Record to JSONL
        record = {
            "date":                today,
            "total_trades":        total_trades,
            "signals_tracked":     len(signals_with_both),
            "backtested_sharpe":   round(w_bt_sharpe, 3),
            "realized_sharpe":     round(w_re_sharpe, 3),
            "overall_realized_sharpe": overall_realized_sharpe,
            "gap":                 round(gap, 3),
            "gap_pct":             round(gap_pct, 1),
            "backtested_wr":       round(w_bt_wr, 1),
            "realized_wr":         round(w_re_wr, 1),
            "wr_gap":              round(w_bt_wr - w_re_wr, 1),
            "per_signal": [
                {
                    "signal": s["signal"][:50],
                    "n":      s["trades"],
                    "bt_s":   round(s["backtested_sharpe"], 2),
                    "re_s":   round(s["realized_sharpe"], 2),
                    "gap":    round(s["gap"], 2),
                }
                for s in sorted(signals_with_both, key=lambda x: abs(x["gap"]), reverse=True)
            ],
        }
        
        with open(SHARPE_GAP_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
        
        state["last_sharpe_gap_date"] = today
        log(f"SHARPE GAP: backtested={w_bt_sharpe:.2f} realized={w_re_sharpe:.2f} gap={gap:+.2f} ({gap_pct:+.1f}%) [{total_trades} trades, {len(signals_with_both)} signals]")
        
        # Alert if gap is dangerously large (>50% of backtested)
        if gap_pct > 50 and total_trades >= 10:
            alerts.append(
                f"🚨 SHARPE GAP ALERT\n"
                f"Backtested: {w_bt_sharpe:.2f} → Realized: {w_re_sharpe:.2f}\n"
                f"Gap: {gap:+.2f} ({gap_pct:+.1f}%)\n"
                f"WR: {w_bt_wr:.0f}% → {w_re_wr:.0f}% ({w_bt_wr - w_re_wr:+.0f}pp)\n"
                f"Trades: {total_trades} across {len(signals_with_both)} signals\n"
                f"⚠️ Signals look good on paper but underperform live"
            )
        elif gap_pct < -20 and total_trades >= 10:
            # We're outperforming backtests — good but suspicious
            alerts.append(
                f"📈 OUTPERFORMANCE: Realized {w_re_sharpe:.2f} > Backtested {w_bt_sharpe:.2f}\n"
                f"({total_trades} trades) — check for survivorship bias"
            )
        
    except Exception as e:
        log(f"Sharpe gap tracking failed: {e}")
    
    return alerts


# ─── DAILY SUMMARY ────────────────────────────────────────────────────────────

def should_send_daily_summary(state: dict) -> bool:
    """Check if it's time for daily summary (midnight UTC ± 2 minutes)."""
    now = datetime.now(timezone.utc)
    if now.hour != 0 or now.minute > 2:
        return False
    last = state.get("last_daily_summary", "")
    if last and last[:10] == now.strftime("%Y-%m-%d"):
        return False  # already sent today
    return True


def build_daily_summary() -> str:
    """Build the daily midnight summary."""
    try:
        from scanner.v6.analytics import full_report
        report = full_report()
    except Exception as e:
        return f"Daily summary failed: {e}"

    risk = load_json(RISK_FILE, {})
    portfolio = load_json(BUS_DIR / "portfolio.json", {})
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])

    equity = portfolio.get("account_value", 0)
    daily_pnl = risk.get("daily_pnl_usd", 0)
    daily_loss = risk.get("daily_loss_usd", 0)
    peak = risk.get("peak_equity", 750)
    drawdown = risk.get("drawdown_pct", 0)

    # Today's trades (V6 only)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = []
    try:
        from scanner.v6.analytics import load_all_trades
        all_trades = load_all_trades()
        today_trades = [t for t in all_trades if (t.get("exit_time") or "")[:10] == today]
    except Exception:
        pass

    today_pnl = sum(t.get("pnl_usd") or t.get("pnl_dollars") or 0 for t in today_trades)
    today_wins = sum(1 for t in today_trades if (t.get("pnl_usd") or t.get("pnl_dollars") or 0) > 0)
    today_count = len(today_trades)

    # Find worst signal
    signals = report.get("per_signal", {})
    worst = sorted(
        [(k, v) for k, v in signals.items() if v["count"] >= 2],
        key=lambda x: x[1]["pnl_total"]
    )[:1]
    worst_line = ""
    if worst:
        k, v = worst[0]
        worst_line = f"\nWorst signal: {k[:35]} ({v['count']}t, ${v['pnl_total']:+.2f})"

    # Sharpe gap data
    sharpe_gap_line = ""
    try:
        if SHARPE_GAP_FILE.exists():
            lines = SHARPE_GAP_FILE.read_text().strip().split("\n")
            if lines:
                latest = json.loads(lines[-1])
                bt = latest.get("backtested_sharpe", 0)
                re = latest.get("realized_sharpe", 0)
                gap = latest.get("gap", 0)
                gap_pct = latest.get("gap_pct", 0)
                wr_gap = latest.get("wr_gap", 0)
                n_signals = latest.get("signals_tracked", 0)
                sharpe_gap_line = (
                    f"\n\n<b>📐 SHARPE GAP</b>\n"
                    f"Backtested: {bt:.2f} → Realized: {re:.2f} (gap: {gap:+.2f}, {gap_pct:+.1f}%)\n"
                    f"WR gap: {wr_gap:+.1f}pp across {n_signals} signals"
                )
    except Exception:
        pass

    msg = (
        f"📊 <b>DAILY SUMMARY</b> — {today}\n\n"
        f"💰 Equity: ${equity:.2f} (peak: ${peak:.2f})\n"
        f"📉 Drawdown: {drawdown:.1f}%\n\n"
        f"Today: {today_count} trades, {today_wins}W, ${today_pnl:+.2f}\n"
        f"All-time: {report['total_trades']} trades, {report['win_rate']}% WR, ${report['total_pnl']:+.2f}\n"
        f"Sharpe (all): {report['sharpe_all']}\n"
        f"Max DD: ${report['max_drawdown_usd']} ({report['max_drawdown_pct']}%)\n"
        f"\nOpen positions: {len(positions)}"
        f"{worst_line}"
        f"{sharpe_gap_line}"
    )
    return msg


# ─── CHECK: POSITION DESYNC WITH HL ──────────────────────────────────────────

def check_position_desync(state: dict) -> list[str]:
    """CRITICAL: detect when local positions.json disagrees with Hyperliquid.

    If local has 0 positions but HL has >0, this means positions.json
    was wiped (e.g. by a bug) and we've lost track of live positions.
    Auto-reconcile by writing HL positions back to local.
    """
    import urllib.request
    from scanner.v6.config import HL_MAIN_ADDRESS, STRATEGY_VERSION

    alerts = []
    local_positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])

    try:
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "clearinghouseState", "user": HL_MAIN_ADDRESS}).encode(),
            headers={"Content-Type": "application/json"},
        )
        result = json.loads(urllib.request.urlopen(req, timeout=10).read())
        hl_positions = []
        for p in result.get("assetPositions", []):
            pos = p.get("position", {})
            sz = float(pos.get("szi", 0))
            if sz != 0:
                hl_positions.append({
                    "coin":        pos["coin"],
                    "direction":   "LONG" if sz > 0 else "SHORT",
                    "size_coins":  abs(sz),
                    "entry_price": float(pos.get("entryPx", 0)),
                })
    except Exception as e:
        log(f"Desync check: HL query failed: {e}")
        return alerts

    local_coins = {p.get("coin") for p in local_positions}
    hl_coins = {p["coin"] for p in hl_positions}

    # CRITICAL: local is empty but HL is not
    if len(local_positions) == 0 and len(hl_positions) > 0:
        alert_msg = (
            f"🚨🚨 CRITICAL DESYNC\n"
            f"Local positions.json: 0 positions\n"
            f"Hyperliquid reality: {len(hl_positions)} positions\n"
            f"Coins on HL: {', '.join(hl_coins)}\n\n"
            f"AUTO-RECONCILING from HL..."
        )
        alerts.append(alert_msg)
        log(f"CRITICAL DESYNC: 0 local, {len(hl_positions)} on HL — auto-reconciling")

        # Auto-reconcile: rebuild positions from HL
        from scanner.v6.bus_io import save_json_atomic
        new_positions = []
        for hl_pos in hl_positions:
            new_positions.append({
                "coin":          hl_pos["coin"],
                "direction":     hl_pos["direction"],
                "entry_price":   hl_pos["entry_price"],
                "size_coins":    hl_pos["size_coins"],
                "size_usd":      hl_pos["entry_price"] * hl_pos["size_coins"],
                "entry_time":    datetime.now(timezone.utc).isoformat(),
                "signal_name":   "reconciled_by_immune_system",
                "stop_loss_pct": 0.05,
                "strategy_version": STRATEGY_VERSION,
                "sharpe":        0,
                "win_rate":      0,
            })
        save_json_atomic(POSITIONS_FILE, {"updated_at": datetime.now(timezone.utc).isoformat(), "positions": new_positions})

        # Also mirror to v5 path
        try:
            v5_path = Path(__file__).parent.parent / "data" / "live" / "positions.json"
            v5_list = []
            for p in new_positions:
                v5_list.append({
                    "coin":             p["coin"],
                    "direction":        p["direction"],
                    "signal":           p["signal_name"],
                    "entry_price":      p["entry_price"],
                    "entry_time":       p["entry_time"],
                    "size_usd":         p["size_usd"],
                    "size_coins":       p["size_coins"],
                    "stop_loss":        0,
                    "stop_loss_pct":    5.0,
                    "peak_pnl_pct":     0.0,
                    "strategy_version": STRATEGY_VERSION,
                })
            save_json_atomic(v5_path, v5_list)
        except Exception as e:
            log(f"v5 mirror during immune reconcile failed: {e}")

        return alerts

    # Non-critical: check for orphans or ghosts
    orphans = hl_coins - local_coins
    ghosts = local_coins - hl_coins

    if orphans:
        alerts.append(
            f"⚠️ POSITION MISMATCH\n"
            f"HL has positions not tracked locally: {', '.join(orphans)}\n"
            f"Next executor reconciliation will adopt them."
        )
    if ghosts:
        alerts.append(
            f"⚠️ GHOST POSITIONS\n"
            f"Local tracks positions not on HL: {', '.join(ghosts)}\n"
            f"Next executor reconciliation will remove them."
        )

    return alerts


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def run_once(state: dict) -> dict:
    """Run all checks once. Returns updated state."""
    all_alerts = []

    # Position desync with HL (CRITICAL — check every cycle)
    all_alerts.extend(check_position_desync(state))

    # Position age
    all_alerts.extend(check_position_age(state))

    # WS freshness
    all_alerts.extend(check_ws_freshness(state))

    # Error rate
    all_alerts.extend(check_error_rate(state))

    # Equity anomaly
    all_alerts.extend(check_equity_anomaly(state))

    # Signal drift (check every 10 minutes, not every minute)
    cycle_count = state.get("cycle_count", 0)
    if cycle_count % 10 == 0:
        all_alerts.extend(check_signal_drift(state))
    
    # Sharpe gap tracker (once per day)
    all_alerts.extend(track_sharpe_gap(state))
    
    state["cycle_count"] = cycle_count + 1

    # Send alerts (max 5 per day to avoid flood)
    today_alerts = state.get("alerts_sent_today", 0)
    today_date = state.get("alerts_date", "")
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if today_date != current_date:
        today_alerts = 0
        state["alerts_date"] = current_date

    for alert in all_alerts:
        if today_alerts < 10:
            log(f"ALERT: {alert[:80]}")
            send_telegram(alert)
            today_alerts += 1
        else:
            log(f"SUPPRESSED (daily limit): {alert[:80]}")

    state["alerts_sent_today"] = today_alerts

    # Daily summary at midnight UTC
    if should_send_daily_summary(state):
        summary = build_daily_summary()
        log("Sending daily summary")
        send_telegram(summary)
        state["last_daily_summary"] = current_date

    # Weekly self-audit (Sunday midnight UTC)
    now_dt = datetime.now(timezone.utc)
    if now_dt.weekday() == 6 and now_dt.hour == 0 and now_dt.minute <= 2:
        last_audit = state.get("last_self_audit", "")
        if last_audit != current_date:
            log("Running weekly self-audit")
            try:
                from scanner.v6.self_audit import audit
                findings = audit()
                n_red = sum(1 for f in findings if "🔴" in f)
                n_yellow = sum(1 for f in findings if "🟡" in f)
                header = f"🔍 <b>WEEKLY SELF-AUDIT</b> — {current_date}\n"
                header += f"Findings: {n_yellow}🟡 {n_red}🔴\n\n"
                body = "\n".join(findings)
                send_telegram(header + body)
                state["last_self_audit"] = current_date
            except Exception as e:
                log(f"Self-audit failed: {e}")

    save_state(state)
    return state


def main():
    log("=== V6 Immune System starting ===")
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    # Run once first
    state = run_once(state)

    if "--loop" in sys.argv:
        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                state = run_once(state)
            except Exception as e:
                log(f"ERROR in immune cycle: {e}")


if __name__ == "__main__":
    main()

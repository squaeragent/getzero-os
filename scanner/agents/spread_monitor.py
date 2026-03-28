#!/usr/bin/env python3
"""
Agent 10: Spot-Perp Spread Monitor

Tracks mark-oracle spread divergence per coin. When spread exceeds threshold
AND funding is extreme, flags potential MM setup (Gleb's playbook).
When spread collapses back toward 0, flags unwind signal.

Data source: Hyperliquid API (free, no key needed)
Cycle: every 120s
Output: scanner/bus/spread.json
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    load_json, save_json, append_jsonl, make_logger, update_heartbeat,
    BUS_DIR,
)

log = make_logger("SPREAD")

SPREAD_FILE = BUS_DIR / "spread.json"
SPREAD_HISTORY = BUS_DIR / "spread_history.jsonl"
FUNDING_FILE = BUS_DIR / "funding.json"

CYCLE_SECONDS = 120

# All coins we monitor (not just whitelist — spread anomalies on any coin are useful)
COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

# Thresholds
SPREAD_WARNING_PCT = 0.10   # 0.1% spread = yellow
SPREAD_ALERT_PCT = 0.30     # 0.3% spread = red (MM setup zone)
SPREAD_COLLAPSE_SPEED = 0.05  # spread dropped >0.05% in one cycle = unwind signal
EXTREME_FUNDING_PCT = 0.005   # 0.5% per 8h = extreme

# Rolling window for velocity calculation
MAX_HISTORY_POINTS = 30  # 30 * 2min = 1 hour of spread history per coin


def fetch_hl_data():
    """Fetch mark, oracle, mid prices and funding from HL."""
    import urllib.request
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
        headers={"Content-Type": "application/json"}
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    
    result = {}
    for i, ctx in enumerate(resp[1]):
        coin = resp[0]["universe"][i]["name"]
        if coin not in COINS:
            continue
        mark = float(ctx.get("markPx", 0))
        oracle = float(ctx.get("oraclePx", 0))
        mid = float(ctx.get("midPx", 0))
        funding = float(ctx.get("funding", 0))
        oi = float(ctx.get("openInterest", 0))
        vol = float(ctx.get("dayNtlVlm", 0))
        
        spread_pct = (mark - oracle) / oracle * 100 if oracle > 0 else 0
        
        result[coin] = {
            "mark": mark,
            "oracle": oracle,
            "mid": mid,
            "funding": funding,
            "funding_ann": funding * 3 * 365 * 100,  # annualized %
            "oi": oi,
            "vol24h": vol,
            "spread_pct": round(spread_pct, 6),
            "spread_abs": round(abs(spread_pct), 6),
        }
    return result


def load_spread_state():
    """Load previous spread state for velocity calculation."""
    return load_json(SPREAD_FILE, {"coins": {}, "alerts": [], "timestamp": None})


def compute_spread_velocity(coin, current_spread, prev_state):
    """Compute spread velocity (change per cycle) and detect collapse."""
    coin_history = prev_state.get("coins", {}).get(coin, {}).get("history", [])
    
    if not coin_history:
        return 0.0, False, coin_history
    
    prev_spread = coin_history[-1]
    velocity = current_spread - prev_spread  # positive = widening, negative = narrowing
    
    # Detect collapse: spread was above alert threshold and dropped sharply
    was_extreme = abs(prev_spread) >= SPREAD_ALERT_PCT
    collapsed = was_extreme and abs(current_spread) < abs(prev_spread) - SPREAD_COLLAPSE_SPEED
    
    return round(velocity, 6), collapsed, coin_history


def classify_spread(spread_abs, funding, velocity, collapsed):
    """Classify the spread state."""
    funding_extreme = abs(funding) >= EXTREME_FUNDING_PCT
    
    if collapsed:
        return "UNWIND"  # spread was extreme, now collapsing = MM exiting
    elif spread_abs >= SPREAD_ALERT_PCT and funding_extreme:
        return "MM_SETUP"  # high spread + extreme funding = Gleb's playbook
    elif spread_abs >= SPREAD_ALERT_PCT:
        return "DIVERGED"  # high spread but funding normal
    elif spread_abs >= SPREAD_WARNING_PCT:
        return "ELEVATED"
    else:
        return "NORMAL"


def run_cycle():
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    
    try:
        hl_data = fetch_hl_data()
    except Exception as e:
        print(f"  [error] HL fetch failed: {e}")
        write_heartbeat()
        return
    
    prev_state = load_spread_state()
    
    # Load funding data for convergence
    fd = load_json(FUNDING_FILE)
    funding_convergence = fd.get("coins", {})
    
    coins_output = {}
    alerts = []
    
    for coin in COINS:
        d = hl_data.get(coin)
        if not d:
            continue
        
        spread = d["spread_pct"]
        spread_abs = d["spread_abs"]
        funding = d["funding"]
        
        velocity, collapsed, history = compute_spread_velocity(coin, spread, prev_state)
        status = classify_spread(spread_abs, funding, velocity, collapsed)
        
        # Update history (keep last MAX_HISTORY_POINTS)
        history.append(spread)
        if len(history) > MAX_HISTORY_POINTS:
            history = history[-MAX_HISTORY_POINTS:]
        
        # Compute rolling stats
        spreads_abs = [abs(s) for s in history]
        avg_spread = sum(spreads_abs) / len(spreads_abs) if spreads_abs else 0
        max_spread = max(spreads_abs) if spreads_abs else 0
        
        coins_output[coin] = {
            "spread_pct": spread,
            "spread_abs": spread_abs,
            "velocity": velocity,
            "status": status,
            "funding": round(funding * 100, 6),
            "funding_ann": round(d["funding_ann"], 1),
            "mark": d["mark"],
            "oracle": d["oracle"],
            "oi": d["oi"],
            "vol24h": d["vol24h"],
            "avg_spread_1h": round(avg_spread, 6),
            "max_spread_1h": round(max_spread, 6),
            "history": history,
        }
        
        # Generate alerts
        if status in ("MM_SETUP", "UNWIND"):
            alert = {
                "coin": coin,
                "status": status,
                "spread_pct": spread,
                "funding_pct": round(funding * 100, 4),
                "velocity": velocity,
                "timestamp": ts_iso,
            }
            alerts.append(alert)
            
            if status == "MM_SETUP":
                print(f"  ⚠ {coin} MM_SETUP: spread={spread:+.4f}% funding={funding*100:+.4f}% oi=${d['oi']/1e6:.1f}M")
            elif status == "UNWIND":
                print(f"  ⚡ {coin} UNWIND: spread collapsing {velocity:+.4f}%/cycle funding={funding*100:+.4f}%")
    
    # Write output
    output = {
        "timestamp": ts_iso,
        "coins": coins_output,
        "alerts": alerts,
        "summary": {
            "total_monitored": len(coins_output),
            "normal": sum(1 for c in coins_output.values() if c["status"] == "NORMAL"),
            "elevated": sum(1 for c in coins_output.values() if c["status"] == "ELEVATED"),
            "diverged": sum(1 for c in coins_output.values() if c["status"] == "DIVERGED"),
            "mm_setup": sum(1 for c in coins_output.values() if c["status"] == "MM_SETUP"),
            "unwind": sum(1 for c in coins_output.values() if c["status"] == "UNWIND"),
        }
    }
    
    save_json(SPREAD_FILE, output)

    # Append alerts to history
    if alerts:
        for a in alerts:
            append_jsonl(SPREAD_HISTORY, a)
    
    write_heartbeat()
    
    # Summary
    s = output["summary"]
    statuses = []
    if s["mm_setup"]: statuses.append(f"{s['mm_setup']} MM_SETUP")
    if s["unwind"]: statuses.append(f"{s['unwind']} UNWIND")
    if s["diverged"]: statuses.append(f"{s['diverged']} DIVERGED")
    if s["elevated"]: statuses.append(f"{s['elevated']} ELEVATED")
    
    status_str = ", ".join(statuses) if statuses else "all normal"
    print(f"  Spread monitor: {s['total_monitored']} coins — {status_str}")


def write_heartbeat():
    update_heartbeat("spread_monitor")


def main():
    loop_mode = "--loop" in sys.argv
    print(f"Spread Monitor {'(loop)' if loop_mode else '(single)'}")
    
    if loop_mode:
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"  [error] {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

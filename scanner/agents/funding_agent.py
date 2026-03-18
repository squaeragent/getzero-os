#!/usr/bin/env python3
"""
ZERO OS — Agent 9: Funding Rate Agent
Monitors Hyperliquid funding rates for all tradeable coins.
Detects extreme funding (shorts/longs paying heavily) as alpha signal.
Combines with regime data for convergence signals.

Outputs:
  scanner/bus/funding.json       — funding rates + convergence signals
  scanner/bus/heartbeat.json     — last-alive timestamp

Usage:
  python3 scanner/agents/funding_agent.py           # single run
  python3 scanner/agents/funding_agent.py --loop    # continuous 5-min cycle
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"

HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
FUNDING_FILE = BUS_DIR / "funding.json"
FUNDING_HISTORY = BUS_DIR / "funding_history.jsonl"
REGIMES_FILE = BUS_DIR / "regimes.json"

# Thresholds
EXTREME_FUNDING_PCT = 0.005   # ±0.005% per 8h = ±5.5% annualized
VERY_EXTREME_PCT = 0.01      # ±0.01% per 8h = ±10.9% annualized
CONVERGENCE_BONUS = 2.0       # Composite score bonus for funding+regime convergence

# Our tradeable coins
TRADED_COINS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ARB", "NEAR", "SUI", "INJ"}

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [FUND] {msg}")


def load_json(path, default=None):
    if default is None:
        default = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_funding():
    """Fetch funding rates and OI for all coins from Hyperliquid."""
    req = urllib.request.Request(HL_INFO_URL,
        data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
        headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    meta = resp[0]
    ctxs = resp[1]
    
    result = {}
    for i, ctx in enumerate(ctxs):
        coin = meta["universe"][i]["name"]
        funding = float(ctx.get("funding", 0))
        oi = float(ctx.get("openInterest", 0))
        mark = float(ctx.get("markPx", 0))
        vol = float(ctx.get("dayNtlVlm", 0))
        
        result[coin] = {
            "funding_rate": funding,
            "funding_pct": round(funding * 100, 6),
            "annualized_pct": round(funding * 365 * 3 * 100, 1),  # 3 funding periods per day
            "open_interest": round(oi, 2),
            "mark_price": mark,
            "volume_24h": round(vol, 2),
        }
    
    return result


def classify_funding(funding_pct):
    """Classify funding rate as signal."""
    abs_f = abs(funding_pct)
    if abs_f >= VERY_EXTREME_PCT:
        return "extreme"
    elif abs_f >= EXTREME_FUNDING_PCT:
        return "elevated"
    else:
        return "neutral"


def detect_convergence(coin, funding_data, regimes):
    """
    Detect funding + regime convergence.
    
    Strongest signals:
    1. regime=trending + extreme negative funding → LONG (shorts paying in uptrend)
    2. regime=trending + extreme positive funding → SHORT (longs paying in downtrend)
    3. regime=reverting + extreme funding either way → FADE the funding
    """
    f_rate = funding_data.get("funding_rate", 0)
    f_class = classify_funding(funding_data.get("funding_pct", 0))
    
    coin_regime = regimes.get("coins", {}).get(coin, {})
    regime = coin_regime.get("regime", "unknown")
    
    signal = None
    strength = 0.0
    reason = ""
    
    if f_class == "neutral":
        return None
    
    if regime == "trending":
        if f_rate < 0 and f_class in ("elevated", "extreme"):
            # Shorts paying in trending market → go LONG
            signal = "LONG"
            strength = 1.5 if f_class == "extreme" else 1.0
            reason = f"trending regime + negative funding ({funding_data['annualized_pct']}% ann) = shorts paying"
        elif f_rate > 0 and f_class in ("elevated", "extreme"):
            # Longs paying in trending market — could be overextended
            signal = "SHORT"
            strength = 1.0 if f_class == "extreme" else 0.7
            reason = f"trending regime + positive funding ({funding_data['annualized_pct']}% ann) = overextended longs"
    
    elif regime == "reverting":
        if f_rate < 0 and f_class in ("elevated", "extreme"):
            # Mean reverting + shorts paying → go LONG (fade shorts)
            signal = "LONG"
            strength = 1.2 if f_class == "extreme" else 0.8
            reason = f"reverting regime + negative funding = fade shorts"
        elif f_rate > 0 and f_class in ("elevated", "extreme"):
            signal = "SHORT"
            strength = 1.2 if f_class == "extreme" else 0.8
            reason = f"reverting regime + positive funding = fade longs"
    
    elif regime == "chaotic":
        # In chaotic regime, extreme funding is a warning sign — reduce confidence
        if f_class == "extreme":
            strength = -0.5  # negative = penalty
            reason = f"chaotic regime + extreme funding = high risk"
    
    elif regime == "stable":
        if f_class == "extreme":
            # Stable + extreme funding = market is building pressure
            if f_rate < 0:
                signal = "LONG"
                strength = 0.8
                reason = f"stable regime + extreme negative funding = pressure building"
            else:
                signal = "SHORT"
                strength = 0.8
                reason = f"stable regime + extreme positive funding = pressure building"
    
    if signal or strength != 0:
        return {
            "direction": signal,
            "strength": round(strength, 2),
            "reason": reason,
            "funding_class": f_class,
            "regime": regime,
        }
    
    return None


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE)
    hb["funding"] = datetime.now(timezone.utc).isoformat()
    save_json(HEARTBEAT_FILE, hb)


def run_cycle():
    ts = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"Funding Rate Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    
    # Fetch funding data
    try:
        all_funding = fetch_funding()
    except Exception as e:
        log(f"Failed to fetch funding: {e}")
        update_heartbeat()
        return
    
    log(f"Fetched funding for {len(all_funding)} coins")
    
    # Load regime data
    regimes = load_json(REGIMES_FILE)
    
    # Process our traded coins
    output = {
        "timestamp": ts.isoformat(),
        "coins": {},
        "convergence_signals": [],
        "extreme_funding": [],
    }
    
    for coin in sorted(TRADED_COINS):
        fd = all_funding.get(coin)
        if not fd:
            continue
        
        f_class = classify_funding(fd["funding_pct"])
        fd["classification"] = f_class
        output["coins"][coin] = fd
        
        # Check for convergence
        conv = detect_convergence(coin, fd, regimes)
        if conv:
            conv["coin"] = coin
            conv["funding_pct"] = fd["funding_pct"]
            conv["annualized_pct"] = fd["annualized_pct"]
            output["convergence_signals"].append(conv)
        
        # Log extreme funding
        if f_class != "neutral":
            output["extreme_funding"].append(coin)
            log(f"  {coin:6s} [{f_class:8s}]  funding={fd['funding_pct']:+.4f}%  ({fd['annualized_pct']:+.1f}% ann)  OI=${fd['open_interest']/1e6:.1f}M")
        else:
            log(f"  {coin:6s} [neutral ]  funding={fd['funding_pct']:+.4f}%")
    
    # Log convergence signals
    if output["convergence_signals"]:
        log(f"\n  CONVERGENCE SIGNALS:")
        for cs in output["convergence_signals"]:
            log(f"    {cs['coin']} {cs.get('direction', 'WARN')} str={cs['strength']:+.1f} — {cs['reason']}")
    else:
        log(f"  No convergence signals")
    
    # Also scan non-traded coins for extreme funding opportunities
    extreme_others = []
    for coin, fd in all_funding.items():
        if coin in TRADED_COINS:
            continue
        f_class = classify_funding(fd["funding_pct"])
        if f_class == "extreme":
            extreme_others.append(f"{coin} {fd['annualized_pct']:+.1f}%")
    if extreme_others:
        log(f"\n  Extreme funding on non-traded coins: {', '.join(extreme_others[:5])}")
    
    # Save
    save_json(FUNDING_FILE, output)
    
    # Append to history
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    history_entry = {
        "t": ts.isoformat(),
        "coins": {c: {"f": d["funding_pct"], "oi": d["open_interest"]} for c, d in output["coins"].items()},
        "convergence": len(output["convergence_signals"]),
    }
    with open(FUNDING_HISTORY, "a") as f:
        f.write(json.dumps(history_entry) + "\n")
    
    update_heartbeat()


def main():
    if "--loop" in sys.argv:
        log("=== ZERO OS Funding Rate Agent LIVE ===")
        log(f"Looping every 300s")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle error: {e}")
            time.sleep(300)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

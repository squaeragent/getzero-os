#!/usr/bin/env python3
"""
V6 Strategy Manager — assembles strategies from ENVY and builds portfolio allocation.

On startup and every 6h:
  1. Calls /paid/strategy/assemble for each coin (falls back to signals cache on 402)
  2. Calls /paid/portfolio/optimize for allocation weights (falls back to sharpe-weighted)
  3. Selects top ACTIVE_COINS_COUNT coins
  4. Saves scanner/v6/bus/strategies.json and scanner/v6/bus/allocation.json

Usage:
  python3 scanner/v6/strategy_manager.py           # single run
  python3 scanner/v6/strategy_manager.py --loop    # run every 6h
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scanner.v6.config import (
    ENVY_BASE_URL, STRATEGIES_FILE, ALLOCATION_FILE, SIGNALS_CACHE_DIR,
    ALL_COINS, ACTIVE_COINS_COUNT, STRATEGY_REFRESH_HOURS, STRATEGY_VERSION,
    BUS_DIR, HEARTBEAT_FILE, get_env,
)

CYCLE_SECONDS = STRATEGY_REFRESH_HOURS * 3600
MIN_SHARPE    = 1.5
MIN_WIN_RATE  = 55.0
TOP_SIGNALS   = 5   # top signals per coin to store


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [STRATEGY] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as _e:
            pass
    return default


def update_heartbeat(component: str):
    hb = load_json(HEARTBEAT_FILE, {})
    hb[component] = now_iso()
    save_json(HEARTBEAT_FILE, hb)


# ─── ENVY API ─────────────────────────────────────────────────────────────────

def envy_get(path: str, params: dict, api_key: str) -> dict | None:
    """GET request to ENVY API. Returns None on 402 or error."""
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 402:
            return None  # payment required — caller handles fallback
        log(f"WARN: {path} HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        log(f"WARN: {path} failed: {e}")
        return None


# ─── STRATEGY ASSEMBLY ────────────────────────────────────────────────────────

MAX_CACHE_AGE_HOURS = 4  # refuse to trade on signals older than this


def assemble_from_cache(coin: str) -> list[dict]:
    """Load signals for a coin from V5 signals cache (fallback).
    REFUSES if cache is older than MAX_CACHE_AGE_HOURS.
    """
    cache_file = SIGNALS_CACHE_DIR / f"{coin}.json"
    if not cache_file.exists():
        return []

    # Check cache age
    try:
        mtime = cache_file.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        if age_hours > MAX_CACHE_AGE_HOURS:
            log(f"  STALE CACHE: {coin} cache is {age_hours:.1f}h old (max {MAX_CACHE_AGE_HOURS}h) — refusing")
            return []
    except OSError:
        pass

    try:
        with open(cache_file) as f:
            packs = json.load(f)
    except (json.JSONDecodeError, OSError) as _e:
        return []

    signals = []
    for p in packs:
        sig_type = p.get("signal_type", p.get("_direction", "")).upper()
        direction = "LONG" if "LONG" in sig_type or sig_type == "LONG" else "SHORT"
        sharpe    = float(p.get("sharpe", 0))
        win_rate  = float(p.get("win_rate", 0))
        if sharpe < MIN_SHARPE or win_rate < MIN_WIN_RATE:
            continue
        signals.append({
            "name":            p.get("name", ""),
            "direction":       direction,
            "expression":      p.get("expression", ""),
            "exit_expression": p.get("exit_expression", ""),
            "max_hold_hours":  p.get("max_hold_hours", 24),
            "sharpe":          sharpe,
            "win_rate":        win_rate,
            "trade_count":     p.get("trade_count", 0),
            "composite_score": float(p.get("composite_score", sharpe)),
            "stop_loss_pct":   p.get("stop_loss_pct", 0.05),
        })

    # Sort by composite_score descending, take top N
    signals.sort(key=lambda s: s["composite_score"], reverse=True)
    for i, s in enumerate(signals):
        s["priority"] = i + 1
    return signals[:TOP_SIGNALS]


def assemble_from_api(coin: str, api_key: str) -> list[dict] | None:
    """Try /paid/strategy/assemble for a coin. Returns None on 402."""
    data = envy_get("/paid/strategy/assemble", {"coin": coin}, api_key)
    if data is None:
        return None  # 402 or error

    # Parse API response — expected format: {signals: [...], coin: "BTC"}
    raw_signals = data.get("signals", [])
    if not raw_signals:
        return []

    signals = []
    for p in raw_signals:
        sig_type  = p.get("signal_type", p.get("direction", "")).upper()
        direction = "LONG" if "LONG" in sig_type else "SHORT"
        sharpe    = float(p.get("sharpe", 0))
        win_rate  = float(p.get("win_rate", 0))
        signals.append({
            "name":            p.get("name", ""),
            "direction":       direction,
            "expression":      p.get("expression", ""),
            "exit_expression": p.get("exit_expression", ""),
            "max_hold_hours":  p.get("max_hold_hours", 24),
            "sharpe":          sharpe,
            "win_rate":        win_rate,
            "trade_count":     p.get("trade_count", 0),
            "composite_score": float(p.get("composite_score", sharpe)),
            "stop_loss_pct":   p.get("stop_loss_pct", 0.05),
        })

    signals.sort(key=lambda s: s["composite_score"], reverse=True)
    for i, s in enumerate(signals):
        s["priority"] = i + 1
    return signals[:TOP_SIGNALS]


def assemble_strategies(api_key: str) -> dict:
    """Assemble strategies for all coins. API first, cache fallback."""
    log(f"Assembling strategies for {len(ALL_COINS)} coins...")
    use_api = True
    coins_data = {}

    for i, coin in enumerate(ALL_COINS):
        if use_api:
            signals = assemble_from_api(coin, api_key)
            if signals is None:
                log("  /paid/strategy/assemble requires payment — using signals cache")
                use_api = False
                signals = assemble_from_cache(coin)
            elif not signals:
                signals = assemble_from_cache(coin)
        else:
            signals = assemble_from_cache(coin)

        if signals:
            best_sharpe = max(s["sharpe"] for s in signals)
            avg_wr      = sum(s["win_rate"] for s in signals) / len(signals)
            coins_data[coin] = {
                "signals":    signals,
                "best_sharpe": best_sharpe,
                "avg_win_rate": avg_wr,
                "signal_count": len(signals),
            }

        if (i + 1) % 10 == 0:
            log(f"  {i+1}/{len(ALL_COINS)} coins processed...")

    log(f"  Assembled {len(coins_data)} coins with valid signals")
    return coins_data


# ─── PORTFOLIO OPTIMIZATION ───────────────────────────────────────────────────

def optimize_from_api(api_key: str) -> dict | None:
    """Try /paid/portfolio/optimize. Returns None on 402."""
    data = envy_get("/paid/portfolio/optimize", {}, api_key)
    if data is None:
        return None

    # Expected: {allocations: [{coin, weight}, ...]}
    allocs = data.get("allocations", data.get("coins", []))
    if not allocs:
        return None

    result = {}
    for a in allocs:
        coin   = a.get("coin", "")
        weight = float(a.get("weight", a.get("allocation", 0)))
        if coin and weight > 0:
            result[coin] = weight
    return result if result else None


def optimize_from_scores(coins_data: dict) -> dict:
    """Build allocation from signal quality — includes all coins above threshold.
    
    Not arbitrary count. Includes any coin with Sharpe >= 1.5 AND >= 2 signals.
    Caps at 12 coins for concentration.
    """
    MIN_SHARPE_THRESHOLD = 1.5
    MIN_SIGNALS = 2
    MAX_ACTIVE = 12

    qualified = {}
    for coin, data in coins_data.items():
        sharpe = data.get("best_sharpe", 0)
        n_signals = len(data.get("signals", []))
        if sharpe >= MIN_SHARPE_THRESHOLD and n_signals >= MIN_SIGNALS:
            qualified[coin] = sharpe

    if not qualified:
        # Fallback: take top ACTIVE_COINS_COUNT by sharpe regardless
        scores = {coin: d.get("best_sharpe", 0) for coin, d in coins_data.items()}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:ACTIVE_COINS_COUNT]
        total = sum(s for _, s in top)
        if total == 0:
            return {coin: 1 / len(top) for coin, _ in top}
        return {coin: round(s / total, 4) for coin, s in top}

    # Cap at MAX_ACTIVE, take highest Sharpe
    top_coins = sorted(qualified.items(), key=lambda x: x[1], reverse=True)[:MAX_ACTIVE]
    total = sum(s for _, s in top_coins)
    log(f"  Active coins: {len(top_coins)} qualified (Sharpe≥{MIN_SHARPE_THRESHOLD}, signals≥{MIN_SIGNALS})")
    return {coin: round(s / total, 4) for coin, s in top_coins}


def build_allocation(api_key: str, coins_data: dict) -> dict:
    """Get allocation weights. API first, scoring fallback."""
    alloc = optimize_from_api(api_key)
    if alloc is None:
        log("  /paid/portfolio/optimize requires payment — using sharpe-weighted allocation")
        alloc = optimize_from_scores(coins_data)
    else:
        log(f"  Got allocation from API: {len(alloc)} coins")

    # Normalize to active coins count
    sorted_coins = sorted(alloc.items(), key=lambda x: x[1], reverse=True)[:ACTIVE_COINS_COUNT]
    total = sum(w for _, w in sorted_coins)
    normalized = {coin: round(w / total, 4) for coin, w in sorted_coins} if total > 0 else {}
    return normalized


# ─── MAIN CYCLE ───────────────────────────────────────────────────────────────

def run_once(api_key: str):
    """Run one strategy assembly + portfolio optimization cycle."""
    log("=== Strategy refresh cycle ===")
    t0 = time.time()

    # Assemble strategies
    coins_data = assemble_strategies(api_key)
    if not coins_data:
        log("ERROR: No coins assembled — check signals cache")
        return

    # Portfolio allocation
    allocation = build_allocation(api_key, coins_data)
    active_coins = list(allocation.keys())
    log(f"  Active coins ({len(active_coins)}): {active_coins}")
    for coin, w in allocation.items():
        best = coins_data[coin]["best_sharpe"]
        nsig = coins_data[coin]["signal_count"]
        log(f"    {coin}: weight={w:.2%}  best_sharpe={best:.2f}  signals={nsig}")

    # Save strategies.json (only active coins with full signal data)
    strategies = {
        "updated_at":    now_iso(),
        "version":       STRATEGY_VERSION,
        "active_coins":  active_coins,
        "coins":         {coin: coins_data[coin] for coin in active_coins if coin in coins_data},
    }
    save_json(STRATEGIES_FILE, strategies)
    log(f"  Saved {STRATEGIES_FILE}")

    # Save allocation.json
    alloc_doc = {
        "updated_at":  now_iso(),
        "version":     STRATEGY_VERSION,
        "allocations": allocation,
    }
    save_json(ALLOCATION_FILE, alloc_doc)
    log(f"  Saved {ALLOCATION_FILE}")

    update_heartbeat("strategy_manager")
    elapsed = time.time() - t0
    log(f"=== Done in {elapsed:.1f}s — next refresh in {STRATEGY_REFRESH_HOURS}h ===")


def main():
    loop = "--loop" in sys.argv
    api_key = get_env("ENVY_API_KEY")
    if not api_key:
        log("FATAL: ENVY_API_KEY not set")
        sys.exit(1)

    log("=== V6 Strategy Manager starting ===")
    BUS_DIR.mkdir(parents=True, exist_ok=True)

    run_once(api_key)

    if loop:
        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                run_once(api_key)
            except Exception as e:
                log(f"ERROR in cycle: {e}")


if __name__ == "__main__":
    main()

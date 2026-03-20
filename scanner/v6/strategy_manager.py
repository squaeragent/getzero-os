#!/usr/bin/env python3
"""
V6 Strategy Manager — assembles strategies from ENVY paid API.

Flow:
  1. Load signals from cache (signal packs previously fetched)
  2. Score each signal via POST /paid/signals/check (returns backtest metrics)
  3. Assemble optimal strategy per coin via POST /paid/strategy/assemble
  4. Optimize portfolio via GET /paid/portfolio/optimize
  5. Save strategies.json and allocation.json

Runs on startup and every STRATEGY_REFRESH_HOURS (default 2h).

Usage:
  python3 scanner/v6/strategy_manager.py           # single run
  python3 scanner/v6/strategy_manager.py --loop    # run every cycle
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
TOP_SIGNALS   = 10   # send up to 10 signals per coin to assemble
MAX_CACHE_AGE_HOURS = 4

# ─── FIX 2: Rolling average smoothing ────────────────────────────────────────
SHARPE_HISTORY_FILE = BUS_DIR / "sharpe_history.json"
SHARPE_HISTORY_WINDOW = 5  # average over last N runs (10h at 2h refresh)

# ─── FIX 1: Hysteresis ───────────────────────────────────────────────────────
COIN_STREAK_FILE = BUS_DIR / "coin_streaks.json"
MIN_CONSECUTIVE_RUNS = 3   # must appear in top-N for 3 consecutive runs (6h)
EXIT_SHARPE_THRESHOLD = 2.0  # only remove if Sharpe drops below this (not just rank)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [STRATEGY] {msg}", flush=True)


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

def envy_get(path: str, params: dict, api_key: str):
    """GET request to ENVY API. Returns raw response text."""
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        log(f"  GET {path} → HTTP {e.code}")
        return None
    except Exception as e:
        log(f"  GET {path} failed: {e}")
        return None


def envy_post_yaml(path: str, yaml_body: str, api_key: str, params: dict = None):
    """POST YAML to ENVY API. Returns raw response text."""
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(
        url,
        data=yaml_body.encode(),
        headers={
            "X-API-Key": api_key,
            "Content-Type": "text/yaml",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        log(f"  POST {path} → HTTP {e.code}: {body}")
        return None
    except Exception as e:
        log(f"  POST {path} failed: {e}")
        return None


def parse_yaml_simple(text: str) -> dict:
    """Minimal YAML parser for ENVY responses. Falls back to JSON."""
    if not text:
        return {}
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # Try PyYAML if available
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    except Exception:
        pass
    # Last resort: return raw text wrapped
    return {"_raw": text}


# ─── LOAD SIGNALS FROM CACHE ──────────────────────────────────────────────────

def load_cached_signals(coin: str) -> list[dict]:
    """Load signals from pack cache for a coin. Respects staleness."""
    cache_file = SIGNALS_CACHE_DIR / f"{coin}.json"
    if not cache_file.exists():
        return []

    try:
        mtime = cache_file.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        if age_hours > MAX_CACHE_AGE_HOURS:
            log(f"  {coin}: cache stale ({age_hours:.1f}h > {MAX_CACHE_AGE_HOURS}h)")
            return []
    except OSError:
        pass

    try:
        with open(cache_file) as f:
            packs = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    signals = []
    for p in packs:
        sig_type = p.get("signal_type", p.get("_direction", "")).upper()
        direction = "LONG" if "LONG" in sig_type or sig_type == "LONG" else "SHORT"
        sharpe    = float(p.get("sharpe", 0))
        win_rate  = float(p.get("win_rate", 0))
        expression = p.get("expression", "")
        exit_expr  = p.get("exit_expression", "")
        name       = p.get("name", "")

        if not expression or not name:
            continue
        if sharpe < MIN_SHARPE or win_rate < MIN_WIN_RATE:
            continue

        signals.append({
            "name":            name,
            "signal_type":     sig_type if sig_type else direction,
            "direction":       direction,
            "expression":      expression,
            "exit_expression": exit_expr,
            "max_hold_hours":  max(1, int(p.get("max_hold_hours", 24))),  # API requires > 0
            "sharpe":          sharpe,
            "win_rate":        win_rate,
            "trade_count":     p.get("trade_count", 0),
            "composite_score": float(p.get("composite_score", sharpe)),
            "stop_loss_pct":   p.get("stop_loss_pct", 0.05),
        })

    signals.sort(key=lambda s: s["composite_score"], reverse=True)
    return signals[:TOP_SIGNALS]


# ─── SCORE SIGNALS VIA API ────────────────────────────────────────────────────

def score_signals(coin: str, signals: list[dict], api_key: str) -> list[dict]:
    """Score signals via POST /paid/signals/check.
    Updates each signal's metrics with fresh backtest data.
    Returns only signals that aren't Discarded.
    """
    scored = []

    for sig in signals:
        yaml_body = (
            f"coin: {coin}\n"
            f"signals:\n"
            f"  - name: {sig['name']}\n"
            f"    signal_type: {sig.get('signal_type', sig['direction'])}\n"
            f'    expression: "{sig["expression"]}"\n'
            f'    exit_expression: "{sig.get("exit_expression", "")}"\n'
            f"    max_hold_hours: {sig.get('max_hold_hours', 24)}\n"
            f"    source: zero_os\n"
        )

        resp = envy_post_yaml("/paid/signals/check", yaml_body, api_key)
        if not resp:
            # API failed — keep original metrics
            scored.append(sig)
            continue

        data = parse_yaml_simple(resp)
        metrics = data.get("metrics", {})
        rarity  = metrics.get("rarity", "Unknown")

        if rarity == "Discarded":
            log(f"    {sig['name'][:40]}: DISCARDED (Sharpe={metrics.get('sharpe', '?')})")
            continue

        # Update signal with fresh API metrics
        sig["sharpe"]          = float(metrics.get("sharpe", sig["sharpe"]))
        sig["win_rate"]        = float(metrics.get("win_rate", sig["win_rate"]))
        sig["trade_count"]     = int(metrics.get("trade_count", sig.get("trade_count", 0)))
        sig["composite_score"] = float(metrics.get("composite_score", sig.get("composite_score", 0)))
        sig["rarity"]          = rarity
        sig["signal_id"]       = data.get("signal_id", "")
        sig["max_drawdown"]    = float(metrics.get("max_drawdown", 0))
        sig["expectancy"]      = float(metrics.get("expectancy", 0))
        sig["overfit_score"]   = float(metrics.get("overfit_score", 0))

        scored.append(sig)
        time.sleep(0.3)  # rate limit

    return scored


# ─── ASSEMBLE STRATEGY VIA API ────────────────────────────────────────────────

def assemble_strategy(coin: str, signals: list[dict], api_key: str) -> list[dict]:
    """POST scored signals to /paid/strategy/assemble for tournament optimization.
    Returns ordered signal list (priority order) with backtest metrics.
    Falls back to input signals sorted by composite_score if API fails.
    """
    if not signals:
        return []

    # Build YAML body
    yaml_lines = [f"coin: {coin}", "signals:"]
    for sig in signals:
        yaml_lines.append(f"  - name: {sig['name']}")
        yaml_lines.append(f"    signal_type: {sig.get('signal_type', sig['direction'])}")
        yaml_lines.append(f'    expression: "{sig["expression"]}"')
        yaml_lines.append(f'    exit_expression: "{sig.get("exit_expression", "")}"')
        yaml_lines.append(f"    max_hold_hours: {sig.get('max_hold_hours', 24)}")
    yaml_body = "\n".join(yaml_lines)

    resp = envy_post_yaml("/paid/strategy/assemble", yaml_body, api_key, {"mode": "normal"})
    if not resp:
        log(f"    {coin}: assemble API failed — using local sort")
        signals.sort(key=lambda s: s.get("composite_score", 0), reverse=True)
        for i, s in enumerate(signals):
            s["priority"] = i + 1
        return signals

    data = parse_yaml_simple(resp)

    # Parse assembled strategy
    assembled_signals = data.get("signals", data.get("strategy", {}).get("signals", []))
    if not assembled_signals:
        log(f"    {coin}: assemble returned empty — using local sort")
        signals.sort(key=lambda s: s.get("composite_score", 0), reverse=True)
        for i, s in enumerate(signals):
            s["priority"] = i + 1
        return signals

    # Map assembled results back, preserving expressions
    result = []
    sig_map = {s["name"]: s for s in signals}
    for i, a in enumerate(assembled_signals):
        name = a.get("name", "")
        base = sig_map.get(name, {})
        merged = {**base, **{
            "name":            name,
            "priority":        i + 1,
            "sharpe":          float(a.get("sharpe", base.get("sharpe", 0))),
            "win_rate":        float(a.get("win_rate", base.get("win_rate", 0))),
            "trade_count":     int(a.get("trade_count", base.get("trade_count", 0))),
            "composite_score": float(a.get("composite_score", base.get("composite_score", 0))),
            "direction":       base.get("direction", "LONG"),
            "expression":      base.get("expression", a.get("expression", "")),
            "exit_expression": base.get("exit_expression", a.get("exit_expression", "")),
            "max_hold_hours":  base.get("max_hold_hours", a.get("max_hold_hours", 24)),
            "stop_loss_pct":   base.get("stop_loss_pct", 0.05),
        }}
        result.append(merged)

    # Strategy-level metrics
    strategy_metrics = data.get("strategy_metrics", data.get("backtest", {}))
    if strategy_metrics:
        log(f"    {coin}: assembled {len(result)} signals — "
            f"Sharpe={strategy_metrics.get('sharpe', '?')}, "
            f"WR={strategy_metrics.get('win_rate', '?')}%, "
            f"Return={strategy_metrics.get('total_return', '?')}%")

    return result


# ─── PORTFOLIO OPTIMIZATION VIA API ──────────────────────────────────────────

def optimize_portfolio(coins_with_signals: list[str], api_key: str) -> dict:
    """GET /paid/portfolio/optimize for correlation-based allocation.
    Falls back to sharpe-weighted allocation if API fails.
    """
    if not coins_with_signals:
        return {}

    # Use top 3 coins by signal quality as "existing", optimize to find best additions
    existing = ",".join(coins_with_signals[:3])
    count = min(12, len(coins_with_signals))

    resp = envy_get(
        "/paid/portfolio/optimize",
        {"existing": existing, "count": str(count), "mode": "normal", "allocation": "weighted"},
        api_key,
    )

    if not resp:
        log("  Portfolio API failed — using local allocation")
        return None

    data = parse_yaml_simple(resp)
    coins_list = data.get("coins", [])
    if not coins_list:
        log("  Portfolio API returned no coins — using local allocation")
        return None

    result = {}
    for c in coins_list:
        coin = c.get("coin", "")
        alloc_pct = float(c.get("allocation_pct", 0))
        if coin and alloc_pct > 0:
            result[coin] = alloc_pct / 100.0  # convert pct to fraction

    if result:
        sharpe = data.get("portfolio_stats", {}).get("expected_sharpe", "?")
        corr   = data.get("portfolio_stats", {}).get("avg_correlation", "?")
        log(f"  Portfolio optimized: {len(result)} coins, Sharpe={sharpe}, avgCorr={corr}")

    return result if result else None


# ─── FIX 2: ROLLING SHARPE AVERAGE ────────────────────────────────────────────

def update_sharpe_history(coins_data: dict) -> dict:
    """Record this run's Sharpes and return smoothed averages.
    
    Each run's assembled Sharpe per coin gets appended to history.
    Returns dict of coin → smoothed_sharpe (average over last N runs).
    This eliminates tournament optimization variance (BTC 4.34 → 1.43).
    """
    history = load_json(SHARPE_HISTORY_FILE, {"runs": []})
    runs = history.get("runs", [])
    
    # Record this run
    this_run = {
        "ts": now_iso(),
        "sharpes": {coin: data.get("best_sharpe", 0) for coin, data in coins_data.items()},
    }
    runs.append(this_run)
    
    # Keep only last N runs
    runs = runs[-SHARPE_HISTORY_WINDOW:]
    history["runs"] = runs
    save_json(SHARPE_HISTORY_FILE, history)
    
    # Compute smoothed averages
    smoothed = {}
    all_coins = set()
    for run in runs:
        all_coins.update(run["sharpes"].keys())
    
    for coin in all_coins:
        values = [run["sharpes"].get(coin, 0) for run in runs if coin in run["sharpes"]]
        if values:
            smoothed[coin] = sum(values) / len(values)
    
    n_runs = len(runs)
    log(f"  Sharpe smoothing: {n_runs} runs averaged (window={SHARPE_HISTORY_WINDOW})")
    
    return smoothed


# ─── FIX 1: HYSTERESIS ───────────────────────────────────────────────────────

def apply_hysteresis(candidate_coins: list[str], smoothed_sharpes: dict) -> list[str]:
    """Prevent constant portfolio rotation.
    
    - A coin must appear in top-N for MIN_CONSECUTIVE_RUNS before entering.
    - A coin only exits when its smoothed Sharpe drops below EXIT_SHARPE_THRESHOLD.
    - This creates stability: coins that are consistently good get in,
      coins that have one bad run don't get kicked.
    """
    streaks = load_json(COIN_STREAK_FILE, {})
    
    # Update streaks: increment coins that appear, reset coins that don't
    candidate_set = set(candidate_coins)
    for coin in candidate_set:
        streaks[coin] = streaks.get(coin, 0) + 1
    
    for coin in list(streaks.keys()):
        if coin not in candidate_set:
            # Don't reset immediately — check if Sharpe is still above threshold
            if smoothed_sharpes.get(coin, 0) >= EXIT_SHARPE_THRESHOLD:
                # Keep the streak but don't increment — coin is still viable
                pass
            else:
                # Sharpe dropped below threshold — reset streak
                streaks[coin] = 0
    
    # Clean up zeros
    streaks = {c: s for c, s in streaks.items() if s > 0}
    save_json(COIN_STREAK_FILE, streaks)
    
    # Select: coins with enough consecutive appearances AND above exit threshold
    stable_coins = []
    for coin in candidate_coins:
        streak = streaks.get(coin, 0)
        sharpe = smoothed_sharpes.get(coin, 0)
        
        if streak >= MIN_CONSECUTIVE_RUNS:
            stable_coins.append(coin)
        elif streak > 0:
            log(f"    {coin}: streak={streak}/{MIN_CONSECUTIVE_RUNS} — waiting (Sharpe={sharpe:.2f})")
    
    # Also keep currently-held coins if their Sharpe is still above exit threshold
    # (Fix 3: don't close profitable positions just because rank dropped)
    prev_alloc = load_json(ALLOCATION_FILE, {}).get("allocations", {})
    for coin in prev_alloc:
        if coin not in stable_coins and smoothed_sharpes.get(coin, 0) >= EXIT_SHARPE_THRESHOLD:
            stable_coins.append(coin)
            log(f"    {coin}: retained (active position, Sharpe={smoothed_sharpes.get(coin, 0):.2f} ≥ {EXIT_SHARPE_THRESHOLD})")
    
    # Deduplicate while preserving order
    seen = set()
    result = []
    for c in stable_coins:
        if c not in seen:
            seen.add(c)
            result.append(c)
    
    # Cap at MAX_ACTIVE (8) — sort retained coins by smoothed Sharpe, keep best
    MAX_ACTIVE = 8
    if len(result) > MAX_ACTIVE:
        # Sort by smoothed Sharpe, keep top MAX_ACTIVE
        result.sort(key=lambda c: smoothed_sharpes.get(c, 0), reverse=True)
        dropped = result[MAX_ACTIVE:]
        result = result[:MAX_ACTIVE]
        for c in dropped:
            log(f"    {c}: dropped (rank>{MAX_ACTIVE}, Sharpe={smoothed_sharpes.get(c, 0):.2f})")
    
    log(f"  Hysteresis: {len(candidate_coins)} candidates → {len(result)} stable (min streak={MIN_CONSECUTIVE_RUNS})")
    return result


def local_allocation(coins_data: dict) -> dict:
    """Sharpe-weighted allocation — concentrated on top signals.
    
    Philosophy: $750 account needs to prove alpha, not minimize risk.
    Concentrate on highest-Sharpe coins. Sharpe^2 weighting rewards
    the best signals disproportionately.
    """
    MIN_SHARPE_THRESHOLD = 2.0   # only trade coins with Sharpe 2.0+
    MIN_SIGNALS = 2
    MAX_ACTIVE = 8               # concentrated: 8 coins max (was 12)

    qualified = {}
    for coin, data in coins_data.items():
        sharpe = data.get("best_sharpe", 0)
        n_signals = len(data.get("signals", []))
        if sharpe >= MIN_SHARPE_THRESHOLD and n_signals >= MIN_SIGNALS:
            qualified[coin] = sharpe

    if not qualified:
        # Nothing above 2.0 — relax to 1.5
        for coin, data in coins_data.items():
            sharpe = data.get("best_sharpe", 0)
            n_signals = len(data.get("signals", []))
            if sharpe >= 1.5 and n_signals >= MIN_SIGNALS:
                qualified[coin] = sharpe

    if not qualified:
        scores = {coin: d.get("best_sharpe", 0) for coin, d in coins_data.items()}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:MAX_ACTIVE]
        total = sum(s for _, s in top)
        if total == 0:
            return {coin: 1 / len(top) for coin, _ in top}
        return {coin: round(s / total, 4) for coin, s in top}

    # Sharpe^2 weighting — concentrates on the best
    top_coins = sorted(qualified.items(), key=lambda x: x[1], reverse=True)[:MAX_ACTIVE]
    total = sum(s ** 2 for _, s in top_coins)
    log(f"  Concentrated allocation: {len(top_coins)} coins (Sharpe≥{MIN_SHARPE_THRESHOLD}, Sharpe² weighted)")
    for coin, s in top_coins:
        w = s ** 2 / total
        log(f"    {coin}: Sharpe={s:.2f} → weight={w:.1%}")
    return {coin: round(s ** 2 / total, 4) for coin, s in top_coins}


# ─── MAIN CYCLE ───────────────────────────────────────────────────────────────

def run_once(api_key: str):
    """Run one full strategy cycle: load → score → assemble → optimize."""
    log("=== Strategy refresh cycle ===")
    t0 = time.time()

    # Phase 1: Load and score signals for each coin
    log("Phase 1: Loading and scoring signals...")
    coins_data = {}
    coins_with_signals = []

    for i, coin in enumerate(ALL_COINS):
        raw_signals = load_cached_signals(coin)
        if not raw_signals:
            continue

        # Score via API
        scored = score_signals(coin, raw_signals, api_key)
        if not scored:
            log(f"  {coin}: all signals discarded")
            continue

        log(f"  {coin}: {len(scored)} signals scored (from {len(raw_signals)} cached)")
        coins_with_signals.append(coin)
        coins_data[coin] = {"scored_signals": scored}

        if (i + 1) % 10 == 0:
            log(f"  Progress: {i+1}/{len(ALL_COINS)} coins...")

    if not coins_data:
        log("ERROR: No coins with valid signals — check cache freshness")
        update_heartbeat("strategy_manager")
        return

    log(f"Phase 1 complete: {len(coins_data)} coins with signals")

    # Phase 2: Assemble optimal strategy per coin
    log("Phase 2: Assembling strategies...")
    for coin in list(coins_data.keys()):
        scored = coins_data[coin]["scored_signals"]
        assembled = assemble_strategy(coin, scored, api_key)
        if assembled:
            best_sharpe = max(s.get("sharpe", 0) for s in assembled)
            avg_wr = sum(s.get("win_rate", 0) for s in assembled) / len(assembled)
            coins_data[coin] = {
                "signals":      assembled,
                "best_sharpe":  best_sharpe,
                "avg_win_rate": avg_wr,
                "signal_count": len(assembled),
            }
        else:
            del coins_data[coin]
            coins_with_signals.remove(coin)
        time.sleep(0.5)  # rate limit between coins

    log(f"Phase 2 complete: {len(coins_data)} coins with assembled strategies")

    # Phase 3: Portfolio allocation with stability
    log("Phase 3: Concentrated allocation with stability filters...")
    
    # Fix 2: Record this run and compute smoothed Sharpes
    smoothed = update_sharpe_history(coins_data)
    
    # Override best_sharpe with smoothed values for allocation
    for coin in coins_data:
        if coin in smoothed:
            raw = coins_data[coin].get("best_sharpe", 0)
            sm = smoothed[coin]
            if abs(raw - sm) > 0.5:
                log(f"    {coin}: raw={raw:.2f} → smoothed={sm:.2f} (Δ={raw-sm:+.2f})")
            coins_data[coin]["best_sharpe"] = sm
    
    # Get candidate top coins (before hysteresis)
    candidates = sorted(
        [c for c, d in coins_data.items() if d.get("best_sharpe", 0) >= 2.0],
        key=lambda c: coins_data[c].get("best_sharpe", 0),
        reverse=True,
    )[:12]  # wider candidate pool for hysteresis to filter
    
    # Fix 1: Apply hysteresis — only trade coins that consistently rank high
    stable_coins = apply_hysteresis(candidates, smoothed)
    
    # Filter coins_data to only stable coins for allocation
    stable_data = {c: coins_data[c] for c in stable_coins if c in coins_data}
    
    if not stable_data:
        log("  WARNING: No stable coins after hysteresis — using raw candidates")
        stable_data = {c: coins_data[c] for c in candidates[:8] if c in coins_data}
    
    allocation = local_allocation(stable_data)

    # Ensure only coins with signals get allocation
    allocation = {c: w for c, w in allocation.items() if c in coins_data}
    if not allocation:
        # If portfolio optimizer returned coins we don't have signals for,
        # fall back to local
        allocation = local_allocation(coins_data)

    # Normalize
    total = sum(allocation.values())
    if total > 0:
        allocation = {c: round(w / total, 4) for c, w in allocation.items()}

    active_coins = list(allocation.keys())
    log(f"  Active coins ({len(active_coins)}): {active_coins}")
    for coin, w in allocation.items():
        cd = coins_data.get(coin, {})
        best = cd.get("best_sharpe", 0)
        nsig = cd.get("signal_count", 0)
        log(f"    {coin}: weight={w:.2%}  sharpe={best:.2f}  signals={nsig}")

    # Save strategies.json
    strategies = {
        "updated_at":    now_iso(),
        "version":       STRATEGY_VERSION,
        "source":        "envy_api",  # vs "cache_only" in old version
        "active_coins":  active_coins,
        "coins":         {c: coins_data[c] for c in active_coins if c in coins_data},
    }
    save_json(STRATEGIES_FILE, strategies)
    log(f"  Saved {STRATEGIES_FILE}")

    # Save allocation.json
    alloc_doc = {
        "updated_at":  now_iso(),
        "version":     STRATEGY_VERSION,
        "source":      "envy_portfolio_optimize",
        "allocations": allocation,
    }
    save_json(ALLOCATION_FILE, alloc_doc)
    log(f"  Saved {ALLOCATION_FILE}")

    update_heartbeat("strategy_manager")
    elapsed = time.time() - t0
    log(f"=== Done in {elapsed:.1f}s — {len(active_coins)} active coins, next refresh in {STRATEGY_REFRESH_HOURS}h ===")


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

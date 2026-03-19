#!/usr/bin/env python3
"""
ZERO OS — Agent 3: Correlation Agent
Prevents correlated bets. Detects divergences. Manages portfolio-level
directional exposure.

Inputs:
  scanner/bus/candidates.json      — trade candidates from Agent 2
  scanner/data/fires.jsonl         — fallback fire signals (pre-Agent 2)
  scanner/data/live/positions.json — current live positions
  scanner/bus/regimes.json         — regime state from Agent 1
  Envy API: ROC_3H, ROC_6H, ROC_12H for correlation measurement

Outputs:
  scanner/bus/approved.json        — approved + blocked trades with reasons
  scanner/bus/heartbeat.json       — last-alive timestamp

Usage:
  python3 scanner/agents/correlation_agent.py           # single run
  python3 scanner/agents/correlation_agent.py --loop    # continuous 5-min cycle
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
LIVE_DIR = DATA_DIR / "live"

CANDIDATES_FILE = BUS_DIR / "candidates.json"
REGIMES_FILE = BUS_DIR / "regimes.json"
APPROVED_FILE = BUS_DIR / "approved.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
FIRES_FILE = DATA_DIR / "fires.jsonl"
POSITIONS_FILE = LIVE_DIR / "positions.json"

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
CYCLE_SECONDS = 300  # 5 minutes
COINS_PER_REQUEST = 10

ROC_INDICATORS = ["ROC_3H", "ROC_6H", "ROC_12H"]

# ─── CORRELATION GROUPS ───
CORRELATION_GROUPS = {
    "majors": ["BTC", "ETH", "SOL"],
    "l1_alts": ["AVAX", "NEAR", "SUI", "ARB", "APT", "SEI", "TIA"],
    "l1_legacy": ["ADA", "DOT", "LTC", "XRP", "BCH", "TRX", "ZEC"],
    "defi": ["LINK", "UNI", "AAVE", "CRV", "LDO", "JUP", "ONDO"],
    "meme": ["DOGE", "FARTCOIN", "PUMP", "TRUMP", "WLD"],
    "infra": ["OP", "BNB", "INJ", "ENA", "HYPE", "TON"],
    "stable_hedge": ["PAXG"],
}

# Reverse lookup: coin -> group name
COIN_TO_GROUP = {}
for group, coins in CORRELATION_GROUPS.items():
    for coin in coins:
        COIN_TO_GROUP[coin] = group

# ─── PORTFOLIO RULES ───
MAX_NET_EXPOSURE_PCT = 80  # max % capital in one direction
MAX_PER_GROUP = 2  # max positions in same correlation group
CORRELATION_BLOCK_THRESHOLD = 0.7  # block if correlation > this


# ─── API ───
def load_api_key():
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("ENVY_API_KEY="):
                val = line.split("=", 1)[1]
                return val.strip().strip('"').strip("'")
    raise RuntimeError("ENVY_API_KEY not found in ~/.config/openclaw/.env")


def api_get(path, params, api_key):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_roc_indicators(coins, api_key):
    """Fetch ROC indicators for a list of coins. Batches by 10."""
    all_data = {}
    indicators_param = ",".join(ROC_INDICATORS)

    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i:i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = api_get(
                "/paid/indicators/snapshot",
                {"coins": coins_param, "indicators": indicators_param},
                api_key,
            )
            for coin, ind_list in resp.get("snapshot", {}).items():
                if not isinstance(ind_list, list):
                    continue
                values = {}
                for ind in ind_list:
                    values[ind["indicatorCode"]] = ind["value"]
                all_data[coin] = values
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            print(f"  [warn] ROC batch {batch[0]}-{batch[-1]} failed: {e}")
            for coin in batch:
                try:
                    resp = api_get(
                        "/paid/indicators/snapshot",
                        {"coins": coin, "indicators": indicators_param},
                        api_key,
                    )
                    for c, ind_list in resp.get("snapshot", {}).items():
                        if isinstance(ind_list, list):
                            values = {}
                            for ind in ind_list:
                                values[ind["indicatorCode"]] = ind["value"]
                            all_data[c] = values
                except Exception:
                    pass
                time.sleep(0.1)

        if i + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)

    return all_data


# ─── CORRELATION ───
def compute_correlation(coin_a, coin_b, roc_data):
    """
    Compute correlation between two coins using ROC indicators.
    Uses sign agreement + magnitude similarity across multiple timeframes.
    Returns value in [-1, 1]. Positive = correlated, negative = anti-correlated.
    """
    roc_a = roc_data.get(coin_a, {})
    roc_b = roc_data.get(coin_b, {})

    if not roc_a or not roc_b:
        # No data — fall back to group-based heuristic
        group_a = COIN_TO_GROUP.get(coin_a)
        group_b = COIN_TO_GROUP.get(coin_b)
        if group_a and group_b and group_a == group_b:
            return 0.75  # same group → assume correlated
        return 0.0

    correlations = []
    for window in ROC_INDICATORS:
        val_a = roc_a.get(window)
        val_b = roc_b.get(window)
        if val_a is None or val_b is None:
            continue

        same_sign = (val_a > 0) == (val_b > 0)
        max_abs = max(abs(val_a), abs(val_b), 0.001)
        mag_ratio = min(abs(val_a), abs(val_b)) / max_abs
        correlations.append(mag_ratio if same_sign else -mag_ratio)

    if not correlations:
        return 0.0

    return sum(correlations) / len(correlations)


# ─── LOAD STATE ───
def load_positions():
    if POSITIONS_FILE.exists() and POSITIONS_FILE.stat().st_size > 0:
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def load_regimes():
    if REGIMES_FILE.exists() and REGIMES_FILE.stat().st_size > 0:
        try:
            with open(REGIMES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_candidates():
    """Load candidates from Agent 2 bus file. Falls back to fires.jsonl.

    Adversary wiring: only pass through candidates that have been evaluated by
    the adversary. Candidates with no adversary_verdict are skipped (the
    adversary hasn't run yet). Candidates with verdict KILLED are dropped.
    WEAK candidates are passed through with a 0.4x size modifier applied.
    """
    if CANDIDATES_FILE.exists() and CANDIDATES_FILE.stat().st_size > 0:
        try:
            with open(CANDIDATES_FILE) as f:
                data = json.load(f)
            raw_candidates = data.get("candidates", [])
            adversary_ts = data.get("adversary_timestamp")

            if raw_candidates:
                if not adversary_ts:
                    # Adversary has never run — skip all candidates to avoid race
                    print("  [warn] candidates.json has no adversary_timestamp — awaiting adversary, skipping all")
                    return []

                approved_candidates = []
                for cand in raw_candidates:
                    verdict = cand.get("adversary_verdict")

                    if verdict is None:
                        # Adversary hasn't evaluated this candidate yet
                        print(f"  [skip] {cand.get('coin')} — awaiting adversary verdict")
                        continue

                    if verdict == "KILLED":
                        print(f"  [skip] {cand.get('coin')} — adversary verdict KILLED")
                        continue

                    if verdict in ("PROCEED", "PROCEED_WITH_CAUTION", "WEAK"):
                        if verdict == "WEAK":
                            # Apply adversary size modifier (0.4x) for weak signals
                            size_mod = cand.get("recommended_size_modifier") or cand.get("adversary_size_modifier") or 0.4
                            cand = dict(cand)
                            cand["size_modifier"] = size_mod
                            print(f"  [weak] {cand.get('coin')} — adversary verdict WEAK, size_modifier={size_mod}")
                        approved_candidates.append(cand)
                    # Any unknown verdict: skip conservatively

                print(f"  Loaded {len(approved_candidates)}/{len(raw_candidates)} candidates after adversary filter")
                return approved_candidates
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: read recent fires from fires.jsonl
    return load_fires_as_candidates()


def load_fires_as_candidates():
    """Convert recent fires into candidate format for pre-Agent 2 operation."""
    if not FIRES_FILE.exists():
        return []

    fires = []
    try:
        with open(FIRES_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        fires.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []

    if not fires:
        return []

    # Deduplicate: keep latest fire per coin+direction
    seen = {}
    for fire in fires:
        key = f"{fire.get('coin')}_{fire.get('direction')}"
        seen[key] = fire

    candidates = []
    for fire in seen.values():
        cand = {
            "coin": fire.get("coin"),
            "direction": fire.get("direction"),
            "signal": fire.get("signal"),
            "sharpe": fire.get("sharpe", 0),
            "win_rate": fire.get("win_rate", 0),
            "source": "fires.jsonl",
        }
        # Pass through all signal metadata
        for key in ("exit_expression", "max_hold_hours", "regime", "regime_match",
                     "regime_match_score", "signal_heat", "composite_score", "recent_record"):
            if key in fire:
                cand[key] = fire[key]
        candidates.append(cand)

    print(f"  Loaded {len(candidates)} candidates from fires.jsonl (fallback)")
    return candidates


# ─── PORTFOLIO ANALYSIS ───
def compute_portfolio_state(positions, capital=115):
    """Compute current portfolio directional exposure."""
    long_usd = 0
    short_usd = 0
    group_counts = {}

    for pos in positions:
        direction = pos.get("direction", "").upper()
        size = pos.get("size_usd", 0)
        coin = pos.get("coin", "")
        group = COIN_TO_GROUP.get(coin, "unknown")

        if direction == "LONG":
            long_usd += size
        elif direction == "SHORT":
            short_usd += size

        group_counts[group] = group_counts.get(group, 0) + 1

    net_usd = long_usd - short_usd
    net_direction = "LONG" if net_usd > 0 else ("SHORT" if net_usd < 0 else "NEUTRAL")
    net_exposure_pct = round(abs(net_usd) / capital * 100, 1) if capital > 0 else 0

    # Diversification: 1.0 = perfectly spread, 0.0 = concentrated in one group
    n_positions = len(positions)
    if n_positions <= 1:
        diversification = 0.0 if n_positions == 0 else 0.5
    else:
        n_groups = len(group_counts)
        diversification = round(min(1.0, n_groups / n_positions), 2)

    return {
        "net_direction": net_direction,
        "net_exposure_pct": net_exposure_pct,
        "long_usd": round(long_usd, 2),
        "short_usd": round(short_usd, 2),
        "group_counts": group_counts,
        "diversification_score": diversification,
    }


def would_exceed_exposure(positions, candidate, capital=750):
    """Check if adding candidate would exceed net exposure cap."""
    direction = candidate.get("direction", "").upper()
    # Estimate size: use recommended_size_pct or default to ~$30
    size_est = candidate.get("size_usd", 30)

    long_usd = sum(p.get("size_usd", 0) for p in positions if p.get("direction", "").upper() == "LONG")
    short_usd = sum(p.get("size_usd", 0) for p in positions if p.get("direction", "").upper() == "SHORT")

    if direction == "LONG":
        long_usd += size_est
    else:
        short_usd += size_est

    net_exposure_pct = abs(long_usd - short_usd) / capital * 100 if capital > 0 else 0
    return net_exposure_pct > MAX_NET_EXPOSURE_PCT


def count_group_positions(positions, group):
    """Count how many current positions are in the given correlation group."""
    group_coins = set(CORRELATION_GROUPS.get(group, []))
    return sum(1 for p in positions if p.get("coin") in group_coins)


# ─── FILTER CANDIDATES ───
def filter_candidates(candidates, positions, roc_data, regimes):
    """Apply correlation and portfolio rules to candidates. Returns (approved, blocked)."""
    approved = []
    blocked = []
    regime_coins = regimes.get("coins", {})

    # Coins already in portfolio
    position_coins = {p.get("coin") for p in positions}

    # Sort candidates by composite_score descending — best signals get priority in concentration limits
    sorted_candidates = sorted(candidates, key=lambda c: c.get("composite_score", 0), reverse=True)

    # Count signal usage in open positions (for signal concentration limit)
    signal_counts_open = {}
    for pos in positions:
        sig = pos.get("signal", "")
        if sig:
            signal_counts_open[sig] = signal_counts_open.get(sig, 0) + 1

    # Track signal counts approved in this batch
    signal_counts_batch = {}

    MAX_SIGNAL_POSITIONS = 2

    for cand in sorted_candidates:
        coin = cand.get("coin")
        direction = cand.get("direction", "").upper()
        signal = cand.get("signal", "")
        reasons = []

        # ── Rule: skip coins already in portfolio ──
        if coin in position_coins:
            blocked.append({
                "coin": coin, "direction": direction, "signal": signal,
                "reason": f"already have position in {coin}",
            })
            continue

        # ── Rule: signal concentration limit (open positions) ──
        if signal:
            open_count = signal_counts_open.get(signal, 0)
            if open_count >= MAX_SIGNAL_POSITIONS:
                print(f"  Signal concentration: {signal} already has {open_count} positions, blocking {coin}")
                blocked.append({
                    "coin": coin, "direction": direction, "signal": signal,
                    "reason": f"signal concentration: {signal} already has {open_count} open positions (max {MAX_SIGNAL_POSITIONS})",
                })
                continue

        # ── Rule: signal concentration limit (this batch) ──
        if signal:
            batch_count = signal_counts_batch.get(signal, 0)
            if batch_count >= MAX_SIGNAL_POSITIONS:
                print(f"  Signal concentration: {signal} already has {batch_count} approved in this batch, blocking {coin}")
                blocked.append({
                    "coin": coin, "direction": direction, "signal": signal,
                    "reason": f"signal concentration: {signal} already approved {batch_count} in this batch (max {MAX_SIGNAL_POSITIONS})",
                })
                continue

        # ── Rule: chaotic regime block ──
        coin_regime = regime_coins.get(coin, {})
        if coin_regime.get("regime") == "chaotic":
            reasons.append(f"regime is chaotic (confidence {coin_regime.get('confidence', 0):.2f})")

        # ── Rule: correlation group limit ──
        group = COIN_TO_GROUP.get(coin, "unknown")
        if group != "unknown":
            group_count = count_group_positions(positions, group)
            if group_count >= MAX_PER_GROUP:
                reasons.append(f"group '{group}' already has {group_count} positions (max {MAX_PER_GROUP})")

        # ── Rule: net exposure cap ──
        if would_exceed_exposure(positions, cand):
            reasons.append(f"would exceed {MAX_NET_EXPOSURE_PCT}% net directional exposure")

        # ── Rule: correlation with existing positions ──
        for pos in positions:
            pos_coin = pos.get("coin")
            pos_direction = pos.get("direction", "").upper()

            # Only block same-direction correlated trades
            if pos_direction != direction:
                continue

            corr = compute_correlation(coin, pos_coin, roc_data)
            if corr > CORRELATION_BLOCK_THRESHOLD:
                reasons.append(f"correlated to existing {pos_coin} {pos_direction} (r={corr:.2f})")
                break  # one correlated position is enough to block

        # ── Decide ──
        if reasons:
            blocked.append({
                "coin": coin, "direction": direction, "signal": signal,
                "reason": "; ".join(reasons),
            })
        else:
            reason_parts = []
            # Check for divergence bonus
            for pos in positions:
                pos_coin = pos.get("coin")
                pos_direction = pos.get("direction", "").upper()
                if pos_direction != direction:
                    corr = compute_correlation(coin, pos_coin, roc_data)
                    if corr < -0.3:
                        reason_parts.append(f"divergent from {pos_coin} (r={corr:.2f})")

            if not reason_parts:
                reason_parts.append("uncorrelated to portfolio")

            if coin_regime.get("transition"):
                reason_parts.append("regime transition active")

            entry = {
                "coin": coin,
                "direction": direction,
                "signal": signal,
                "sharpe": cand.get("sharpe", 0),
                "win_rate": cand.get("win_rate", 0),
                "reason": ", ".join(reason_parts),
            }
            # Pass through signal metadata for execution agent
            for key in ("exit_expression", "max_hold_hours", "regime", "regime_match",
                         "regime_match_score", "signal_heat", "composite_score", "recent_record"):
                if key in cand:
                    entry[key] = cand[key]
            approved.append(entry)
            # Track signal usage in this batch
            if signal:
                signal_counts_batch[signal] = signal_counts_batch.get(signal, 0) + 1

    return approved, blocked


# ─── OUTPUT ───
def write_approved(approved, blocked, portfolio_state):
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()

    # Determine overall correlation risk
    n_blocked_corr = sum(1 for b in blocked if "correlated to" in b.get("reason", ""))
    if n_blocked_corr >= 3:
        corr_risk = "high"
    elif n_blocked_corr >= 1:
        corr_risk = "medium"
    else:
        corr_risk = "low"

    output = {
        "timestamp": ts,
        "approved": approved,
        "blocked": blocked,
        "portfolio_state": {
            "net_direction": portfolio_state["net_direction"],
            "net_exposure_pct": portfolio_state["net_exposure_pct"],
            "correlation_risk": corr_risk,
            "diversification_score": portfolio_state["diversification_score"],
        },
    }

    with open(APPROVED_FILE, "w") as f:
        json.dump(output, f, indent=2)


def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = {}
    if HEARTBEAT_FILE.exists() and HEARTBEAT_FILE.stat().st_size > 0:
        try:
            with open(HEARTBEAT_FILE) as f:
                heartbeat = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    heartbeat["correlation"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── MAIN ───
def run_cycle(api_key):
    ts = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"Correlation Agent — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Load state
    positions = load_positions()
    regimes = load_regimes()
    candidates = load_candidates()

    print(f"  Positions: {len(positions)}")
    for p in positions:
        print(f"    {p.get('coin')} {p.get('direction')} ${p.get('size_usd', 0):.0f}")

    if not candidates:
        print("  No candidates to evaluate")
        portfolio_state = compute_portfolio_state(positions)
        write_approved([], [], portfolio_state)
        write_heartbeat()
        print(f"  Written empty approved to {APPROVED_FILE}")
        print(f"{'='*60}\n")
        return

    print(f"  Candidates: {len(candidates)}")

    # Collect all coins we need ROC data for
    all_coins = set()
    for p in positions:
        all_coins.add(p.get("coin"))
    for c in candidates:
        all_coins.add(c.get("coin"))
    all_coins.discard(None)
    all_coins = sorted(all_coins)

    # Fetch ROC indicators
    print(f"  Fetching ROC indicators for {len(all_coins)} coins...")
    roc_data = fetch_roc_indicators(all_coins, api_key)
    print(f"  Got ROC data for {len(roc_data)} coins")

    # Compute portfolio state
    portfolio_state = compute_portfolio_state(positions)
    print(f"  Portfolio: {portfolio_state['net_direction']} "
          f"exposure={portfolio_state['net_exposure_pct']}% "
          f"diversification={portfolio_state['diversification_score']}")

    # Filter
    approved, blocked_list = filter_candidates(candidates, positions, roc_data, regimes)

    # Write outputs
    write_approved(approved, blocked_list, portfolio_state)
    write_heartbeat()

    # Summary
    print(f"\n  Approved: {len(approved)}")
    for a in approved:
        print(f"    ✓ {a['coin']} {a['direction']} — {a['reason']}")

    print(f"  Blocked: {len(blocked_list)}")
    for b in blocked_list:
        print(f"    ✗ {b['coin']} {b['direction']} — {b['reason']}")

    print(f"\n  Written to {APPROVED_FILE}")
    print(f"{'='*60}\n")


def main():
    api_key = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Correlation Agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle(api_key)
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(api_key)


if __name__ == "__main__":
    main()

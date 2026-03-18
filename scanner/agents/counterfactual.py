#!/usr/bin/env python3
"""
ZERO OS — Counterfactual Learning Agent
Resolves killed hypotheses by checking what actually happened to the price.
Determines whether the adversary was correct to kill each hypothesis.

Runs every 30 minutes via supervisor.

Logic:
  For each KILLED episode with no existing counterfactual key:
  1. Parse kill timestamp from filename
  2. Require 6+ hours elapsed since kill
  3. Fetch price at kill, +6h, +24h from Hyperliquid candle API
  4. Compute counterfactual P&L for the proposed direction
  5. Determine if adversary was correct
  6. Update episode file + append to counterfactual_log.jsonl

Usage:
  python3 scanner/agents/counterfactual.py        # single run
  python3 scanner/agents/counterfactual.py --loop # continuous 1800s cycle
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
MEMORY_DIR = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"
BUS_DIR = SCANNER_DIR / "bus"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
COUNTERFACTUAL_LOG = MEMORY_DIR / "counterfactual_log.jsonl"

HL_API_URL = "https://api.hyperliquid.xyz/info"
CYCLE_SECONDS = 1800       # 30 minutes
MIN_AGE_HOURS = 6          # require 6h elapsed before resolving
FETCH_RATE_LIMIT_S = 0.5   # polite rate limit for HL API


# ─── DEFAULT WEIGHTS (mirrors adversary.py) ───
DEFAULT_WEIGHTS = {
    "similar_failure":          1.5,
    "kill_condition_proximity": 1.0,
    "portfolio_stress":         1.0,
    "confidence_vs_antithesis": 1.0,
    "regime_mismatch":          1.0,
    "funding_headwind":         1.0,
    "oi_divergence":            1.0,
    "rule_based":               1.2,
    "macro_regime":             1.2,
    "session_risk":             1.0,
}


# ─── LOGGING ───
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [COUNTERFACTUAL] {msg}")


# ─── FILE HELPERS ───
def load_json_safe(path, default=None):
    if default is None:
        default = {}
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


# ─── FILENAME PARSING ───
def parse_episode_filename(filename):
    """
    Parse hyp_YYYYMMDD_HHMMSS_COIN_DIR_SEQ.json
    Returns (kill_dt_utc, coin, direction) or None on failure.
    """
    stem = Path(filename).stem  # remove .json
    parts = stem.split("_")
    # Minimum: hyp, YYYYMMDD, HHMMSS, COIN, DIR, SEQ → 6 parts
    if len(parts) < 6:
        return None
    try:
        date_str = parts[1]  # YYYYMMDD
        time_str = parts[2]  # HHMMSS
        dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
        dt = dt.replace(tzinfo=timezone.utc)
        coin = parts[3].upper()
        direction = parts[4].upper()
        return dt, coin, direction
    except (ValueError, IndexError):
        return None


# ─── HYPERLIQUID PRICE FETCH ───
def hl_candle_snapshot(coin, start_ms, end_ms):
    """
    Fetch 1h candles from Hyperliquid for the given time window.
    Returns list of candle dicts, or [] on failure.
    """
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "1h",
            "startTime": start_ms,
            "endTime": end_ms,
        }
    }
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            HL_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log(f"  HL API error for {coin}: {e}")
        return []


def fetch_price_at_time(coin, timestamp_ms):
    """
    Get the close price of the candle closest to timestamp_ms.
    Searches ±1h window. Returns float or None.
    """
    candles = hl_candle_snapshot(coin, timestamp_ms - 3_600_000, timestamp_ms + 3_600_000)
    if not candles:
        return None

    # Candles format: [T, open, high, low, close, vol, ...]  or dict with 't','c'
    best = None
    best_dist = float("inf")

    for c in candles:
        # Handle both list and dict formats
        if isinstance(c, (list, tuple)):
            t = c[0]
            close = float(c[4])
        elif isinstance(c, dict):
            t = c.get("t", c.get("T", 0))
            close = float(c.get("c", c.get("close", 0)))
        else:
            continue
        dist = abs(t - timestamp_ms)
        if dist < best_dist:
            best_dist = dist
            best = close

    return best


# ─── KILLED EPISODE SCANNER ───
def find_killed_episodes():
    """
    Scan episodes dir for KILLED episodes that don't yet have counterfactual key.
    Returns list of (path, episode_data) tuples.
    """
    if not EPISODES_DIR.exists():
        return []

    candidates = []
    for ep_path in sorted(EPISODES_DIR.glob("hyp_*.json")):
        try:
            data = load_json_safe(ep_path, {})
        except Exception:
            continue

        adversary = data.get("adversary", {})
        if adversary.get("verdict") != "KILLED":
            continue

        if "counterfactual" in data:
            continue  # already resolved

        candidates.append((ep_path, data))

    return candidates


# ─── RESOLVE ONE EPISODE ───
def resolve_episode(ep_path, episode):
    """
    Resolve counterfactual for a single killed episode.
    Returns resolved counterfactual dict or None if not yet resolvable.
    """
    parsed = parse_episode_filename(ep_path.name)
    if not parsed:
        log(f"  [skip] Cannot parse filename: {ep_path.name}")
        return None

    kill_dt, coin, direction = parsed
    now_utc = datetime.now(timezone.utc)

    # Check age
    age_hours = (now_utc - kill_dt).total_seconds() / 3600
    if age_hours < MIN_AGE_HOURS:
        log(f"  [skip] {ep_path.name}: only {age_hours:.1f}h old, need {MIN_AGE_HOURS}h")
        return None

    kill_ms = int(kill_dt.timestamp() * 1000)
    target_6h_ms = kill_ms + 6 * 3_600_000
    target_24h_ms = kill_ms + 24 * 3_600_000

    log(f"  Resolving {ep_path.name} ({coin} {direction}, killed {age_hours:.1f}h ago)...")

    # Fetch prices
    price_at_kill = fetch_price_at_time(coin, kill_ms)
    time.sleep(FETCH_RATE_LIMIT_S)

    if price_at_kill is None:
        log(f"  [warn] Could not fetch price at kill time for {coin}")
        return None

    price_6h = None
    if age_hours >= 6:
        price_6h = fetch_price_at_time(coin, target_6h_ms)
        time.sleep(FETCH_RATE_LIMIT_S)

    price_24h = None
    if age_hours >= 24:
        price_24h = fetch_price_at_time(coin, target_24h_ms)
        time.sleep(FETCH_RATE_LIMIT_S)

    # Compute P&L
    def pnl_pct(price_entry, price_exit, dir_):
        if price_entry is None or price_exit is None or price_entry == 0:
            return None
        raw = (price_exit - price_entry) / price_entry * 100
        return round(raw if dir_ == "LONG" else -raw, 4)

    pnl_6h_pct = pnl_pct(price_at_kill, price_6h, direction)
    pnl_24h_pct = pnl_pct(price_at_kill, price_24h, direction)

    # Primary evaluation uses 6h; fall back to 24h if 6h unavailable
    primary_pnl = pnl_6h_pct if pnl_6h_pct is not None else pnl_24h_pct
    would_have_won = (primary_pnl > 0) if primary_pnl is not None else False
    adversary_correct = not would_have_won  # adversary was right if trade would have lost

    # Extract killing attacks from adversary data
    adversary_data = episode.get("adversary", {})
    all_attacks = adversary_data.get("attacks", [])
    killing_attacks = [
        {"attack": a.get("attack", ""), "severity": a.get("severity", 0.0)}
        for a in all_attacks
        if a.get("severity", 0) > 0
    ]
    dominant_attack = None
    if killing_attacks:
        dominant_attack = max(killing_attacks, key=lambda x: x["severity"])["attack"]

    counterfactual = {
        "resolved_at": now_utc.isoformat(),
        "price_at_kill": price_at_kill,
        "price_6h": price_6h,
        "price_24h": price_24h,
        "pnl_6h_pct": pnl_6h_pct,
        "pnl_24h_pct": pnl_24h_pct,
        "would_have_won": would_have_won,
        "adversary_correct": adversary_correct,
        "killing_attacks": killing_attacks,
        "dominant_attack": dominant_attack,
    }

    return counterfactual


# ─── WRITE HEARTBEAT ───
def write_heartbeat():
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    hb = load_json_safe(HEARTBEAT_FILE, {})
    hb["counterfactual"] = ts
    save_json(HEARTBEAT_FILE, hb)


# ─── MAIN RUN CYCLE ───
def run_cycle():
    ts = datetime.now(timezone.utc)
    log("=" * 60)
    log(f"Counterfactual Cycle — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    log("=" * 60)

    candidates = find_killed_episodes()
    log(f"Found {len(candidates)} unresolved killed episodes")

    if not candidates:
        write_heartbeat()
        return

    resolved = 0
    correct = 0
    false_kills = 0
    false_kill_attacks = {}

    for ep_path, episode in candidates:
        try:
            cf = resolve_episode(ep_path, episode)
        except Exception as e:
            log(f"  [error] {ep_path.name}: {e}")
            continue

        if cf is None:
            continue  # not resolvable yet

        # Update episode file
        episode["counterfactual"] = cf
        try:
            save_json(ep_path, episode)
        except OSError as e:
            log(f"  [warn] Failed to save {ep_path.name}: {e}")
            continue

        # Append to log
        log_record = {
            "timestamp": cf["resolved_at"],
            "episode_id": ep_path.name,
            "coin": episode.get("coin", ""),
            "direction": episode.get("direction", ""),
            "adversary_correct": cf["adversary_correct"],
            "would_have_won": cf["would_have_won"],
            "pnl_6h_pct": cf["pnl_6h_pct"],
            "pnl_24h_pct": cf["pnl_24h_pct"],
            "killing_attacks": cf["killing_attacks"],
            "dominant_attack": cf["dominant_attack"],
        }
        append_jsonl(COUNTERFACTUAL_LOG, log_record)

        resolved += 1
        if cf["adversary_correct"]:
            correct += 1
        else:
            false_kills += 1
            # Track which attacks caused false kills
            for atk in cf.get("killing_attacks", []):
                name = atk.get("attack", "unknown")
                false_kill_attacks[name] = false_kill_attacks.get(name, 0) + 1

        # Brief per-episode log
        pnl_str = f"{cf['pnl_6h_pct']:+.2f}%" if cf["pnl_6h_pct"] is not None else "N/A"
        verdict_str = "✓ correct" if cf["adversary_correct"] else "✗ false kill"
        log(f"  {ep_path.name}: pnl_6h={pnl_str} {verdict_str}")

    if resolved > 0:
        pct = correct / resolved * 100
        log(f"Resolved {resolved} kills: adversary correct {correct}/{resolved} ({pct:.0f}%), "
            f"false kills {false_kills}/{resolved} ({100-pct:.0f}%)")
        if false_kill_attacks:
            sorted_atks = sorted(false_kill_attacks.items(), key=lambda x: -x[1])
            atk_str = ", ".join(f"{a} ({n})" for a, n in sorted_atks)
            log(f"False kill attacks: {atk_str}")
    else:
        log("No new episodes resolved this cycle (all too recent or already done)")

    write_heartbeat()
    log("=" * 60)


# ─── ENTRYPOINT ───
def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"Counterfactual agent starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

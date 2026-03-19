#!/usr/bin/env python3
"""
ZERO OS — Candlestick Pattern Scanner (TAAPI-Based)
Independent signal generation from candlestick pattern recognition.

Fetches 12 patterns × 2 intervals (1h, 4h) × 10 coins from TAAPI bulk API.
Combines pattern signals with current regime from world_state.json to
produce scored trade candidates.

Inputs:
  TAAPI bulk API — candlestick pattern recognition
  scanner/bus/world_state.json — regime state from perception agent

Outputs:
  scanner/bus/pattern_candidates.json — pattern-based candidates
  scanner/bus/heartbeat.json — updated last-alive timestamp

Usage:
  python3 scanner/agents/pattern_scanner.py           # single run
  python3 scanner/agents/pattern_scanner.py --loop    # continuous 900s cycle
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
WORLD_STATE_FILE = BUS_DIR / "world_state.json"
PATTERN_CANDIDATES_FILE = BUS_DIR / "pattern_candidates.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# ─── CONFIG ───
TAAPI_BULK_URL = "https://api.taapi.io/bulk"
EXCHANGE = "binance"
CYCLE_SECONDS = 900  # 15 minutes

CORE_COINS = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX",
    "LINK", "ARB", "NEAR", "SUI", "INJ",
]

INTERVALS = ["1h", "4h"]

# Candlestick patterns to fetch
PATTERNS = [
    "doji",
    "hammer",
    "invertedhammer",
    "engulfing",
    "morningstar",
    "eveningstar",
    "harami",
    "shootingstar",
    "hangingman",
    "3blackcrows",
    "3whitesoldiers",
    "marubozu",
]

# Pattern base strength (0-1 before multipliers)
PATTERN_STRENGTH = {
    "doji":           0.5,
    "hammer":         0.7,
    "invertedhammer": 0.7,
    "engulfing":      0.8,
    "morningstar":    0.8,
    "eveningstar":    0.8,
    "harami":         0.5,
    "shootingstar":   0.7,
    "hangingman":     0.7,
    "3blackcrows":    0.75,
    "3whitesoldiers": 0.75,
    "marubozu":       0.9,
}

# Pattern → inherent direction bias
# "bullish" = always LONG, "bearish" = always SHORT, "dynamic" = depends on value sign
PATTERN_DIRECTION = {
    "doji":           "dynamic",   # value 100/-100, or treat as indecision
    "hammer":         "bullish",
    "invertedhammer": "bullish",
    "engulfing":      "dynamic",   # 100=bullish, -100=bearish
    "morningstar":    "bullish",
    "eveningstar":    "bearish",
    "harami":         "dynamic",   # 100=bullish, -100=bearish
    "shootingstar":   "bearish",
    "hangingman":     "bearish",
    "3blackcrows":    "bearish",
    "3whitesoldiers": "bullish",
    "marubozu":       "dynamic",   # 100=bullish, -100=bearish
}

# Timeframe multiplier
TIMEFRAME_MULT = {
    "1h": 1.0,
    "4h": 1.2,
}

# Regime multipliers
REGIME_MULT = {
    "trending_aligned":   1.3,  # regime trending + pattern direction matches trend
    "trending_counter":   0.8,  # regime trending + pattern direction counter
    "stable":             0.9,  # stable regime = medium confidence
    "chaotic":            0.5,  # chaotic = noise
    "reverting":          1.0,  # reverting with reversals is fair
    "unknown":            0.7,
}

# Coins with known TAAPI Binance availability (some altcoins may not be listed)
# TAAPI uses symbol format like BTC/USDT
COIN_SYMBOL_MAP = {coin: f"{coin}/USDT" for coin in CORE_COINS}

# Max indicators per bulk call
MAX_INDICATORS_PER_CALL = 18


# ─── API KEY ───
def _load_api_key() -> str:
    key = os.environ.get("TAAPI_API_KEY")
    if key:
        return key
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("TAAPI_API_KEY="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("TAAPI_API_KEY not found in env or ~/.config/openclaw/.env")


# ─── BULK API CALL ───
def _bulk_request(secret: str, construct: dict) -> list[dict]:
    """POST a single bulk request to TAAPI. Returns list of {id, result, errors}."""
    body = json.dumps({"secret": secret, "construct": construct}).encode()
    req = urllib.request.Request(
        TAAPI_BULK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept-Encoding": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    data = json.loads(raw)
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []


def _execute_bulk(secret: str, construct: dict, retries: int = 1) -> dict[str, dict]:
    """Execute one bulk call. Returns {indicator_id: result_dict}."""
    for attempt in range(retries + 1):
        try:
            items = _bulk_request(secret, construct)
            results: dict[str, dict] = {}
            for item in items:
                item_id = item.get("id", "")
                result = item.get("result", {})
                errors = item.get("errors", [])
                if errors:
                    print(f"  [taapi] WARN {item_id}: {errors}")
                if item_id and result is not None:
                    results[item_id] = result
            return results
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            if e.code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [taapi] WARN 429 rate limit — waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [taapi] WARN HTTP {e.code}: {body}")
                break
        except Exception as e:
            print(f"  [taapi] WARN bulk call failed: {e}")
            break
    return {}


# ─── PATTERN BATCH BUILDER ───
def _build_pattern_batches(coin: str, interval: str) -> list[tuple[dict, list[str]]]:
    """
    Build bulk construct(s) for all 12 patterns for a coin/interval.
    Splits into batches of MAX_INDICATORS_PER_CALL if needed.
    Returns list of (construct, pattern_ids).
    """
    sym = COIN_SYMBOL_MAP.get(coin, f"{coin}/USDT")
    all_indicators = []
    for pattern in PATTERNS:
        ind_id = f"{pattern.upper()}_{interval.upper()}_{coin}"
        all_indicators.append({"id": ind_id, "indicator": pattern})

    batches = []
    for i in range(0, len(all_indicators), MAX_INDICATORS_PER_CALL):
        batch = all_indicators[i:i + MAX_INDICATORS_PER_CALL]
        construct = {
            "exchange": EXCHANGE,
            "symbol": sym,
            "interval": interval,
            "indicators": batch,
        }
        pattern_ids = [ind["id"] for ind in batch]
        batches.append((construct, pattern_ids))
    return batches


# ─── WORLD STATE ───
def load_world_state() -> dict:
    """Load coin regime data from world_state.json."""
    if not WORLD_STATE_FILE.exists():
        return {}
    try:
        with open(WORLD_STATE_FILE) as f:
            data = json.load(f)
        return data.get("coins", {})
    except (json.JSONDecodeError, OSError):
        return {}


def get_coin_regime(world_coins: dict, coin: str) -> str:
    """Extract regime string for a coin."""
    info = world_coins.get(coin, {})
    return info.get("regime", "unknown")


def get_trend_direction(world_coins: dict, coin: str) -> str | None:
    """
    Infer trending direction from indicators.
    Returns 'bullish', 'bearish', or None.
    """
    info = world_coins.get(coin, {})
    indicators = info.get("indicators", {})
    timeframe = info.get("timeframe", {})

    # Use fast_bias from timeframe if available
    fast_bias = timeframe.get("fast_bias", "")
    if fast_bias == "bullish":
        return "bullish"
    if fast_bias == "bearish":
        return "bearish"

    # Fallback: use MACD_N_24H sign
    macd = indicators.get("MACD_N_24H")
    if macd is not None:
        return "bullish" if macd > 0 else "bearish"

    # Fallback: use CMO_3H30M
    cmo = indicators.get("CMO_3H30M")
    if cmo is not None:
        return "bullish" if cmo > 0 else "bearish"

    return None


# ─── SIGNAL GENERATION ───
def resolve_pattern_direction(pattern: str, value: float) -> str | None:
    """
    Determine trade direction for a fired pattern.
    Returns 'LONG', 'SHORT', or None (if ambiguous/zero).
    """
    if value == 0:
        return None

    bias = PATTERN_DIRECTION.get(pattern, "dynamic")
    if bias == "bullish":
        return "LONG"
    if bias == "bearish":
        return "SHORT"
    # dynamic: value 100 = bullish, -100 = bearish
    if value > 0:
        return "LONG"
    if value < 0:
        return "SHORT"
    return None


def compute_confidence(
    pattern: str,
    interval: str,
    regime: str,
    direction: str,
    trend_direction: str | None,
) -> float:
    """
    Compute composite confidence score (0-1).

    Formula:
      base = PATTERN_STRENGTH[pattern]
      × TIMEFRAME_MULT[interval]
      × regime_multiplier
      clamped to [0.0, 1.0]
    """
    base = PATTERN_STRENGTH.get(pattern, 0.5)
    tf_mult = TIMEFRAME_MULT.get(interval, 1.0)

    # Determine regime multiplier
    if regime == "trending":
        if trend_direction is not None:
            if (direction == "LONG" and trend_direction == "bullish") or \
               (direction == "SHORT" and trend_direction == "bearish"):
                regime_mult = REGIME_MULT["trending_aligned"]
            else:
                regime_mult = REGIME_MULT["trending_counter"]
        else:
            regime_mult = 1.0  # no trend info, neutral
    elif regime == "chaotic":
        regime_mult = REGIME_MULT["chaotic"]
    elif regime == "stable":
        regime_mult = REGIME_MULT["stable"]
    elif regime == "reverting":
        # Reversals shine in reverting regimes
        reversal_patterns = {"hammer", "invertedhammer", "engulfing", "morningstar",
                             "eveningstar", "harami", "shootingstar", "hangingman",
                             "3blackcrows", "3whitesoldiers"}
        if pattern in reversal_patterns:
            regime_mult = 1.2
        else:
            regime_mult = REGIME_MULT["reverting"]
    else:
        regime_mult = REGIME_MULT["unknown"]

    confidence = base * tf_mult * regime_mult
    return round(min(1.0, max(0.0, confidence)), 4)


def build_signal_name(pattern: str, interval: str, direction: str) -> str:
    """Format: TAAPI_{PATTERN}_{INTERVAL}_{DIRECTION}"""
    return f"TAAPI_{pattern.upper()}_{interval.upper()}_{direction}"


# ─── MAIN FETCH + SIGNAL LOOP ───
def fetch_patterns_and_generate_signals(
    secret: str,
    world_coins: dict,
) -> list[dict]:
    """
    Fetch all patterns for all coins × intervals. Generate signals for fired patterns.
    Returns list of pattern candidate dicts.
    """
    all_signals: list[dict] = []
    total_calls = 0

    for interval in INTERVALS:
        print(f"\n  Fetching {interval} patterns...")
        for coin in CORE_COINS:
            regime = get_coin_regime(world_coins, coin)
            trend_dir = get_trend_direction(world_coins, coin)

            batches = _build_pattern_batches(coin, interval)
            coin_results: dict[str, dict] = {}

            for construct, _ids in batches:
                results = _execute_bulk(secret, construct)
                total_calls += 1
                coin_results.update(results)
                time.sleep(0.2)  # 200ms between calls

            # Parse results
            fired_count = 0
            for pattern in PATTERNS:
                ind_id = f"{pattern.upper()}_{interval.upper()}_{coin}"
                result = coin_results.get(ind_id, {})
                if not isinstance(result, dict):
                    continue
                raw_value = result.get("value")
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue

                if value == 0:
                    continue  # no pattern

                direction = resolve_pattern_direction(pattern, value)
                if direction is None:
                    continue

                confidence = compute_confidence(pattern, interval, regime, direction, trend_dir)
                signal_name = build_signal_name(pattern, interval, direction)

                signal = {
                    "coin": coin,
                    "direction": direction,
                    "pattern": pattern,
                    "interval": interval,
                    "confidence": confidence,
                    "regime": regime,
                    "signal_name": signal_name,
                }
                all_signals.append(signal)
                fired_count += 1
                print(f"    🕯️  {coin:5s} {interval}  {pattern:20s} → {direction:5s}  conf={confidence:.3f}  regime={regime}")

            if fired_count == 0:
                print(f"    {coin:5s} {interval}: no patterns fired")

    print(f"\n  Total TAAPI bulk calls: {total_calls}")
    return all_signals


# ─── HEARTBEAT ───
def write_heartbeat() -> None:
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat = {}
    if HEARTBEAT_FILE.exists() and HEARTBEAT_FILE.stat().st_size > 0:
        try:
            with open(HEARTBEAT_FILE) as f:
                heartbeat = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    heartbeat["pattern_scanner"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── MAIN CYCLE ───
def run_cycle(secret: str) -> None:
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Pattern Scanner — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"  Coins: {', '.join(CORE_COINS)}")
    print(f"  Intervals: {', '.join(INTERVALS)}")
    print(f"  Patterns: {len(PATTERNS)}")

    # Load world state for regime context
    world_coins = load_world_state()
    if world_coins:
        print(f"  World state loaded: {len(world_coins)} coins")
    else:
        print("  [warn] world_state.json not found — using unknown regime for all coins")

    # Fetch patterns + generate signals
    signals = fetch_patterns_and_generate_signals(secret, world_coins)

    # Write output
    output = {
        "timestamp": ts_iso,
        "patterns": signals,
    }
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PATTERN_CANDIDATES_FILE, "w") as f:
        json.dump(output, f, indent=2)

    write_heartbeat()

    # Summary
    print(f"\n  {'='*50}")
    print(f"  Pattern signals fired: {len(signals)}")
    if signals:
        long_count = sum(1 for s in signals if s["direction"] == "LONG")
        short_count = sum(1 for s in signals if s["direction"] == "SHORT")
        print(f"  LONG: {long_count}  SHORT: {short_count}")
        print(f"\n  Top signals by confidence:")
        sorted_signals = sorted(signals, key=lambda x: -x["confidence"])
        for s in sorted_signals[:10]:
            print(
                f"    {s['confidence']:.3f}  {s['coin']:5s} {s['direction']:5s}  "
                f"{s['pattern']:20s} [{s['interval']}]  regime={s['regime']}"
            )
    print(f"\n  Written to {PATTERN_CANDIDATES_FILE}")
    print(f"{'='*60}\n")


def main() -> None:
    secret = _load_api_key()
    print(f"[pattern_scanner] API key loaded: {secret[:12]}...")

    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"[pattern_scanner] Starting loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle(secret)
            except Exception as e:
                print(f"[pattern_scanner] Cycle error: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(secret)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ZERO OS — Agent 2: Hypothesis Generator (Cognitive Loop Phase 2)
Replaces Signal Harvester with structured hypothesis reasoning.

Every candidate becomes a HYPOTHESIS with:
  - thesis / anti_thesis (human-readable reasoning)
  - evidence_for / evidence_against (structured evidence)
  - kill_conditions (what would invalidate the thesis)
  - similar_past_trades (looked up from closed.jsonl)
  - confidence (0-1, calibrated from win_rate + evidence)
  - world_snapshot (key world_state values at hypothesis time)

Backward compatible: candidates.json retains all original fields.
New: hypotheses.json for the reflection layer (Phase 5).
New: scanner/memory/episodes/ — one JSON file per hypothesis.

Inputs:
  scanner/data/signals_cache/*.json  — 30 signal packs per coin
  scanner/bus/world_state.json       — unified world model from perception (Phase 1)
  scanner/bus/regimes.json           — regime state (fallback)
  scanner/data/live/closed.jsonl     — historical closed trades
  Envy API indicator snapshots       — current indicator values

Outputs:
  scanner/bus/candidates.json        — scored trade candidates (backward compat)
  scanner/bus/hypotheses.json        — full hypothesis objects (Phase 5 input)
  scanner/bus/heartbeat.json         — last-alive timestamp
  scanner/memory/episodes/<id>.json  — per-hypothesis episode files

Usage:
  python3 scanner/agents/hypothesis_generator.py           # single run
  python3 scanner/agents/hypothesis_generator.py --loop    # continuous 10-min cycle
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import yaml
from datetime import datetime, timezone
from pathlib import Path


# ─── GENEALOGY: SIGNAL FAMILY EXTRACTOR ───
def extract_signal_family(signal_name: str) -> str:
    """Extract the conceptual family from a full signal name (mirrors genealogy.py)."""
    if not signal_name:
        return signal_name
    parts = signal_name.split("_")
    family_parts = []
    for p in parts:
        if any(p.startswith(prefix) and (len(p) == 1 or p[1:].isdigit())
               for prefix in ("V", "EX", "Q", "MH")):
            break
        if p in ("LONG", "SHORT") and family_parts:
            break
        family_parts.append(p)
    return "_".join(family_parts) if family_parts else signal_name


def load_family_stats() -> dict:
    """Load genealogy data for confidence adjustment."""
    gen_file = Path(__file__).parent.parent / "bus" / "genealogy.json"
    if not gen_file.exists():
        return {}
    try:
        with open(gen_file) as f:
            data = json.load(f)
        return data.get("families", {})
    except (json.JSONDecodeError, OSError):
        return {}


# ─── UPGRADE 2: CALIBRATION ADJUSTMENT ───
def load_calibration_adjustment():
    """
    Read calibration.jsonl. Group by confidence bucket (0.3, 0.4, 0.5, etc.).
    If a bucket has 10+ trades and predicted >> actual, return adjustment.
    """
    cal_file = Path(__file__).parent.parent / "memory" / "calibration.jsonl"
    if not cal_file.exists():
        return {}

    buckets = {}
    try:
        with open(cal_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    conf    = entry.get("predicted_confidence", 0.5)
                    outcome = entry.get("actual_outcome", 0)
                    bucket  = round(conf, 1)
                    buckets.setdefault(bucket, []).append(outcome)
                except (json.JSONDecodeError, ValueError):
                    pass
    except OSError:
        return {}

    adjustments = {}
    for bucket, outcomes in buckets.items():
        if len(outcomes) >= 10:
            actual_wr  = sum(outcomes) / len(outcomes)
            predicted  = bucket
            if actual_wr < predicted - 0.15:
                adjustments[bucket] = actual_wr - predicted  # negative
            elif actual_wr > predicted + 0.15:
                adjustments[bucket] = actual_wr - predicted  # positive
    return adjustments

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
SIGNALS_CACHE_DIR = DATA_DIR / "signals_cache"
CLOSED_FILE = DATA_DIR / "live" / "closed.jsonl"
REGIMES_FILE = BUS_DIR / "regimes.json"
WORLD_STATE_FILE = BUS_DIR / "world_state.json"
CANDIDATES_FILE = BUS_DIR / "candidates.json"
HYPOTHESES_FILE = BUS_DIR / "hypotheses.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"
PATTERN_CANDIDATES_FILE = BUS_DIR / "pattern_candidates.json"
MEMORY_DIR = SCANNER_DIR / "memory"
EPISODES_DIR = MEMORY_DIR / "episodes"
RULES_DIR = MEMORY_DIR / "rules"
ACTIVE_RULES_FILE = RULES_DIR / "active.json"

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
CYCLE_SECONDS = 600  # 10 minutes
COINS_PER_REQUEST = 10
INDICATORS_PER_REQUEST = 7
MIN_SHARPE = 1.5
MIN_WIN_RATE = 55
CHAOTIC_MIN_SHARPE = 2.5
FEAR_MIN_SHARPE = 2.0  # When F&G < 30

# Expression tokens that are operators, not indicator names
EXPR_OPERATORS = {"AND", "OR", "NOT"}

# Signal direction keywords for regime matching
MOMENTUM_KEYWORDS = {"TREND", "MOMENTUM", "EMA", "BREAKOUT", "MACD", "CROSS"}
REVERSAL_KEYWORDS = {"REVERSAL", "REVERT", "RSI", "BB", "BOUNCE", "OVERSOLD", "OVERBOUGHT", "DOJI"}


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


def _parse_snapshot(snapshot):
    """Extract indicator values from API snapshot response."""
    result = {}
    for coin, ind_list in snapshot.items():
        if not isinstance(ind_list, list):
            continue
        values = {}
        for ind in ind_list:
            values[ind["indicatorCode"]] = ind["value"]
        result[coin] = values
    return result


def fetch_indicators(coins, indicators, api_key):
    """Fetch indicator values for coins. Respects batch limits."""
    all_data = {}
    ind_batches = [
        indicators[i:i + INDICATORS_PER_REQUEST]
        for i in range(0, len(indicators), INDICATORS_PER_REQUEST)
    ]
    for ind_batch in ind_batches:
        ind_param = ",".join(ind_batch)
        for i in range(0, len(coins), COINS_PER_REQUEST):
            coin_batch = coins[i:i + COINS_PER_REQUEST]
            coins_param = ",".join(coin_batch)
            try:
                resp = api_get(
                    "/paid/indicators/snapshot",
                    {"coins": coins_param, "indicators": ind_param},
                    api_key,
                )
                parsed = _parse_snapshot(resp.get("snapshot", {}))
                for coin, vals in parsed.items():
                    all_data.setdefault(coin, {}).update(vals)
            except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
                print(f"  [warn] batch failed ({coin_batch[0]}-{coin_batch[-1]}): {e}")
                for coin in coin_batch:
                    try:
                        resp = api_get(
                            "/paid/indicators/snapshot",
                            {"coins": coin, "indicators": ind_param},
                            api_key,
                        )
                        parsed = _parse_snapshot(resp.get("snapshot", {}))
                        for c, vals in parsed.items():
                            all_data.setdefault(c, {}).update(vals)
                    except Exception:
                        pass
                    time.sleep(0.1)
            time.sleep(0.3)
    return all_data


# ─── SIGNAL PACK REFRESH ───
SIGNAL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]
PACK_TYPES = ["common", "rare", "trump"]
CACHE_MAX_AGE_SECONDS = 3600


def refresh_signal_cache(api_key):
    """Fetch fresh signal packs from Envy API (YAML format) and save as JSON."""
    SIGNALS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    needs_refresh = False
    for coin in SIGNAL_COINS:
        cache_file = SIGNALS_CACHE_DIR / f"{coin}.json"
        if not cache_file.exists():
            needs_refresh = True
            break
        age = time.time() - cache_file.stat().st_mtime
        if age > CACHE_MAX_AGE_SECONDS:
            needs_refresh = True
            break

    if not needs_refresh:
        return

    print("  Refreshing signal packs from Envy API...")
    refreshed = 0
    for coin in SIGNAL_COINS:
        all_signals = []
        for pack_type in PACK_TYPES:
            url = f"{BASE_URL}/paid/signals/pack?coin={coin}&type={pack_type}"
            try:
                req = urllib.request.Request(url, headers={"X-API-Key": api_key})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    raw = resp.read().decode()
                    if "yaml" in content_type or raw.startswith("#"):
                        data = yaml.safe_load(raw)
                    else:
                        data = json.loads(raw)
                signals = data.get("signals", [])
                for sig in signals:
                    sig["_pack"] = pack_type
                    sig["_coin"] = coin
                all_signals.extend(signals)
            except Exception as e:
                print(f"    WARN: {coin}/{pack_type}: {e}")
            time.sleep(0.3)

        if all_signals:
            cache_file = SIGNALS_CACHE_DIR / f"{coin}.json"
            with open(cache_file, "w") as f:
                json.dump(all_signals, f, indent=2)
            refreshed += 1

    print(f"  Refreshed {refreshed}/{len(SIGNAL_COINS)} coins")


# ─── SIGNAL PACK LOADING ───
def load_signal_packs():
    """Load all signal packs from cache directory."""
    packs = {}
    if not SIGNALS_CACHE_DIR.exists():
        return packs
    for f in SIGNALS_CACHE_DIR.glob("*.json"):
        coin = f.stem
        try:
            with open(f) as fh:
                packs[coin] = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [warn] Failed to load {f.name}: {e}")
    return packs


def extract_indicators_from_packs(packs):
    """Extract all unique indicator codes referenced in signal expressions."""
    indicators = set()
    for coin, pack_list in packs.items():
        for pack in pack_list:
            for expr_field in ("expression", "exit_expression"):
                expr = pack.get(expr_field, "")
                tokens = re.findall(r'[A-Z][A-Z0-9_]+', expr)
                for t in tokens:
                    if t not in EXPR_OPERATORS:
                        indicators.add(t)
    return sorted(indicators)


# ─── WORLD STATE ───
def load_world_state():
    """Load unified world model from Phase 1 perception agent."""
    if WORLD_STATE_FILE.exists() and WORLD_STATE_FILE.stat().st_size > 0:
        try:
            with open(WORLD_STATE_FILE) as f:
                data = json.load(f)
            return data.get("coins", {})
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_regimes():
    """Load current regime state (fallback if world_state unavailable)."""
    if REGIMES_FILE.exists() and REGIMES_FILE.stat().st_size > 0:
        try:
            with open(REGIMES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ─── CLOSED TRADES ───
def load_closed_trades(max_lines=500):
    """Load recent closed trades from closed.jsonl (tail)."""
    trades = []
    if not CLOSED_FILE.exists():
        return trades
    try:
        with open(CLOSED_FILE) as f:
            lines = f.readlines()
        for line in lines[-max_lines:]:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return trades


# ─── SIGNAL HEAT ───
def compute_signal_heat(signal_name, closed_trades):
    """Score 0-1 based on recent performance of this signal."""
    trades = [t for t in closed_trades if t.get("signal") == signal_name]
    if len(trades) < 3:
        return 0.5

    recent = trades[-10:]
    wins = sum(1 for t in recent if t.get("pnl_dollars", 0) > 0 or t.get("pnl_usd", 0) > 0)
    total = len(recent)
    last_pnl = recent[-1].get("pnl_dollars", recent[-1].get("pnl_usd", 0))
    recency_bonus = 0.1 if last_pnl > 0 else -0.1

    return min(1.0, max(0.0, wins / total + recency_bonus))


def compute_recent_record(signal_name, closed_trades):
    """Return W/L string for recent trades."""
    trades = [t for t in closed_trades if t.get("signal") == signal_name]
    recent = trades[-10:]
    if not recent:
        return "0W/0L"
    wins = sum(1 for t in recent if t.get("pnl_dollars", 0) > 0 or t.get("pnl_usd", 0) > 0)
    losses = len(recent) - wins
    return f"{wins}W/{losses}L"


# ─── REGIME MATCHING ───
def classify_signal_style(signal_name):
    """Classify a signal as momentum-style or reversal-style."""
    upper = signal_name.upper()
    momentum_score = sum(1 for kw in MOMENTUM_KEYWORDS if kw in upper)
    reversal_score = sum(1 for kw in REVERSAL_KEYWORDS if kw in upper)
    if momentum_score > reversal_score:
        return "momentum"
    if reversal_score > momentum_score:
        return "reversal"
    return "neutral"


def compute_regime_match(signal_name, regime):
    """Score regime alignment."""
    if regime == "chaotic":
        return -1.0
    style = classify_signal_style(signal_name)
    if regime == "trending" and style == "momentum":
        return 1.5
    if regime == "reverting" and style == "reversal":
        return 1.5
    if regime == "shift":
        return 0.0
    if regime == "stable":
        return 0.5
    return 0.0


# ─── EXPRESSION EVALUATION ───
def _evaluate_weighted_expression(expression, indicator_values):
    """Evaluate weighted sum expressions."""
    missing = []
    threshold_match = re.search(r'\)\s*(>=|>|<=|<)\s*(-?[\d.]+)\s*$', expression)
    if not threshold_match:
        return False, missing

    threshold_op = threshold_match.group(1)
    threshold_val = float(threshold_match.group(2))

    terms = re.findall(
        r'\(\(([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)\)\s*\*\s*([\d.]+)\)',
        expression
    )
    if not terms:
        return False, missing

    weighted_sum = 0.0
    for indicator, op, val_str, weight_str in terms:
        val = float(val_str)
        weight = float(weight_str)
        current = indicator_values.get(indicator)
        if current is None:
            missing.append(indicator)
            continue

        condition = False
        if op == ">=":   condition = current >= val
        elif op == "<=": condition = current <= val
        elif op == ">":  condition = current > val
        elif op == "<":  condition = current < val
        elif op == "==": condition = current == val
        elif op == "!=": condition = current != val

        if condition:
            weighted_sum += weight

    if threshold_op == ">=":   result = weighted_sum >= threshold_val
    elif threshold_op == ">":  result = weighted_sum > threshold_val
    elif threshold_op == "<=": result = weighted_sum <= threshold_val
    elif threshold_op == "<":  result = weighted_sum < threshold_val
    else: result = False

    return result, missing


def evaluate_expression(expression, indicator_values):
    """Evaluate a signal entry/exit expression against current indicator values."""
    if not expression or not expression.strip():
        return False, []

    missing = []

    if "((" in expression and "*" in expression:
        return _evaluate_weighted_expression(expression, indicator_values)

    clauses = re.split(r'\s+(AND|OR)\s+', expression)
    results = []
    operators = []

    for part in clauses:
        part = part.strip()
        if part in ("AND", "OR"):
            operators.append(part)
            continue

        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            results.append(False)
            continue

        indicator, op, val_str = m.group(1), m.group(2), m.group(3)
        val = float(val_str)
        current = indicator_values.get(indicator)
        if current is None:
            missing.append(indicator)
            results.append(False)
            continue

        if op == ">=":   results.append(current >= val)
        elif op == "<=": results.append(current <= val)
        elif op == ">":  results.append(current > val)
        elif op == "<":  results.append(current < val)
        elif op == "==": results.append(current == val)
        elif op == "!=": results.append(current != val)
        else: results.append(False)

    if not results:
        return False, missing

    final = results[0]
    for i, op in enumerate(operators):
        if i + 1 < len(results):
            if op == "AND":
                final = final and results[i + 1]
            elif op == "OR":
                final = final or results[i + 1]

    return final, missing


# ─── COMPOSITE SCORE ───
def compute_composite_score(sharpe, win_rate, signal_heat, regime_match):
    """Composite score (0-10)."""
    score = (sharpe * 1.5) + (win_rate / 100 * 3) + (signal_heat * 2) + (regime_match * 1.5)
    return round(max(0.0, min(10.0, score)), 2)


# ─── PATTERN CANDIDATES (TAAPI) ───
def load_pattern_candidates(ts_iso: str) -> list[dict]:
    """
    Load pattern candidates from pattern_scanner output and convert to
    hypothesis-compatible candidate format for the enrichment pipeline.

    Only loads if the file is fresh (< 30 min old) to avoid stale signals.
    """
    if not PATTERN_CANDIDATES_FILE.exists():
        return []

    # Staleness check: skip if > 30 minutes old
    age_min = (time.time() - PATTERN_CANDIDATES_FILE.stat().st_mtime) / 60
    if age_min > 30:
        print(f"  [pattern] Skipping stale pattern_candidates.json (age={age_min:.1f} min)")
        return []

    try:
        with open(PATTERN_CANDIDATES_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [pattern] Failed to load pattern_candidates.json: {e}")
        return []

    raw_patterns = data.get("patterns", [])
    if not raw_patterns:
        return []

    candidates = []
    for p in raw_patterns:
        # Convert confidence (0-1) to composite_score (0-10) scale
        # Pattern signals are independent, so we map: conf * 7.0 as a reasonable proxy
        # (max 7.0 for pattern-only signals, leaving headroom for rule/archetype signals)
        confidence = p.get("confidence", 0.0)
        composite_score = round(min(7.0, confidence * 7.0), 2)

        # Map confidence to implied sharpe/win_rate (estimations only)
        # High confidence (0.9+) ≈ sharpe ~2.0, win_rate ~70%
        implied_sharpe = round(0.5 + confidence * 2.0, 2)
        implied_win_rate = round(50 + confidence * 25, 1)

        coin = p.get("coin", "")
        direction = p.get("direction", "LONG")
        pattern = p.get("pattern", "")
        interval = p.get("interval", "1h")
        regime = p.get("regime", "unknown")
        signal_name = p.get("signal_name", f"TAAPI_{pattern.upper()}_{interval.upper()}_{direction}")

        candidate = {
            "coin": coin,
            "direction": direction,
            "signal": signal_name,
            "sharpe": implied_sharpe,
            "win_rate": implied_win_rate,
            "regime": regime,
            "regime_match": True,  # by construction — pattern_scanner already applied regime logic
            "regime_match_score": confidence,
            "signal_heat": 0.5,    # neutral — no historical record yet
            "recent_record": "pattern",
            "composite_score": composite_score,
            "timeframe_pattern": "",
            "timeframe_confirmation": 0.0,
            "signal_weight": 1.0,
            "vwap_bonus": 0.0,
            "funding_bonus": 0.0,
            "max_hold_hours": 4 if interval == "1h" else 16,
            "exit_expression": "",
            "entry_expression": f"TAAPI_{pattern.upper()}_{interval.upper()} fired",
            "source": "taapi_pattern",
            # Extra metadata for reflection/logging
            "pattern": pattern,
            "pattern_interval": interval,
            "pattern_confidence": confidence,
        }
        candidates.append(candidate)

    return candidates


# ─── HEARTBEAT ───
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
    heartbeat["hypothesis"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── HYPOTHESIS REASONING ───
def build_hypothesis_reasoning(coin, direction, signal_name, world_coin, indicator_data_coin):
    """
    Build thesis, anti_thesis, evidence_for, evidence_against, kill_conditions
    from world_state data for this coin.
    Returns dict with all reasoning fields.
    """
    evidence_for = []
    evidence_against = []
    kill_conditions = []

    # Extract world state fields
    regime = world_coin.get("regime", "unknown")
    regime_confidence = world_coin.get("regime_confidence", 0.5)
    funding = world_coin.get("funding", {})
    funding_direction = funding.get("velocity_direction", "stable")
    funding_rate = funding.get("rate", 0)
    funding_reversal = funding.get("reversal", False)
    spread = world_coin.get("spread", {})
    spread_status = spread.get("status", "NORMAL")
    liquidity = world_coin.get("liquidity", {})
    liquidity_score = liquidity.get("score", 50)
    tradeable = liquidity.get("tradeable", True)
    timeframe = world_coin.get("timeframe", {})
    tf_pattern = timeframe.get("pattern", "NEUTRAL")
    fast_bias = timeframe.get("fast_bias", "neutral")
    slow_bias = timeframe.get("slow_bias", "neutral")

    # Indicator snapshots from world_state (augmented by live fetch)
    ind = world_coin.get("indicators", {})
    # Prefer live-fetched data if passed
    if indicator_data_coin:
        ind = {**ind, **indicator_data_coin}

    hurst_24h = ind.get("HURST_24H", 0.5)
    hurst_48h = ind.get("HURST_48H", 0.5)
    adx = ind.get("ADX_3H30M", 0)
    rsi = ind.get("RSI_3H30M", 50)

    # ── EVIDENCE FOR ──
    # Regime alignment
    if regime == "trending":
        style = classify_signal_style(signal_name)
        if style == "momentum":
            evidence_for.append(f"regime trending — momentum signal aligned")
        # Check Hurst for direction
        if direction == "LONG" and hurst_24h > 0.55:
            evidence_for.append(f"Hurst {hurst_24h:.2f} > 0.55 → persistent upward momentum")
        elif direction == "SHORT" and hurst_24h < 0.45:
            evidence_for.append(f"Hurst {hurst_24h:.2f} < 0.45 → persistent downward momentum")
        elif hurst_24h > 0.5:
            evidence_for.append(f"Hurst {hurst_24h:.2f} > 0.5 → trending market")
    elif regime == "reverting":
        style = classify_signal_style(signal_name)
        if style == "reversal":
            evidence_for.append(f"regime reverting — reversal signal aligned")
    elif regime == "stable":
        evidence_for.append(f"regime stable — low volatility environment")

    # Timeframe pattern
    if direction == "LONG":
        if tf_pattern == "CONFIRMATION_LONG":
            evidence_for.append(f"fast+slow timeframes both bullish (CONFIRMATION_LONG)")
        elif tf_pattern == "DIVERGENCE_BULL":
            evidence_for.append(f"fast bullish divergence building (DIVERGENCE_BULL)")
    elif direction == "SHORT":
        if tf_pattern == "CONFIRMATION_SHORT":
            evidence_for.append(f"fast+slow timeframes both bearish (CONFIRMATION_SHORT)")
        elif tf_pattern == "DIVERGENCE_BEAR":
            evidence_for.append(f"fast bearish divergence building (DIVERGENCE_BEAR)")

    # Funding support
    if funding_direction == "stable":
        evidence_for.append(f"funding stable (rate {funding_rate:.6f})")
    elif direction == "LONG" and funding_rate > 0:
        evidence_for.append(f"funding positive supports LONG (rate {funding_rate:.6f})")
    elif direction == "SHORT" and funding_rate < 0:
        evidence_for.append(f"funding negative supports SHORT (rate {funding_rate:.6f})")

    # Liquidity
    if liquidity_score >= 80:
        evidence_for.append(f"deep liquidity (score {liquidity_score:.0f})")
    elif liquidity_score >= 60:
        evidence_for.append(f"adequate liquidity (score {liquidity_score:.0f})")

    # Spread
    if spread_status == "NORMAL":
        evidence_for.append(f"spread normal — no MM manipulation detected")

    # ── EVIDENCE AGAINST ──
    # Regime conflicts
    if regime == "chaotic":
        evidence_against.append(f"regime chaotic — unpredictable price action")
    elif regime == "shift":
        evidence_against.append(f"regime transitioning (shift) — direction unclear")

    # Timeframe conflicts
    if direction == "LONG":
        if tf_pattern in ("CONFIRMATION_SHORT", "TRAP_LONG"):
            evidence_against.append(f"timeframe pattern {tf_pattern} conflicts with LONG")
        elif tf_pattern == "DIVERGENCE_BEAR":
            evidence_against.append(f"bearish divergence building on higher timeframe")
    elif direction == "SHORT":
        if tf_pattern in ("CONFIRMATION_LONG", "TRAP_SHORT"):
            evidence_against.append(f"timeframe pattern {tf_pattern} conflicts with SHORT")
        elif tf_pattern == "DIVERGENCE_BULL":
            evidence_against.append(f"bullish divergence building on higher timeframe")

    # Spread elevated
    if spread_status == "ELEVATED":
        evidence_against.append(f"spread elevated — potential MM activity")
    elif spread_status == "MM_SETUP":
        evidence_against.append(f"spread in MM_SETUP — manipulation risk HIGH")

    # Funding against direction
    if funding_reversal:
        evidence_against.append(f"funding reversal detected")
    if direction == "LONG" and funding_rate < -0.002:
        evidence_against.append(f"funding strongly negative ({funding_rate:.6f}) — shorts rewarded")
    elif direction == "SHORT" and funding_rate > 0.002:
        evidence_against.append(f"funding strongly positive ({funding_rate:.6f}) — longs rewarded")

    # Hurst declining (trend exhaustion)
    hurst_drift = hurst_24h - hurst_48h
    if hurst_drift < -0.04 and hurst_24h > 0.5:
        evidence_against.append(f"Hurst declining {hurst_48h:.2f}→{hurst_24h:.2f} — momentum may be exhausting")

    # Liquidity concerns
    if not tradeable:
        evidence_against.append(f"liquidity below tradeable threshold")
    elif liquidity_score < 50:
        evidence_against.append(f"thin liquidity (score {liquidity_score:.0f})")

    # RSI extremes
    if direction == "LONG" and rsi > 75:
        evidence_against.append(f"RSI {rsi:.0f} overbought — limited upside room")
    elif direction == "SHORT" and rsi < 25:
        evidence_against.append(f"RSI {rsi:.0f} oversold — limited downside room")

    # ── KILL CONDITIONS ──
    # Regime-based kills
    if regime == "trending":
        kill_conditions.append("regime shifts to chaotic or shift")
    if hurst_24h > 0.5:
        kill_conditions.append("Hurst drops below 0.5 (momentum collapses)")

    # Direction-based kills
    if direction == "LONG":
        kill_conditions.append("funding reverses to intensifying short")
    elif direction == "SHORT":
        kill_conditions.append("funding reverses to intensifying long")

    # Always present
    kill_conditions.append("spread enters MM_SETUP")
    kill_conditions.append("liquidity drops below tradeable threshold")

    # Pattern kills
    if tf_pattern == "CONFIRMATION_LONG" and direction == "LONG":
        kill_conditions.append("timeframe pattern flips to CONFIRMATION_SHORT")
    elif tf_pattern == "CONFIRMATION_SHORT" and direction == "SHORT":
        kill_conditions.append("timeframe pattern flips to CONFIRMATION_LONG")

    # ── THESIS STRING ──
    thesis_parts = []
    if hurst_24h > 0.5:
        thesis_parts.append(f"Hurst {hurst_24h:.2f}")
    if tf_pattern != "NEUTRAL":
        thesis_parts.append(tf_pattern)
    if funding_direction == "stable":
        thesis_parts.append("funding stable")
    elif funding_rate > 0:
        thesis_parts.append(f"funding positive ({funding_rate:.5f})")
    else:
        thesis_parts.append(f"funding negative ({funding_rate:.5f})")
    if regime != "unknown":
        thesis_parts.append(f"{regime} regime")

    if direction == "LONG":
        thesis_parts.append("→ momentum continuation LONG")
    else:
        thesis_parts.append("→ momentum continuation SHORT")

    thesis = " + ".join(thesis_parts) if thesis_parts else f"{regime} regime supports {direction}"

    # ── ANTI-THESIS STRING ──
    if evidence_against:
        anti_thesis = "; ".join(evidence_against[:3])
    elif hurst_drift < 0:
        anti_thesis = f"Hurst drifting {hurst_48h:.2f}→{hurst_24h:.2f}, regime may rotate"
    else:
        anti_thesis = "No significant contradicting evidence at this time"

    return {
        "thesis": thesis,
        "anti_thesis": anti_thesis,
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "kill_conditions": kill_conditions,
        "world_snapshot": {
            "regime": regime,
            "regime_confidence": round(regime_confidence, 3),
            "hurst_24h": round(hurst_24h, 4),
            "hurst_48h": round(hurst_48h, 4),
            "funding_rate": funding_rate,
            "funding_direction": funding_direction,
            "funding_reversal": funding_reversal,
            "spread_status": spread_status,
            "liquidity_score": liquidity_score,
            "tradeable": tradeable,
            "timeframe_pattern": tf_pattern,
            "fast_bias": fast_bias,
            "slow_bias": slow_bias,
        },
    }


# ─── SIMILAR PAST TRADES ───
def find_similar_past_trades(coin, direction, signal_name, regime, closed_trades, max_results=3):
    """
    Find similar past trades from closed.jsonl.
    Similarity: same coin+direction, OR same regime, OR same signal.
    Returns list of up to max_results most similar trades.
    """
    scored = []
    for t in closed_trades:
        score = 0
        if t.get("coin") == coin and t.get("direction") == direction:
            score += 3  # strongest match
        elif t.get("coin") == coin:
            score += 1
        if t.get("signal") == signal_name:
            score += 2
        if t.get("regime") == regime:
            score += 1

        if score > 0:
            pnl = t.get("pnl_pct", t.get("pnl_dollars", t.get("pnl_usd", 0)))
            outcome = "win" if pnl > 0 else "loss"
            scored.append({
                "score": score,
                "coin": t.get("coin", "?"),
                "direction": t.get("direction", "?"),
                "signal": t.get("signal", "?"),
                "outcome": outcome,
                "pnl_pct": round(float(pnl), 4),
                "exit_reason": t.get("exit_reason", "?"),
                "entry_time": t.get("entry_time", "?"),
            })

    # Sort by similarity score desc, then recency (end of list = more recent)
    scored.sort(key=lambda x: x["score"], reverse=True)
    # Keep top results, remove internal score field
    result = []
    for item in scored[:max_results]:
        item.pop("score", None)
        result.append(item)
    return result


# ─── CONFIDENCE SCORING ───
def compute_confidence(win_rate, evidence_for, evidence_against, similar_past_trades):
    """
    Calibrated confidence score 0-1.
    Starts from win_rate/100, adjusted by evidence and history.
    """
    confidence = win_rate / 100.0

    # Evidence adjustments
    if evidence_for and not evidence_against:
        confidence += 0.05
    confidence -= 0.05 * len(evidence_against)

    # Similar past trades adjustment
    if similar_past_trades:
        pnls = [t["pnl_pct"] for t in similar_past_trades]
        avg_pnl = sum(pnls) / len(pnls)
        all_lost = all(p <= 0 for p in pnls)

        if all_lost and len(pnls) >= 3:
            confidence -= 0.15
        elif avg_pnl < 0:
            confidence -= 0.10
        elif avg_pnl > 0 and len(similar_past_trades) >= 3:
            confidence += 0.10

    # Clamp to valid range
    return round(max(0.1, min(0.95, confidence)), 3)


# ─── HYPOTHESIS ID ───
_hyp_counter = {}

def make_hypothesis_id(ts, coin, direction):
    """Generate unique hypothesis ID."""
    date_str = ts.strftime("%Y%m%d_%H%M%S")
    key = f"{date_str}_{coin}_{direction}"
    _hyp_counter[key] = _hyp_counter.get(key, 0) + 1
    return f"hyp_{date_str}_{coin}_{direction}_{_hyp_counter[key]:03d}"


# ─── ACTIVE RULES INTEGRATION ───
def load_active_rules():
    """Load active rules from rule lifecycle store."""
    if not ACTIVE_RULES_FILE.exists():
        return []
    try:
        with open(ACTIVE_RULES_FILE) as f:
            rules = json.load(f)
        return rules if isinstance(rules, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def apply_active_rules(hypothesis, active_rules):
    """
    Check hypothesis against active rules. Apply matching rule actions.
    Actions: boost_confidence, reduce_confidence, kill, boost_size, reduce_size.
    Returns modified hypothesis and list of applied rule IDs.
    """
    if not active_rules:
        return hypothesis, []

    applied = []
    coin = hypothesis.get("coin", "").lower()
    direction = hypothesis.get("direction", "").upper().lower()
    regime = hypothesis.get("regime", "").lower()
    pattern = str(hypothesis.get("signal", "")).lower()

    for rule in active_rules:
        condition = rule.get("condition", "").lower()
        action = rule.get("action", "")
        value = float(rule.get("value", 0.0))

        # Match condition to hypothesis fields
        matched = False
        if f"regime == '{regime}'" in condition or f'regime == "{regime}"' in condition:
            matched = True
        elif f"direction == '{direction}'" in condition or f'direction == "{direction}"' in condition:
            matched = True
        elif f"pattern == '{pattern}'" in condition or f'pattern == "{pattern}"' in condition:
            matched = True

        if not matched:
            continue

        rule_id = rule.get("id", "?")

        # Apply action
        if action == "boost_confidence":
            old = hypothesis.get("confidence", 0.5)
            hypothesis["confidence"] = round(min(0.95, old + value), 3)
            applied.append(rule_id)
        elif action == "reduce_confidence":
            old = hypothesis.get("confidence", 0.5)
            hypothesis["confidence"] = round(max(0.05, old - value), 3)
            hypothesis.setdefault("evidence_against", []).append(
                f"Rule {rule_id}: {rule.get('evidence', 'active rule')[:80]}"
            )
            applied.append(rule_id)
        elif action == "kill":
            hypothesis["adversary_verdict"] = "KILLED"
            hypothesis["killed_by_rule"] = rule_id
            hypothesis.setdefault("evidence_against", []).append(
                f"Rule {rule_id} KILL: {rule.get('evidence', '')[:80]}"
            )
            applied.append(rule_id)
            break  # killed, stop processing rules
        elif action == "boost_size":
            old = hypothesis.get("recommended_size_modifier", 1.0)
            hypothesis["recommended_size_modifier"] = round(min(2.0, old + value), 3)
            applied.append(rule_id)
        elif action == "reduce_size":
            old = hypothesis.get("recommended_size_modifier", 1.0)
            hypothesis["recommended_size_modifier"] = round(max(0.1, old - value), 3)
            applied.append(rule_id)

    if applied:
        hypothesis["applied_rules"] = applied

    return hypothesis, applied


# ─── EPISODE MEMORY ───
def write_episode(hypothesis):
    """Write hypothesis to scanner/memory/episodes/ as individual JSON file."""
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    hyp_id = hypothesis.get("hypothesis_id", "unknown")
    episode_file = EPISODES_DIR / f"{hyp_id}.json"
    try:
        with open(episode_file, "w") as f:
            json.dump(hypothesis, f, indent=2)
    except OSError as e:
        print(f"  [warn] Failed to write episode {hyp_id}: {e}")


# ─── ARCHETYPE SIGNALS ───
ARCHETYPE_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]


def generate_archetype_signals(indicator_data, regime_data, timeframe_data, funding_data, ts_iso):
    """Generate high-conviction compound signals from multiple data domains."""
    candidates = []

    for coin in ARCHETYPE_COINS:
        ind = indicator_data.get(coin, {})
        if not ind:
            continue

        regime_info = regime_data.get(coin, {})
        regime = regime_info.get("regime", "unknown")
        tf_info = timeframe_data.get(coin, {})
        tf_pattern = tf_info.get("pattern", "NEUTRAL")
        coin_funding = funding_data.get(coin)

        h24 = ind.get("HURST_24H", 0.5)
        h48 = ind.get("HURST_48H", 0.5)
        dfa24 = ind.get("DFA_24H", 0.5)
        dfa48 = ind.get("DFA_48H", 0.5)
        ly24 = ind.get("LYAPUNOV_24H", 1.9)
        adx = ind.get("ADX_3H30M", 0)
        rsi = ind.get("RSI_3H30M", 50)
        cmo = ind.get("CMO_3H30M", 0)
        bb24 = ind.get("BB_POS_24H", 0.5)
        xi_net = ind.get("XONE_I_NET", 50)
        xa_net = ind.get("XONE_AVG_NET", 50)
        ema24 = ind.get("EMA_N_24H", 1.0)
        macd24 = ind.get("MACD_N_24H", 0)
        doji_v = ind.get("DOJI_VELOCITY", 0)
        doji_s = ind.get("DOJI_SIGNAL", 0)
        ema_cross = ind.get("EMA_CROSS_15M_N", 0)

        # ── ARCHETYPE 1: Chaos Regime Convergence ──
        h_diff = h24 - h48
        d_diff = dfa24 - dfa48
        if abs(h_diff) > 0.05 and d_diff > 0.05 and ly24 < 1.90 and adx >= 25:
            direction = "LONG" if h24 > 0.55 else "SHORT" if h24 < 0.45 else None
            if direction:
                score = 7.0 + abs(h_diff) * 5
                candidates.append(_archetype_candidate(
                    coin, direction, "ARCH_CHAOS_REGIME_CONVERGENCE", score,
                    f"HURST_24H {'<' if direction == 'SHORT' else '>'} {'0.45' if direction == 'SHORT' else '0.55'} AND "
                    f"LYAPUNOV_24H >= 1.90",
                    24, regime, tf_pattern, ts_iso
                ))

        # ── ARCHETYPE 2: Social Exhaustion Reversal ──
        if xi_net >= 80 and xa_net >= 50 and rsi >= 70 and cmo >= 30 and bb24 >= 0.8:
            candidates.append(_archetype_candidate(
                coin, "SHORT", "ARCH_SOCIAL_EXHAUSTION_SHORT", 8.5,
                "XONE_I_NET <= 50 OR RSI_3H30M <= 40",
                12, regime, tf_pattern, ts_iso
            ))
        if xi_net <= 20 and xa_net <= -50 and rsi <= 30 and cmo <= -30 and bb24 <= 0.2:
            candidates.append(_archetype_candidate(
                coin, "LONG", "ARCH_SOCIAL_EXHAUSTION_LONG", 8.5,
                "XONE_I_NET >= 50 OR RSI_3H30M >= 60",
                12, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 3: Triple Convergence ──
        f_rate = 0
        if coin_funding:
            f_rate = coin_funding.get("strength", 0)

        if (regime == "trending" and h24 > 0.55 and ema24 > 1.005 and macd24 > 0
                and tf_pattern == "CONFIRMATION_LONG"):
            score = 7.5
            if coin_funding and coin_funding.get("direction") == "LONG":
                score += coin_funding.get("strength", 0)
            if score >= 7.5:
                candidates.append(_archetype_candidate(
                    coin, "LONG", "ARCH_TRIPLE_CONVERGENCE_LONG", min(score, 10.0),
                    "HURST_24H < 0.50 OR EMA_N_24H < 1.0",
                    48, regime, tf_pattern, ts_iso
                ))

        if (regime == "trending" and h24 < 0.45 and ema24 < 0.995 and macd24 < 0
                and tf_pattern == "CONFIRMATION_SHORT"):
            score = 7.5
            if coin_funding and coin_funding.get("direction") == "SHORT":
                score += coin_funding.get("strength", 0)
            if score >= 7.5:
                candidates.append(_archetype_candidate(
                    coin, "SHORT", "ARCH_TRIPLE_CONVERGENCE_SHORT", min(score, 10.0),
                    "HURST_24H > 0.50 OR EMA_N_24H > 1.0",
                    48, regime, tf_pattern, ts_iso
                ))

        # ── ARCHETYPE 4: Doji Velocity Breakout ──
        if doji_v >= 3.0 and doji_s >= 1.0 and ly24 < 1.80 and adx < 20:
            direction = "LONG" if ema_cross > 0 or cmo > 0 else "SHORT"
            score = 6.5 + min(doji_v / 10, 1.5)
            candidates.append(_archetype_candidate(
                coin, direction, "ARCH_DOJI_BREAKOUT", score,
                "ADX_3H30M >= 40 OR DOJI_VELOCITY < 1.0",
                8, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 6: Compound Killer ──
        score_long, score_short = _compound_score(
            coin, ind, regime, tf_pattern, coin_funding
        )
        best_dir = "LONG" if score_long >= score_short else "SHORT"
        best_score = max(score_long, score_short)
        if best_score >= 6.0:
            candidates.append(_archetype_candidate(
                coin, best_dir, "ARCH_COMPOUND_KILLER", best_score,
                "RSI_3H30M >= 80 OR RSI_3H30M <= 20" if best_dir == "LONG"
                else "RSI_3H30M <= 20 OR RSI_3H30M >= 80",
                24, regime, tf_pattern, ts_iso
            ))

    return candidates


def _archetype_candidate(coin, direction, signal_name, composite, exit_expr, max_hold_h, regime, tf_pattern, ts_iso):
    """Build a candidate dict for an archetype signal."""
    return {
        "coin": coin,
        "direction": direction,
        "signal": signal_name,
        "sharpe": round(composite / 3, 2),
        "win_rate": round(min(55 + composite * 3, 90), 1),
        "regime": regime,
        "regime_match": True,
        "regime_match_score": 1.0,
        "signal_heat": 0.5,
        "recent_record": "archetype",
        "composite_score": round(composite, 2),
        "timeframe_pattern": tf_pattern,
        "timeframe_confirmation": 1.0 if "CONFIRMATION" in tf_pattern else 0.0,
        "signal_weight": 1.0,
        "vwap_bonus": 0.0,
        "funding_bonus": 0.0,
        "max_hold_hours": max_hold_h,
        "exit_expression": exit_expr,
        "source": "archetype",
    }


def _compound_score(coin, ind, regime, tf_pattern, coin_funding):
    """Score a coin 0-10 across all domains for LONG and SHORT."""
    h24 = ind.get("HURST_24H", 0.5)
    rsi = ind.get("RSI_3H30M", 50)
    bb24 = ind.get("BB_POS_24H", 0.5)
    xi = ind.get("XONE_I_NET", 50)
    doji_s = ind.get("DOJI_SIGNAL", 0)

    score_long = 0.0
    score_short = 0.0

    if regime == "trending" and h24 > 0.55:
        score_long += 2.0
    if regime == "trending" and h24 < 0.45:
        score_short += 2.0
    if regime == "reverting":
        score_long += 1.0 if bb24 < 0.3 else 0
        score_short += 1.0 if bb24 > 0.7 else 0

    if tf_pattern == "CONFIRMATION_LONG":
        score_long += 1.5
    if tf_pattern == "CONFIRMATION_SHORT":
        score_short += 1.5
    if tf_pattern == "DIVERGENCE_BULL":
        score_long += 0.8
    if tf_pattern == "DIVERGENCE_BEAR":
        score_short += 0.8

    if coin_funding:
        fd = coin_funding.get("direction")
        fs = coin_funding.get("strength", 0)
        if fd == "LONG" and fs > 0:
            score_long += min(fs, 1.5)
        if fd == "SHORT" and fs > 0:
            score_short += min(fs, 1.5)

    if xi > 70:
        score_long += 1.5
    elif xi > 60:
        score_long += 0.8
    if xi < 30:
        score_short += 1.5
    elif xi < 40:
        score_short += 0.8

    if bb24 < 0.25:
        score_long += 1.0
    elif bb24 < 0.35:
        score_long += 0.5
    if bb24 > 0.75:
        score_short += 1.0
    elif bb24 > 0.65:
        score_short += 0.5

    if 25 < rsi < 60:
        score_long += 0.5
    if 40 < rsi < 75:
        score_short += 0.5

    if doji_s >= 50:
        score_long += 0.5
        score_short += 0.5

    return round(score_long, 2), round(score_short, 2)


# ─── DEDUP ───
def dedup_candidates(candidates):
    """Keep only best instance of each signal ID per cycle."""
    best = {}
    for c in candidates:
        sig_id = c.get("signal", "")
        if sig_id not in best or c.get("composite_score", 0) > best[sig_id].get("composite_score", 0):
            best[sig_id] = c
    deduped = sorted(best.values(), key=lambda x: -x.get("composite_score", 0))
    return deduped


# ─── ENRICH CANDIDATE WITH HYPOTHESIS ───
def enrich_with_hypothesis(candidate, ts, world_coins, indicator_data, closed_trades):
    """
    Add hypothesis fields to a candidate dict.
    Returns enriched candidate (in-place mutation + return).
    """
    coin = candidate.get("coin", "")
    direction = candidate.get("direction", "LONG")
    signal_name = candidate.get("signal", "")
    win_rate = candidate.get("win_rate", 55)
    regime = candidate.get("regime", "unknown")

    # Generate unique ID
    hyp_id = make_hypothesis_id(ts, coin, direction)

    # World state for this coin
    world_coin = world_coins.get(coin, {})
    live_ind = indicator_data.get(coin, {})

    # Build reasoning
    reasoning = build_hypothesis_reasoning(coin, direction, signal_name, world_coin, live_ind)

    # Find similar past trades
    similar = find_similar_past_trades(coin, direction, signal_name, regime, closed_trades)

    # Compute confidence
    confidence = compute_confidence(
        win_rate,
        reasoning["evidence_for"],
        reasoning["evidence_against"],
        similar,
    )

    # Upgrade 2: Calibration adjustment
    calibration = load_calibration_adjustment()
    bucket = round(confidence, 1)
    if bucket in calibration:
        adj = calibration[bucket]
        confidence = max(0.1, min(0.95, confidence + adj))
        print(f"  [calibration] adjusted confidence by {adj:+.2f} for bucket {bucket} → {confidence:.3f}")

    # Genealogy: Family-aware confidence blending
    family_stats = load_family_stats()
    family_key   = f"{extract_signal_family(signal_name)}|{regime}|{direction}"
    family       = family_stats.get(family_key, {})
    if family.get("mature") and family.get("traded", 0) >= 10 and family.get("win_rate") is not None:
        family_wr  = family["win_rate"]
        # Blend: 60% family data, 40% individual hypothesis
        confidence = round(0.6 * family_wr + 0.4 * confidence, 3)
        confidence = max(0.1, min(0.95, confidence))
        print(
            f"  [genealogy] family {family_key}: "
            f"WR {family_wr:.0%} over {family['traded']} trades → confidence adjusted to {confidence:.3f}"
        )

    # Add hypothesis fields
    candidate["hypothesis_id"] = hyp_id
    candidate["thesis"] = reasoning["thesis"]
    candidate["anti_thesis"] = reasoning["anti_thesis"]
    candidate["confidence"] = confidence
    candidate["evidence_for"] = reasoning["evidence_for"]
    candidate["evidence_against"] = reasoning["evidence_against"]
    candidate["kill_conditions"] = reasoning["kill_conditions"]
    candidate["similar_past_trades"] = similar
    candidate["world_snapshot"] = reasoning["world_snapshot"]

    return candidate


# ─── MAIN CYCLE ───
def run_cycle(api_key):
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Hypothesis Generator — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Ensure memory dirs exist
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    # Refresh signal packs if stale (hourly)
    refresh_signal_cache(api_key)

    # Load signal packs
    packs = load_signal_packs()
    if not packs:
        print("  [error] No signal packs found in cache")
        write_heartbeat()
        return

    total_packs = sum(len(v) for v in packs.values())
    coins = sorted(packs.keys())
    print(f"  Loaded {total_packs} signal packs across {len(coins)} coins")

    # Load world state (Phase 1 output — preferred)
    world_coins = load_world_state()
    if world_coins:
        print(f"  World state loaded for {len(world_coins)} coins")
    else:
        print("  [warn] world_state.json not found — falling back to regimes.json")

    # Load regime state (fallback + backward compat)
    regimes_data = load_regimes()
    regime_coins = regimes_data.get("coins", {})
    regime_ts = regimes_data.get("timestamp", "unknown")
    print(f"  Regime data from: {regime_ts}")

    # Build regime_coins from world_state if available (preferred)
    if world_coins:
        regime_coins_ws = {}
        for coin, wc in world_coins.items():
            regime_coins_ws[coin] = {
                "regime": wc.get("regime", "stable"),
                "regime_confidence": wc.get("regime_confidence", 0.5),
            }
        # Merge: world_state takes priority
        regime_coins = {**regime_coins, **regime_coins_ws}

    # Load closed trades
    closed_trades = load_closed_trades()
    print(f"  Loaded {len(closed_trades)} closed trades for heat scoring + hypothesis lookup")

    # Load cross-timeframe signals
    timeframe_data = {}
    tf_file = BUS_DIR / "timeframe_signals.json"
    if tf_file.exists():
        try:
            with open(tf_file) as f:
                tf_raw = json.load(f)
            timeframe_data = tf_raw.get("coins", {})
            print(f"  Loaded timeframe signals for {len(timeframe_data)} coins")
        except Exception:
            pass

    # Override timeframe_data from world_state (more comprehensive)
    if world_coins:
        for coin, wc in world_coins.items():
            if wc.get("timeframe"):
                timeframe_data[coin] = wc["timeframe"]

    # Load signal weights
    signal_weights = {}
    sw_file = BUS_DIR / "signal_weights.json"
    if sw_file.exists():
        try:
            with open(sw_file) as f:
                sw_raw = json.load(f)
            signal_weights = sw_raw.get("weights", {})
            print(f"  Loaded signal weights for {len(signal_weights)} signals")
        except Exception:
            pass

    # Load funding data
    funding_data = {}
    funding_file = BUS_DIR / "funding.json"
    if funding_file.exists():
        try:
            with open(funding_file) as f:
                fd_raw = json.load(f)
            for cs in fd_raw.get("convergence_signals", []):
                funding_data[cs["coin"]] = cs
            print(f"  Loaded funding convergence for {len(funding_data)} coins")
        except Exception:
            pass

    # Also pull funding from world_state
    if world_coins:
        for coin, wc in world_coins.items():
            if wc.get("funding") and coin not in funding_data:
                f = wc["funding"]
                funding_data[coin] = {
                    "coin": coin,
                    "direction": "LONG" if f.get("rate", 0) > 0 else "SHORT",
                    "strength": abs(f.get("rate", 0)) * 100,
                    "velocity_direction": f.get("velocity_direction", "stable"),
                    "reversal": f.get("reversal", False),
                }

    # Extract all indicators needed
    all_indicators = extract_indicators_from_packs(packs)
    volume_indicators = {"VWAP_15M", "VWAP_24H", "VOLUME_PROFILE_15M", "VOLUME_PROFILE_1H",
                         "VOLUME_PROFILE_4H", "VOLUME_PROFILE_12H", "VOLUME_PROFILE_24H", "VOLUME_PROFILE_48H"}
    all_indicators = list(set(all_indicators) | volume_indicators)
    print(f"  Need {len(all_indicators)} unique indicators (incl. volume profile)")

    # Fetch current indicator values
    print(f"  Fetching indicators for {len(coins)} coins...")
    indicator_data = fetch_indicators(coins, all_indicators, api_key)
    print(f"  Got indicator data for {len(indicator_data)} coins")

    if not indicator_data:
        print("  [error] No indicator data returned, skipping cycle")
        write_heartbeat()
        return

    # ── EVALUATE SIGNAL PACKS ──
    # Read Fear & Greed for dynamic Sharpe floor
    fg = 50
    try:
        macro_path = Path(__file__).parent.parent / "bus" / "macro_intel.json"
        if macro_path.exists():
            with open(macro_path) as _mf:
                fg = json.load(_mf).get("fear_greed", 50)
    except Exception:
        pass

    candidates = []
    stats = {"evaluated": 0, "fired": 0, "filtered": 0, "missing_data": 0}

    for coin in coins:
        coin_indicators = indicator_data.get(coin, {})
        if not coin_indicators:
            continue

        regime_info = regime_coins.get(coin, {})
        regime = regime_info.get("regime", "stable")

        for pack in packs[coin]:
            stats["evaluated"] += 1
            signal_name = pack.get("name", "unknown")
            sharpe = pack.get("sharpe", 0)
            win_rate = pack.get("win_rate", 0)
            direction = pack.get("signal_type", "LONG")
            expression = pack.get("expression", "")

            trade_count = pack.get("trade_count", 0)

            # Dynamic Sharpe floor based on market conditions
            effective_min_sharpe = MIN_SHARPE
            if regime == "chaotic":
                effective_min_sharpe = max(effective_min_sharpe, CHAOTIC_MIN_SHARPE)
            elif fg < 30:
                effective_min_sharpe = max(effective_min_sharpe, FEAR_MIN_SHARPE)

            if sharpe < effective_min_sharpe or win_rate < MIN_WIN_RATE or trade_count < 5:
                stats["filtered"] += 1
                continue

            fired, missing = evaluate_expression(expression, coin_indicators)

            if missing:
                stats["missing_data"] += 1

            if not fired:
                continue

            stats["fired"] += 1

            # Compute scores
            heat = compute_signal_heat(signal_name, closed_trades)
            regime_match_score = compute_regime_match(signal_name, regime)
            composite = compute_composite_score(sharpe, win_rate, heat, regime_match_score)
            record = compute_recent_record(signal_name, closed_trades)

            # Cross-timeframe confirmation/penalty
            tf_info = timeframe_data.get(coin, {})
            tf_pattern = tf_info.get("pattern", "")
            tf_conf = tf_info.get("confirmation_score", 0)
            if tf_pattern.startswith("CONFIRMATION") and (
                (direction == "LONG" and tf_pattern == "CONFIRMATION_LONG") or
                (direction == "SHORT" and tf_pattern == "CONFIRMATION_SHORT")
            ):
                composite *= 1.2
            elif tf_pattern.startswith("TRAP") and (
                (direction == "LONG" and tf_pattern == "TRAP_LONG") or
                (direction == "SHORT" and tf_pattern == "TRAP_SHORT")
            ):
                composite *= 0.7

            # Signal evolution weight
            sig_weight = signal_weights.get(signal_name, 1.0)
            composite *= sig_weight

            # Volume profile scoring
            vwap_bonus = 0.0
            close_px = coin_indicators.get("CLOSE_PRICE_15M", 0)
            vwap = coin_indicators.get("VWAP_24H", 0) or coin_indicators.get("VWAP_15M", 0)
            if close_px > 0 and vwap > 0:
                distance_from_vwap = abs(close_px - vwap) / close_px
                if distance_from_vwap < 0.005:
                    vwap_bonus = 0.5
                elif distance_from_vwap < 0.01:
                    vwap_bonus = 0.25
                if vwap_bonus > 0:
                    composite += vwap_bonus

            # Funding rate convergence scoring
            funding_bonus = 0.0
            coin_funding = funding_data.get(coin)
            if coin_funding:
                f_direction = coin_funding.get("direction")
                f_strength = coin_funding.get("strength", 0)
                if f_direction == direction and f_strength > 0:
                    funding_bonus = f_strength
                    composite += funding_bonus
                elif f_strength < 0:
                    composite += f_strength

            composite = round(max(0.0, min(10.0, composite)), 2)

            candidates.append({
                # ── Backward-compatible fields ──
                "coin": coin,
                "direction": direction,
                "signal": signal_name,
                "sharpe": round(sharpe, 4),
                "win_rate": round(win_rate, 2),
                "regime": regime,
                "regime_match": regime_match_score > 0,
                "regime_match_score": regime_match_score,
                "signal_heat": round(heat, 3),
                "recent_record": record,
                "composite_score": composite,
                "timeframe_pattern": tf_pattern,
                "timeframe_confirmation": round(tf_conf, 2),
                "signal_weight": round(sig_weight, 2),
                "vwap_bonus": round(vwap_bonus, 2),
                "funding_bonus": round(funding_bonus, 2),
                "max_hold_hours": pack.get("max_hold_hours"),
                "exit_expression": pack.get("exit_expression", ""),
                "entry_expression": expression,
                "source": "signal_pack",
            })

    # ── ARCHETYPE SIGNALS ──
    archetype_candidates = generate_archetype_signals(
        indicator_data, regime_coins, timeframe_data, funding_data, ts_iso
    )
    if archetype_candidates:
        print(f"\n  Archetype signals fired: {len(archetype_candidates)}")
        for ac in archetype_candidates:
            print(f"    {ac['composite_score']:5.2f}  {ac['coin']:6s} {ac['direction']:5s}  {ac['signal']}")
        candidates.extend(archetype_candidates)

    # ── TAAPI PATTERN CANDIDATES ──
    pattern_candidates = load_pattern_candidates(ts_iso)
    if pattern_candidates:
        print(f"\n  TAAPI pattern candidates loaded: {len(pattern_candidates)}")
        for pc in pattern_candidates[:5]:
            print(f"    {pc['composite_score']:.2f}  {pc['coin']:6s} {pc['direction']:5s}  {pc['signal']}")
        candidates.extend(pattern_candidates)

    # Sort + dedup
    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    before = len(candidates)
    candidates = dedup_candidates(candidates)
    print(f"  Dedup: {before} → {len(candidates)} candidates ({before - len(candidates)} duplicates removed)")

    # ── HYPOTHESIS ENRICHMENT ──
    print(f"  Building hypotheses for {len(candidates)} candidates...")
    active_rules = load_active_rules()
    if active_rules:
        print(f"  Loaded {len(active_rules)} active rules for hypothesis adjustment")

    hypotheses = []
    rules_applied_total = 0
    rules_killed_total = 0
    for candidate in candidates:
        enriched = enrich_with_hypothesis(candidate, ts, world_coins, indicator_data, closed_trades)
        # Apply active rules from rule lifecycle
        enriched, applied = apply_active_rules(enriched, active_rules)
        if applied:
            rules_applied_total += len(applied)
            if enriched.get("killed_by_rule"):
                rules_killed_total += 1
                print(f"    Rule-killed: {enriched['coin']} {enriched['direction']} by {enriched['killed_by_rule']}")
                continue  # don't add rule-killed hypotheses
        hypotheses.append(enriched)

    if active_rules:
        print(f"  Rules applied: {rules_applied_total} adjustments, {rules_killed_total} killed")

    # Write episode files
    episodes_written = 0
    for hyp in hypotheses:
        write_episode(hyp)
        episodes_written += 1

    # ── OUTPUT ──
    BUS_DIR.mkdir(parents=True, exist_ok=True)

    # candidates.json — backward compatible (correlation_agent reads this)
    output = {
        "timestamp": ts_iso,
        "candidates": hypotheses,  # hypotheses ARE candidates (superset)
    }
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(output, f, indent=2)

    # hypotheses.json — full objects for reflection layer (Phase 5)
    hypotheses_output = {
        "timestamp": ts_iso,
        "count": len(hypotheses),
        "hypotheses": hypotheses,
    }
    with open(HYPOTHESES_FILE, "w") as f:
        json.dump(hypotheses_output, f, indent=2)

    write_heartbeat()

    # ── LOG ALL SIGNAL FIRES (for training dataset) ──
    # Captures every candidate that fired — including filtered/killed ones.
    # Non-blocking: wrapped in try/except so any failure is logged, not raised.
    try:
        from scanner.indicators.signal_logger import log_all_fires
        all_fired = []
        for h in hypotheses:
            all_fired.append({
                "coin":          h.get("coin"),
                "direction":     h.get("direction"),
                "signal_name":   h.get("signal"),
                "score":         h.get("composite_score"),
                "passed_filter": True,
            })
        # Also capture rule-killed candidates if accessible
        if all_fired:
            load_world = {}
            if WORLD_STATE_FILE.exists():
                try:
                    with open(WORLD_STATE_FILE) as _f:
                        load_world = json.load(_f)
                except Exception:
                    pass
            log_all_fires(all_fired, load_world)
    except Exception as _e:
        print(f"  [signal_logger] non-fatal: {_e}")

    # ── SUMMARY ──
    print(f"\n  Evaluated: {stats['evaluated']}")
    print(f"  Filtered (quality): {stats['filtered']}")
    print(f"  Missing data: {stats['missing_data']}")
    print(f"  Entry fired: {stats['fired']}")
    print(f"  Candidates/Hypotheses written: {len(hypotheses)}")
    print(f"  Episodes written: {episodes_written}")

    if hypotheses:
        print(f"\n  Top hypotheses:")
        for h in hypotheses[:5]:
            regime_tag = "✓" if h.get("regime_match") else "✗"
            print(
                f"    {h['composite_score']:5.2f}  {h['coin']:6s} {h['direction']:5s}  "
                f"conf={h.get('confidence', 0):.2f}  "
                f"sharpe={h['sharpe']:.2f}  wr={h['win_rate']:.0f}%  "
                f"heat={h['signal_heat']:.2f}  regime={h['regime']}({regime_tag})  "
                f"{h['recent_record']}"
            )
            print(f"           {h['signal']}")
            print(f"           thesis: {h.get('thesis', '')[:80]}")
            if h.get('evidence_against'):
                print(f"           anti:   {h.get('anti_thesis', '')[:80]}")

    print(f"\n  Written to {CANDIDATES_FILE}")
    print(f"  Written to {HYPOTHESES_FILE}")
    print(f"  Episodes in {EPISODES_DIR}")
    print(f"{'='*60}\n")


def main():
    api_key = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Hypothesis Generator starting in loop mode (every {CYCLE_SECONDS}s)")
        while True:
            try:
                run_cycle(api_key)
            except Exception as e:
                print(f"  [error] Cycle failed: {e}")
                import traceback
                traceback.print_exc()
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle(api_key)


if __name__ == "__main__":
    main()

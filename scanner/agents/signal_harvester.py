#!/usr/bin/env python3
"""
ZERO OS — Agent 2: Signal Harvester
Evaluates signal packs against current Envy indicators, produces scored
trade candidates ranked by composite score and regime alignment.

Inputs:
  scanner/data/signals_cache/*.json  — 30 signal packs per coin
  scanner/bus/regimes.json           — regime state from Agent 1
  scanner/data/closed.jsonl          — historical closed trades
  Envy API indicator snapshots       — current indicator values

Outputs:
  scanner/bus/candidates.json        — scored trade candidates
  scanner/bus/heartbeat.json         — last-alive timestamp

Usage:
  python3 scanner/agents/signal_harvester.py           # single run
  python3 scanner/agents/signal_harvester.py --loop    # continuous 10-min cycle
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

# ─── PATHS ───
AGENT_DIR = Path(__file__).parent
SCANNER_DIR = AGENT_DIR.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data"
SIGNALS_CACHE_DIR = DATA_DIR / "signals_cache"
CLOSED_FILE = DATA_DIR / "closed.jsonl"
REGIMES_FILE = BUS_DIR / "regimes.json"
CANDIDATES_FILE = BUS_DIR / "candidates.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

# ─── CONFIG ───
BASE_URL = "https://gate.getzero.dev/api/claw"
CYCLE_SECONDS = 600  # 10 minutes
COINS_PER_REQUEST = 10
INDICATORS_PER_REQUEST = 7
MIN_SHARPE = 1.0
MIN_WIN_RATE = 55
CHAOTIC_MIN_SHARPE = 2.5

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
    # Split indicators into batches of INDICATORS_PER_REQUEST
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
                # Retry individually
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
# All coins with both Envy API signals AND Hyperliquid markets
SIGNAL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]
PACK_TYPES = ["common", "rare", "trump"]
CACHE_MAX_AGE_SECONDS = 3600  # refresh every hour


def refresh_signal_cache(api_key):
    """Fetch fresh signal packs from Envy API (YAML format) and save as JSON."""
    SIGNALS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Check if refresh is needed
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
            time.sleep(0.3)  # rate limit

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


# ─── REGIME ───
def load_regimes():
    """Load current regime state from Agent 1."""
    if REGIMES_FILE.exists() and REGIMES_FILE.stat().st_size > 0:
        try:
            with open(REGIMES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# ─── SIGNAL HEAT ───
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
                trades.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return trades


def compute_signal_heat(signal_name, closed_trades):
    """
    Score 0-1 based on recent performance of this signal.
    Decay: most recent trades weighted via recency bonus.
    """
    trades = [t for t in closed_trades if t.get("signal") == signal_name]
    if len(trades) < 3:
        return 0.5  # neutral — not enough data

    recent = trades[-10:]
    wins = sum(1 for t in recent if t.get("pnl_dollars", 0) > 0)
    total = len(recent)
    recency_bonus = 0.1 if trades[-1].get("pnl_dollars", 0) > 0 else -0.1

    return min(1.0, max(0.0, wins / total + recency_bonus))


def compute_recent_record(signal_name, closed_trades):
    """Return W/L string for recent trades."""
    trades = [t for t in closed_trades if t.get("signal") == signal_name]
    recent = trades[-10:]
    if not recent:
        return "0W/0L"
    wins = sum(1 for t in recent if t.get("pnl_dollars", 0) > 0)
    losses = len(recent) - wins
    return f"{wins}W/{losses}L"


# ─── REGIME MATCHING ───
def classify_signal_style(signal_name):
    """Classify a signal as momentum-style or reversal-style based on its name."""
    upper = signal_name.upper()
    momentum_score = sum(1 for kw in MOMENTUM_KEYWORDS if kw in upper)
    reversal_score = sum(1 for kw in REVERSAL_KEYWORDS if kw in upper)
    if momentum_score > reversal_score:
        return "momentum"
    if reversal_score > momentum_score:
        return "reversal"
    return "neutral"


def compute_regime_match(signal_name, regime):
    """
    Score regime alignment:
      trending + momentum = +1.5
      reverting + reversal = +1.5
      chaotic = -1.0 penalty
      otherwise = 0
    """
    if regime == "chaotic":
        return -1.0

    style = classify_signal_style(signal_name)

    if regime == "trending" and style == "momentum":
        return 1.5
    if regime == "reverting" and style == "reversal":
        return 1.5
    if regime == "shift":
        return 0.0  # neutral during transitions
    if regime == "stable":
        return 0.5  # slight bonus, low vol is okay
    return 0.0


# ─── EXPRESSION EVALUATION ───
def _evaluate_weighted_expression(expression, indicator_values):
    """Evaluate weighted sum expressions like:
    ((RSI_12H <= 42) * 3) + ((MACD_N_12H <= -0.0025) * 3) + ((EMA_N_24H <= 0.993) * 2) >= 4

    Each term: ((INDICATOR OP VALUE) * WEIGHT) evaluates to WEIGHT if condition is true, 0 otherwise.
    Sum of weights compared against final threshold.
    """
    missing = []

    # Extract the final threshold: ... >= THRESHOLD or ... > THRESHOLD at the end
    threshold_match = re.search(r'\)\s*(>=|>|<=|<)\s*(-?[\d.]+)\s*$', expression)
    if not threshold_match:
        return False, missing  # can't parse threshold, reject

    threshold_op = threshold_match.group(1)
    threshold_val = float(threshold_match.group(2))

    # Extract all weighted terms: ((INDICATOR OP VALUE) * WEIGHT)
    terms = re.findall(
        r'\(\(([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)\)\s*\*\s*([\d.]+)\)',
        expression
    )

    if not terms:
        return False, missing  # no parseable terms, reject

    weighted_sum = 0.0
    for indicator, op, val_str, weight_str in terms:
        val = float(val_str)
        weight = float(weight_str)

        current = indicator_values.get(indicator)
        if current is None:
            missing.append(indicator)
            continue  # missing indicator contributes 0

        condition = False
        if op == ">=":   condition = current >= val
        elif op == "<=": condition = current <= val
        elif op == ">":  condition = current > val
        elif op == "<":  condition = current < val
        elif op == "==": condition = current == val
        elif op == "!=": condition = current != val

        if condition:
            weighted_sum += weight

    # Compare sum against threshold
    if threshold_op == ">=":   result = weighted_sum >= threshold_val
    elif threshold_op == ">":  result = weighted_sum > threshold_val
    elif threshold_op == "<=": result = weighted_sum <= threshold_val
    elif threshold_op == "<":  result = weighted_sum < threshold_val
    else: result = False

    return result, missing


def evaluate_expression(expression, indicator_values):
    """
    Evaluate a signal entry/exit expression against current indicator values.
    Expressions are like: "BB_POSITION_15M >= 0.87 AND EMA_CROSS_15M_N <= -0.0008"

    Returns (True/False, list of missing indicators).
    """
    if not expression or not expression.strip():
        return False, []

    missing = []

    # Detect weighted expressions: ((INDICATOR OP VALUE) * WEIGHT) + ... >= THRESHOLD
    if "((" in expression and "*" in expression:
        return _evaluate_weighted_expression(expression, indicator_values)

    # Split on AND/OR while preserving the operator
    # We handle AND/OR with simple left-to-right evaluation
    # (all packs use AND predominantly, some use OR)
    clauses = re.split(r'\s+(AND|OR)\s+', expression)

    # clauses is like: [condition, 'AND', condition, 'OR', condition, ...]
    results = []
    operators = []

    for part in clauses:
        part = part.strip()
        if part in ("AND", "OR"):
            operators.append(part)
            continue

        # Parse comparison: INDICATOR_CODE OP VALUE
        m = re.match(r'([A-Z][A-Z0-9_]+)\s*(>=|<=|>|<|==|!=)\s*(-?[\d.]+)', part)
        if not m:
            # Can't parse this clause — reject to avoid false positives
            results.append(False)
            continue

        indicator, op, val_str = m.group(1), m.group(2), m.group(3)
        val = float(val_str)

        current = indicator_values.get(indicator)
        if current is None:
            missing.append(indicator)
            results.append(False)
            continue

        if op == ">=":
            results.append(current >= val)
        elif op == "<=":
            results.append(current <= val)
        elif op == ">":
            results.append(current > val)
        elif op == "<":
            results.append(current < val)
        elif op == "==":
            results.append(current == val)
        elif op == "!=":
            results.append(current != val)
        else:
            results.append(False)

    if not results:
        return False, missing

    # Combine: AND/OR left-to-right
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
    """
    Composite score (0-10):
    (sharpe * 1.5) + (win_rate/100 * 3) + (signal_heat * 2) + (regime_match * 1.5)
    """
    score = (sharpe * 1.5) + (win_rate / 100 * 3) + (signal_heat * 2) + (regime_match * 1.5)
    return round(max(0.0, min(10.0, score)), 2)


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
    heartbeat["harvester"] = ts
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(heartbeat, f, indent=2)


# ─── ARCHETYPE SIGNALS ───
# S-tier multi-domain convergence signals that combine chaos theory,
# social sentiment, funding rates, cross-timeframe, and technical structure.

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
                score = 7.0 + abs(h_diff) * 5  # stronger divergence = higher score
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

        # ── ARCHETYPE 3: Triple Convergence (Funding + Regime + Timeframe) ──
        f_rate = 0
        if coin_funding:
            f_rate = coin_funding.get("strength", 0)
        
        # LONG triple
        if (regime == "trending" and h24 > 0.55 and ema24 > 1.005 and macd24 > 0
                and tf_pattern == "CONFIRMATION_LONG"):
            score = 7.5
            if coin_funding and coin_funding.get("direction") == "LONG":
                score += coin_funding.get("strength", 0)  # up to +1.5
            if score >= 7.5:
                candidates.append(_archetype_candidate(
                    coin, "LONG", "ARCH_TRIPLE_CONVERGENCE_LONG", min(score, 10.0),
                    "HURST_24H < 0.50 OR EMA_N_24H < 1.0",
                    48, regime, tf_pattern, ts_iso
                ))
        
        # SHORT triple
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
            score = 6.5 + min(doji_v / 10, 1.5)  # higher velocity = more conviction
            candidates.append(_archetype_candidate(
                coin, direction, "ARCH_DOJI_BREAKOUT", score,
                "ADX_3H30M >= 40 OR DOJI_VELOCITY < 1.0",
                8, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 5b: Multi-Timeframe Momentum Alignment ──
        # Uses 6h + 12h + 24h — the gap we were missing
        rsi_6h = ind.get("RSI_6H", 50)
        rsi_12h = ind.get("RSI_12H", 50)
        rsi_24h = ind.get("RSI_24H", 50)
        roc_6h = ind.get("ROC_6H", 0)
        roc_12h = ind.get("ROC_12H", 0)
        roc_24h = ind.get("ROC_24H", 0)
        macd_6h = ind.get("MACD_N_6H", 0)
        macd_12h = ind.get("MACD_N_12H", 0)
        ema_6h = ind.get("EMA_N_6H", 1.0)
        ema_12h = ind.get("EMA_N_12H", 1.0)
        bb_6h = ind.get("BB_POS_6H", 0.5)
        rsi_48h = ind.get("RSI_48H", 50)
        roc_48h = ind.get("ROC_48H", 0)

        # LONG: all 3 timeframes bullish
        if (rsi_6h > 55 and rsi_12h > 55 and rsi_24h > 45
                and roc_6h > 0 and roc_12h > 0 and macd_6h > 0 and macd_12h > 0
                and ema_6h > 1.001 and ema_12h > 1.001):
            score = 7.0 + min((rsi_6h - 55) / 20, 1.0)
            candidates.append(_archetype_candidate(
                coin, "LONG", "ARCH_MTF_MOMENTUM_LONG", score,
                "RSI_6H < 45 OR ROC_12H < 0",
                24, regime, tf_pattern, ts_iso
            ))

        # SHORT: all 3 timeframes bearish
        if (rsi_6h < 45 and rsi_12h < 45 and rsi_24h < 55
                and roc_6h < 0 and roc_12h < 0 and macd_6h < 0 and macd_12h < 0
                and ema_6h < 0.999 and ema_12h < 0.999):
            score = 7.0 + min((45 - rsi_6h) / 20, 1.0)
            candidates.append(_archetype_candidate(
                coin, "SHORT", "ARCH_MTF_MOMENTUM_SHORT", score,
                "RSI_6H > 55 OR ROC_12H > 0",
                24, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 5c: DOJI Reversal Predictor ──
        # Uses the proprietary DOJI predictor — unique edge
        doji_dist = ind.get("DOJI_DISTANCE", 999)
        doji_dist_l = ind.get("DOJI_DISTANCE_L", 999)
        doji_vel = ind.get("DOJI_VELOCITY", 0)
        doji_vel_l = ind.get("DOJI_VELOCITY_L", 0)
        doji_sig = ind.get("DOJI_SIGNAL", 0)
        doji_sig_l = ind.get("DOJI_SIGNAL_L", 0)

        # SHORT: doji signal fired + approaching from above + bearish momentum
        if doji_sig >= 1.0 and doji_dist < 5 and doji_vel < -2 and rsi > 60:
            score = 7.5 + min(abs(doji_vel) / 5, 1.5)
            candidates.append(_archetype_candidate(
                coin, "SHORT", "ARCH_DOJI_REVERSAL_SHORT", score,
                "DOJI_DISTANCE > 15 OR RSI_3H30M < 40",
                12, regime, tf_pattern, ts_iso
            ))

        # LONG: doji_l signal fired + approaching from below + bullish momentum
        if doji_sig_l >= 1.0 and doji_dist_l < 5 and doji_vel_l > 2 and rsi < 40:
            score = 7.5 + min(abs(doji_vel_l) / 5, 1.5)
            candidates.append(_archetype_candidate(
                coin, "LONG", "ARCH_DOJI_REVERSAL_LONG", score,
                "DOJI_DISTANCE_L > 15 OR RSI_3H30M > 60",
                12, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 5d: BB Squeeze Breakout ──
        # Uses raw BB bands for squeeze detection
        bb_upper = ind.get("BB_UPPER_5H_N", 1.01)
        bb_lower = ind.get("BB_LOWER_5H_N", 0.99)
        bb_width = bb_upper - bb_lower
        if bb_width < 0.012 and adx < 20:  # Tight squeeze + low ADX = breakout imminent
            mom = ind.get("MOMENTUM_2H30M_N", 0)
            direction = "LONG" if mom > 0 else "SHORT"
            score = 7.0 + (0.012 - bb_width) * 200  # tighter = higher score
            candidates.append(_archetype_candidate(
                coin, direction, "ARCH_BB_SQUEEZE_BREAKOUT", min(score, 9.0),
                "BB_UPPER_5H_N - BB_LOWER_5H_N > 0.025 OR ADX_3H30M > 40",
                16, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 5e: 48h Trend Exhaustion ──
        # Long-term trend losing steam
        if rsi_48h < 30 and roc_48h < -5 and rsi < 35 and h24 > 0.55:
            # Extreme oversold on 48h + persistent trend = potential reversal LONG
            score = 7.0 + min((30 - rsi_48h) / 10, 1.5)
            candidates.append(_archetype_candidate(
                coin, "LONG", "ARCH_48H_EXHAUSTION_LONG", score,
                "RSI_48H > 50 OR ROC_48H > 0",
                48, regime, tf_pattern, ts_iso
            ))
        if rsi_48h > 70 and roc_48h > 5 and rsi > 65 and h24 > 0.55:
            score = 7.0 + min((rsi_48h - 70) / 10, 1.5)
            candidates.append(_archetype_candidate(
                coin, "SHORT", "ARCH_48H_EXHAUSTION_SHORT", score,
                "RSI_48H < 50 OR ROC_48H < 0",
                48, regime, tf_pattern, ts_iso
            ))

        # ── ARCHETYPE 6: Compound Killer (meta-signal) ──
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
        "sharpe": round(composite / 3, 2),  # approximate: high composite → high implied sharpe
        "win_rate": round(min(55 + composite * 3, 90), 1),  # estimated from conviction
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

    # Chaos regime (+2.0)
    if regime == "trending" and h24 > 0.55:
        score_long += 2.0
    if regime == "trending" and h24 < 0.45:
        score_short += 2.0
    if regime == "reverting":
        score_long += 1.0 if bb24 < 0.3 else 0
        score_short += 1.0 if bb24 > 0.7 else 0

    # Cross-timeframe (+1.5)
    if tf_pattern == "CONFIRMATION_LONG":
        score_long += 1.5
    if tf_pattern == "CONFIRMATION_SHORT":
        score_short += 1.5
    if tf_pattern == "DIVERGENCE_BULL":
        score_long += 0.8
    if tf_pattern == "DIVERGENCE_BEAR":
        score_short += 0.8

    # Funding (+1.5)
    if coin_funding:
        fd = coin_funding.get("direction")
        fs = coin_funding.get("strength", 0)
        if fd == "LONG" and fs > 0:
            score_long += min(fs, 1.5)
        if fd == "SHORT" and fs > 0:
            score_short += min(fs, 1.5)

    # Social sentiment (+1.5)
    if xi > 70:
        score_long += 1.5
    elif xi > 60:
        score_long += 0.8
    if xi < 30:
        score_short += 1.5
    elif xi < 40:
        score_short += 0.8

    # Bollinger position (+1.0)
    if bb24 < 0.25:
        score_long += 1.0
    elif bb24 < 0.35:
        score_long += 0.5
    if bb24 > 0.75:
        score_short += 1.0
    elif bb24 > 0.65:
        score_short += 0.5

    # RSI room (+0.5)
    if 25 < rsi < 60:
        score_long += 0.5
    if 40 < rsi < 75:
        score_short += 0.5

    # Doji confirmation (+1.0)
    if doji_s >= 50:
        score_long += 0.5
        score_short += 0.5

    # Multi-timeframe alignment bonus (+1.5) — uses 6h + 12h
    rsi_6h = ind.get("RSI_6H", 50)
    rsi_12h = ind.get("RSI_12H", 50)
    roc_6h = ind.get("ROC_6H", 0)
    roc_12h = ind.get("ROC_12H", 0)

    long_align = sum([rsi_6h > 55, rsi_12h > 55, roc_6h > 0, roc_12h > 0])
    short_align = sum([rsi_6h < 45, rsi_12h < 45, roc_6h < 0, roc_12h < 0])
    if long_align >= 3:
        score_long += 0.5 * long_align  # up to +2.0
    if short_align >= 3:
        score_short += 0.5 * short_align

    # DOJI predictor distance bonus (+1.0) — closer = stronger signal
    doji_dist = ind.get("DOJI_DISTANCE", 999)
    doji_dist_l = ind.get("DOJI_DISTANCE_L", 999)
    if doji_dist < 5:
        score_short += 1.0
    elif doji_dist < 10:
        score_short += 0.5
    if doji_dist_l < 5:
        score_long += 1.0
    elif doji_dist_l < 10:
        score_long += 0.5

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


# ─── MAIN CYCLE ───
def run_cycle(api_key):
    ts = datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    print(f"\n{'='*60}")
    print(f"Signal Harvester — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

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

    # Load regime state
    regimes_data = load_regimes()
    regime_coins = regimes_data.get("coins", {})
    regime_ts = regimes_data.get("timestamp", "unknown")
    print(f"  Regime data from: {regime_ts}")

    # Load closed trades for signal heat
    closed_trades = load_closed_trades()
    print(f"  Loaded {len(closed_trades)} closed trades for heat scoring")

    # Load cross-timeframe signals (from Agent 7)
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

    # Load signal weights (from Agent 8)
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

    # Load funding data for convergence scoring
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

    # Extract all indicators needed from expressions
    # Also request volume profile + VWAP for scoring
    all_indicators = extract_indicators_from_packs(packs)
    volume_indicators = {"VWAP_15M", "VWAP_24H", "VOLUME_PROFILE_15M", "VOLUME_PROFILE_1H",
                         "VOLUME_PROFILE_4H", "VOLUME_PROFILE_12H", "VOLUME_PROFILE_24H", "VOLUME_PROFILE_48H"}
    all_indicators = list(set(all_indicators) | volume_indicators)
    print(f"  Need {len(all_indicators)} unique indicators (incl. volume profile)")

    # Fetch current indicator values from Envy API
    print(f"  Fetching indicators for {len(coins)} coins...")
    indicator_data = fetch_indicators(coins, all_indicators, api_key)
    print(f"  Got indicator data for {len(indicator_data)} coins")

    if not indicator_data:
        print("  [error] No indicator data returned, skipping cycle")
        write_heartbeat()
        return

    # Evaluate each signal pack
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

            # Basic quality filter
            if sharpe < MIN_SHARPE or win_rate < MIN_WIN_RATE:
                stats["filtered"] += 1
                continue

            # In chaotic regime, only allow very high sharpe signals
            if regime == "chaotic" and sharpe < CHAOTIC_MIN_SHARPE:
                stats["filtered"] += 1
                continue

            # Evaluate entry expression
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

            # Apply cross-timeframe confirmation/penalty
            tf_info = timeframe_data.get(coin, {})
            tf_pattern = tf_info.get("pattern", "")
            tf_conf = tf_info.get("confirmation_score", 0)
            if tf_pattern.startswith("CONFIRMATION") and (
                (direction == "LONG" and tf_pattern == "CONFIRMATION_LONG") or
                (direction == "SHORT" and tf_pattern == "CONFIRMATION_SHORT")
            ):
                composite *= 1.2  # 20% boost for timeframe confirmation
            elif tf_pattern.startswith("TRAP") and (
                (direction == "LONG" and tf_pattern == "TRAP_LONG") or
                (direction == "SHORT" and tf_pattern == "TRAP_SHORT")
            ):
                composite *= 0.7  # 30% penalty for trap signals

            # Apply signal evolution weight
            sig_weight = signal_weights.get(signal_name, 1.0)
            composite *= sig_weight

            # Volume profile scoring: trades near VWAP get a boost
            vwap_bonus = 0.0
            coin_inds = indicator_data.get(coin, {})
            close_px = coin_inds.get("CLOSE_PRICE_15M", 0)
            vwap = coin_inds.get("VWAP_24H", 0) or coin_inds.get("VWAP_15M", 0)
            if close_px > 0 and vwap > 0:
                distance_from_vwap = abs(close_px - vwap) / close_px
                if distance_from_vwap < 0.005:  # within 0.5% of VWAP
                    vwap_bonus = 0.5  # strong support/resistance
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
                    # Funding agrees with signal direction
                    funding_bonus = f_strength  # up to +1.5 for extreme convergence
                    composite += funding_bonus
                elif f_strength < 0:
                    # Chaotic + extreme funding = penalty
                    composite += f_strength  # negative

            composite = round(max(0.0, min(10.0, composite)), 2)

            candidates.append({
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
            })

    # ─── ARCHETYPE SIGNALS (multi-domain convergence) ───
    archetype_candidates = generate_archetype_signals(
        indicator_data, regime_coins, timeframe_data, funding_data, ts_iso
    )
    if archetype_candidates:
        print(f"\n  Archetype signals fired: {len(archetype_candidates)}")
        for ac in archetype_candidates:
            print(f"    {ac['composite_score']:5.2f}  {ac['coin']:6s} {ac['direction']:5s}  {ac['signal']}")
        candidates.extend(archetype_candidates)

    # Sort by composite score descending
    candidates.sort(key=lambda c: c["composite_score"], reverse=True)

    # Dedup: keep only best instance of each signal ID per cycle
    before = len(candidates)
    candidates = dedup_candidates(candidates)
    print(f"  Dedup: {before} → {len(candidates)} candidates ({before - len(candidates)} duplicates removed)")

    # Write output
    output = {
        "timestamp": ts_iso,
        "candidates": candidates,
    }
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(output, f, indent=2)

    write_heartbeat()

    # Summary
    print(f"\n  Evaluated: {stats['evaluated']}")
    print(f"  Filtered (quality): {stats['filtered']}")
    print(f"  Missing data: {stats['missing_data']}")
    print(f"  Entry fired: {stats['fired']}")
    print(f"  Candidates written: {len(candidates)}")

    if candidates:
        print(f"\n  Top candidates:")
        for c in candidates[:5]:
            regime_tag = "✓" if c["regime_match"] else "✗"
            print(
                f"    {c['composite_score']:5.2f}  {c['coin']:6s} {c['direction']:5s}  "
                f"sharpe={c['sharpe']:.2f}  wr={c['win_rate']:.0f}%  "
                f"heat={c['signal_heat']:.2f}  regime={c['regime']}({regime_tag})  "
                f"{c['recent_record']}"
            )
            print(f"           {c['signal']}")

    print(f"\n  Written to {CANDIDATES_FILE}")
    print(f"{'='*60}\n")


def main():
    api_key = load_api_key()
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print(f"Signal Harvester starting in loop mode (every {CYCLE_SECONDS}s)")
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

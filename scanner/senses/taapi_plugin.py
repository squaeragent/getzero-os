"""
ZERO OS — TAAPI.io SensePlugin.

Fetches pre-computed technical indicators from the TAAPI.io REST API
using bulk queries to minimize API call count.

Bulk endpoint: POST https://api.taapi.io/bulk
Format: {"secret": ..., "construct": {"exchange": ..., "symbol": ..., "interval": ..., "indicators": [...]}}
Max 20 indicators per bulk call.

For multiple constructs (Pro/Expert plan required), the construct field is an array.
This implementation uses single-construct bulk calls for compatibility with all plans.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from scanner.core.interfaces import Observation
from scanner.senses.base import SensePlugin

TAAPI_BULK_URL = "https://api.taapi.io/bulk"
EXCHANGE = "binance"
MAX_INDICATORS_PER_CALL = 18  # Conservative below the 20 hard limit

CORE_COINS = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX",
    "LINK", "ARB", "NEAR", "SUI", "INJ",
]


# ---------------------------------------------------------------------------
# API Key Loader
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HTTP Bulk Call
# ---------------------------------------------------------------------------

def _bulk_request(secret: str, construct: dict) -> list[dict]:
    """
    POST a single bulk request to TAAPI and return list of {id, result, errors} items.

    construct format:
    {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "interval": "1h",
        "indicators": [
            {"id": "RSI_24H", "indicator": "rsi", "period": 24},
            ...
        ]
    }
    """
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
    # TAAPI returns {"data": [...]}
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []


# ---------------------------------------------------------------------------
# Indicator Definitions
# ---------------------------------------------------------------------------

def _build_1h_batch_a(coin: str) -> tuple[dict, dict[str, str]]:
    """
    First batch of 1h indicators (18 max).
    RSI x4, EMA x4, MACD x4, BBANDS x4, ADX x1, CMO x1

    Returns (construct_dict, id_to_name_map)
    """
    sym = f"{coin}/USDT"
    indicators = [
        # RSI 4 periods
        {"id": "RSI_6H",  "indicator": "rsi", "period": 6},
        {"id": "RSI_12H", "indicator": "rsi", "period": 12},
        {"id": "RSI_24H", "indicator": "rsi", "period": 24},
        {"id": "RSI_48H", "indicator": "rsi", "period": 48},

        # EMA 4 periods
        {"id": "EMA_N_6H",  "indicator": "ema", "period": 6},
        {"id": "EMA_N_12H", "indicator": "ema", "period": 12},
        {"id": "EMA_N_24H", "indicator": "ema", "period": 24},
        {"id": "EMA_N_48H", "indicator": "ema", "period": 48},

        # MACD 4 variants
        {"id": "MACD_N_6H",  "indicator": "macd", "fastPeriod": 6,  "slowPeriod": 12,  "signalPeriod": 9},
        {"id": "MACD_N_12H", "indicator": "macd", "fastPeriod": 12, "slowPeriod": 26,  "signalPeriod": 9},
        {"id": "MACD_N_24H", "indicator": "macd", "fastPeriod": 24, "slowPeriod": 52,  "signalPeriod": 9},
        {"id": "MACD_N_48H", "indicator": "macd", "fastPeriod": 48, "slowPeriod": 104, "signalPeriod": 9},

        # BBANDS 4 periods
        {"id": "BB_POS_6H",  "indicator": "bbands", "period": 6},
        {"id": "BB_POS_12H", "indicator": "bbands", "period": 12},
        {"id": "BB_POS_24H", "indicator": "bbands", "period": 24},
        {"id": "BB_POS_48H", "indicator": "bbands", "period": 48},

        # ADX and CMO (period=14 on 1h ~ 14h, ENVY names for ~3h30m equiv)
        {"id": "ADX_3H30M", "indicator": "adx", "period": 14},
        {"id": "CMO_3H30M", "indicator": "cmo", "period": 14},
    ]
    construct = {
        "exchange": EXCHANGE,
        "symbol": sym,
        "interval": "1h",
        "indicators": indicators,
    }
    id_map = {ind["id"]: ind["id"] for ind in indicators}
    return construct, id_map


def _build_1h_batch_b(coin: str) -> tuple[dict, dict[str, str]]:
    """
    Second batch of 1h indicators.
    ROC x5, MOM x2, ICHIMOKU x1, ATR x1
    """
    sym = f"{coin}/USDT"
    indicators = [
        # ROC 5 periods
        {"id": "ROC_3H",  "indicator": "roc", "period": 3},
        {"id": "ROC_6H",  "indicator": "roc", "period": 6},
        {"id": "ROC_12H", "indicator": "roc", "period": 12},
        {"id": "ROC_24H", "indicator": "roc", "period": 24},
        {"id": "ROC_48H", "indicator": "roc", "period": 48},

        # Momentum 2 periods
        {"id": "MOMENTUM_N_6H",  "indicator": "mom", "period": 6},
        {"id": "MOMENTUM_N_12H", "indicator": "mom", "period": 12},

        # Ichimoku (single call, multi-value response)
        {"id": "ICHIMOKU", "indicator": "ichimoku"},

        # ATR 24 periods = 24h on 1h candles
        {"id": "ATR_24H", "indicator": "atr", "period": 24},
    ]
    construct = {
        "exchange": EXCHANGE,
        "symbol": sym,
        "interval": "1h",
        "indicators": indicators,
    }
    id_map = {ind["id"]: ind["id"] for ind in indicators}
    return construct, id_map


def _build_15m_batch(coin: str) -> tuple[dict, dict[str, str]]:
    """
    15m indicators for a coin.
    RSI_14, EMA_3, EMA_6, BBANDS_20, MACD_std
    """
    sym = f"{coin}/USDT"
    indicators = [
        # RSI 14 on 15m = 14×15min = 3.5h → maps to RSI_3H30M
        {"id": "RSI_3H30M_15M", "indicator": "rsi", "period": 14},

        # EMA 3 and EMA 6 for cross signal
        {"id": "EMA_3_15M", "indicator": "ema", "period": 3},
        {"id": "EMA_6_15M", "indicator": "ema", "period": 6},

        # Bollinger Bands 20-period
        {"id": "BB_POSITION_15M", "indicator": "bbands", "period": 20},

        # MACD standard
        {"id": "MACD_CROSS_15M", "indicator": "macd"},
    ]
    construct = {
        "exchange": EXCHANGE,
        "symbol": sym,
        "interval": "15m",
        "indicators": indicators,
    }
    id_map = {ind["id"]: ind["id"] for ind in indicators}
    return construct, id_map


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_indicator(indicator: str, raw_value: float, close_price: float = 1.0) -> Optional[float]:
    """
    Map raw TAAPI values to ENVY's 0-1 normalized scale.

    Mappings:
      RSI_*         → raw / 100             (0-100 → 0-1)
      EMA_N_*       → raw / close_price     (price ratio)
      MACD_N_*      → raw / close_price     (% of price)
      BB_POS_*      → already 0-1           (pass-through, clamped)
      BB_POSITION_* → already 0-1           (pass-through, clamped)
      ROC_*         → keep as percentage    (unbounded)
      ADX_*         → raw / 100             (0-100 → 0-1)
      CMO_*         → (raw + 100) / 200    (-100..100 → 0..1)
      MOMENTUM_N_*  → raw / close_price     (% of price)
      ATR_*         → raw / close_price     (% of price)
    """
    ind = indicator.upper()

    if ind.startswith("RSI_"):
        return max(0.0, min(1.0, raw_value / 100.0))

    if ind.startswith("EMA_N_") or (ind.startswith("EMA_") and not ind.startswith("EMA_CROSS")):
        return (raw_value / close_price) if close_price > 0 else raw_value

    if ind.startswith("MACD_N_") or ind.startswith("MACD_"):
        return (raw_value / close_price) if close_price > 0 else raw_value

    if ind.startswith("BB_POS") or ind.startswith("BB_POSITION"):
        return max(0.0, min(1.0, raw_value))

    if ind.startswith("ROC_"):
        return raw_value  # percentage, unbounded

    if ind.startswith("ADX_"):
        return max(0.0, min(1.0, raw_value / 100.0))

    if ind.startswith("CMO_"):
        return max(0.0, min(1.0, (raw_value + 100.0) / 200.0))

    if ind.startswith("MOMENTUM_"):
        return (raw_value / close_price) if close_price > 0 else raw_value

    if ind.startswith("ATR_"):
        return (raw_value / close_price) if close_price > 0 else raw_value

    if ind in ("TENKAN", "KIJUN", "SENKOU_A", "SENKOU_B"):
        return (raw_value / close_price) if close_price > 0 else raw_value

    return raw_value  # default: pass-through


# ---------------------------------------------------------------------------
# Main Plugin Class
# ---------------------------------------------------------------------------

class TaapiPlugin(SensePlugin):
    """
    Fetches pre-computed technical indicators from TAAPI.io.

    Uses POST /bulk with single-construct format (compatible with all plan tiers).
    Each coin requires 3 bulk calls (1h batch A, 1h batch B, 15m batch).
    Implements 200ms sleep between calls to respect rate limits.
    """

    name = "taapi"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._call_count: int = 0  # tracks calls this session

    def _get_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        self._api_key = _load_api_key()
        return self._api_key

    def _execute_bulk(self, secret: str, construct: dict) -> dict[str, Any]:
        """
        Execute one bulk API call. Returns dict mapping indicator id → result dict.
        """
        try:
            items = _bulk_request(secret, construct)
            self._call_count += 1
            results: dict[str, Any] = {}
            for item in items:
                item_id = item.get("id", "")
                result  = item.get("result", {})
                errors  = item.get("errors", [])
                if errors:
                    print(f"[taapi] WARN indicator {item_id}: errors={errors}")
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
                print(f"[taapi] WARN: Rate limit (429). Waiting 5s then retrying...")
                time.sleep(5)
                try:
                    items = _bulk_request(secret, construct)
                    self._call_count += 1
                    results = {}
                    for item in items:
                        item_id = item.get("id", "")
                        result  = item.get("result", {})
                        if item_id and result is not None:
                            results[item_id] = result
                    return results
                except Exception as retry_e:
                    print(f"[taapi] WARN: Retry failed: {retry_e}")
            else:
                print(f"[taapi] WARN: HTTP {e.code} — {body}")
        except Exception as e:
            print(f"[taapi] WARN: Bulk call failed: {e}")
        return {}

    def _parse_results_to_obs(
        self,
        coin: str,
        results: dict[str, Any],
        timestamp: float,
        close_price: float = 0.0,
    ) -> list[Observation]:
        """Parse raw TAAPI results into Observation objects."""
        observations: list[Observation] = []

        def add(indicator: str, raw: float) -> None:
            norm = normalize_indicator(indicator, raw, close_price)
            meta: dict = {"raw": raw}
            if norm is not None:
                meta["normalized"] = round(norm, 8)
            observations.append(Observation(
                coin=coin,
                dimension=f"taapi.{indicator}",
                value=raw,
                confidence=0.95,
                source="taapi",
                timestamp=timestamp,
                metadata=meta,
            ))

        # ── Simple single-value indicators ──
        simple_ids = [
            "RSI_6H", "RSI_12H", "RSI_24H", "RSI_48H",
            "EMA_N_6H", "EMA_N_12H", "EMA_N_24H", "EMA_N_48H",
            "ROC_3H", "ROC_6H", "ROC_12H", "ROC_24H", "ROC_48H",
            "ADX_3H30M", "CMO_3H30M",
            "MOMENTUM_N_6H", "MOMENTUM_N_12H",
            "ATR_24H",
            "RSI_3H30M_15M",  # will be renamed below
        ]
        for ind_id in simple_ids:
            r = results.get(ind_id)
            if not isinstance(r, dict):
                continue
            val = r.get("value")
            if val is None:
                continue
            try:
                raw = float(val)
            except (TypeError, ValueError):
                continue
            # Update close_price estimate from EMA_N_24H if not set
            if ind_id == "EMA_N_24H" and close_price == 0.0:
                close_price = raw
            # Rename 15m RSI to ENVY-compatible name
            out_name = "RSI_3H30M" if ind_id == "RSI_3H30M_15M" else ind_id
            add(out_name, raw)

        # ── MACD (multi-value: valueMACD, valueMACDSignal, valueMACDHist) ──
        for ind_id in ["MACD_N_6H", "MACD_N_12H", "MACD_N_24H", "MACD_N_48H"]:
            r = results.get(ind_id)
            if not isinstance(r, dict):
                continue
            macd_val = r.get("valueMACD")
            hist_val = r.get("valueMACDHist")
            if macd_val is not None:
                try:
                    add(ind_id, float(macd_val))
                except (TypeError, ValueError):
                    pass
            if hist_val is not None:
                try:
                    add(f"{ind_id}_HIST", float(hist_val))
                except (TypeError, ValueError):
                    pass

        # ── MACD 15m → MACD_CROSS_15M_N (histogram) ──
        r = results.get("MACD_CROSS_15M")
        if isinstance(r, dict):
            hist = r.get("valueMACDHist")
            if hist is not None:
                try:
                    add("MACD_CROSS_15M_N", float(hist))
                except (TypeError, ValueError):
                    pass

        # ── Bollinger Bands → compute position = (middle - lower) / (upper - lower) ──
        for ind_id in ["BB_POS_6H", "BB_POS_12H", "BB_POS_24H", "BB_POS_48H", "BB_POSITION_15M"]:
            r = results.get(ind_id)
            if not isinstance(r, dict):
                continue
            upper  = r.get("valueUpperBand")
            lower  = r.get("valueLowerBand")
            middle = r.get("valueMiddleBand")
            if upper is None or lower is None or middle is None:
                continue
            try:
                u, l, m = float(upper), float(lower), float(middle)
                if close_price == 0.0:
                    close_price = m  # use midband as price proxy
                band = u - l
                if band > 0:
                    pos = (m - l) / band  # midband as close proxy
                    add(ind_id, round(pos, 6))
            except (TypeError, ValueError):
                pass

        # ── EMA cross 15m → EMA_CROSS_15M_N ──
        r3 = results.get("EMA_3_15M")
        r6 = results.get("EMA_6_15M")
        if isinstance(r3, dict) and isinstance(r6, dict):
            v3 = r3.get("value")
            v6 = r6.get("value")
            if v3 is not None and v6 is not None:
                try:
                    e3, e6 = float(v3), float(v6)
                    if e6 > 0:
                        cross_pct = (e3 - e6) / e6 * 100
                        add("EMA_CROSS_15M_N", round(cross_pct, 6))
                    if close_price == 0.0:
                        close_price = e6
                except (TypeError, ValueError):
                    pass

        # ── Ichimoku → ENVY-compatible sub-indicators ──
        r = results.get("ICHIMOKU")
        if isinstance(r, dict):
            tenkan   = r.get("valueTenkanSen")
            kijun    = r.get("valueKijunSen")
            senkou_a = r.get("valueSenkouSpanA")
            senkou_b = r.get("valueSenkouSpanB")

            for name, val in [
                ("TENKAN",   tenkan),
                ("KIJUN",    kijun),
                ("SENKOU_A", senkou_a),
                ("SENKOU_B", senkou_b),
            ]:
                if val is not None:
                    try:
                        add(name, float(val))
                    except (TypeError, ValueError):
                        pass

            if all(v is not None for v in [tenkan, kijun, senkou_a, senkou_b]):
                try:
                    t  = float(tenkan)
                    sa = float(senkou_a)
                    sb = float(senkou_b)
                    cloud_top = max(sa, sb)
                    cloud_bot = min(sa, sb)
                    if t > cloud_top:
                        cloud_pos, bull = 1.0, 1.0
                    elif t < cloud_bot:
                        cloud_pos, bull = 0.0, 0.0
                    else:
                        cloud_pos, bull = 0.5, 0.5
                    add("CLOUD_POSITION", cloud_pos)
                    add("ICHIMOKU_BULL", bull)
                except (TypeError, ValueError):
                    pass

        return observations

    def normalize_to_envy_scale(
        self, indicator: str, raw_value: float, close_price: float = 1.0
    ) -> Optional[float]:
        """Public normalization stub — delegates to module-level normalize_indicator."""
        return normalize_indicator(indicator, raw_value, close_price)

    def fetch(self, coins: list[str]) -> list[Observation]:
        """
        Fetch TAAPI indicators for all given coins using bulk queries.

        Per coin: 3 bulk calls (1h batch A, 1h batch B, 15m batch).
        200ms sleep between each call.
        """
        secret = self._get_api_key()
        now = time.time()
        all_observations: list[Observation] = []

        for coin in coins:
            # Track close price across batches for normalization
            close_price: float = 0.0
            coin_results: dict[str, Any] = {}

            # Build all 3 constructs
            batches = [
                _build_1h_batch_a(coin),
                _build_1h_batch_b(coin),
                _build_15m_batch(coin),
            ]

            coin_ok = True
            for construct, _ in batches:
                results = self._execute_bulk(secret, construct)
                if not results:
                    print(
                        f"[taapi] WARN {coin}: empty result on batch "
                        f"(interval={construct['interval']}) — "
                        "coin may not be on Binance or API limit hit"
                    )
                    coin_ok = False
                    break
                coin_results.update(results)
                time.sleep(0.2)  # 200ms between calls

            if not coin_ok or not coin_results:
                continue

            # Estimate close price from EMA_N_24H result
            ema24_r = coin_results.get("EMA_N_24H")
            if isinstance(ema24_r, dict) and ema24_r.get("value") is not None:
                try:
                    close_price = float(ema24_r["value"])
                except (TypeError, ValueError):
                    pass

            obs = self._parse_results_to_obs(coin, coin_results, now, close_price)
            all_observations.extend(obs)
            print(f"[taapi] {coin}: {len(obs)} observations | total calls: {self._call_count}")

        print(f"[taapi] Run complete. Total API calls this session: {self._call_count}")
        return all_observations

    def health_check(self) -> dict:
        try:
            self._get_api_key()
            return {
                "name": self.name,
                "status": "ok",
                "calls_this_session": self._call_count,
            }
        except RuntimeError:
            return {"name": self.name, "status": "no_api_key"}


# ---------------------------------------------------------------------------
# Integration Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== TAAPI.io SensePlugin — Integration Test ===")
    print("Fetching BTC indicators from TAAPI.io...\n")

    try:
        plugin = TaapiPlugin()
        api_key = plugin._get_api_key()
        print(f"[taapi] API key loaded: {api_key[:8]}{'*' * max(0, len(api_key) - 8)}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    observations = plugin.fetch(["BTC"])

    if not observations:
        print("\nNo observations returned. Check API key or Binance availability.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Fetched {len(observations)} observations for BTC:")
    print(f"{'='*60}")
    for obs in sorted(observations, key=lambda o: o.dimension):
        norm_str = ""
        if "normalized" in obs.metadata:
            norm_str = f"  | normalized={obs.metadata['normalized']:.6f}"
        print(f"  {obs.dimension:<40}  raw={obs.value:<18.6f}{norm_str}")

    print(f"\n[taapi] Total API calls: {plugin._call_count}")
    print("\nDone.")

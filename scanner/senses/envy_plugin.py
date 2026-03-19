"""
ZERO OS — Envy API SensePlugin.

Fetches ALL 81 technical, chaos, predictor, and social indicators from the Envy API.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from scanner.core.interfaces import Observation
from scanner.senses.base import SensePlugin

ENVY_BASE_URL = "https://gate.getzero.dev/api/claw"
COINS_PER_REQUEST = 10

# ── ALL 81 INDICATORS ──────────────────────────────────────────────────────────
# Chaos (6)
CHAOS_INDICATORS = [
    "HURST_24H", "HURST_48H", "DFA_24H", "DFA_48H",
    "LYAPUNOV_24H", "LYAPUNOV_48H",
]

# Predictor (6)
PREDICTOR_INDICATORS = [
    "DOJI_SIGNAL", "DOJI_DISTANCE", "DOJI_VELOCITY",
    "DOJI_SIGNAL_L", "DOJI_DISTANCE_L", "DOJI_VELOCITY_L",
]

# Price (1)
PRICE_INDICATORS = ["CLOSE_PRICE_15M"]

# Social (7)
SOCIAL_INDICATORS = [
    "XONE_A_NET", "XONE_I_NET", "XONE_U_NET", "XONE_A_U_DIV",
    "XONE_AVG_NET_DELTA", "XONE_AVG_NET", "XONE_SPREAD",
]

# Technical — fast (3h30m / 15m / 2h30m)
TECH_FAST = [
    "RSI_3H30M", "ROC_3H", "ADX_3H30M", "CMO_3H30M", "ICHIMOKU_BULL",
    "BB_POSITION_15M", "CLOUD_POSITION_15M", "EMA_3H_N",
    "EMA_6H30M_N", "MACD_6H30M_N", "MACD_SIGNAL_2H15M_N",
    "BB_UPPER_5H_N", "BB_LOWER_5H_N",
    "TENKAN_2H15M_N", "KIJUN_6H30M_N", "SENKOU_A_6H30M_N",
    "SENKOU_B_13H_N", "CHIKOU_6H30M_N",
    "MOMENTUM_2H30M_N", "EMA_CROSS_15M_N", "MACD_CROSS_15M_N",
    "TENKAN_KIJUN_CROSS_15M_N",
]

# Technical — 6h
TECH_6H = [
    "RSI_6H", "MACD_N_6H", "MOMENTUM_N_6H", "ROC_6H", "BB_POS_6H", "EMA_N_6H",
]

# Technical — 12h
TECH_12H = [
    "RSI_12H", "MACD_N_12H", "MOMENTUM_N_12H", "ROC_12H", "BB_POS_12H", "EMA_N_12H",
]

# Technical — 24h
TECH_24H = [
    "RSI_24H", "MACD_N_24H", "MOMENTUM_N_24H", "ROC_24H", "BB_POS_24H", "EMA_N_24H",
]

# Technical — 48h
TECH_48H = [
    "RSI_48H", "MACD_N_48H", "MOMENTUM_N_48H", "ROC_48H", "BB_POS_48H", "EMA_N_48H",
]

# TechnicalRaw (15)
TECH_RAW = [
    "EMA_3H", "EMA_6H30M", "MACD_6H30M", "MACD_SIGNAL_2H15M",
    "BB_UPPER_5H", "BB_LOWER_5H", "TENKAN_2H15M", "KIJUN_6H30M",
    "SENKOU_A_6H30M", "SENKOU_B_13H", "CHIKOU_6H30M",
    "MOMENTUM_2H30M", "EMA_CROSS_15M", "MACD_CROSS_15M", "TENKAN_KIJUN_CROSS_15M",
]

# Split into batches of 16 (API limit per request)
ALL_INDICATORS = (
    CHAOS_INDICATORS + PREDICTOR_INDICATORS + PRICE_INDICATORS +
    SOCIAL_INDICATORS + TECH_FAST + TECH_6H + TECH_12H +
    TECH_24H + TECH_48H + TECH_RAW
)

# Split into batches of 16 indicators per request
INDICATOR_BATCHES = [
    ALL_INDICATORS[i:i+16] for i in range(0, len(ALL_INDICATORS), 16)
]


def _load_api_key() -> str:
    key = os.environ.get("ENVY_API_KEY")
    if key:
        return key
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("ENVY_API_KEY="):
                    val = line.split("=", 1)[1]
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("ENVY_API_KEY not found in env or ~/.config/openclaw/.env")


def _envy_get(path: str, params: dict, api_key: str) -> dict:
    url = f"{ENVY_BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_snapshot(snapshot: dict) -> dict:
    result = {}
    for coin, ind_list in snapshot.items():
        if not isinstance(ind_list, list):
            continue
        values = {}
        for ind in ind_list:
            values[ind["indicatorCode"]] = ind["value"]
        result[coin] = values
    return result


def _fetch_all_indicators(
    coins: list[str], api_key: str
) -> dict[str, dict[str, float]]:
    """Fetch ALL 81 indicators for given coins.
    
    Batches by: coins (10 per request) × indicators (16 per request).
    Total API calls: ceil(coins/10) × ceil(81/16) = ceil(coins/10) × 6.
    For 37 coins: 4 × 6 = 24 API calls.
    """
    all_data: dict[str, dict[str, float]] = {}

    for coin_offset in range(0, len(coins), COINS_PER_REQUEST):
        coin_batch = coins[coin_offset:coin_offset + COINS_PER_REQUEST]
        coins_param = ",".join(coin_batch)

        for ind_batch in INDICATOR_BATCHES:
            ind_param = ",".join(ind_batch)
            try:
                resp = _envy_get(
                    "/paid/indicators/snapshot",
                    {"coins": coins_param, "indicators": ind_param},
                    api_key,
                )
                parsed = _parse_snapshot(resp.get("snapshot", {}))
                for coin, vals in parsed.items():
                    all_data.setdefault(coin, {}).update(vals)
            except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
                # Single-coin fallback for failed batches
                for coin in coin_batch:
                    try:
                        resp = _envy_get(
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
            time.sleep(0.15)  # Rate limit between batches

        if coin_offset + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)  # Rate limit between coin batches

    return all_data


class EnvyPlugin(SensePlugin):
    """Fetches ALL 81 indicators from the Envy API."""

    name = "envy"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key

    def _get_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        self._api_key = _load_api_key()
        return self._api_key

    def fetch(self, coins: list[str]) -> list[Observation]:
        api_key = self._get_api_key()
        now = time.time()

        data = _fetch_all_indicators(coins, api_key)

        observations: list[Observation] = []
        for coin, indicators in data.items():
            for indicator_code, value in indicators.items():
                if value is None:
                    continue
                try:
                    val = float(value)
                except (TypeError, ValueError):
                    continue
                observations.append(Observation(
                    coin=coin,
                    dimension=f"envy.{indicator_code}",
                    value=val,
                    confidence=1.0,
                    source="envy",
                    timestamp=now,
                ))
        return observations

    def health_check(self) -> dict:
        try:
            self._get_api_key()
            return {"name": self.name, "status": "ok"}
        except RuntimeError:
            return {"name": self.name, "status": "no_api_key"}

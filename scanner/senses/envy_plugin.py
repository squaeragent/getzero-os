"""
ZERO OS — Envy API SensePlugin.

Fetches technical and chaos indicators from the Envy (OpenClaw) API.
Extracted from scanner/agents/perception.py.
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

FAST_INDICATORS = [
    "CLOSE_PRICE_15M", "RSI_3H30M", "EMA_CROSS_15M_N", "MACD_CROSS_15M_N",
    "BB_POSITION_15M", "CMO_3H30M", "ADX_3H30M", "MOMENTUM_2H30M_N",
    "EMA_3H_N", "CLOUD_POSITION_15M",
]

SLOW_AND_CHAOS_INDICATORS = [
    "RSI_24H", "EMA_N_24H", "MACD_N_24H", "ROC_24H",
    "HURST_24H", "HURST_48H", "DFA_24H", "DFA_48H",
    "LYAPUNOV_24H", "LYAPUNOV_48H", "BB_POS_24H",
    "MOMENTUM_N_24H", "EMA_N_48H",
]

ALL_INDICATORS = FAST_INDICATORS + SLOW_AND_CHAOS_INDICATORS
INDICATORS_PER_REQUEST = 16


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


def _fetch_indicators_batch(
    coins: list[str], indicators: list[str], api_key: str
) -> dict[str, dict[str, float]]:
    """Fetch indicators in batches of COINS_PER_REQUEST coins."""
    all_data: dict[str, dict[str, float]] = {}
    ind_param = ",".join(indicators)
    for i in range(0, len(coins), COINS_PER_REQUEST):
        batch = coins[i : i + COINS_PER_REQUEST]
        coins_param = ",".join(batch)
        try:
            resp = _envy_get(
                "/paid/indicators/snapshot",
                {"coins": coins_param, "indicators": ind_param},
                api_key,
            )
            parsed = _parse_snapshot(resp.get("snapshot", {}))
            for coin, vals in parsed.items():
                all_data.setdefault(coin, {}).update(vals)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError):
            for coin in batch:
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
        if i + COINS_PER_REQUEST < len(coins):
            time.sleep(0.3)
    return all_data


class EnvyPlugin(SensePlugin):
    """Fetches indicators from the Envy (OpenClaw) API."""

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

        fast_data = _fetch_indicators_batch(coins, FAST_INDICATORS, api_key)
        slow_data = _fetch_indicators_batch(coins, SLOW_AND_CHAOS_INDICATORS, api_key)

        observations: list[Observation] = []
        seen_coins = set(list(fast_data.keys()) + list(slow_data.keys()))
        for coin in seen_coins:
            merged = {}
            merged.update(fast_data.get(coin, {}))
            merged.update(slow_data.get(coin, {}))
            for indicator_code, value in merged.items():
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

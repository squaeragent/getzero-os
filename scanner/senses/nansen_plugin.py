"""
ZERO OS — Nansen SensePlugin

On-chain intelligence: smart money signals, holder changes, whale distribution.
Reads NANSEN_API_KEY from ~/.config/openclaw/.env.
Returns empty observations gracefully when key is missing or API is down.

Output dimensions:
  onchain.smart_money_buy_pressure   — 0-1 (1 = heavy smart money buying)
  onchain.smart_money_accumulating   — bool as float (1.0 = accumulating)
  onchain.whale_distribution         — 0-1 (1 = whales dumping)
  onchain.holder_growth_24h          — % change in unique holders over 24h
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from scanner.core.interfaces import Observation
from scanner.senses.base import SensePlugin

log = logging.getLogger("zero.nansen")

# ─── COIN → CONTRACT ADDRESS MAPPING (EVM) ────────────────────────────────────
# Nansen's token analytics endpoints take EVM contract addresses for most coins.
# ETH, BNB, AVAX use their native chain addresses. Non-EVM (SOL, etc.) fall back
# to symbol-based queries where supported.
COIN_CONTRACTS: dict[str, dict[str, str]] = {
    "BTC": {
        # WBTC on Ethereum (most liquid on-chain proxy)
        "address": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
        "chain": "ethereum",
        "symbol": "WBTC",
    },
    "ETH": {
        "address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "chain": "ethereum",
        "symbol": "WETH",
    },
    "SOL": {
        # No EVM address — use symbol fallback
        "address": None,
        "chain": "solana",
        "symbol": "SOL",
    },
    "AVAX": {
        "address": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",
        "chain": "avalanche",
        "symbol": "WAVAX",
    },
    "LINK": {
        "address": "0x514910771af9ca656af840dff83e8264ecf986ca",
        "chain": "ethereum",
        "symbol": "LINK",
    },
    "ARB": {
        "address": "0x912ce59144191c1204e64559fe8253a0e49e6548",
        "chain": "arbitrum",
        "symbol": "ARB",
    },
    "NEAR": {
        "address": None,
        "chain": "near",
        "symbol": "NEAR",
    },
    "SUI": {
        "address": None,
        "chain": "sui",
        "symbol": "SUI",
    },
    "INJ": {
        "address": "0xe28b3b32b6c345a34ff64674606124dd5aceca30",
        "chain": "ethereum",
        "symbol": "INJ",
    },
    "DOGE": {
        "address": None,
        "chain": "dogecoin",
        "symbol": "DOGE",
    },
    "OP": {
        "address": "0x4200000000000000000000000000000000000042",
        "chain": "optimism",
        "symbol": "OP",
    },
    "MATIC": {
        "address": "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0",
        "chain": "ethereum",
        "symbol": "MATIC",
    },
    "APT": {
        "address": None,
        "chain": "aptos",
        "symbol": "APT",
    },
    "TIA": {
        "address": None,
        "chain": "celestia",
        "symbol": "TIA",
    },
    "SEI": {
        "address": None,
        "chain": "sei",
        "symbol": "SEI",
    },
}

NANSEN_BASE = "https://api.nansen.ai"


def _load_env() -> dict[str, str]:
    env_path = Path("~/.config/openclaw/.env").expanduser()
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get(url: str, api_key: str, timeout: int = 10) -> dict | None:
    """GET request to Nansen API. Returns None on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "apiKey": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log.warning("Nansen API: unauthorized — check NANSEN_API_KEY")
        elif e.code == 429:
            log.warning("Nansen API: rate limited")
        elif e.code == 404:
            log.debug("Nansen API: 404 for %s", url)
        else:
            log.warning("Nansen API HTTP %s for %s", e.code, url)
        return None
    except urllib.error.URLError as e:
        log.warning("Nansen API network error: %s", e)
        return None
    except Exception as e:
        log.warning("Nansen API unexpected error: %s", e)
        return None


class NansenPlugin(SensePlugin):
    """
    On-chain intelligence from Nansen.
    Fetches smart money signals and holder metrics for tracked coins.
    """

    name = "nansen"

    def __init__(self):
        env = _load_env()
        self._api_key = env.get("NANSEN_API_KEY", "")
        if not self._api_key:
            log.info("NANSEN_API_KEY not set — Nansen plugin will return empty observations")

    # ─── INTERNAL FETCHERS ────────────────────────────────────────────────────

    def _fetch_smart_money_signals(self, address: str, chain: str) -> dict | None:
        """
        Fetch smart money flow signals for a token.
        Endpoint: /api/v1/token/smartmoney?tokenAddress=...&chain=...
        """
        if not address:
            return None
        url = f"{NANSEN_BASE}/api/v1/token/smartmoney?tokenAddress={address}&chain={chain}"
        return _get(url, self._api_key)

    def _fetch_holder_stats(self, address: str, chain: str) -> dict | None:
        """
        Fetch holder statistics (growth, whale concentration).
        Endpoint: /api/v1/token/holders?tokenAddress=...&chain=...
        """
        if not address:
            return None
        url = f"{NANSEN_BASE}/api/v1/token/holders?tokenAddress={address}&chain={chain}"
        return _get(url, self._api_key)

    def _fetch_whale_activity(self, address: str, chain: str) -> dict | None:
        """
        Fetch whale wallet activity for a token.
        Endpoint: /api/v1/token/whale-activity?tokenAddress=...&chain=...
        """
        if not address:
            return None
        url = f"{NANSEN_BASE}/api/v1/token/whale-activity?tokenAddress={address}&chain={chain}"
        return _get(url, self._api_key)

    # ─── SIGNAL EXTRACTORS ────────────────────────────────────────────────────

    def _extract_smart_money_buy_pressure(self, sm_data: dict | None) -> float | None:
        """
        Extract buy pressure score (0-1) from smart money response.
        Nansen returns net_flow, buy_volume, sell_volume.
        buy_pressure = buy_volume / (buy_volume + sell_volume)
        """
        if not sm_data:
            return None
        try:
            buy = float(sm_data.get("buy_volume") or sm_data.get("buyVolume") or 0)
            sell = float(sm_data.get("sell_volume") or sm_data.get("sellVolume") or 0)
            total = buy + sell
            if total <= 0:
                return None
            return round(buy / total, 4)
        except (TypeError, ValueError, KeyError):
            return None

    def _extract_smart_money_accumulating(self, sm_data: dict | None) -> float | None:
        """
        Returns 1.0 if smart money net flow is positive (accumulating), 0.0 if not.
        """
        if not sm_data:
            return None
        try:
            net_flow = float(
                sm_data.get("net_flow") or sm_data.get("netFlow") or
                sm_data.get("net_inflow") or 0
            )
            return 1.0 if net_flow > 0 else 0.0
        except (TypeError, ValueError):
            return None

    def _extract_whale_distribution(self, whale_data: dict | None) -> float | None:
        """
        Returns 0-1 score where 1.0 = heavy whale distribution (selling).
        Based on whale sell volume / total whale volume.
        """
        if not whale_data:
            return None
        try:
            sell = float(
                whale_data.get("whale_sell_volume") or whale_data.get("whaleSellVolume") or 0
            )
            buy = float(
                whale_data.get("whale_buy_volume") or whale_data.get("whaleBuyVolume") or 0
            )
            total = buy + sell
            if total <= 0:
                return None
            return round(sell / total, 4)
        except (TypeError, ValueError):
            return None

    def _extract_holder_growth(self, holder_data: dict | None) -> float | None:
        """
        Returns 24h holder count growth as a percentage.
        Positive = growing holder base. Negative = declining.
        """
        if not holder_data:
            return None
        try:
            # Try various field names Nansen might return
            growth = (
                holder_data.get("holder_growth_24h") or
                holder_data.get("holderGrowth24h") or
                holder_data.get("holder_change_24h_pct") or
                holder_data.get("holderChangePct24h")
            )
            if growth is not None:
                return round(float(growth), 4)

            # Fallback: compute from current vs 24h-ago counts
            current = float(holder_data.get("holder_count") or holder_data.get("holderCount") or 0)
            prev = float(holder_data.get("holder_count_24h_ago") or holder_data.get("holderCount24hAgo") or 0)
            if current > 0 and prev > 0:
                return round((current - prev) / prev * 100, 4)
            return None
        except (TypeError, ValueError):
            return None

    # ─── MAIN FETCH ───────────────────────────────────────────────────────────

    def fetch(self, coins: list[str]) -> list[Observation]:
        """
        Fetch on-chain observations for the given coins.
        Returns [] if NANSEN_API_KEY is not configured.
        """
        if not self._api_key:
            return []

        observations: list[Observation] = []
        now = time.time()

        for coin in coins:
            contract = COIN_CONTRACTS.get(coin)
            if not contract:
                log.debug("No contract mapping for %s — skipping Nansen lookup", coin)
                continue

            address = contract.get("address")
            chain = contract.get("chain", "ethereum")

            # ── Smart money signals ──
            sm_data = self._fetch_smart_money_signals(address, chain) if address else None

            buy_pressure = self._extract_smart_money_buy_pressure(sm_data)
            if buy_pressure is not None:
                observations.append(Observation(
                    coin=coin,
                    dimension="onchain.smart_money_buy_pressure",
                    value=buy_pressure,
                    confidence=0.75,
                    source="nansen",
                    timestamp=now,
                    metadata={"chain": chain, "address": address},
                ))

            accumulating = self._extract_smart_money_accumulating(sm_data)
            if accumulating is not None:
                observations.append(Observation(
                    coin=coin,
                    dimension="onchain.smart_money_accumulating",
                    value=accumulating,
                    confidence=0.70,
                    source="nansen",
                    timestamp=now,
                    metadata={"chain": chain, "address": address},
                ))

            # ── Whale activity ──
            whale_data = self._fetch_whale_activity(address, chain) if address else None

            whale_dist = self._extract_whale_distribution(whale_data)
            if whale_dist is not None:
                observations.append(Observation(
                    coin=coin,
                    dimension="onchain.whale_distribution",
                    value=whale_dist,
                    confidence=0.70,
                    source="nansen",
                    timestamp=now,
                    metadata={"chain": chain, "address": address},
                ))

            # ── Holder stats ──
            holder_data = self._fetch_holder_stats(address, chain) if address else None

            holder_growth = self._extract_holder_growth(holder_data)
            if holder_growth is not None:
                observations.append(Observation(
                    coin=coin,
                    dimension="onchain.holder_growth_24h",
                    value=holder_growth,
                    confidence=0.65,
                    source="nansen",
                    timestamp=now,
                    metadata={"chain": chain, "address": address},
                ))

            # Micro-delay to stay under rate limits
            time.sleep(0.2)

        log.info("Nansen: fetched %d observations for %d coins", len(observations), len(coins))
        return observations

    def health_check(self) -> dict:
        if not self._api_key:
            return {"name": self.name, "status": "disabled", "reason": "NANSEN_API_KEY not set"}

        # Ping with WBTC as a known token
        test_address = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
        url = f"{NANSEN_BASE}/api/v1/token/smartmoney?tokenAddress={test_address}&chain=ethereum"
        result = _get(url, self._api_key, timeout=5)
        if result is not None:
            return {"name": self.name, "status": "ok"}
        return {"name": self.name, "status": "error", "reason": "Nansen API unreachable or key invalid"}

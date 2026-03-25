#!/usr/bin/env python3
"""
Signal Provider abstraction — operating modes for self-protection.

Modes:
  SMART      — SmartProvider local signals (default, 7/10)
  CACHED     — Using cached data, quality depends on freshness
  BASIC      — Local RSI/EMA/MACD only, quality 3/10
  PROTECTION — No new trades, manage existing positions only
"""

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from scanner.v6.signal_cache import SignalCache


class SignalMode:
    SMART = "smart"
    CACHED = "cached"
    BASIC = "basic"
    PROTECTION = "protection"

    @staticmethod
    def quality(mode: str) -> int:
        return {
            "smart": 7, "cached": 7, "basic": 3, "protection": 0,
        }.get(mode, 0)


class SignalProvider(ABC):
    """Abstract signal provider interface."""

    @abstractmethod
    def check_signals(self, coin: str, expressions: list = None) -> dict:
        """Check/compute signals for a coin."""

    @abstractmethod
    def assemble_strategy(self, coin: str) -> dict:
        """Assemble optimal strategy for a coin."""

    @abstractmethod
    def optimize_portfolio(self, coins: list[str]) -> dict:
        """Optimize portfolio allocation across coins."""


class CachedProvider(SignalProvider):
    """Reads from local cache. Returns data with staleness info."""

    def __init__(self):
        self.cache = SignalCache()

    def check_signals(self, coin: str, expressions: list = None) -> dict:
        data = self.cache.load_signals(coin)
        if not data:
            return {"coin": coin, "signals": [], "error": "no_cache"}
        data["source"] = "cached"
        return data

    def assemble_strategy(self, coin: str) -> dict:
        data = self.cache.load_strategy(coin)
        if not data:
            return {"coin": coin, "signals": []}
        data["source"] = "cached"
        return data

    def optimize_portfolio(self, coins: list[str]) -> dict:
        data = self.cache.load_portfolio()
        if not data:
            return {}
        return data.get("allocations", {})


class BasicProvider(SignalProvider):
    """Local RSI/EMA/MACD using FREE HL WebSocket prices.

    Lower quality (5/10) but requires NO API key.
    """

    def __init__(self):
        from scanner.v6.basic_signals import BasicSignalEngine
        self.engine = BasicSignalEngine()

    def check_signals(self, coin: str, expressions: list = None) -> dict:
        return self.engine.check_signals(coin, expressions)

    def assemble_strategy(self, coin: str) -> dict:
        return self.engine.assemble_strategy(coin)

    def optimize_portfolio(self, coins: list[str]) -> dict:
        return self.engine.optimize_portfolio(coins)

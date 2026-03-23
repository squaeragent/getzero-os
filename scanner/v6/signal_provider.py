#!/usr/bin/env python3
"""
Signal Provider abstraction — 4 operating modes for self-protection.

Modes:
  FULL       — API available, full signal quality (10/10)
  CACHED     — Using cached data, quality depends on freshness
  BASIC      — Local RSI/EMA/MACD only, quality 5/10
  PROTECTION — No new trades, manage existing positions only
"""

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from scanner.v6.signal_cache import SignalCache


class SignalMode:
    SMART = "smart"
    FULL = "full"
    ENHANCED = "enhanced"
    CACHED = "cached"
    BASIC = "basic"
    PROTECTION = "protection"

    @staticmethod
    def quality(mode: str) -> int:
        return {
            "smart": 7, "enhanced": 9, "full": 10,
            "cached": 7, "basic": 3, "protection": 0,
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


class SignalAPIProvider(SignalProvider):
    """Full API provider — uses strategy_manager.py signal API functions.

    Wraps signal_api_get/signal_api_post_yaml with automatic cache saves.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.cache = SignalCache()
        self._import_api_funcs()

    def _import_api_funcs(self):
        from scanner.v6.strategy_manager import (
            signal_api_get, signal_api_post_yaml, parse_yaml_simple,
            score_signals, assemble_strategy as sm_assemble,
            optimize_portfolio as sm_optimize,
            load_cached_signals,
        )
        self._signal_api_get = signal_api_get
        self._signal_api_post_yaml = signal_api_post_yaml
        self._parse_yaml = parse_yaml_simple
        self._score_signals = score_signals
        self._sm_assemble = sm_assemble
        self._sm_optimize = sm_optimize
        self._load_cached_signals = load_cached_signals

    def check_signals(self, coin: str, expressions: list = None) -> dict:
        """Score signals via upstream signal API and cache the result."""
        raw_signals = self._load_cached_signals(coin)
        if not raw_signals:
            return {"coin": coin, "signals": [], "error": "no_cache"}

        scored = self._score_signals(coin, raw_signals, self.api_key)
        result = {"coin": coin, "signals": scored, "source": "signal_api"}

        # Cache every response
        self.cache.save_signals(coin, result)
        self.cache.save_metadata()
        return result

    def assemble_strategy(self, coin: str) -> dict:
        """Assemble strategy via upstream signal API and cache."""
        check = self.check_signals(coin)
        signals = check.get("signals", [])
        if not signals:
            return {"coin": coin, "signals": []}

        assembled = self._sm_assemble(coin, signals, self.api_key)
        result = {
            "coin": coin,
            "signals": assembled,
            "best_sharpe": max((s.get("sharpe", 0) for s in assembled), default=0),
            "signal_count": len(assembled),
            "source": "signal_api",
        }

        self.cache.save_strategy(coin, result)
        self.cache.save_metadata()
        return result

    def optimize_portfolio(self, coins: list[str]) -> dict:
        """Optimize portfolio via upstream signal API and cache."""
        result = self._sm_optimize(coins, self.api_key)
        if result:
            self.cache.save_portfolio({"allocations": result, "source": "signal_api"})
            self.cache.save_metadata()
        return result or {}


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

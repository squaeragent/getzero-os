#!/usr/bin/env python3
"""
Signal Manager — mode controller for self-protection.

Manages graceful degradation: FULL → CACHED → BASIC → PROTECTION.
Writes mode to bus/signal_mode.json for dashboard display.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner.v6.config import BUS_DIR, get_env
from scanner.v6.signal_cache import SignalCache, freshness
from scanner.v6.signal_provider import (
    SignalMode, SignalAPIProvider, CachedProvider, BasicProvider,
)

SIGNAL_MODE_FILE = BUS_DIR / "signal_mode.json"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [SIGMGR] {msg}", flush=True)


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


class SignalManager:
    """Manages signal mode transitions and provider selection."""

    def __init__(self):
        self.cache = SignalCache()
        self._mode = SignalMode.FULL
        self._last_mode_change = time.time()
        self._api_key = get_env("ENVY_API_KEY")
        self._providers = {}
        self._x402_monitor = None

    def _get_provider(self, mode: str):
        """Lazy-init providers."""
        if mode not in self._providers:
            if mode == SignalMode.FULL:
                self._providers[mode] = SignalAPIProvider(self._api_key)
            elif mode == SignalMode.CACHED:
                self._providers[mode] = CachedProvider()
            elif mode == SignalMode.BASIC:
                self._providers[mode] = BasicProvider()
        return self._providers.get(mode)

    def _get_x402_monitor(self):
        if self._x402_monitor is None:
            try:
                from scanner.v6.x402_monitor import X402Monitor
                self._x402_monitor = X402Monitor()
            except Exception:
                pass
        return self._x402_monitor

    def _set_mode(self, mode: str, reason: str = ""):
        if mode != self._mode:
            old = self._mode
            self._mode = mode
            self._last_mode_change = time.time()
            _log(f"MODE CHANGE: {old} → {mode} ({reason})")
            self._write_mode_file(reason)

            # Telegram alert on degradation
            if SignalMode.quality(mode) < SignalMode.quality(old):
                self._alert_mode_change(old, mode, reason)

    def _write_mode_file(self, reason: str = ""):
        data = {
            "mode": self._mode,
            "quality": SignalMode.quality(self._mode),
            "reason": reason,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "cache_freshness": self.cache.overall_freshness(),
        }
        # x402 status if available
        monitor = self._get_x402_monitor()
        if monitor:
            try:
                x402_status = monitor.get_status()
                data["x402"] = x402_status
            except Exception:
                pass
        _save_json(SIGNAL_MODE_FILE, data)

    def _alert_mode_change(self, old: str, new: str, reason: str):
        try:
            from scanner.v6.executor import send_alert
            send_alert(
                f"⚠️ Signal mode degraded: {old} → {new}\n"
                f"Reason: {reason}\n"
                f"Quality: {SignalMode.quality(old)}/10 → {SignalMode.quality(new)}/10"
            )
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────────────

    def get_mode(self) -> str:
        return self._mode

    def get_signals(self, coins: list[str]) -> dict:
        """Get signals for coins with automatic fallback.

        Returns: {coin: signal_data}
        Tries: FULL → CACHED → BASIC → PROTECTION
        """
        results = {}

        # Check x402 proactively
        monitor = self._get_x402_monitor()
        if monitor:
            try:
                if monitor.is_depleted():
                    self._set_mode(SignalMode.BASIC, "x402_depleted")
            except Exception:
                pass

        # Check NVArena credit balance from bus file (written by strategy_manager)
        try:
            from scanner.v6.config import BUS_DIR
            credit_file = BUS_DIR / "credit_status.json"
            if credit_file.exists():
                import json
                cdata = json.loads(credit_file.read_text())
                if cdata.get("is_revoked"):
                    self._set_mode(SignalMode.BASIC, "subscription_revoked")
                elif cdata.get("credits", 999999) <= 1000:
                    self._set_mode(SignalMode.CACHED, "credits_critical")
        except Exception:
            pass

        # Try FULL (API)
        if self._api_key or self._mode == SignalMode.FULL:
            provider = self._get_provider(SignalMode.FULL)
            if provider:
                try:
                    for coin in coins:
                        result = provider.check_signals(coin)
                        if result.get("signals") or not result.get("error"):
                            results[coin] = result
                    if results:
                        self._set_mode(SignalMode.FULL, "api_available")
                        return results
                except Exception as e:
                    _log(f"FULL provider failed: {e}")

        # Try CACHED
        _log("Falling back to CACHED provider")
        provider = self._get_provider(SignalMode.CACHED)
        if provider:
            cache_fresh = self.cache.overall_freshness()
            if cache_fresh != "expired":
                for coin in coins:
                    result = provider.check_signals(coin)
                    if result.get("signals"):
                        results[coin] = result
                if results:
                    self._set_mode(SignalMode.CACHED, f"cache_{cache_fresh}")
                    return results

        # Try BASIC (local indicators, FREE HL data)
        _log("Falling back to BASIC provider (local indicators)")
        provider = self._get_provider(SignalMode.BASIC)
        if provider:
            try:
                for coin in coins:
                    result = provider.check_signals(coin)
                    if result.get("signal") != "NEUTRAL" and not result.get("error"):
                        results[coin] = result
                if results:
                    self._set_mode(SignalMode.BASIC, "local_indicators")
                    return results
            except Exception as e:
                _log(f"BASIC provider failed: {e}")

        # PROTECTION mode — no signals, manage existing only
        self._set_mode(SignalMode.PROTECTION, "all_providers_failed")
        return {}

    def get_strategies(self, coins: list[str]) -> dict:
        """Get assembled strategies for coins with fallback.

        Returns: {coin: strategy_data}
        """
        results = {}
        mode = self._mode

        # Use appropriate provider based on current mode
        if mode == SignalMode.PROTECTION:
            return {}

        provider = self._get_provider(mode)
        if not provider:
            return {}

        for coin in coins:
            try:
                result = provider.assemble_strategy(coin)
                if result.get("signals"):
                    results[coin] = result
            except Exception as e:
                _log(f"Strategy assembly failed for {coin}: {e}")

        return results

    def get_portfolio_allocation(self, coins: list[str]) -> dict:
        """Get portfolio allocation with fallback."""
        mode = self._mode
        if mode == SignalMode.PROTECTION:
            return {}

        provider = self._get_provider(mode)
        if not provider:
            return {}

        try:
            return provider.optimize_portfolio(coins)
        except Exception as e:
            _log(f"Portfolio optimization failed: {e}")
            return {}

    def position_size_multiplier(self) -> float:
        """Position size multiplier based on cache freshness.

        FULL: 1.0 (normal)
        CACHED fresh: 1.0
        CACHED aging: 0.5 (reduce 50%)
        CACHED stale: 0.0 (no new entries)
        BASIC: 0.5 (reduced confidence)
        PROTECTION: 0.0
        """
        if self._mode == SignalMode.FULL:
            return 1.0
        if self._mode == SignalMode.PROTECTION:
            return 0.0
        if self._mode == SignalMode.BASIC:
            return 0.5
        # CACHED — depends on freshness
        f = self.cache.overall_freshness()
        return {"fresh": 1.0, "aging": 0.5, "stale": 0.0, "expired": 0.0}.get(f, 0.0)


# ─── Singleton ───────────────────────────────────────────────────────────────

_instance = None


def get_signal_manager() -> SignalManager:
    global _instance
    if _instance is None:
        _instance = SignalManager()
    return _instance

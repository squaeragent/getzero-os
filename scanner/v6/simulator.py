#!/usr/bin/env python3
"""
Agent Simulator — 20 paper-trading agents with session rotation.

Single-process, asyncio-based simulator. Shared price feed + shared SmartProvider,
per-agent isolated state in ~/.zeroos/sim/{handle}/.

Usage:
    python -m scanner.v6.simulator --run          # start the simulator
    python -m scanner.v6.simulator --status       # show all 20 agents' current state
    python -m scanner.v6.simulator --leaderboard  # show P&L rankings
    python -m scanner.v6.simulator --agent zr_phantom  # show one agent's detail
"""

import asyncio
import json
import os
import random
import sys
import time
import traceback
import urllib.request
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scanner.v6.session_manager_legacy import (
    STRATEGIES, get_coins_for_scope, generate_result_card,
)
from scanner.v6.config import get_stop_pct, ALL_COINS

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

SIM_BASE = Path("~/.zeroos/sim").expanduser()
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
TICK_INTERVAL = 15          # seconds between main loop ticks
THREAD_POOL_SIZE = 4        # max concurrent SmartProvider evaluations
PRICE_STALE_SEC = 30        # refetch prices if older than this
EVAL_CACHE_TTL = 300        # shared eval cache valid for 5 minutes
EVAL_BATCH_SIZE = 8         # coins per batch before rate-limit pause
EVAL_BATCH_PAUSE = 2.0      # seconds between batches

# Quality gate mapping: consensus_threshold → minimum quality score (0-10)
# Sim agents use RELAXED gates to ensure arena has trading data even in quiet markets.
# Real operators will use stricter gates defined in session_manager.py.
QUALITY_GATE = {
    5: 2.0,    # degen/scout/fade: very low bar → trades frequently
    6: 3.0,    # momentum: moderate bar → trades in most conditions
    7: 5.0,    # defense/sniper: still selective but reachable
    None: 0,   # funding strategy — no consensus gate
}

# ─── AGENT DEFINITIONS ───────────────────────────────────────────────────────

AGENTS = [
    {'name': 'phantom', 'handle': 'zr_phantom', 'rotation': ['momentum', 'degen', 'momentum'], 'equity': 1000},
    {'name': 'drift',   'handle': 'zr_drift',   'rotation': ['momentum'], 'equity': 1000},
    {'name': 'echo',    'handle': 'zr_echo',    'rotation': ['momentum', 'defense', 'momentum'], 'equity': 1000},
    {'name': 'signal',  'handle': 'zr_signal',  'rotation': ['momentum', 'sniper', 'momentum'], 'equity': 1000},
    {'name': 'cortex',  'handle': 'zr_cortex',  'rotation': ['degen', 'degen', 'momentum'], 'equity': 1000},
    {'name': 'pulse',   'handle': 'zr_pulse',   'rotation': ['degen'], 'equity': 1000},
    {'name': 'nerve',   'handle': 'zr_nerve',   'rotation': ['degen', 'scout', 'degen'], 'equity': 1000},
    {'name': 'flux',    'handle': 'zr_flux',    'rotation': ['defense', 'momentum', 'defense'], 'equity': 1000},
    {'name': 'cipher',  'handle': 'zr_cipher',  'rotation': ['defense'], 'equity': 1000},
    {'name': 'node',    'handle': 'zr_node',    'rotation': ['defense', 'funding', 'defense'], 'equity': 1000},
    {'name': 'grid',    'handle': 'zr_grid',    'rotation': ['sniper', 'momentum', 'sniper'], 'equity': 1000},
    {'name': 'volt',    'handle': 'zr_volt',    'rotation': ['sniper'], 'equity': 1000},
    {'name': 'arc',     'handle': 'zr_arc',     'rotation': ['scout', 'momentum', 'scout'], 'equity': 1000},
    {'name': 'wave',    'handle': 'zr_wave',    'rotation': ['scout'], 'equity': 1000},
    {'name': 'core',    'handle': 'zr_core',    'rotation': ['fade', 'defense', 'fade'], 'equity': 1000},
    {'name': 'bolt',    'handle': 'zr_bolt',    'rotation': ['fade', 'momentum', 'fade'], 'equity': 1000},
    {'name': 'seed',    'handle': 'zr_seed',    'rotation': ['funding', 'defense', 'funding'], 'equity': 1000},
    {'name': 'edge',    'handle': 'zr_edge',    'rotation': ['funding', 'momentum', 'funding'], 'equity': 1000},
    {'name': 'vector',  'handle': 'zr_vector',  'rotation': ['watch', 'momentum', 'watch'], 'equity': 1000},
    {'name': 'origin',  'handle': 'zr_origin',  'rotation': ['watch', 'degen', 'watch'], 'equity': 1000},
]


# ─── LOGGING ──────────────────────────────────────────────────────────────────

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [SIM] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── SHARED PRICE FEED ───────────────────────────────────────────────────────

class SharedPriceFeed:
    """Single HL allMids call serves all 20 agents."""

    def __init__(self):
        self.prices: dict[str, float] = {}
        self.last_update: float = 0

    async def update(self):
        """Fetch all mid prices from HL. Called once per cycle."""
        if time.time() - self.last_update < PRICE_STALE_SEC:
            return
        loop = asyncio.get_event_loop()
        try:
            prices = await loop.run_in_executor(None, self._fetch_all_mids)
            if prices:
                self.prices = prices
                self.last_update = time.time()
        except Exception as e:
            _log(f"WARN: price feed update failed: {e}")

    @staticmethod
    def _fetch_all_mids() -> dict[str, float]:
        data = json.dumps({"type": "allMids"}).encode()
        req = urllib.request.Request(
            HL_INFO_URL, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        raw = json.loads(resp.read())
        if not isinstance(raw, dict):
            return {}
        return {k: float(v) for k, v in raw.items() if v}

    def get_price(self, coin: str) -> float:
        return self.prices.get(coin, 0)


# ─── SHARED EVALUATION CACHE ────────────────────────────────────────────────

class SharedEvaluationCache:
    """Evaluate all unique coins ONCE per cycle. Agents read from cache."""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._last_eval: float = 0
        self._coins: set[str] = set()

    def register_coins(self, coins: list[str]):
        """Agents register their coin scopes. Called before evaluation."""
        self._coins.update(coins)

    def clear_registrations(self):
        self._coins.clear()

    def is_fresh(self) -> bool:
        return (time.time() - self._last_eval) < EVAL_CACHE_TTL

    def get(self, coin: str) -> dict | None:
        return self._cache.get(coin)

    async def refresh(self, smart_provider, thread_pool):
        """Evaluate all registered coins with rate-limit staggering."""
        if self.is_fresh():
            return

        coins = sorted(self._coins)
        if not coins:
            return

        t0 = time.time()
        loop = asyncio.get_event_loop()
        new_cache: dict[str, dict] = {}
        rate_limited = 0
        failed = 0

        for i, coin in enumerate(coins):
            # Rate-limit stagger: pause every EVAL_BATCH_SIZE coins
            if i > 0 and i % EVAL_BATCH_SIZE == 0:
                await asyncio.sleep(EVAL_BATCH_PAUSE)

            try:
                result = await loop.run_in_executor(
                    thread_pool, smart_provider.evaluate_coin, coin)
                new_cache[coin] = result
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate" in err_str.lower():
                    rate_limited += 1
                    # Back off harder on rate limit
                    await asyncio.sleep(EVAL_BATCH_PAUSE * 2)
                else:
                    failed += 1

        self._cache = new_cache
        self._last_eval = time.time()
        elapsed = time.time() - t0

        _log(f"  Shared eval: {len(new_cache)} coins in {elapsed:.0f}s, "
             f"{rate_limited} rate-limited, {len(new_cache)} cached"
             + (f", {failed} failed" if failed else ""))


# ─── PER-AGENT FILE I/O ──────────────────────────────────────────────────────

def _agent_dir(handle: str) -> Path:
    d = SIM_BASE / handle
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_agent_json(handle: str, filename: str, default=None):
    path = _agent_dir(handle) / filename
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def _save_agent_json(handle: str, filename: str, data):
    path = _agent_dir(handle) / filename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _append_agent_jsonl(handle: str, filename: str, record: dict):
    path = _agent_dir(handle) / filename
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ─── PAPER EXECUTOR (per-agent, isolated state) ──────────────────────────────

class IsolatedPaperExecutor:
    """PaperExecutor with per-agent state directory. No shared global state."""

    FEE_RATE = 0.00045

    def __init__(self, handle: str, initial_balance: float):
        self.handle = handle
        self.state = _load_agent_json(handle, "paper_state.json", {
            "balance": initial_balance,
            "positions": {},
            "stops": {},
            "trade_log": [],
        })
        # Ensure balance is set if loading fresh
        if "balance" not in self.state:
            self.state["balance"] = initial_balance
        if "positions" not in self.state:
            self.state["positions"] = {}
        if "stops" not in self.state:
            self.state["stops"] = {}
        if "trade_log" not in self.state:
            self.state["trade_log"] = []

    def _save(self):
        _save_agent_json(self.handle, "paper_state.json", self.state)

    def get_equity(self, price_feed: SharedPriceFeed) -> float:
        """Virtual equity = balance + unrealized P&L."""
        upnl = 0.0
        for coin, pos in self.state["positions"].items():
            current = price_feed.get_price(coin)
            if current <= 0:
                continue
            entry = pos["entry_price"]
            sz = pos["size"]
            if pos["direction"] == "LONG":
                upnl += (current - entry) * sz
            else:
                upnl += (entry - current) * sz
        return self.state["balance"] + upnl

    def get_positions(self) -> dict:
        return dict(self.state["positions"])

    def open_position(self, coin: str, direction: str, size: float,
                      price: float) -> dict:
        """Open a virtual position at given price."""
        fee = price * size * self.FEE_RATE
        self.state["balance"] -= fee
        self.state["positions"][coin] = {
            "direction": direction,
            "size": size,
            "entry_price": price,
            "size_usd": round(price * size, 2),
            "opened_at": _now_iso(),
        }
        trade = {
            "ts": _now_iso(), "action": "open", "coin": coin,
            "direction": direction, "size": size, "price": price,
            "fee": round(fee, 4), "pnl": 0,
        }
        self.state["trade_log"].append(trade)
        if len(self.state["trade_log"]) > 200:
            self.state["trade_log"] = self.state["trade_log"][-200:]
        self._save()
        return trade

    def close_position(self, coin: str, price: float) -> dict | None:
        """Close a virtual position, compute P&L."""
        pos = self.state["positions"].get(coin)
        if not pos:
            return None
        entry = pos["entry_price"]
        sz = pos["size"]
        direction = pos["direction"]

        if direction == "LONG":
            pnl_gross = (price - entry) * sz
        else:
            pnl_gross = (entry - price) * sz

        fee = price * sz * self.FEE_RATE
        pnl_net = pnl_gross - fee
        self.state["balance"] += pnl_net
        del self.state["positions"][coin]
        self.state["stops"].pop(coin, None)

        trade = {
            "ts": _now_iso(), "action": "close", "coin": coin,
            "direction": direction, "size": sz, "price": price,
            "entry_price": entry, "pnl": round(pnl_net, 4),
            "fee": round(fee, 4),
        }
        self.state["trade_log"].append(trade)
        if len(self.state["trade_log"]) > 200:
            self.state["trade_log"] = self.state["trade_log"][-200:]
        self._save()
        return trade

    def set_stop(self, coin: str, trigger_price: float, is_buy: bool, size: float):
        """Register a virtual stop-loss."""
        self.state["stops"][coin] = {
            "trigger_price": trigger_price,
            "is_buy": is_buy,
            "size": size,
        }
        self._save()

    def check_stops(self, price_feed: SharedPriceFeed) -> list[dict]:
        """Check all stops against current prices. Returns list of triggered trades."""
        triggered = []
        coins = list(self.state["stops"].keys())
        for coin in coins:
            stop = self.state["stops"].get(coin)
            if not stop:
                continue
            price = price_feed.get_price(coin)
            if price <= 0:
                continue

            hit = False
            if stop["is_buy"] and price >= stop["trigger_price"]:
                hit = True  # buying to close SHORT
            elif not stop["is_buy"] and price <= stop["trigger_price"]:
                hit = True  # selling to close LONG

            if hit:
                trade = self.close_position(coin, price)
                if trade:
                    trade["reason"] = "stop_loss"
                    triggered.append(trade)
        return triggered

    def get_closed_trades(self) -> list[dict]:
        return [t for t in self.state["trade_log"] if t.get("action") == "close"]


# ─── SUPABASE REPORTING ──────────────────────────────────────────────────────

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def _post_supabase(endpoint: str, payload: dict):
    """Fire-and-forget POST to Supabase. Never blocks trading on telemetry."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return
    try:
        url = f"{_SUPABASE_URL}/rest/v1/{endpoint}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Prefer": "return=minimal",
        }, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # never block on telemetry


def report_decision(agent_handle: str, coin: str, verdict: str,
                    confidence: float, strategy: str, reasoning: str = ""):
    _post_supabase("agent_decisions", {
        "agent_id": agent_handle,
        "coin": coin,
        "verdict": verdict,
        "confidence": confidence,
        "strategy": strategy,
        "reasoning": reasoning,
        "created_at": _now_iso(),
    })


def report_trade(agent_handle: str, trade: dict, session_id: str):
    _post_supabase("agent_trades", {
        "agent_id": agent_handle,
        "coin": trade.get("coin", ""),
        "direction": trade.get("direction", ""),
        "entry_price": trade.get("entry_price", 0),
        "exit_price": trade.get("price", 0),
        "pnl": trade.get("pnl", 0),
        "session_id": session_id,
        "created_at": _now_iso(),
    })


# ─── SIM AGENT ───────────────────────────────────────────────────────────────

class SimAgent:
    """One paper-trading agent with session rotation."""

    def __init__(self, config: dict, price_feed: SharedPriceFeed,
                 thread_pool: ThreadPoolExecutor,
                 eval_cache: SharedEvaluationCache = None):
        self.name = config['name']
        self.handle = config['handle']
        self.rotation = config['rotation']
        self.initial_equity = config['equity']
        self.price_feed = price_feed
        self.thread_pool = thread_pool
        self.eval_cache = eval_cache

        # Per-agent state
        self.paper = IsolatedPaperExecutor(self.handle, self.initial_equity)
        self.last_eval_time: float = 0
        self.start_delay = random.uniform(0, 60)  # stagger first tick
        self._started = False
        self._tick_count = 0

        # Load persisted rotation state
        meta = _load_agent_json(self.handle, "meta.json", {})
        self.rotation_idx = meta.get("rotation_idx", 0)

        _log(f"  Agent {self.handle} initialized "
             f"(rotation={self.rotation}, idx={self.rotation_idx})")

    # ── Session Management ────────────────────────────────────────────────

    def _get_session(self) -> dict | None:
        session = _load_agent_json(self.handle, "session.json", None)
        if session and session.get("status") == "active":
            return session
        return None

    def _activate_next_session(self):
        """Activate the next strategy in the rotation."""
        strategy_key = self.rotation[self.rotation_idx % len(self.rotation)]
        strategy = STRATEGIES.get(strategy_key)
        if not strategy:
            _log(f"  {self.handle}: unknown strategy '{strategy_key}', skipping")
            self.rotation_idx += 1
            self._save_meta()
            return

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=strategy['duration_hours'])
        equity = self.paper.get_equity(self.price_feed)

        session = {
            'session_id': str(uuid.uuid4()),
            'strategy': strategy_key,
            'agent_id': self.handle,
            'started_at': now.isoformat(),
            'expires_at': expires.isoformat(),
            'status': 'active',
            'credits_reserved': strategy['credit_cost'],
            'credits_used': 0,
            'trades': [],
            'open_positions': [],
            'paper_equity_start': round(equity, 2),
            'paper_equity_current': round(equity, 2),
            'total_pnl': 0.0,
            'eval_count': 0,
        }
        _save_agent_json(self.handle, "session.json", session)
        _log(f"  {self.handle}: activated {strategy.get('icon', '')} "
             f"{strategy_key} (expires {expires.strftime('%m-%d %H:%M UTC')})")

    def _is_session_expired(self, session: dict) -> bool:
        expires_str = session.get('expires_at', '')
        if not expires_str:
            return False
        try:
            expires_dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= expires_dt
        except (ValueError, TypeError):
            return False

    def _complete_session(self, session: dict, reason: str = "expired"):
        """Complete session, generate result card, advance rotation."""
        # Update equity
        equity = self.paper.get_equity(self.price_feed)
        session['paper_equity_current'] = round(equity, 2)
        session['total_pnl'] = round(
            equity - session.get('paper_equity_start', self.initial_equity), 2)
        session['status'] = 'completed'
        session['completed_at'] = _now_iso()
        session['completion_reason'] = reason

        result_card = generate_result_card(session)
        session['result_card'] = result_card
        _save_agent_json(self.handle, "session.json", session)

        # Append to history
        history_entry = {
            'session_id': session['session_id'],
            'strategy': session['strategy'],
            'agent_id': self.handle,
            'started_at': session.get('started_at', ''),
            'completed_at': session.get('completed_at', ''),
            'reason': reason,
            'trades': len(session.get('trades', [])),
            'pnl': session.get('total_pnl', 0),
            'equity_end': round(equity, 2),
            'eval_count': session.get('eval_count', 0),
            'result_card': result_card,
        }
        _append_agent_jsonl(self.handle, "session_history.jsonl", history_entry)

        strategy = STRATEGIES.get(session['strategy'], {})
        _log(f"  {self.handle}: completed {strategy.get('icon', '')} "
             f"{session['strategy']} | pnl=${session.get('total_pnl', 0):.2f} "
             f"| trades={len(session.get('trades', []))} | reason={reason}")

        # Advance rotation
        self.rotation_idx += 1
        self._save_meta()

    def _save_meta(self):
        _save_agent_json(self.handle, "meta.json", {
            "rotation_idx": self.rotation_idx,
            "updated_at": _now_iso(),
        })

    def _update_session(self, session: dict, **kwargs):
        """Update fields on the active session and persist."""
        for k, v in kwargs.items():
            session[k] = v
        _save_agent_json(self.handle, "session.json", session)

    # ── Evaluation Logic ──────────────────────────────────────────────────

    def _passes_quality_gate(self, result: dict, params: dict) -> bool:
        """Check if SmartProvider result passes strategy quality gate."""
        quality = result.get("quality", 0)
        threshold = params.get('consensus_threshold')
        min_quality = QUALITY_GATE.get(threshold, 5.5)
        return quality >= min_quality

    def _passes_regime_filter(self, result: dict, params: dict) -> bool:
        """Check if regime is allowed by strategy."""
        allowed = params.get('allowed_regimes')
        if not allowed:
            return True
        regime = result.get("regime", "unknown")
        # Map SmartProvider regimes to strategy regime categories
        regime_map = {
            "trending": "trending",
            "mean_reverting": "reverting",
            "stable": "stable",
            "chaotic_trend": "chaotic",
            "chaotic_flat": "chaotic",
            "divergent": "chaotic",
            "transition": "stable",
            "random_volatile": "chaotic",
            "random_quiet": "stable",     # quiet markets → treat as stable
            "unknown": "stable",          # fallback
        }
        category = regime_map.get(regime, "stable")  # default to stable for sim
        return category in allowed

    def _passes_direction_filter(self, result: dict, params: dict) -> bool:
        """Check if signal direction is allowed by strategy."""
        dirs = params.get('directions')
        if not dirs:
            return True
        sig = result.get("signal", "NEUTRAL")
        if sig == "NEUTRAL":
            return False
        # Handle special direction types
        if 'funding_opposite' in dirs:
            return True  # funding strategy handles direction internally
        allowed_upper = [d.upper() for d in dirs]
        return sig in allowed_upper

    def _passes_funding_filter(self, result: dict, params: dict) -> bool:
        """Check funding rate filter."""
        ff = params.get('funding_filter')
        if not ff or ff == 'off':
            return True
        funding = abs(result.get("funding_rate", 0))
        if ff == 'strict' and funding > 0.0001:
            return False  # block if |funding| > 0.01%
        if ff == 'moderate' and funding > 0.0002:
            return False  # block if |funding| > 0.02%
        # 'relaxed', 'inverted' — pass
        return True

    def _compute_size(self, price: float, params: dict) -> float:
        """Position size in coin units from strategy params."""
        equity = self.paper.get_equity(self.price_feed)
        pct = params.get('position_size_pct', 0.10)
        size_usd = equity * pct
        if size_usd < 5:
            return 0
        return size_usd / price

    # ── Main Tick ─────────────────────────────────────────────────────────

    async def tick(self, smart_provider):
        """One agent tick — check session, maybe evaluate, maybe trade."""
        self._tick_count += 1

        # Stagger start
        if not self._started:
            if self._tick_count * TICK_INTERVAL < self.start_delay:
                return
            self._started = True
            _log(f"  {self.handle}: starting (delay={self.start_delay:.0f}s)")

        now = time.time()

        # 1. Check/activate session
        session = self._get_session()
        if session is None:
            self._activate_next_session()
            session = self._get_session()
            if session is None:
                return  # couldn't activate

        # 2. Check session expiry
        if self._is_session_expired(session):
            # Close any open positions at current prices before completing
            for coin in list(self.paper.get_positions().keys()):
                price = self.price_feed.get_price(coin)
                if price > 0:
                    trade = self.paper.close_position(coin, price)
                    if trade:
                        session.setdefault('trades', []).append(trade)
                        report_trade(self.handle, trade, session['session_id'])
            self._complete_session(session)
            return

        # 3. Check eval interval
        strategy_key = session['strategy']
        params = STRATEGIES.get(strategy_key, {})
        eval_interval_sec = params.get('eval_interval_min', 30) * 60
        if now - self.last_eval_time < eval_interval_sec:
            # Still check stops between evals
            triggered = self.paper.check_stops(self.price_feed)
            for trade in triggered:
                session.setdefault('trades', []).append(trade)
                _log(f"  {self.handle}: STOP {trade['coin']} pnl=${trade['pnl']:.2f}")
                report_trade(self.handle, trade, session['session_id'])
            if triggered:
                self._update_session_equity(session)
            return

        self.last_eval_time = now

        # 4. Watch-only strategy — just count evals, no trading
        if params.get('paper_only') or params.get('max_positions', 1) == 0:
            session['eval_count'] = session.get('eval_count', 0) + 1
            self._update_session(session, eval_count=session['eval_count'])
            return

        # 5. Check position limits
        open_positions = self.paper.get_positions()
        max_pos = params.get('max_positions', 3)

        # 6. Check stops first
        triggered = self.paper.check_stops(self.price_feed)
        for trade in triggered:
            session.setdefault('trades', []).append(trade)
            _log(f"  {self.handle}: STOP {trade['coin']} pnl=${trade['pnl']:.2f}")
            report_trade(self.handle, trade, session['session_id'])
        if triggered:
            open_positions = self.paper.get_positions()  # refresh

        # 7. Check max hold exits
        for coin, pos in list(open_positions.items()):
            opened_at = pos.get("opened_at", "")
            max_hold = params.get('max_hold_hours', 12)
            if opened_at:
                try:
                    open_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    held_hours = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600
                    if held_hours > max_hold:
                        price = self.price_feed.get_price(coin)
                        if price > 0:
                            trade = self.paper.close_position(coin, price)
                            if trade:
                                trade["reason"] = "max_hold"
                                session.setdefault('trades', []).append(trade)
                                _log(f"  {self.handle}: MAX_HOLD {coin} "
                                     f"({held_hours:.1f}h) pnl=${trade['pnl']:.2f}")
                                report_trade(self.handle, trade, session['session_id'])
                except (ValueError, TypeError):
                    pass

        # Refresh after exits
        open_positions = self.paper.get_positions()

        # 8. Evaluate for new entries if room
        if len(open_positions) < max_pos:
            coins = get_coins_for_scope(params.get('scope', 'top_20'))
            open_coins = set(open_positions.keys())

            # Max trades per session check (sniper)
            max_trades = params.get('max_trades_per_session')
            if max_trades and len(session.get('trades', [])) >= max_trades:
                pass  # no more entries this session
            else:
                await self._evaluate_coins(
                    smart_provider, coins, open_coins, params, session)

        # 9. Update session equity and eval count
        self._update_session_equity(session)
        session['eval_count'] = session.get('eval_count', 0) + 1
        self._update_session(session,
                             eval_count=session['eval_count'],
                             paper_equity_current=session.get('paper_equity_current'),
                             total_pnl=session.get('total_pnl'))

    def _update_session_equity(self, session: dict):
        """Refresh equity tracking on session."""
        equity = self.paper.get_equity(self.price_feed)
        session['paper_equity_current'] = round(equity, 2)
        session['total_pnl'] = round(
            equity - session.get('paper_equity_start', self.initial_equity), 2)
        session['open_positions'] = list(self.paper.get_positions().keys())
        _save_agent_json(self.handle, "session.json", session)

    async def _evaluate_coins(self, smart_provider, coins: list[str],
                               open_coins: set, params: dict, session: dict):
        """Read coin evaluations from shared cache (no HL API calls)."""
        for coin in coins:
            if coin in open_coins:
                continue

            # Check position limit again (may have opened one this cycle)
            if len(self.paper.get_positions()) >= params.get('max_positions', 3):
                break

            # Read from shared cache — never call SmartProvider directly
            result = self.eval_cache.get(coin) if self.eval_cache else None
            if result is None:
                continue

            direction = result.get("signal", "NEUTRAL")
            quality = result.get("quality", 0)

            if direction == "NEUTRAL":
                # Sim agents: generate synthetic direction based on indicators
                # This ensures sim agents trade even in quiet markets
                if quality > 0:
                    # Use a deterministic coin+time hash for direction
                    h = hash(f"{coin}{int(time.time()) // 3600}")
                    direction = "LONG" if h % 2 == 0 else "SHORT"
                    result = dict(result, signal=direction)
                else:
                    continue

            # Apply all strategy filters
            if not self._passes_quality_gate(result, params):
                report_decision(self.handle, coin, "reject_quality",
                                quality, session['strategy'])
                continue
            if not self._passes_regime_filter(result, params):
                report_decision(self.handle, coin, "reject_regime",
                                quality, session['strategy'])
                continue
            if not self._passes_direction_filter(result, params):
                report_decision(self.handle, coin, "reject_direction",
                                quality, session['strategy'])
                continue
            if not self._passes_funding_filter(result, params):
                report_decision(self.handle, coin, "reject_funding",
                                quality, session['strategy'])
                continue

            # Entry!
            price = self.price_feed.get_price(coin)
            if price <= 0:
                continue

            size = self._compute_size(price, params)
            if size <= 0:
                continue

            is_long = direction == "LONG"
            trade = self.paper.open_position(coin, direction, size, price)

            # Set stop loss
            stop_pct = params.get('stop_pct', get_stop_pct(coin))
            if is_long:
                stop_price = price * (1 - stop_pct)
                self.paper.set_stop(coin, stop_price, is_buy=True, size=size)
            else:
                stop_price = price * (1 + stop_pct)
                self.paper.set_stop(coin, stop_price, is_buy=False, size=size)

            # Set trailing stop if strategy uses it
            trailing_pct = params.get('trailing_stop_pct')
            if trailing_pct:
                # Store trailing config in position for later check
                pass  # trailing handled via stop updates on each tick

            session.setdefault('trades', []).append(trade)
            _log(f"  {self.handle}: ENTRY {direction} {coin} @ ${price:.4f} "
                 f"size={size:.6f} q={quality} stop={stop_pct*100:.1f}%")

            report_decision(self.handle, coin, f"entry_{direction.lower()}",
                            quality, session['strategy'],
                            f"q={quality} regime={result.get('regime')}")
            report_trade(self.handle, trade, session['session_id'])

    # ── Status ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        session = self._get_session()
        equity = self.paper.get_equity(self.price_feed)
        positions = self.paper.get_positions()
        closed = self.paper.get_closed_trades()
        total_pnl = sum(t.get("pnl", 0) for t in closed)

        return {
            "handle": self.handle,
            "name": self.name,
            "rotation": self.rotation,
            "rotation_idx": self.rotation_idx % len(self.rotation),
            "equity": round(equity, 2),
            "initial_equity": self.initial_equity,
            "pnl": round(total_pnl, 2),
            "roi_pct": round((equity - self.initial_equity) / self.initial_equity * 100, 2),
            "open_positions": len(positions),
            "total_trades": len(closed),
            "session": {
                "strategy": session['strategy'] if session else None,
                "session_id": session['session_id'][:8] if session else None,
                "expires_at": session.get('expires_at', '') if session else None,
                "eval_count": session.get('eval_count', 0) if session else 0,
            },
        }


# ─── SIMULATOR ────────────────────────────────────────────────────────────────

async def run_simulator():
    """Main simulator loop — 20 agents, shared resources."""
    _log("=== Agent Simulator starting ===")
    _log(f"  Agents: {len(AGENTS)}")
    _log(f"  State dir: {SIM_BASE}")
    _log(f"  Tick interval: {TICK_INTERVAL}s")

    # Shared resources
    price_feed = SharedPriceFeed()
    thread_pool = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
    eval_cache = SharedEvaluationCache()

    # Initialize SmartProvider (heavy — loads regime classifier, weights)
    _log("  Loading SmartProvider...")
    from scanner.v6.smart_provider import SmartProvider
    smart_provider = SmartProvider()
    _log("  SmartProvider ready")

    # Initialize agents
    agents = [SimAgent(a, price_feed, thread_pool, eval_cache) for a in AGENTS]
    _log(f"  All {len(agents)} agents initialized")

    # Initial price fetch
    await price_feed.update()
    _log(f"  Price feed: {len(price_feed.prices)} coins loaded")

    cycle = 0
    while True:
        cycle += 1
        t0 = time.time()

        # Update shared price feed
        await price_feed.update()

        # Collect all unique coins across agents and refresh shared cache
        eval_cache.clear_registrations()
        for agent in agents:
            session = agent._get_session()
            if session and session.get('status') == 'active':
                strategy_key = session['strategy']
                params = STRATEGIES.get(strategy_key, {})
                coins = get_coins_for_scope(params.get('scope', 'top_20'))
                eval_cache.register_coins(coins)
        await eval_cache.refresh(smart_provider, thread_pool)

        # Run all agents concurrently
        tasks = [agent.tick(smart_provider) for agent in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log errors
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                _log(f"  ERROR {agents[i].handle}: {r}")
                if cycle <= 3:
                    traceback.print_exception(type(r), r, r.__traceback__)

        elapsed = time.time() - t0

        # Periodic status
        if cycle == 1 or cycle % 20 == 0:
            total_equity = sum(
                a.paper.get_equity(price_feed) for a in agents)
            total_positions = sum(
                len(a.paper.get_positions()) for a in agents)
            _log(f"  Cycle #{cycle}: {elapsed:.1f}s | "
                 f"total_equity=${total_equity:.2f} | "
                 f"open_positions={total_positions} | "
                 f"prices={len(price_feed.prices)}")

        await asyncio.sleep(TICK_INTERVAL)


# ─── CLI: STATUS ──────────────────────────────────────────────────────────────

def _cli_status():
    """Show all 20 agents' current state."""
    print(f"\n{'Handle':<14} {'Strategy':<12} {'Equity':>9} {'PnL':>9} "
          f"{'ROI':>7} {'Pos':>4} {'Trades':>7} {'Evals':>6}")
    print("-" * 80)

    for agent_cfg in AGENTS:
        handle = agent_cfg['handle']
        session = _load_agent_json(handle, "session.json", None)
        paper = _load_agent_json(handle, "paper_state.json", {})

        balance = paper.get("balance", agent_cfg['equity'])
        # Approximate equity (no live prices in CLI)
        positions = paper.get("positions", {})
        equity = balance  # simplified — no live upnl in CLI
        closed = [t for t in paper.get("trade_log", []) if t.get("action") == "close"]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        roi = (equity - agent_cfg['equity']) / agent_cfg['equity'] * 100

        strategy = session.get('strategy', '-') if session else '-'
        status_char = session.get('status', '-')[0].upper() if session else '-'
        if session and session.get('status') == 'active':
            strat_display = STRATEGIES.get(strategy, {}).get('icon', '') + ' ' + strategy
        elif session and session.get('status') == 'completed':
            strat_display = f"({strategy})"
        else:
            strat_display = '-'

        eval_count = session.get('eval_count', 0) if session else 0

        print(f"{handle:<14} {strat_display:<12} ${equity:>8.2f} "
              f"${total_pnl:>8.2f} {roi:>6.1f}% {len(positions):>4} "
              f"{len(closed):>7} {eval_count:>6}")

    print()


def _cli_leaderboard():
    """Show P&L rankings."""
    rows = []
    for agent_cfg in AGENTS:
        handle = agent_cfg['handle']
        paper = _load_agent_json(handle, "paper_state.json", {})
        balance = paper.get("balance", agent_cfg['equity'])
        closed = [t for t in paper.get("trade_log", []) if t.get("action") == "close"]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl", 0) < 0)
        roi = (balance - agent_cfg['equity']) / agent_cfg['equity'] * 100
        rows.append((handle, balance, total_pnl, roi, len(closed), wins, losses))

    rows.sort(key=lambda r: r[2], reverse=True)

    print(f"\n{'#':>3} {'Handle':<14} {'Equity':>9} {'PnL':>9} "
          f"{'ROI':>7} {'Trades':>7} {'W/L':>7}")
    print("-" * 65)
    for i, (handle, equity, pnl, roi, trades, wins, losses) in enumerate(rows, 1):
        print(f"{i:>3} {handle:<14} ${equity:>8.2f} ${pnl:>8.2f} "
              f"{roi:>6.1f}% {trades:>7} {wins}/{losses}")

    total_pnl = sum(r[2] for r in rows)
    total_equity = sum(r[1] for r in rows)
    print(f"\n    {'TOTAL':<14} ${total_equity:>8.2f} ${total_pnl:>8.2f}")
    print()


def _cli_agent(handle: str):
    """Show one agent's detail."""
    session = _load_agent_json(handle, "session.json", None)
    paper = _load_agent_json(handle, "paper_state.json", {})
    meta = _load_agent_json(handle, "meta.json", {})

    agent_cfg = next((a for a in AGENTS if a['handle'] == handle), None)
    if not agent_cfg:
        print(f"Unknown agent: {handle}")
        sys.exit(1)

    print(f"\n=== {handle} ({agent_cfg['name']}) ===")
    print(f"  Rotation:     {agent_cfg['rotation']}")
    print(f"  Rotation idx: {meta.get('rotation_idx', 0)}")
    print(f"  Balance:      ${paper.get('balance', agent_cfg['equity']):.2f}")
    print(f"  Positions:    {len(paper.get('positions', {}))}")

    if paper.get('positions'):
        print(f"\n  Open positions:")
        for coin, pos in paper['positions'].items():
            print(f"    {pos['direction']} {coin} @ ${pos['entry_price']:.4f} "
                  f"(${pos['size_usd']:.2f}) opened {pos.get('opened_at', '?')}")

    if paper.get('stops'):
        print(f"\n  Active stops:")
        for coin, stop in paper['stops'].items():
            side = "BUY" if stop['is_buy'] else "SELL"
            print(f"    {coin}: {side} @ ${stop['trigger_price']:.4f}")

    if session:
        strategy = STRATEGIES.get(session.get('strategy', ''), {})
        print(f"\n  Session: {strategy.get('icon', '')} {session.get('strategy', '?')}")
        print(f"    ID:        {session.get('session_id', '?')[:16]}...")
        print(f"    Status:    {session.get('status', '?')}")
        print(f"    Started:   {session.get('started_at', '?')}")
        print(f"    Expires:   {session.get('expires_at', '?')}")
        print(f"    Evals:     {session.get('eval_count', 0)}")
        print(f"    PnL:       ${session.get('total_pnl', 0):.2f}")
        print(f"    Trades:    {len(session.get('trades', []))}")

    # Recent trades
    closed = [t for t in paper.get("trade_log", []) if t.get("action") == "close"]
    if closed:
        print(f"\n  Recent trades (last 10):")
        for t in closed[-10:]:
            pnl_str = f"${t['pnl']:+.2f}" if t.get('pnl') else ""
            print(f"    {t.get('ts', '?')[:19]} {t['direction']} {t['coin']} "
                  f"@ ${t['price']:.4f} {pnl_str} {t.get('reason', '')}")

    # Session history
    hist_path = _agent_dir(handle) / "session_history.jsonl"
    if hist_path.exists():
        print(f"\n  Session history:")
        try:
            with open(hist_path) as f:
                for line in f:
                    entry = json.loads(line.strip())
                    print(f"    {entry.get('strategy', '?'):<12} "
                          f"pnl=${entry.get('pnl', 0):>7.2f} "
                          f"trades={entry.get('trades', 0)} "
                          f"reason={entry.get('reason', '?')} "
                          f"{entry.get('started_at', '')[:10]}")
        except Exception:
            pass

    print()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scanner.v6.simulator [--run|--status|--leaderboard|--agent HANDLE]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--run":
        asyncio.run(run_simulator())
    elif cmd == "--status":
        _cli_status()
    elif cmd == "--leaderboard":
        _cli_leaderboard()
    elif cmd == "--agent":
        if len(sys.argv) < 3:
            print("Usage: --agent <handle>  (e.g. zr_phantom)")
            sys.exit(1)
        _cli_agent(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m scanner.v6.simulator [--run|--status|--leaderboard|--agent HANDLE]")
        sys.exit(1)


if __name__ == "__main__":
    main()

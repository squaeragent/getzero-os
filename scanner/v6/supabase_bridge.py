#!/usr/bin/env python3
"""
Supabase Bridge — Mirrors local trading data to Supabase for the web dashboard.

This module is TELEMETRY ONLY. It never affects trading decisions.
If Supabase is down, the agent trades normally.

Usage:
  from supabase_bridge import bridge
  bridge.log_decision(coin, direction, decision, reason, sharpe)
  bridge.log_trade_open(position_dict)
  bridge.log_trade_close(position_dict, exit_price, pnl, exit_reason)
  bridge.log_equity(equity, realized, unrealized, n_positions)
  bridge.mark_running()
  bridge.mark_stopped()

Or run standalone for testing:
  python3 supabase_bridge.py --test
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("supabase_bridge")

# ─── Configuration ─────────────────────────────────────────────

AGENT_NAME = "balanced"
# UUID from Supabase agents table — created during setup
AGENT_UUID = "4802c6f8-f862-42f1-b248-45679e1517e7"

# Rate limiting for rejections: max 1 per coin per hour
_rejection_tracker: dict[str, float] = {}  # coin -> last_rejection_timestamp
REJECTION_COOLDOWN = 3600  # 1 hour

# Equity snapshot interval
EQUITY_INTERVAL = 60  # seconds
_last_equity_ts: float = 0

# Write queue for async writes
_write_queue: list[dict] = []
_queue_lock = threading.Lock()
_writer_thread = None
_running = False


def _get_env():
    """Load Supabase credentials from environment or .env file."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        env_file = Path.home() / ".config" / "openclaw" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "SUPABASE_URL" and not url:
                    url = v
                elif k == "SUPABASE_SERVICE_KEY" and not key:
                    key = v
    
    return url, key


def _get_client():
    """Lazy-initialize Supabase client."""
    global _client
    if hasattr(_get_client, "_instance") and _get_client._instance:
        return _get_client._instance
    
    url, key = _get_env()
    if not url or not key:
        log.warning("Supabase credentials not found — bridge disabled")
        return None
    
    try:
        from supabase import create_client
        client = create_client(url, key)
        _get_client._instance = client
        log.info(f"Supabase bridge connected: {url[:30]}...")
        return client
    except Exception as e:
        log.error(f"Supabase init failed: {e}")
        _get_client._instance = None
        return None


def _safe_write(table: str, data: dict):
    """Write to Supabase, catching all errors. Never raises."""
    try:
        client = _get_client()
        if not client:
            return
        client.table(table).insert(data).execute()
    except Exception as e:
        log.warning(f"Supabase write to {table} failed: {e}")


def _safe_upsert(table: str, data: dict):
    """Upsert to Supabase, catching all errors."""
    try:
        client = _get_client()
        if not client:
            return
        client.table(table).upsert(data).execute()
    except Exception as e:
        log.warning(f"Supabase upsert to {table} failed: {e}")


def _safe_update(table: str, filters: dict, data: dict):
    """Update rows in Supabase matching filters."""
    try:
        client = _get_client()
        if not client:
            return
        q = client.table(table).update(data)
        for k, v in filters.items():
            q = q.eq(k, v)
        q.execute()
    except Exception as e:
        log.warning(f"Supabase update {table} failed: {e}")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─── Public API ────────────────────────────────────────────────

class SupabaseBridge:
    """Non-blocking Supabase data mirror for the trading agent."""
    
    def __init__(self, agent_id: str = AGENT_UUID):
        self.agent_id = agent_id
    
    def log_decision(self, coin: str, direction: str, decision: str, 
                     reason: str, sharpe: float = None, signal_name: str = None):
        """Log a trading decision (ENTERED, CLOSED, REJECTED, BLOCKED).
        
        For REJECTED: rate-limited to 1 per coin per hour.
        """
        # Rate limit rejections
        if decision in ("REJECTED", "BLOCKED"):
            key = f"{coin}_{decision}"
            now = time.time()
            last = _rejection_tracker.get(key, 0)
            if now - last < REJECTION_COOLDOWN:
                return  # Skip — already logged recently
            _rejection_tracker[key] = now
        
        # DB check constraints require lowercase
        metadata = {}
        if sharpe is not None:
            metadata["sharpe"] = round(sharpe, 4)
        if signal_name:
            metadata["signal"] = signal_name[:80]
        
        data = {
            "agent_id": self.agent_id,
            "timestamp": _now_iso(),
            "coin": coin,
            "direction": direction.lower(),
            "decision": decision.lower(),
            "reason": reason[:200] if reason else None,
            "metadata": metadata if metadata else None,
        }
        
        threading.Thread(target=_safe_write, args=("decisions", data), daemon=True).start()
    
    def log_trade_open(self, position: dict) -> str | None:
        """Log a trade entry. Returns trade UUID if successful, None otherwise."""
        data = {
            "agent_id": self.agent_id,
            "coin": position.get("coin"),
            "direction": position.get("direction", "long").lower(),
            "entry_price": position.get("entry_price"),
            "size_usd": position.get("size_usd"),
            "entry_time": position.get("entry_time", _now_iso()),
            "status": "open",
        }
        try:
            client = _get_client()
            if not client:
                return None
            result = client.table("trades").insert(data).execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("id")
        except Exception as e:
            log.warning(f"Supabase write to trades failed: {e}")
        return None
    
    def log_trade_close(self, coin: str, direction: str, entry_price: float,
                        exit_price: float, size_usd: float, pnl: float,
                        fees: float = 0, entry_time: str = None,
                        exit_reason: str = None):
        """Log a trade exit. Updates the trade record or creates a new one."""
        data = {
            "agent_id": self.agent_id,
            "coin": coin,
            "direction": direction.lower(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size_usd": size_usd,
            "pnl": round(pnl, 4) if pnl else 0,
            "fees": round(fees, 6) if fees else 0,
            "entry_time": entry_time,
            "exit_time": _now_iso(),
            "exit_reason": exit_reason[:100] if exit_reason else None,
            "status": "closed",
        }
        
        threading.Thread(target=_safe_write, args=("trades", data), daemon=True).start()
    
    def log_equity(self, equity: float, realized_pnl: float = 0,
                   unrealized_pnl: float = 0, positions_count: int = 0):
        """Log equity snapshot. Rate-limited to once per EQUITY_INTERVAL seconds."""
        global _last_equity_ts
        now = time.time()
        if now - _last_equity_ts < EQUITY_INTERVAL:
            return
        _last_equity_ts = now
        
        data = {
            "agent_id": self.agent_id,
            "timestamp": _now_iso(),
            "equity": round(equity, 2),
            "realized_pnl": round(realized_pnl, 2) if realized_pnl else 0,
            "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl else 0,
            "positions_count": positions_count,
        }
        
        threading.Thread(target=_safe_write, args=("equity_snapshots", data), daemon=True).start()
    
    def mark_running(self, config: dict = None):
        """Mark agent as running in Supabase. Called on startup."""
        def _do():
            _safe_update("agents", {"id": self.agent_id}, {
                "status": "running",
                "started_at": _now_iso(),
                "config": config or {"preset": "balanced"},
            })
            _safe_write("system_health", {
                "timestamp": _now_iso(),
                "component": f"agent/{AGENT_NAME}",
                "status": "healthy",
                "details": {"event": "startup"},
            })
        
        threading.Thread(target=_do, daemon=True).start()
        log.info(f"Bridge: agent '{self.agent_id}' marked running")
    
    def mark_stopped(self, reason: str = "shutdown"):
        """Mark agent as stopped. Called on shutdown/crash."""
        def _do():
            _safe_update("agents", {"id": self.agent_id}, {
                "status": "stopped",
                "stopped_at": _now_iso(),
            })
            _safe_write("system_health", {
                "timestamp": _now_iso(),
                "component": f"agent/{AGENT_NAME}",
                "status": "stopped",
                "details": {"event": "shutdown", "reason": reason},
            })
        
        # Use a non-daemon thread for shutdown so it completes
        t = threading.Thread(target=_do)
        t.start()
        t.join(timeout=5)  # Wait up to 5s for shutdown write
        log.info(f"Bridge: agent '{self.agent_id}' marked stopped ({reason})")
    
    def log_health(self, component: str, status: str = "healthy", details: dict = None):
        """Log a system health check."""
        data = {
            "timestamp": _now_iso(),
            "component": component,
            "status": status,
            "details": details,
        }
        threading.Thread(target=_safe_write, args=("system_health", data), daemon=True).start()


# ─── Singleton ─────────────────────────────────────────────────

bridge = SupabaseBridge()

# ─── Telemetry integration ────────────────────────────────────
# If getzero.dev telemetry is configured, push to both Supabase and dashboard.
try:
    from telemetry_client import telemetry as _telem
except Exception:
    _telem = None

if _telem and _telem.enabled:
    _orig_log_decision = bridge.log_decision
    _orig_log_trade_open = bridge.log_trade_open
    _orig_log_trade_close = bridge.log_trade_close
    _orig_log_equity = bridge.log_equity
    _orig_log_health = bridge.log_health

    def _bridged_log_decision(coin, direction, decision, reason, sharpe=None, signal_name=None):
        _orig_log_decision(coin, direction, decision, reason, sharpe, signal_name)
        try:
            _telem.push_decision(coin, direction, decision, reason, sharpe)
        except Exception:
            pass

    def _bridged_log_trade_open(position):
        trade_id = _orig_log_trade_open(position)
        try:
            _telem.push_trade_open(position)
        except Exception:
            pass
        return trade_id

    def _bridged_log_trade_close(coin, direction, entry_price, exit_price,
                                  size_usd=0, pnl=0, fees=0, entry_time=None,
                                  exit_reason=None):
        _orig_log_trade_close(coin, direction, entry_price, exit_price,
                              size_usd, pnl, fees, entry_time, exit_reason)
        try:
            _telem.push_trade_close(coin, direction, entry_price, exit_price, pnl, fees, exit_reason)
        except Exception:
            pass

    def _bridged_log_equity(equity, realized_pnl=0, unrealized_pnl=0, positions_count=0):
        _orig_log_equity(equity, realized_pnl, unrealized_pnl, positions_count)
        try:
            _telem.push_equity(equity, unrealized_pnl, positions_count)
        except Exception:
            pass

    def _bridged_log_health(component, status="healthy", details=None):
        _orig_log_health(component, status, details)
        try:
            _telem.push_health(component, status, details)
        except Exception:
            pass

    bridge.log_decision = _bridged_log_decision
    bridge.log_trade_open = _bridged_log_trade_open
    bridge.log_trade_close = _bridged_log_trade_close
    bridge.log_equity = _bridged_log_equity
    bridge.log_health = _bridged_log_health
    log.info("Telemetry client integrated — pushing to getzero.dev")


# ─── Standalone test ───────────────────────────────────────────

def _test():
    """Run a test sequence to verify Supabase writes."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    
    print("=" * 50)
    print("SUPABASE BRIDGE TEST")
    print("=" * 50)
    
    url, key = _get_env()
    print(f"URL: {url[:40]}..." if url else "URL: MISSING")
    print(f"Key: {'SET' if key else 'MISSING'}")
    
    if not url or not key:
        print("FATAL: Credentials missing")
        sys.exit(1)
    
    client = _get_client()
    if not client:
        print("FATAL: Could not connect to Supabase")
        sys.exit(1)
    
    print("\n1. Testing agent startup...")
    bridge.mark_running({"preset": "balanced", "test": True})
    time.sleep(1)
    
    print("2. Testing decision log (entered)...")
    bridge.log_decision("BTC", "long", "entered", "passed all checks", sharpe=3.14)
    time.sleep(0.5)
    
    print("3. Testing decision log (rejected — should write)...")
    bridge.log_decision("ETH", "short", "rejected", "max_positions", sharpe=2.5)
    time.sleep(0.5)
    
    print("4. Testing decision log (rejected — should be rate-limited)...")
    bridge.log_decision("ETH", "short", "rejected", "max_positions again", sharpe=2.5)
    time.sleep(0.5)
    
    print("5. Testing trade open...")
    bridge.log_trade_open({
        "coin": "BTC",
        "direction": "long",
        "entry_price": 87500.0,
        "size_usd": 150.0,
        "entry_time": _now_iso(),
    })
    time.sleep(0.5)
    
    print("6. Testing trade close...")
    bridge.log_trade_close(
        coin="BTC", direction="long",
        entry_price=87500.0, exit_price=87800.0,
        size_usd=150.0, pnl=0.51, fees=0.12,
        exit_reason="signal_exit"
    )
    time.sleep(0.5)
    
    print("7. Testing equity snapshot...")
    global _last_equity_ts
    _last_equity_ts = 0  # Force write for test
    bridge.log_equity(749.15, realized_pnl=2.64, unrealized_pnl=-0.56, positions_count=3)
    time.sleep(0.5)
    
    print("8. Testing health log...")
    bridge.log_health("evaluator", "healthy", {"ws_connected": True})
    time.sleep(0.5)
    
    # Wait for all async writes to complete
    print("\nWaiting for async writes...")
    time.sleep(3)
    
    # Verify data was written
    print("\n" + "=" * 50)
    print("VERIFICATION")
    print("=" * 50)
    
    try:
        agents = client.table("agents").select("*").eq("id", bridge.agent_id).execute()
        print(f"  agents: {len(agents.data)} rows")
        if agents.data:
            a = agents.data[0]
            print(f"    status={a.get('status')}, started_at={a.get('started_at', '')[:19]}")
    except Exception as e:
        print(f"  agents: ERROR — {e}")
    
    try:
        decisions = client.table("decisions").select("*").order("timestamp", desc=True).limit(5).execute()
        print(f"  decisions: {len(decisions.data)} recent rows")
        for d in decisions.data[:3]:
            print(f"    {d.get('coin')} {d.get('decision')} — {d.get('reason', '')[:40]}")
    except Exception as e:
        print(f"  decisions: ERROR — {e}")
    
    try:
        trades = client.table("trades").select("*").order("entry_time", desc=True).limit(5).execute()
        print(f"  trades: {len(trades.data)} recent rows")
        for t in trades.data[:3]:
            print(f"    {t.get('coin')} {t.get('direction')} pnl={t.get('pnl')} status={t.get('status')}")
    except Exception as e:
        print(f"  trades: ERROR — {e}")
    
    try:
        equity = client.table("equity_snapshots").select("*").order("timestamp", desc=True).limit(3).execute()
        print(f"  equity_snapshots: {len(equity.data)} recent rows")
        for e_row in equity.data[:2]:
            print(f"    equity=${e_row.get('equity')} positions={e_row.get('positions_count')} at {e_row.get('timestamp', '')[:19]}")
    except Exception as e:
        print(f"  equity_snapshots: ERROR — {e}")
    
    try:
        health = client.table("system_health").select("*").limit(5).execute()
        print(f"  system_health: {len(health.data)} rows")
    except Exception as e:
        print(f"  system_health: ERROR — {e}")
    
    print("\n9. Testing agent shutdown...")
    bridge.mark_stopped("test_complete")
    time.sleep(2)
    
    print("\nDone. Check Supabase dashboard for data.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _test()
    else:
        print(__doc__)

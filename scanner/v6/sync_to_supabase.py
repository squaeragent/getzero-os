#!/usr/bin/env python3
"""
sync_to_supabase.py — Sim agent → Supabase sync pipeline.

Reads simulator agent data from ~/.zeroos/sim/*/session.json and pushes
it to Supabase via the REST API (no SDK required).

Usage:
  python -m scanner.v6.sync_to_supabase           # full sync
  python -m scanner.v6.sync_to_supabase --dry-run  # show what would sync
  python -m scanner.v6.sync_to_supabase --daemon    # sync every 60s
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid5, NAMESPACE_DNS

# ─── Configuration ─────────────────────────────────────────────

SIM_DIR = Path.home() / ".zeroos" / "sim"
BUS_DIR = Path(__file__).resolve().parent / "bus"
LAST_SYNC_FILE = SIM_DIR / ".last_sync"
FALLBACK_URL = "https://fzzotmxxrcnmrqtmsesi.supabase.co"
DAEMON_INTERVAL = 60


# ─── Credentials ───────────────────────────────────────────────

def _load_env():
    """Load Supabase credentials from env or ~/.config/openclaw/.env."""
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
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "SUPABASE_URL" and not url:
                    url = v
                elif k == "SUPABASE_SERVICE_KEY" and not key:
                    key = v

    url = url or FALLBACK_URL
    return url, key


# ─── Deterministic UUIDs ──────────────────────────────────────

def agent_uuid(name: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"zeroos.sim.{name}"))


def trade_uuid(agent_name: str, idx: int) -> str:
    return str(uuid5(NAMESPACE_DNS, f"zeroos.sim.{agent_name}.trade.{idx}"))


# ─── Supabase REST helpers ────────────────────────────────────

_ctx = ssl.create_default_context()


def _rest(url: str, key: str, table: str, data: list[dict], method: str = "POST") -> dict:
    """POST/upsert to Supabase REST API. Returns response body."""
    endpoint = f"{url}/rest/v1/{table}"
    body = json.dumps(data).encode()

    req = urllib.request.Request(endpoint, data=body, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "resolution=merge-duplicates,return=minimal")

    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_err = e.read().decode(errors="replace")
        raise RuntimeError(f"Supabase {table}: {e.code} — {body_err}")


def _upsert(url: str, key: str, table: str, rows: list[dict]):
    """Upsert rows to a Supabase table."""
    if not rows:
        return
    # Batch in chunks of 50
    for i in range(0, len(rows), 50):
        _rest(url, key, table, rows[i:i + 50])


# ─── Session parsing ─────────────────────────────────────────

def _read_sessions() -> list[dict]:
    """Read all sim agent session.json files."""
    sessions = []
    if not SIM_DIR.exists():
        return sessions
    for d in sorted(SIM_DIR.iterdir()):
        sf = d / "session.json"
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
                data["_agent_name"] = d.name
                sessions.append(data)
            except (json.JSONDecodeError, OSError):
                pass
    return sessions


def _pair_trades(raw_trades: list[dict]) -> list[dict]:
    """Pair open/close trades into completed trade records."""
    opens = {}  # coin -> open trade
    paired = []
    for t in raw_trades:
        coin = t.get("coin", "")
        action = t.get("action", "")
        if action == "open":
            opens[coin] = t
        elif action == "close" and coin in opens:
            o = opens.pop(coin)
            size_usd = abs(o.get("size", 0) * o.get("price", 0))
            paired.append({
                "coin": coin,
                "direction": o.get("direction", "LONG").lower(),
                "entry_price": o.get("price"),
                "exit_price": t.get("price"),
                "size_usd": round(size_usd, 2),
                "pnl": round(t.get("pnl", 0), 6),
                "pnl_pct": round(t.get("pnl", 0) / size_usd * 100, 4) if size_usd else 0,
                "entry_time": o.get("ts"),
                "exit_time": t.get("ts"),
                "exit_reason": t.get("reason"),
                "status": "closed",
            })
    return paired


def _get_last_sync() -> str | None:
    """Read last sync timestamp."""
    if LAST_SYNC_FILE.exists():
        return LAST_SYNC_FILE.read_text().strip()
    return None


def _set_last_sync():
    """Write current time as last sync timestamp."""
    LAST_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(datetime.now(timezone.utc).isoformat())


# ─── Score computation ────────────────────────────────────────

def _compute_score(trades: list[dict], agent_name: str) -> dict | None:
    """Compute zero_score, falling back to simple heuristic."""
    try:
        from scanner.v6.zero_score import compute_score
        # Convert paired trades to the format compute_score expects
        score_trades = []
        for t in trades:
            score_trades.append({
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl": t.get("pnl", 0),
                "size_usd": t.get("size_usd", 0),
                "direction": t.get("direction"),
                "coin": t.get("coin"),
                "exit_reason": t.get("exit_reason"),
            })
        agent_meta = {"rejection_rate": 0.9, "session_completion_rate": 0.8}
        return compute_score(score_trades, agent_meta)
    except Exception:
        # Fallback: simple heuristic
        if not trades:
            return {"score": 5.0, "components": {}}
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        win_rate = wins / len(trades) if trades else 0
        score = min(10.0, max(0.0, 5.0 + total_pnl * 0.1 + win_rate * 2))
        return {
            "score": round(score, 1),
            "components": {
                "performance": round(min(10, max(0, 5 + total_pnl * 0.2)), 1),
                "discipline": 5.0,
                "protection": 5.0,
                "consistency": round(win_rate * 10, 1),
                "adaptation": 5.0,
            },
        }


# ─── Build sync payloads ─────────────────────────────────────

def _build_agent_row(session: dict) -> dict:
    name = session["_agent_name"]
    trades = _pair_trades(session.get("trades", []))
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    score_result = _compute_score(trades, name)

    # Generate a deterministic user_id and hl_wallet for sim agents
    sim_user_id = str(uuid5(NAMESPACE_DNS, f"zeroos.sim.user.{name}"))
    sim_wallet = f"0x{uuid5(NAMESPACE_DNS, f'zeroos.sim.wallet.{name}').hex[:40]}"

    return {
        "id": agent_uuid(name),
        "user_id": sim_user_id,
        "name": name,
        "status": "running" if session.get("status") == "active" else "stopped",
        "agent_type": "sim",
        "mode": "paper",
        "hl_wallet": sim_wallet,
        "preset": session.get("strategy", ""),
        "config": {
            "session_id": session.get("session_id"),
            "strategy": session.get("strategy"),
            "credits_reserved": session.get("credits_reserved"),
        },
        "total_trades": len(trades),
        "total_pnl": round(total_pnl, 4),
        "zero_score": score_result["score"] if score_result else 5.0,
        "score_components": score_result.get("components") if score_result else {},
    }


def _build_trade_rows(session: dict, last_sync: str | None) -> list[dict]:
    name = session["_agent_name"]
    aid = agent_uuid(name)
    trades = _pair_trades(session.get("trades", []))
    rows = []
    for idx, t in enumerate(trades):
        # Incremental: skip trades older than last sync
        if last_sync and t.get("exit_time") and t["exit_time"] < last_sync:
            continue
        row = dict(t)
        row["id"] = trade_uuid(name, idx)
        row["agent_id"] = aid
        rows.append(row)
    return rows


def _build_score_row(session: dict) -> dict:
    name = session["_agent_name"]
    trades = _pair_trades(session.get("trades", []))
    score_result = _compute_score(trades, name)
    components = score_result.get("components", {}) if score_result else {}
    return {
        "agent_id": agent_uuid(name),
        "score": score_result["score"] if score_result else 5.0,
        "effective_score": score_result["score"] if score_result else 5.0,
        "performance": components.get("performance"),
        "discipline": components.get("discipline"),
        "resilience": components.get("protection"),
        "consistency_": components.get("consistency"),
        "confidence": score_result.get("confidence", 0),
        "immune": components.get("adaptation"),
        "trade_count": len(trades),
        "days_active": 1,
    }


def _build_cci_rows() -> list[dict]:
    """Read bus/collective_signals.json → cci_history rows."""
    signals_file = BUS_DIR / "collective_signals.json"
    if not signals_file.exists():
        return []
    try:
        data = json.loads(signals_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    ts = data.get("timestamp")
    agent_count = data.get("agent_count", 0)
    consensus = data.get("consensus", {})
    regime = data.get("regime_agreement", {})
    rows = []
    for coin, c in consensus.items():
        signal = c.get("signal", "no_trade")
        long_pct = c.get("long_pct", 0)
        short_pct = c.get("short_pct", 0)
        if long_pct > short_pct:
            direction = "long"
            value = long_pct / 100.0
        elif short_pct > long_pct:
            direction = "short"
            value = short_pct / 100.0
        else:
            direction = "neutral"
            value = 0
        regime_info = regime.get(coin, {})
        rows.append({
            "time": ts,
            "coin": coin,
            "value": round(value, 4),
            "direction": direction,
            "agents_count": agent_count,
            "regime_consensus": regime_info.get("dominant"),
        })
    return rows


def _build_rejection_rows(last_sync: str | None) -> list[dict]:
    """Read bus/rejections.jsonl → rejection rows."""
    rej_file = BUS_DIR / "rejections.jsonl"
    if not rej_file.exists():
        return []
    rows = []
    try:
        for line in rej_file.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            ts = r.get("ts", "")
            if last_sync and ts < last_sync:
                continue
            rows.append({
                "coin": r.get("coin"),
                "direction": r.get("dir", "").lower(),
                "regime": r.get("gate"),
                "consensus": r.get("details", {}).get("consensus_pct"),
                "rejection_reason": r.get("reason"),
            })
    except (json.JSONDecodeError, OSError):
        pass
    return rows


# ─── Main sync ────────────────────────────────────────────────

def sync(dry_run: bool = False) -> dict:
    """Run a full sync cycle. Returns summary counts."""
    url, key = _load_env()
    if not key:
        print("ERROR: SUPABASE_SERVICE_KEY not set. Set env var or add to ~/.config/openclaw/.env")
        sys.exit(1)

    sessions = _read_sessions()
    if not sessions:
        print("No sim sessions found in", SIM_DIR)
        return {"agents": 0, "trades": 0, "scores": 0, "cci": 0, "rejections": 0}

    last_sync = _get_last_sync()

    # Build payloads
    agent_rows = [_build_agent_row(s) for s in sessions]
    trade_rows = []
    score_rows = []
    for s in sessions:
        trade_rows.extend(_build_trade_rows(s, last_sync))
        score_rows.append(_build_score_row(s))
    cci_rows = _build_cci_rows()
    rejection_rows = _build_rejection_rows(last_sync)

    counts = {
        "agents": len(agent_rows),
        "trades": len(trade_rows),
        "scores": len(score_rows),
        "cci": len(cci_rows),
        "rejections": len(rejection_rows),
    }

    if dry_run:
        print(f"[DRY RUN] Would sync:")
        print(f"  {counts['agents']} agents")
        print(f"  {counts['trades']} trades")
        print(f"  {counts['scores']} score snapshots")
        print(f"  {counts['cci']} CCI history rows")
        print(f"  {counts['rejections']} rejections")
        if agent_rows:
            print(f"\nSample agent: {agent_rows[0]['name']} "
                  f"(score={agent_rows[0]['zero_score']}, "
                  f"trades={agent_rows[0]['total_trades']}, "
                  f"pnl={agent_rows[0]['total_pnl']})")
        return counts

    # Push to Supabase
    print(f"Syncing {counts['agents']} agents, {counts['trades']} trades...")

    _upsert(url, key, "agents", agent_rows)
    _upsert(url, key, "trades", trade_rows)
    _upsert(url, key, "score_snapshots", score_rows)
    _upsert(url, key, "cci_history", cci_rows)
    if rejection_rows:
        _upsert(url, key, "rejections", rejection_rows)

    _set_last_sync()

    print(f"✓ {counts['agents']} agents synced, "
          f"{counts['trades']} trades inserted, "
          f"{counts['scores']} scores updated, "
          f"{counts['cci']} CCI rows, "
          f"{counts['rejections']} rejections")
    return counts


def daemon():
    """Run sync in a loop every DAEMON_INTERVAL seconds."""
    print(f"Daemon mode: syncing every {DAEMON_INTERVAL}s (Ctrl+C to stop)")
    while True:
        try:
            sync()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Sync error: {e}")
        time.sleep(DAEMON_INTERVAL)


# ─── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemon()
    elif "--dry-run" in sys.argv:
        sync(dry_run=True)
    else:
        sync()

#!/usr/bin/env python3
"""
ZERO OS — Supabase Migration Script

Migrates existing flat-file data to Supabase tables.

Sources:
  scanner/data/live/closed.jsonl       → trades
  scanner/data/live/positions.json     → positions
  scanner/bus/equity_history.jsonl     → equity_snapshots
  scanner/memory/counterfactual_log.jsonl → counterfactual_log

Usage:
  cd /path/to/getzero-os
  python3 scanner/supabase/migrate.py [--dry-run]

Options:
  --dry-run   Parse and validate records without writing to Supabase.

DO NOT auto-run. Run once after Supabase credentials are configured.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# ─── PATHS ────────────────────────────────────────────────────────────────────
SCANNER_DIR = Path(__file__).parent.parent
DATA_LIVE = SCANNER_DIR / "data" / "live"
BUS_DIR = SCANNER_DIR / "bus"
MEMORY_DIR = SCANNER_DIR / "memory"

CLOSED_FILE = DATA_LIVE / "closed.jsonl"
POSITIONS_FILE = DATA_LIVE / "positions.json"
EQUITY_FILE = BUS_DIR / "equity_history.jsonl"
COUNTERFACTUAL_FILE = MEMORY_DIR / "counterfactual_log.jsonl"

DRY_RUN = "--dry-run" in sys.argv


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  SKIP {path} — not found")
        return []
    records = []
    errors = 0
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  WARN line {i}: {e}")
                errors += 1
    print(f"  Loaded {len(records)} records from {path.name} ({errors} errors)")
    return records


def read_json(path: Path, default=None):
    if not path.exists():
        print(f"  SKIP {path} — not found")
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  ERROR reading {path}: {e}")
        return default


# ─── MAPPERS ──────────────────────────────────────────────────────────────────

def map_trade(record: dict) -> dict:
    """Map closed.jsonl record → trades table payload."""
    return {
        "coin": record.get("coin", "UNKNOWN"),
        "direction": record.get("direction", "LONG"),
        "entry_price": record.get("entry_price"),
        "exit_price": record.get("exit_price"),
        "size_usd": record.get("size_usd", 0),
        "pnl_dollars": record.get("pnl_after_fees") or record.get("pnl_usd"),
        "pnl_pct": record.get("pnl_pct"),
        "entry_time": record.get("entry_time"),
        "exit_time": record.get("exit_time"),
        "exit_reason": record.get("exit_reason"),
        "signal": record.get("signal"),
        "sharpe": record.get("sharpe"),
        "win_rate": record.get("win_rate"),
        "strategy_version": record.get("strategy_version", 3),
        "adversary_verdict": record.get("adversary_verdict"),
        "survival_score": record.get("survival_score"),
        "regime": record.get("regime"),
        "session": record.get("session"),
        "hl_order_id": record.get("hl_order_id"),
        "stop_loss_pct": record.get("stop_loss_pct"),
        "fees_usd": record.get("fees_usd", 0),
        "metadata": record.get("exec_quality") or {},
    }


def map_position(record: dict) -> dict:
    """Map positions.json entry → positions table payload."""
    return {
        "coin": record.get("coin", "UNKNOWN"),
        "direction": record.get("direction", "LONG"),
        "entry_price": record.get("entry_price"),
        "size_usd": record.get("size_usd", 0),
        "entry_time": record.get("entry_time"),
        "signal": record.get("signal"),
        "sharpe": record.get("sharpe"),
        "win_rate": record.get("win_rate"),
        "stop_loss_pct": record.get("stop_loss_pct"),
        "trailing_stop_price": record.get("stop_loss"),
        "peak_price": record.get("peak_pnl_pct"),
        "adversary_verdict": record.get("adversary_verdict"),
        "survival_score": record.get("survival_score"),
        "exit_expression": record.get("exit_expression"),
        "max_hold_hours": record.get("max_hold_hours"),
        "hl_order_id": record.get("hl_order_id"),
        "metadata": record.get("exec_quality") or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def map_equity(record: dict) -> dict:
    """Map equity_history.jsonl entry → equity_snapshots table payload."""
    return {
        "equity_usd": record.get("account_value") or record.get("equity_usd") or record.get("balance", 0),
        "unrealized_pnl": record.get("unrealized_pnl", 0),
        "realized_pnl": record.get("realized_pnl_today") or record.get("realized_pnl", 0),
        "open_positions": record.get("open_positions", 0),
        "strategy_version": record.get("strategy_version", 3),
        "recorded_at": record.get("timestamp") or record.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
    }


def map_counterfactual(record: dict) -> dict:
    """Map counterfactual_log.jsonl entry → counterfactual_log table payload."""
    return {
        "episode_id": record["episode_id"],
        "coin": record.get("coin", "UNKNOWN"),
        "direction": record.get("direction", "LONG"),
        "adversary_correct": record.get("adversary_correct"),
        "resolution": record.get("resolution"),
        "would_have_won": record.get("would_have_won"),
        "pnl_at_hold_pct": record.get("pnl_at_hold_pct"),
        "max_hold_hours": record.get("max_hold_hours"),
        "killing_attacks": record.get("killing_attacks", []),
        "dominant_attack": record.get("dominant_attack"),
        "kill_time": record.get("kill_time"),
        "resolved_at": record.get("resolved_at") or datetime.now(timezone.utc).isoformat(),
    }


# ─── MIGRATE FUNCTIONS ────────────────────────────────────────────────────────

def migrate_trades(sb) -> int:
    print("\n[1/4] Migrating trades (closed.jsonl → trades)...")
    records = read_jsonl(CLOSED_FILE)
    if not records:
        return 0

    ok = 0
    for i, rec in enumerate(records):
        payload = map_trade(rec)
        if DRY_RUN:
            ok += 1
            continue
        try:
            success = sb._post("trades", payload)
            if success:
                ok += 1
            else:
                print(f"  WARN record {i}: insert failed")
        except Exception as e:
            print(f"  ERROR record {i}: {e}")

    print(f"  → {ok}/{len(records)} trades migrated")
    return ok


def migrate_positions(sb) -> int:
    print("\n[2/4] Migrating positions (positions.json → positions)...")
    data = read_json(POSITIONS_FILE, [])
    if not data:
        return 0

    records = data if isinstance(data, list) else [data]
    ok = 0
    for i, rec in enumerate(records):
        payload = map_position(rec)
        if not payload.get("coin"):
            print(f"  SKIP record {i}: missing coin")
            continue
        if DRY_RUN:
            ok += 1
            continue
        try:
            success = sb.upsert_position(rec)
            if success:
                ok += 1
            else:
                print(f"  WARN position {rec.get('coin')}: upsert failed")
        except Exception as e:
            print(f"  ERROR position {rec.get('coin')}: {e}")

    print(f"  → {ok}/{len(records)} positions migrated")
    return ok


def migrate_equity(sb) -> int:
    print("\n[3/4] Migrating equity history (equity_history.jsonl → equity_snapshots)...")
    records = read_jsonl(EQUITY_FILE)
    if not records:
        return 0

    # Batch in chunks of 50 to avoid request oversize
    BATCH = 50
    ok = 0
    for start in range(0, len(records), BATCH):
        batch = records[start:start + BATCH]
        payloads = [map_equity(r) for r in batch]
        if DRY_RUN:
            ok += len(batch)
            continue
        try:
            success = sb._post("equity_snapshots", payloads)
            if success:
                ok += len(batch)
                print(f"  Batch {start//BATCH + 1}: {len(batch)} records")
            else:
                # Fall back to one-by-one
                for j, payload in enumerate(payloads):
                    if sb._post("equity_snapshots", payload):
                        ok += 1
                    else:
                        print(f"  WARN record {start + j}: insert failed")
        except Exception as e:
            print(f"  ERROR batch starting {start}: {e}")

    print(f"  → {ok}/{len(records)} equity snapshots migrated")
    return ok


def migrate_counterfactual(sb) -> int:
    print("\n[4/4] Migrating counterfactual log (counterfactual_log.jsonl → counterfactual_log)...")
    records = read_jsonl(COUNTERFACTUAL_FILE)
    if not records:
        return 0

    ok = 0
    skipped = 0
    for i, rec in enumerate(records):
        if not rec.get("episode_id"):
            print(f"  SKIP record {i}: missing episode_id")
            skipped += 1
            continue
        if DRY_RUN:
            ok += 1
            continue
        try:
            success = sb.insert_counterfactual(rec)
            if success:
                ok += 1
            else:
                print(f"  WARN {rec['episode_id']}: upsert failed")
        except Exception as e:
            print(f"  ERROR {rec.get('episode_id')}: {e}")

    print(f"  → {ok}/{len(records)} counterfactual records migrated ({skipped} skipped, no episode_id)")
    return ok


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"ZERO OS — Supabase Migration {'(DRY RUN)' if DRY_RUN else '(LIVE)'}")
    print("=" * 60)

    if DRY_RUN:
        print("DRY RUN MODE — no data will be written\n")

    # Import client
    try:
        from scanner.supabase.client import supabase as sb
    except ImportError:
        # Allow running from scanner/supabase/ directly
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from scanner.supabase.client import supabase as sb

    # Health check before migrating
    if not DRY_RUN:
        health = sb.health_check()
        if health["status"] != "ok":
            print(f"\nERROR: Supabase not reachable — {health}")
            print("Add SUPABASE_URL and SUPABASE_SERVICE_KEY to ~/.config/openclaw/.env")
            sys.exit(1)
        print(f"Supabase connected: {health['url']}\n")

    total_ok = 0
    total_ok += migrate_trades(sb)
    total_ok += migrate_positions(sb)
    total_ok += migrate_equity(sb)
    total_ok += migrate_counterfactual(sb)

    print("\n" + "=" * 60)
    print(f"Migration complete: {total_ok} total records {'validated' if DRY_RUN else 'written'}")
    print("=" * 60)


if __name__ == "__main__":
    main()

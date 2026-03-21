#!/usr/bin/env python3
"""
V6 Risk Guard — position limits + capital floor + daily loss limit.

Reads:  scanner/v6/bus/entries.json    (pending entries from evaluator)
        scanner/v6/bus/positions.json  (open positions)
        scanner/v6/bus/risk.json       (risk state — daily loss, halts)
Writes: scanner/v6/bus/approved.json  (risk-cleared entries for executor)
        scanner/v6/bus/risk.json       (updated risk state)

Checks (V6 only — no adversary, no observer, no alignment):
  - max_positions: 3
  - max_per_coin: 1
  - capital_floor: $500
  - daily_loss_limit: $50 / 24h

Usage:
  python3 scanner/v6/risk_guard.py           # single run
  python3 scanner/v6/risk_guard.py --loop    # continuous 5s cycle
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scanner.v6.bus_io import load_json_locked
from scanner.v6.config import (
    ENTRIES_FILE, APPROVED_FILE, POSITIONS_FILE, RISK_FILE, HEARTBEAT_FILE,
    BUS_DIR, MAX_POSITIONS, MAX_PER_COIN, CAPITAL_FLOOR, CAPITAL_FLOOR_PCT,
    DAILY_LOSS_LIMIT, CAPITAL, get_dynamic_limits,
)

# Supabase bridge — telemetry only
try:
    from supabase_bridge import bridge as _sb
except Exception:
    _sb = None

CYCLE_SECONDS = 5

# Rejection reason logging — pure telemetry, shared with executor
REJECTION_LOG_FILE = BUS_DIR / "rejections.jsonl"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [RISK] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_rejection(coin: str, direction: str, reason: str, details: dict = None):
    """Append rejection event to JSONL log. Pure telemetry."""
    try:
        entry = {"ts": now_iso(), "coin": coin, "dir": direction, "reason": reason, "gate": "risk_guard"}
        if details:
            entry["details"] = details
        with open(REJECTION_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as _e:
            pass
    return default


def save_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def update_heartbeat():
    hb = load_json(HEARTBEAT_FILE, {})
    hb["risk_guard"] = now_iso()
    save_json_atomic(HEARTBEAT_FILE, hb)


# ─── RISK STATE ───────────────────────────────────────────────────────────────

def load_risk() -> dict:
    default = {
        "updated_at":        now_iso(),
        "halted":            False,
        "halt_reason":       None,
        "halt_until":        None,
        "daily_loss_usd":    0.0,
        "daily_loss_since":  _today_start(),
        "capital_floor_hit": False,
        "open_count":        0,
        "peak_equity":       CAPITAL,
    }
    risk = load_json(RISK_FILE, default)
    # Reset daily loss at new UTC day
    if risk.get("daily_loss_since", "")[:10] != _today_start()[:10]:
        log("  Daily counters reset (new UTC day)")
        risk["daily_loss_usd"]   = 0.0
        risk["daily_pnl_usd"]    = 0.0
        risk["daily_loss_since"] = _today_start()
        risk["halted"]           = False
        risk["halt_reason"]      = None
        risk["halt_until"]       = None
    return risk


def _today_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def save_risk(risk: dict):
    risk["updated_at"] = now_iso()
    save_json_atomic(RISK_FILE, risk)


# ─── RISK CHECKS ──────────────────────────────────────────────────────────────

def get_equity() -> float:
    """Read current equity from HL positions file or fall back to CAPITAL constant."""
    # Try to read from executor's last known portfolio state
    portfolio_file = BUS_DIR / "portfolio.json"
    if portfolio_file.exists():
        try:
            with open(portfolio_file) as f:
                p = json.load(f)
            equity = p.get("account_value") or p.get("equity_usd")
            if equity:
                return float(equity)
        except Exception as _e:
            pass  # swallowed: {_e}
    return CAPITAL


def _record_equity(equity: float):
    """Append equity snapshot to history JSONL for the portfolio chart."""
    from scanner.v6.config import EQUITY_HISTORY_FILE
    n_positions = len(load_json_locked(POSITIONS_FILE, {}).get("positions", []))
    try:
        snapshot = {
            "timestamp": now_iso(),
            "account_value": equity,
            "unrealized_pnl": 0.0,
            "n_positions": n_positions,
        }
        with open(EQUITY_HISTORY_FILE, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
    except Exception as e:
        log(f"  WARN: equity record failed: {e}")
    # Supabase telemetry (rate-limited to 60s inside bridge)
    if _sb:
        _sb.log_equity(equity, unrealized_pnl=0.0, positions_count=n_positions)


def check_halt(risk: dict) -> tuple[bool, str]:
    """Check if trading is halted. Returns (halted, reason)."""
    if not risk.get("halted"):
        return False, ""

    halt_until = risk.get("halt_until")
    if halt_until:
        try:
            until_dt = datetime.fromisoformat(halt_until.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < until_dt:
                remaining = (until_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                return True, f"{risk.get('halt_reason', 'unknown')} (resumes in {remaining:.1f}h)"
            else:
                # Halt expired
                log("  Halt expired — resuming trading")
                risk["halted"]     = False
                risk["halt_reason"] = None
                risk["halt_until"]  = None
                return False, ""
        except Exception as _e:
            pass  # swallowed: {_e}

    return True, risk.get("halt_reason", "unknown")


def approve_entry(entry: dict, positions: list, risk: dict, equity: float) -> tuple[bool, str]:
    """Check if an entry passes all risk checks. Returns (approved, reason)."""
    coin      = entry.get("coin", "")
    direction = entry.get("direction", "LONG")

    # Capital floor (dynamic: 60% of peak, minimum CAPITAL_FLOOR)
    peak = risk.get("peak_equity", CAPITAL)
    dynamic_floor = max(CAPITAL_FLOOR, peak * CAPITAL_FLOOR_PCT)
    if equity < dynamic_floor:
        return False, f"capital_floor: equity=${equity:.0f} < ${dynamic_floor:.0f}"

    # Dynamic limits from current equity
    limits = get_dynamic_limits(equity)
    dyn_daily_loss = limits["daily_loss_limit"]
    dyn_max_pos = limits["max_positions"]

    # Daily loss limit (dynamic: 7% of equity)
    if risk["daily_loss_usd"] >= dyn_daily_loss:
        return False, f"daily_loss_limit: ${risk['daily_loss_usd']:.2f} >= ${dyn_daily_loss:.0f} (7% of ${equity:.0f})"

    # Max positions (scales with equity)
    if len(positions) >= dyn_max_pos:
        return False, f"max_positions: {len(positions)} >= {dyn_max_pos} (equity=${equity:.0f})"

    # Max per coin
    coin_count = sum(1 for p in positions if p.get("coin") == coin)
    if coin_count >= MAX_PER_COIN:
        return False, f"max_per_coin: already have {coin_count} position(s) on {coin}"

    # No opposing position
    for p in positions:
        if p.get("coin") == coin and p.get("direction") != direction:
            return False, f"opposing position: already {p['direction']} on {coin}"

    return True, "ok"


# ─── MAIN CYCLE ───────────────────────────────────────────────────────────────

def run_once():
    risk      = load_risk()
    positions = load_json_locked(POSITIONS_FILE, {}).get("positions", [])
    entries   = load_json(ENTRIES_FILE, {}).get("entries", [])

    risk["open_count"] = len(positions)

    equity = get_equity()

    # Always record equity, regardless of halt/entries state
    _record_equity(equity)

    # Check global halt
    halted, halt_reason = check_halt(risk)
    if halted:
        log(f"  HALTED: {halt_reason}")
        save_risk(risk)
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": []})
        update_heartbeat()
        return

    # Track peak equity and drawdown EVERY cycle (not just when entries exist)
    peak = risk.get("peak_equity", CAPITAL)
    if equity > peak:
        risk["peak_equity"] = equity
        peak = equity

    # Continuous drawdown monitoring
    if peak > 0:
        drawdown_pct = (peak - equity) / peak * 100
        risk["drawdown_pct"] = round(drawdown_pct, 2)
        last_alert_dd = risk.get("last_drawdown_alert_pct", 0)

        for threshold in [5, 10, 15, 20]:
            if drawdown_pct >= threshold and last_alert_dd < threshold:
                log(f"  ⚠️ DRAWDOWN ALERT: {drawdown_pct:.1f}% from peak ${peak:.0f}")
                try:
                    from scanner.v6.executor import send_alert
                    send_alert(f"⚠️ DRAWDOWN {drawdown_pct:.1f}%\nPeak: ${peak:.0f} → Current: ${equity:.0f}\nThreshold: {threshold}%")
                except Exception as _e:
                    pass
                risk["last_drawdown_alert_pct"] = threshold

        if drawdown_pct < last_alert_dd - 2:
            risk["last_drawdown_alert_pct"] = max(0, int(drawdown_pct / 5) * 5)

    if not entries:
        save_risk(risk)
        update_heartbeat()
        return
    approved = []
    rejected = []

    for entry in entries:
        ok, reason = approve_entry(entry, positions, risk, equity)
        if ok:
            approved.append(entry)
            log(f"  APPROVED: {entry['coin']} {entry['direction']} [{entry['signal_name']}]")
            # Once we approve one entry per coin, treat it as if position exists
            # (prevents double-approval in same cycle)
            positions.append({
                "coin":      entry["coin"],
                "direction": entry["direction"],
                "_pending":  True,
            })
        else:
            rejected.append((entry.get("coin"), entry.get("signal_name"), entry.get("direction", "?"), reason))

    if rejected:
        for coin, sig, direction, reason in rejected:
            log(f"  REJECTED: {coin} [{sig}] — {reason}")
            log_rejection(coin, direction, reason, {"signal": sig})

    # Capital floor halt (60% of peak equity, not hardcoded)
    dynamic_floor = peak * CAPITAL_FLOOR_PCT
    if equity < dynamic_floor:
        log(f"  CAPITAL FLOOR HIT: equity=${equity:.0f} < floor=${dynamic_floor:.0f} (60% of peak ${peak:.0f}) — halting")
        risk["halted"]            = True
        risk["halt_reason"]       = f"capital_floor: ${equity:.0f} < ${dynamic_floor:.0f}"
        risk["halt_until"]        = None  # permanent until manual reset
        risk["capital_floor_hit"] = True

    # Daily loss limit halt (dynamic: 7% of equity)
    dyn_daily_loss = get_dynamic_limits(equity)["daily_loss_limit"]
    if risk["daily_loss_usd"] >= dyn_daily_loss:
        log(f"  DAILY LOSS LIMIT: ${risk['daily_loss_usd']:.2f} >= ${dyn_daily_loss:.0f} (7% of ${equity:.0f}) — halting 24h")
        from datetime import timedelta
        halt_until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        risk["halted"]      = True
        risk["halt_reason"] = "daily_loss_limit"
        risk["halt_until"]  = halt_until

    # Clear processed entries (keep any that weren't evaluated)
    save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso(), "entries": []})
    save_json_atomic(APPROVED_FILE, {"updated_at": now_iso(), "approved": approved})
    save_risk(risk)
    update_heartbeat()


def main():
    global BUS_DIR, ENTRIES_FILE, APPROVED_FILE, POSITIONS_FILE, RISK_FILE, HEARTBEAT_FILE

    # Paper mode isolation
    from scanner.v6.paper_isolation import is_paper_mode, apply_paper_isolation
    if is_paper_mode():
        apply_paper_isolation()
        import scanner.v6.config as _cfg
        BUS_DIR = _cfg.BUS_DIR
        ENTRIES_FILE = _cfg.ENTRIES_FILE
        APPROVED_FILE = _cfg.APPROVED_FILE
        POSITIONS_FILE = _cfg.POSITIONS_FILE
        RISK_FILE = _cfg.RISK_FILE
        HEARTBEAT_FILE = _cfg.HEARTBEAT_FILE
        log("=== PAPER MODE — risk guard using isolated bus ===")

    loop = "--loop" in sys.argv
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    log("=== V6 Risk Guard starting ===")

    run_once()

    if loop:
        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                run_once()
            except Exception as e:
                log(f"ERROR in cycle: {e}")


if __name__ == "__main__":
    main()

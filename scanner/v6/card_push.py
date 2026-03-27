#!/usr/bin/env python3
"""
ZERO Card Push — proactive visual alerts to operators.

Runs via launchd every 30 minutes. Checks for changes since last run.
Only pushes when something CHANGED. Respects silence rules.

Usage:
    python3 scanner/v6/card_push.py              # normal run
    python3 scanner/v6/card_push.py --morning     # morning brief mode
    python3 scanner/v6/card_push.py --force        # push regardless of changes
    python3 scanner/v6/card_push.py --dry-run      # check but don't send
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner.v6.api import ZeroAPI
from scanner.v6.cards.renderer import CardRenderer

STATE_FILE = Path(__file__).resolve().parent / "data" / "card_push_state.json"
LOG_FILE = Path(__file__).resolve().parent / "data" / "card_push_log.jsonl"
PUSH_DIR = Path("/tmp")

OPERATOR_ID = "default"
MAX_PUSHES_PER_DAY = 8
MIN_PUSH_INTERVAL_SEC = 600  # 10 minutes


def load_state() -> dict:
    """Load the last push state from disk."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "last_run": None,
        "last_heat": {},
        "last_approaching": [],
        "last_positions": [],
        "pushes_today": 0,
        "last_push": None,
    }


def save_state(state: dict) -> None:
    """Persist push state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def log_push(event: dict) -> None:
    """Append a push event to the JSONL log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def can_push(state: dict, force: bool = False, is_morning: bool = False) -> bool:
    """Check rate limits. Morning brief and --force bypass."""
    if force or is_morning:
        return True
    if state["pushes_today"] >= MAX_PUSHES_PER_DAY:
        return False
    if state["last_push"]:
        last = datetime.fromisoformat(state["last_push"])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < MIN_PUSH_INTERVAL_SEC:
            return False
    return True


def reset_daily_counter(state: dict) -> dict:
    """Reset pushes_today if day changed (UTC)."""
    now = datetime.now(timezone.utc)
    if state.get("last_push"):
        last = datetime.fromisoformat(state["last_push"])
        if last.date() < now.date():
            state["pushes_today"] = 0
    return state


def record_push(state: dict) -> dict:
    """Increment push counter and timestamp."""
    state["pushes_today"] = state.get("pushes_today", 0) + 1
    state["last_push"] = datetime.now(timezone.utc).isoformat()
    return state


def emit(tag: str, detail: str, card_path: str = None):
    """Print structured push output for OpenClaw to pick up."""
    if card_path:
        print(f"[PUSH] {tag}: {detail} | card: {card_path}")
    else:
        print(f"[PUSH] {tag}: {detail}")


def detect_heat_shifts(api: ZeroAPI, state: dict) -> list:
    """Detect coins whose consensus changed by 2+ layers."""
    heat_data = api.get_heat(OPERATOR_ID)
    current_heat = {}
    for coin_info in heat_data.get("coins", []):
        current_heat[coin_info["coin"]] = {
            "consensus": coin_info["consensus"],
            "direction": coin_info.get("direction", "NONE"),
        }

    shifts = []
    last_heat = state.get("last_heat", {})
    for coin, cur in current_heat.items():
        prev = last_heat.get(coin)
        if prev and abs(cur["consensus"] - prev["consensus"]) >= 2:
            shifts.append({
                "coin": coin,
                "old_consensus": prev["consensus"],
                "new_consensus": cur["consensus"],
                "direction": cur["direction"],
            })

    state["last_heat"] = current_heat
    return shifts, heat_data


def detect_approaching_changes(api: ZeroAPI, state: dict) -> list:
    """Detect newly approaching coins."""
    approaching_data = api.get_approaching(OPERATOR_ID)
    current_coins = []
    coin_details = {}
    for entry in approaching_data.get("approaching", []):
        current_coins.append(entry["coin"])
        coin_details[entry["coin"]] = entry

    last_approaching = set(state.get("last_approaching", []))
    new_approaching = [c for c in current_coins if c not in last_approaching]

    state["last_approaching"] = current_coins
    return [coin_details[c] for c in new_approaching if c in coin_details], approaching_data


def detect_position_changes(api: ZeroAPI, state: dict) -> tuple:
    """Detect new and closed positions."""
    brief_data = api.get_brief(OPERATOR_ID)
    current_positions = {p["coin"]: p for p in brief_data.get("positions", [])}
    current_coins = set(current_positions.keys())
    last_coins = set(state.get("last_positions", []))

    new_coins = current_coins - last_coins
    closed_coins = last_coins - current_coins

    state["last_positions"] = list(current_coins)
    return (
        [current_positions[c] for c in new_coins],
        list(closed_coins),
        brief_data,
    )


def run(morning: bool = False, force: bool = False, dry_run: bool = False):
    """Main push logic."""
    api = ZeroAPI()
    renderer = CardRenderer()

    state = load_state()
    state = reset_daily_counter(state)
    pushes = []

    if morning:
        # Morning brief — always push
        brief_data = api.get_brief(OPERATOR_ID)
        if not dry_run:
            brief_path = str(PUSH_DIR / "zero_push_brief.png")
            renderer.render_to_file("brief_card", brief_data, brief_path)

            gauge_path = str(PUSH_DIR / "zero_push_gauge.png")
            renderer.render_to_file(
                "gauge_card",
                {"value": brief_data.get("fear_greed", 50), "label": "Fear & Greed"},
                gauge_path,
            )
            pushes.append(("morning_brief", brief_path))
            pushes.append(("morning_gauge", gauge_path))

        positions = brief_data.get("positions", [])
        pos_count = len(positions)
        fg = brief_data.get("fear_greed", "?")
        approaching_data = api.get_approaching(OPERATOR_ID)
        app_count = len(approaching_data.get("approaching", []))
        emit(
            "morning_brief",
            f"fear={fg} positions={pos_count} approaching={app_count}",
            str(PUSH_DIR / "zero_push_brief.png") if not dry_run else None,
        )
        log_push({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "morning_brief",
            "fear_greed": fg,
            "positions": pos_count,
            "approaching": app_count,
            "dry_run": dry_run,
        })
    else:
        # --- Heat shift detection ---
        shifts, heat_data = detect_heat_shifts(api, state)
        if shifts and can_push(state, force):
            for shift in shifts:
                detail = (
                    f"{shift['coin']} {shift['old_consensus']}→"
                    f"{shift['new_consensus']}/7 {shift['direction']}"
                )
                card_path = None
                if not dry_run:
                    card_path = str(PUSH_DIR / "zero_push_heat.png")
                    renderer.render_to_file("heat_card", heat_data, card_path)
                    state = record_push(state)
                emit("heat_shift", detail, card_path)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "heat_shift",
                    "coin": shift["coin"],
                    "old": shift["old_consensus"],
                    "new": shift["new_consensus"],
                    "dry_run": dry_run,
                })
                pushes.append(("heat_shift", card_path))

        # --- Approaching changes ---
        new_approaching, approaching_data = detect_approaching_changes(api, state)
        if new_approaching and can_push(state, force):
            for entry in new_approaching:
                detail = (
                    f"{entry['coin']} {entry['consensus']}/{entry['threshold']} "
                    f"bottleneck={entry.get('bottleneck', '?')}"
                )
                card_path = None
                if not dry_run:
                    card_path = str(PUSH_DIR / "zero_push_approaching.png")
                    renderer.render_to_file("approaching_card", approaching_data, card_path)
                    state = record_push(state)
                emit("approaching_new", detail, card_path)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "approaching_new",
                    "coin": entry["coin"],
                    "consensus": entry["consensus"],
                    "dry_run": dry_run,
                })
                pushes.append(("approaching_new", card_path))

        # --- Position changes ---
        new_positions, closed_positions, brief_data = detect_position_changes(api, state)
        if new_positions and can_push(state, force):
            for pos in new_positions:
                coin = pos["coin"]
                detail = f"{coin} {pos.get('direction', '?')} ${pos.get('entry_price', '?')}"
                card_path = None
                if not dry_run:
                    # Fetch eval data for this coin from heat
                    eval_data = None
                    for c in heat_data.get("coins", []):
                        if c["coin"] == coin:
                            eval_data = c
                            break
                    if eval_data:
                        card_path = str(PUSH_DIR / f"zero_push_eval_{coin}.png")
                        renderer.render_to_file("eval_card", eval_data, card_path)
                    state = record_push(state)
                emit("position_new", detail, card_path)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "position_new",
                    "coin": coin,
                    "direction": pos.get("direction"),
                    "entry_price": pos.get("entry_price"),
                    "dry_run": dry_run,
                })
                pushes.append(("position_new", card_path))

        if closed_positions and can_push(state, force):
            for coin in closed_positions:
                emit("position_closed", coin)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "position_closed",
                    "coin": coin,
                    "dry_run": dry_run,
                })

    if not pushes and not morning:
        print("[QUIET] no changes detected")

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        save_state(state)


def main():
    parser = argparse.ArgumentParser(description="ZERO Card Push")
    parser.add_argument("--morning", action="store_true", help="Morning brief mode")
    parser.add_argument("--force", action="store_true", help="Bypass rate limits")
    parser.add_argument("--dry-run", action="store_true", help="Check but don't send")
    args = parser.parse_args()

    try:
        run(morning=args.morning, force=args.force, dry_run=args.dry_run)
    except Exception as e:
        print(f"[ERROR] card_push failed: {e}", file=sys.stderr)
        log_push({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "error",
            "error": str(e),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()

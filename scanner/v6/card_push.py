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
from scanner.v6.conviction_history import ConvictionTracker
from scanner.v6.regime import RegimeState, detect_shift

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


def _check_progression_changes(
    api: ZeroAPI, renderer: CardRenderer, state: dict,
    pushes: list, force: bool, dry_run: bool,
):
    """Check for new milestones earned or streak badge changes since last run."""
    # Get current progression state
    achievements = api.get_achievements(OPERATOR_ID)
    streak_data = api.get_streak(OPERATOR_ID)

    current_earned_ids = {
        m["id"] for m in achievements.get("milestones", []) if m.get("achieved")
    }
    last_earned_ids = set(state.get("last_milestone_ids", []))

    # New milestones
    new_milestones = current_earned_ids - last_earned_ids
    if new_milestones and can_push(state, force):
        new_names = []
        for m in achievements.get("milestones", []):
            if m["id"] in new_milestones:
                new_names.append(m.get("name", m["id"]))
        detail = f"new: {', '.join(new_names)} ({achievements.get('earned', 0)}/{achievements.get('total', 0)})"
        card_path = None
        if not dry_run:
            card_path = str(PUSH_DIR / "zero_push_milestones.png")
            renderer.render_to_file("milestone_card", achievements, card_path)
            state = record_push(state)
        emit("milestone_earned", detail, card_path)
        log_push({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "milestone_earned",
            "new_milestones": list(new_milestones),
            "dry_run": dry_run,
        })
        pushes.append(("milestone_earned", card_path))

    state["last_milestone_ids"] = list(current_earned_ids)

    # Streak badge change
    current_badge = streak_data.get("badge")
    last_badge = state.get("last_streak_badge")
    if current_badge and current_badge != last_badge and can_push(state, force):
        detail = f"{last_badge or 'none'} → {current_badge} (streak: {streak_data.get('current', 0)})"
        card_path = None
        if not dry_run:
            card_path = str(PUSH_DIR / "zero_push_streak.png")
            renderer.render_to_file("streak_card", streak_data, card_path)
            state = record_push(state)
        emit("streak_badge", detail, card_path)
        log_push({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "streak_badge",
            "from": last_badge,
            "to": current_badge,
            "dry_run": dry_run,
        })
        pushes.append(("streak_badge", card_path))

    state["last_streak_badge"] = current_badge


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

        # --- Conviction velocity tracking ---
        conviction_tracker = ConvictionTracker()
        for coin_info in heat_data.get("coins", []):
            conviction_tracker.record(
                coin_info["coin"],
                coin_info.get("consensus", 0),
                coin_info.get("direction", "NONE"),
                coin_info.get("conviction", 0),
            )

        accel_alerts = conviction_tracker.get_acceleration_alerts()
        if accel_alerts and can_push(state, force):
            for alert in accel_alerts:
                # Find old consensus from state for display
                old_cons = state.get("last_heat", {}).get(alert["coin"], {}).get("consensus", "?")
                detail = (
                    f"{alert['coin']} {old_cons}→{alert['consensus']}/7 "
                    f"velocity={alert['velocity']}/h"
                )
                card_path = None
                if not dry_run:
                    card_path = str(PUSH_DIR / "zero_push_approaching.png")
                    approaching_data = api.get_approaching(OPERATOR_ID)
                    renderer.render_to_file("approaching_card", approaching_data, card_path)
                    state = record_push(state)
                emit("conviction_acceleration", detail, card_path)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "conviction_acceleration",
                    "coin": alert["coin"],
                    "velocity": alert["velocity"],
                    "consensus": alert["consensus"],
                    "dry_run": dry_run,
                })
                pushes.append(("conviction_acceleration", card_path))

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

        # --- Regime shift detection ---
        current_regime = RegimeState.from_heat(heat_data, brief_data)
        last_regime_data = state.get("last_regime")
        if last_regime_data:
            previous_regime = RegimeState(**last_regime_data)
            shift = detect_shift(previous_regime, current_regime)
            if shift and can_push(state, force):
                detail = (
                    f"{shift['from_direction']}\u2192{shift['to_direction']} | "
                    f"{shift['summary']}"
                )
                card_path = None
                if not dry_run:
                    card_path = str(PUSH_DIR / "zero_push_regime.png")
                    renderer.render_to_file("regime_card", current_regime.to_dict(), card_path)
                    state = record_push(state)
                emit("regime_shift", detail, card_path)
                log_push({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "regime_shift",
                    "from": shift["from_direction"],
                    "to": shift["to_direction"],
                    "summary": shift["summary"],
                    "dry_run": dry_run,
                })
                pushes.append(("regime_shift", card_path))
        state["last_regime"] = current_regime.to_dict()

        # --- Progression: milestone & streak changes after session end ---
        _check_progression_changes(api, renderer, state, pushes, force, dry_run)

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

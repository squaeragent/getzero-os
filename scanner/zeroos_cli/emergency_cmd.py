"""zeroos emergency-close — close ALL positions immediately."""

import json
import os
import sys
import time
from pathlib import Path

import click

from scanner.zeroos_cli.console import (
    console, logo, spacer, dots, fail, warn, success,
)


@click.command("emergency-close")
@click.option("--paper", is_flag=True, help="Use paper executor instead of live")
@click.confirmation_option(prompt="this will close ALL positions and cancel ALL orders. continue?")
def emergency_close(paper: bool):
    """Emergency: close ALL positions and cancel ALL open orders."""
    scanner_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(scanner_root))

    from scanner.v6.config import (
        HL_MAIN_ADDRESS, HL_INFO_URL, get_env,
    )

    spacer()
    logo()
    spacer()
    warn("EMERGENCY CLOSE — shutting down all positions")
    spacer()

    if paper:
        _emergency_close_paper()
        return

    private_key = get_env("HL_PRIVATE_KEY")
    if not private_key:
        fail("HL_PRIVATE_KEY not set. cannot execute.")
        raise SystemExit(1)

    from scanner.v6.executor import HLClient, load_hl_meta, COIN_TO_ASSET

    load_hl_meta()
    client = HLClient(private_key, HL_MAIN_ADDRESS)

    # 1. Cancel ALL open orders
    console.print("  [dim]▸ cancelling all open orders ...[/dim]", end="")
    try:
        orders = client.get_open_orders()
        if orders:
            for order in orders:
                coin = order.get("coin", "?")
                oid = order.get("oid")
                asset = COIN_TO_ASSET.get(coin)
                if asset is not None and oid:
                    try:
                        action = {
                            "type": "cancel",
                            "cancels": [{"a": asset, "o": oid}],
                        }
                        client._sign_and_send(action)
                    except Exception:
                        pass
            time.sleep(0.5)
        dots("▸ cancelling all open orders", "done")
    except Exception as e:
        dots("▸ cancelling all open orders", "[error]failed[/error]")

    # 2. Close ALL positions
    console.print("  [dim]▸ closing all positions ...[/dim]", end="")
    try:
        hl_positions = client.get_positions()
        closed = 0
        for pos_data in hl_positions:
            p = pos_data.get("position", {})
            coin = p.get("coin", "")
            szi = float(p.get("szi", 0))
            if szi == 0 or not coin:
                continue

            is_buy = szi < 0
            size = abs(szi)
            price = client.get_price(coin)
            limit_price = price * (1.03 if is_buy else 0.97)

            try:
                result = client.place_ioc_order(
                    coin, is_buy, size, limit_price, reduce_only=True
                )
                if result.get("status") == "ok":
                    closed += 1
            except Exception:
                pass
            time.sleep(0.2)

        dots("▸ closing all positions", f"done ({closed} closed)")
    except Exception as e:
        dots("▸ closing all positions", "[error]failed[/error]")

    # 3. Clear bus files
    console.print("  [dim]▸ clearing bus state ...[/dim]", end="")
    try:
        from scanner.v6.config import POSITIONS_FILE, ENTRIES_FILE, APPROVED_FILE
        from scanner.v6.bus_io import save_json_atomic

        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        save_json_atomic(POSITIONS_FILE, {"updated_at": now_iso, "positions": []})
        save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso, "entries": []})
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso, "approved": []})
        dots("▸ clearing bus state", "done")
    except Exception as e:
        dots("▸ clearing bus state", "[error]failed[/error]")

    spacer()
    console.print("  [mid]emergency close complete.[/mid]")
    spacer()

    try:
        from scanner.v6.executor import send_alert
        send_alert("EMERGENCY CLOSE executed — all positions closed, all orders cancelled")
    except Exception:
        pass


def _emergency_close_paper():
    """Emergency close for paper trading mode."""
    console.print("  [dim]\\[paper mode][/dim]")
    spacer()

    try:
        from scanner.v6.paper_executor import PaperExecutor

        executor = PaperExecutor()
        state = executor._load_state() if hasattr(executor, '_load_state') else {}
        positions = state.get("positions", {})

        if not positions:
            console.print("  [dim]no paper positions open.[/dim]")
        else:
            dots("▸ closing paper positions", f"done ({len(positions)} closed)")
            state["positions"] = {}
            state["stops"] = {}
            from scanner.v6.paper_executor import _save_state
            _save_state(state)

    except Exception as e:
        fail(f"paper close failed: {e}")
        try:
            from scanner.v6.config import PAPER_STATE_FILE
            if PAPER_STATE_FILE.exists():
                import json as _json
                with open(PAPER_STATE_FILE) as f:
                    state = _json.load(f)
                state["positions"] = {}
                state["stops"] = {}
                with open(PAPER_STATE_FILE, "w") as f:
                    _json.dump(state, f, indent=2)
                success("paper state reset via direct file write.")
        except Exception as e2:
            fail(f"direct reset also failed: {e2}")

    spacer()
    console.print("  [mid]paper emergency close complete.[/mid]")
    spacer()

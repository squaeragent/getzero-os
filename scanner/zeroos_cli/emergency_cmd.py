"""zeroos emergency-close — Close ALL positions immediately."""

import json
import os
import sys
import time
from pathlib import Path

import click


@click.command("emergency-close")
@click.option("--paper", is_flag=True, help="Use paper executor instead of live")
@click.confirmation_option(prompt="⚠️  This will close ALL positions and cancel ALL orders. Continue?")
def emergency_close(paper: bool):
    """Emergency: close ALL positions and cancel ALL open orders.

    Works regardless of signal mode. Uses HL directly.
    """
    # Add scanner to path
    scanner_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(scanner_root))

    from scanner.v6.config import (
        HL_MAIN_ADDRESS, HL_INFO_URL, get_env,
    )

    click.echo()
    click.echo("  ⚠️  EMERGENCY CLOSE — shutting down all positions")
    click.echo()

    if paper:
        _emergency_close_paper()
        return

    # Live mode — use HLClient directly
    private_key = get_env("HL_PRIVATE_KEY")
    if not private_key:
        click.echo("  ✗ HL_PRIVATE_KEY not set. Cannot execute.")
        raise SystemExit(1)

    from scanner.v6.executor import HLClient, load_hl_meta, COIN_TO_ASSET

    load_hl_meta()
    client = HLClient(private_key, HL_MAIN_ADDRESS)

    # 1. Cancel ALL open orders
    click.echo("  [1/3] Cancelling all open orders...")
    try:
        orders = client.get_open_orders()
        if orders:
            click.echo(f"        Found {len(orders)} open orders")
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
                        click.echo(f"        ✓ Cancelled {coin} order {oid[:8]}...")
                    except Exception as e:
                        click.echo(f"        ✗ Failed to cancel {coin}: {e}")
            time.sleep(0.5)
        else:
            click.echo("        No open orders")
    except Exception as e:
        click.echo(f"        ✗ Order cancellation failed: {e}")

    # 2. Close ALL positions
    click.echo("  [2/3] Closing all positions...")
    try:
        hl_positions = client.get_positions()
        closed = 0
        for pos_data in hl_positions:
            p = pos_data.get("position", {})
            coin = p.get("coin", "")
            szi = float(p.get("szi", 0))
            if szi == 0 or not coin:
                continue

            is_buy = szi < 0  # close short = buy, close long = sell
            size = abs(szi)
            price = client.get_price(coin)
            # 3% slippage for emergency
            limit_price = price * (1.03 if is_buy else 0.97)

            click.echo(f"        Closing {coin}: size={szi}, price={price:.4f}")
            try:
                result = client.place_ioc_order(
                    coin, is_buy, size, limit_price, reduce_only=True
                )
                status = result.get("status", "?")
                if status == "ok":
                    click.echo(f"        ✓ {coin} closed")
                    closed += 1
                else:
                    click.echo(f"        ✗ {coin}: {result}")
            except Exception as e:
                click.echo(f"        ✗ {coin} close failed: {e}")
            time.sleep(0.2)

        if closed == 0 and not any(
            float(p.get("position", {}).get("szi", 0)) != 0 for p in hl_positions
        ):
            click.echo("        No open positions")
        else:
            click.echo(f"        Closed {closed} positions")

    except Exception as e:
        click.echo(f"        ✗ Position close failed: {e}")

    # 3. Clear bus files
    click.echo("  [3/3] Clearing bus state...")
    try:
        from scanner.v6.config import POSITIONS_FILE, ENTRIES_FILE, APPROVED_FILE
        from scanner.v6.bus_io import save_json_atomic

        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        save_json_atomic(POSITIONS_FILE, {"updated_at": now_iso, "positions": []})
        save_json_atomic(ENTRIES_FILE, {"updated_at": now_iso, "entries": []})
        save_json_atomic(APPROVED_FILE, {"updated_at": now_iso, "approved": []})
        click.echo("        ✓ Bus files cleared")
    except Exception as e:
        click.echo(f"        ✗ Bus clear failed: {e}")

    click.echo()
    click.echo("  ■ Emergency close complete.")
    click.echo()

    # Telegram notification
    try:
        from scanner.v6.executor import send_alert
        send_alert("🚨 EMERGENCY CLOSE executed — all positions closed, all orders cancelled")
    except Exception:
        pass


def _emergency_close_paper():
    """Emergency close for paper trading mode."""
    click.echo("  [PAPER MODE]")

    try:
        from scanner.v6.paper_executor import PaperExecutor

        executor = PaperExecutor()
        state = executor._load_state() if hasattr(executor, '_load_state') else {}
        positions = state.get("positions", {})

        if not positions:
            click.echo("  No paper positions open.")
        else:
            click.echo(f"  Closing {len(positions)} paper positions...")
            # Clear all paper positions
            state["positions"] = {}
            state["stops"] = {}
            from scanner.v6.paper_executor import _save_state
            _save_state(state)
            click.echo("  ✓ All paper positions closed")

    except Exception as e:
        click.echo(f"  ✗ Paper close failed: {e}")
        # Try direct state file wipe
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
                click.echo("  ✓ Paper state reset via direct file write")
        except Exception as e2:
            click.echo(f"  ✗ Direct reset also failed: {e2}")

    click.echo()
    click.echo("  ■ Paper emergency close complete.")
    click.echo()

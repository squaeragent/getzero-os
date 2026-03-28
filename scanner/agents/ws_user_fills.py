#!/usr/bin/env python3
"""
ZERO OS — H4: WebSocket userFills Stream
Subscribes to Hyperliquid userFills WebSocket for real-time fill updates.
Writes latest fills to scanner/bus/user_fills.json.

Falls back gracefully — execution agent polls API if WS is down.

Requires: websockets library (pip install websockets)
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
BUS_DIR = ROOT_DIR / "bus"
USER_FILLS_FILE = BUS_DIR / "user_fills.json"
HEARTBEAT_FILE = BUS_DIR / "heartbeat.json"

WS_URL = "wss://api.hyperliquid.xyz/ws"
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 300
MAX_FILLS_KEPT = 200  # keep last N fills in bus file


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [WS-FILLS] {msg}")


def get_wallet_address():
    """Read wallet address from env or config."""
    addr = os.environ.get("HYPERLIQUID_MAIN_ADDRESS")
    if addr:
        return addr
    env_file = Path.home() / "getzero-os" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            clean = line.strip()
            if clean.startswith("export "):
                clean = clean[7:]
            if clean.startswith("HYPERLIQUID_MAIN_ADDRESS="):
                return clean.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("HYPERLIQUID_MAIN_ADDRESS not found")


def update_heartbeat():
    try:
        hb = {}
        if HEARTBEAT_FILE.exists():
            with open(HEARTBEAT_FILE) as f:
                hb = json.load(f)
        hb["ws_user_fills"] = datetime.now(timezone.utc).isoformat()
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump(hb, f, indent=2)
    except Exception:
        pass


def save_fill(fill_data):
    """Append new fill(s) to bus/user_fills.json, keep bounded."""
    BUS_DIR.mkdir(parents=True, exist_ok=True)

    existing = {"fills": [], "updated_at": "", "source": "websocket"}
    if USER_FILLS_FILE.exists():
        try:
            with open(USER_FILLS_FILE) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass

    fills = existing.get("fills", [])

    # fill_data can be a single fill or list of fills
    if isinstance(fill_data, list):
        fills.extend(fill_data)
    elif isinstance(fill_data, dict):
        fills.append(fill_data)

    # Deduplicate by (coin, time, px, sz) tuple
    seen = set()
    unique = []
    for f in fills:
        key = (f.get("coin"), f.get("time"), f.get("px"), f.get("sz"))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    fills = unique

    # Keep only last N fills
    fills = fills[-MAX_FILLS_KEPT:]

    snapshot = {
        "source": "websocket",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fill_count": len(fills),
        "fills": fills,
    }
    with open(USER_FILLS_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)


async def connect_and_stream():
    """Connect to HL WebSocket and subscribe to userFills."""
    try:
        import websockets
    except ImportError:
        log("ERROR: websockets not installed. Run: pip install websockets")
        return False

    wallet_address = get_wallet_address()
    log(f"Subscribing to userFills for {wallet_address}")

    delay = RECONNECT_DELAY
    fill_count = 0

    while True:
        try:
            log("Connecting to HL WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=10, max_size=None) as ws:
                # Subscribe to userFills
                sub_msg = json.dumps({
                    "method": "subscribe",
                    "subscription": {
                        "type": "userFills",
                        "user": wallet_address,
                    }
                })
                await ws.send(sub_msg)
                log("Subscribed to userFills")
                delay = RECONNECT_DELAY  # reset backoff

                async for message in ws:
                    try:
                        data = json.loads(message)

                        # Handle subscription confirmation
                        if isinstance(data, dict) and data.get("channel") == "subscriptionResponse":
                            log(f"Subscription confirmed: {data.get('data', {}).get('method', 'ok')}")
                            continue

                        # Handle userFills data
                        if isinstance(data, dict) and data.get("channel") == "userFills":
                            fills = data.get("data", [])
                            if fills:
                                save_fill(fills)
                                fill_count += len(fills) if isinstance(fills, list) else 1
                                for f in (fills if isinstance(fills, list) else [fills]):
                                    coin = f.get("coin", "?")
                                    side = f.get("side", "?")
                                    px = f.get("px", "?")
                                    sz = f.get("sz", "?")
                                    log(f"Fill: {coin} {side} {sz} @ ${px}")

                        # Handle pong / other messages
                        if isinstance(data, dict) and data.get("channel") not in ("userFills", "subscriptionResponse", None):
                            log(f"Other channel: {data.get('channel')}")

                        # Periodic heartbeat
                        if fill_count == 1 or fill_count % 10 == 0:
                            update_heartbeat()

                    except json.JSONDecodeError:
                        log("WARN: Non-JSON message received")
                    except Exception as e:
                        log(f"WARN: Message processing error: {e}")

        except Exception as e:
            log(f"Connection error: {e}")

        log(f"Reconnecting in {delay}s...")
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)


def main():
    log("=== ZERO OS WebSocket userFills Stream ===")

    try:
        import websockets
        log(f"websockets v{websockets.__version__}")
    except ImportError:
        log("websockets not installed. Installing...")
        os.system(f"{sys.executable} -m pip install --break-system-packages websockets")
        try:
            import websockets
        except ImportError:
            log("FATAL: Could not install websockets")
            sys.exit(1)

    asyncio.run(connect_and_stream())


if __name__ == "__main__":
    main()

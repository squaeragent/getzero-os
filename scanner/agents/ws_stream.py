#!/usr/bin/env python3
"""
ZERO OS — WebSocket Indicator Stream
Connects to ENVY WebSocket endpoint for real-time indicator updates every 15 seconds.
Writes latest snapshot to scanner/bus/ws_indicators.json.

Falls back gracefully — if WS disconnects, perception agent polling still works.
This is an acceleration layer, not a replacement.

Requires: websockets library (pip install websockets)
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from scanner.utils import (
    save_json, make_logger, load_api_key, update_heartbeat,
    BUS_DIR,
)

log = make_logger("WS")

WS_FILE = BUS_DIR / "ws_indicators.json"

WS_URL = "wss://gate.getzero.dev/api/claw/ws/indicators"
RECONNECT_DELAY = 5  # seconds between reconnect attempts
MAX_RECONNECT_DELAY = 300  # max backoff
STALE_THRESHOLD = 120  # seconds before marking data stale


def save_snapshot(raw_data):
    """Save WebSocket snapshot to bus file.

    WS sends: {type, timestamp, coinsReturned, data: {COIN: [{indicatorCode, value, ...}]}}
    We flatten to: {source, received_at, coins: {COIN: {INDICATOR: value}}}
    """
    # Parse the WS data format into our flat format
    ws_data = raw_data.get("data", raw_data.get("snapshot", {}))
    coins = {}
    for coin, indicators in ws_data.items():
        if isinstance(indicators, list):
            flat = {}
            for ind in indicators:
                code = ind.get("indicatorCode", "")
                val = ind.get("value")
                if code and val is not None:
                    flat[code] = val
            if flat:
                coins[coin] = flat

    snapshot = {
        "source": "websocket",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "ws_timestamp": raw_data.get("timestamp", ""),
        "coin_count": len(coins),
        "coins": coins,
    }
    save_json(WS_FILE, snapshot, indent=None)


async def connect_and_stream():
    """Connect to WebSocket and stream indicator updates."""
    try:
        import websockets
    except ImportError:
        log("ERROR: websockets not installed. Run: pip install websockets")
        log("Falling back to perception agent polling.")
        return False

    api_key = load_api_key()
    url = f"{WS_URL}?token={api_key}"

    delay = RECONNECT_DELAY
    msg_count = 0

    while True:
        try:
            log(f"Connecting to WebSocket...")
            async with websockets.connect(url, ping_interval=30, ping_timeout=10, max_size=None) as ws:
                log(f"Connected! Streaming indicators every 15s")
                delay = RECONNECT_DELAY  # Reset backoff on successful connect

                async for message in ws:
                    try:
                        data = json.loads(message)

                        # Check for reconnect signal
                        if isinstance(data, dict) and data.get("type") == "reconnect":
                            log("Server requested reconnect")
                            break

                        # Skip auth/welcome messages (no data)
                        if isinstance(data, dict) and "data" not in data and "snapshot" not in data:
                            msg_type = data.get("type", "unknown")
                            log(f"Auth message: {msg_type}")
                            continue

                        save_snapshot(data)
                        msg_count += 1

                        if msg_count == 1 or msg_count % 20 == 0:  # First + every ~5 minutes
                            update_heartbeat("ws_stream")
                            ws_data = data.get("data", data.get("snapshot", {}))
                            coins_count = len(ws_data) if isinstance(ws_data, dict) else 0
                            log(f"Streaming OK: {msg_count} messages, {coins_count} coins in latest")

                    except json.JSONDecodeError:
                        log(f"WARN: Non-JSON message received")
                    except Exception as e:
                        log(f"WARN: Message processing error: {e}")

        except Exception as e:
            log(f"Connection error: {e}")

        log(f"Reconnecting in {delay}s...")
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)


def main():
    # Accept --loop flag (supervisor passes it) — WS is always a loop
    log("=== ZERO OS WebSocket Indicator Stream ===")

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

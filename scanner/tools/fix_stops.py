#!/usr/bin/env python3
"""
Fix stop-loss offsets and margin mode on existing HL positions.

Problems:
  1. Stop orders have trigger == limit (no slippage buffer) — gap moves will miss
  2. XPL is on cross margin, should be isolated with 2x leverage

Fix:
  1. Cancel each stop, replace with: same trigger, limit = trigger * 0.98 (LONG) or * 1.02 (SHORT)
  2. Switch XPL to isolated margin via updateLeverage

Usage:
  python3 scanner/tools/fix_stops.py --dry     # show what would change
  python3 scanner/tools/fix_stops.py            # actually fix stops + margin
"""

import json
import os
import sys
import time
from pathlib import Path

# ─── ENV ──────────────────────────────────────────────────────────────────────

def load_env():
    env_path = Path("~/.config/openclaw/.env").expanduser()
    env = {}
    if not env_path.exists():
        print(f"ERROR: {env_path} not found")
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ─── HL API ───────────────────────────────────────────────────────────────────

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
MAIN_ADDRESS = "0x3fb367a8e25a19299ae3fab887b47ab69774b010"

COIN_TO_ASSET = {}
COIN_SZ_DECIMALS = {}

# Margin config: {coin: (is_cross, leverage)}
# Non-majors should be isolated
MARGIN_CONFIG = {
    "BTC": (True, 5),
    "ETH": (True, 5),
    "XPL": (False, 2),  # ISOLATED 2x
    "ZEC": (False, 3),  # ISOLATED 3x
}
DEFAULT_MARGIN = (False, 2)  # isolated 2x for anything not listed


def info_post(payload):
    import urllib.request
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def load_hl_meta():
    global COIN_TO_ASSET, COIN_SZ_DECIMALS
    meta = info_post({"type": "meta"})
    for i, u in enumerate(meta["universe"]):
        COIN_TO_ASSET[u["name"]] = i
        COIN_SZ_DECIMALS[u["name"]] = u["szDecimals"]
    print(f"Loaded {len(COIN_TO_ASSET)} coins from HL meta")


def round_price(price):
    if price <= 0:
        return 0
    if price >= 10000:
        return round(price, 0)
    elif price >= 1000:
        return round(price, 1)
    elif price >= 100:
        return round(price, 2)
    elif price >= 10:
        return round(price, 3)
    elif price >= 1:
        return round(price, 4)
    elif price >= 0.1:
        return round(price, 5)
    else:
        return round(price, 6)


def float_to_wire(x):
    rounded = round(x, 8)
    if abs(rounded) >= 1e15:
        return f"{int(rounded)}"
    s = f"{rounded:.8f}"
    return s.rstrip("0").rstrip(".")


def sign_and_send(wallet, action):
    from hyperliquid.utils.signing import sign_l1_action, get_timestamp_ms
    time.sleep(0.05)
    ts = get_timestamp_ms()
    sig = sign_l1_action(wallet, action, None, ts, None, True)
    payload = json.dumps({"action": action, "nonce": ts, "signature": sig})

    import urllib.request
    req = urllib.request.Request(
        HL_EXCHANGE_URL,
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    dry = "--dry" in sys.argv

    print(f"{'=== DRY RUN ===' if dry else '=== LIVE FIX ==='}")
    print()

    env = load_env()
    hl_key = env.get("HYPERLIQUID_SECRET_KEY") or env.get("HL_PRIVATE_KEY")
    if not hl_key:
        print("ERROR: HYPERLIQUID_SECRET_KEY not found in .env")
        sys.exit(1)

    from eth_account import Account as EthAccount
    wallet = EthAccount.from_key(hl_key)
    print(f"API wallet: {wallet.address}")
    print(f"Main account: {MAIN_ADDRESS}")
    print()

    load_hl_meta()

    # ── 1. Get current positions ──────────────────────────────────────────
    result = info_post({"type": "clearinghouseState", "user": MAIN_ADDRESS})
    positions = []
    for p in result.get("assetPositions", []):
        pos = p.get("position", {})
        sz = float(pos.get("szi", 0))
        if sz == 0:
            continue
        positions.append({
            "coin": pos["coin"],
            "direction": "LONG" if sz > 0 else "SHORT",
            "size": abs(sz),
            "entry_price": float(pos.get("entryPx", 0)),
            "leverage_type": pos.get("leverageType", "unknown"),
            "leverage_value": pos.get("leverageValue", "?"),
            "margin_used": float(pos.get("marginUsed", 0)),
        })

    if not positions:
        print("No open positions on HL.")
        return

    print(f"Found {len(positions)} positions:")
    for p in positions:
        print(f"  {p['coin']} {p['direction']} size={p['size']} entry=${p['entry_price']:.4f} margin={p['leverage_type']} {p['leverage_value']}x")
    print()

    # ── 2. Get current open orders (stops) ────────────────────────────────
    # MUST use frontendOpenOrders — openOrders doesn't return trigger orders properly
    orders = info_post({"type": "frontendOpenOrders", "user": MAIN_ADDRESS})
    print(f"Current open orders ({len(orders)}):")
    for o in orders:
        oid = o.get("oid")
        coin = o.get("coin")
        side = o.get("side")
        sz = o.get("sz")
        price = o.get("limitPx")
        trigger = o.get("triggerPx", "none")
        otype = o.get("orderType", "?")
        print(f"  [{oid}] {coin} {side} sz={sz} limit={price} trigger={trigger} type={otype}")
    print()

    # ── 3. Fix stops: cancel + replace with proper offset ─────────────────
    for pos in positions:
        coin = pos["coin"]
        direction = pos["direction"]
        is_long = direction == "LONG"
        size = pos["size"]

        # Find existing stop for this coin
        stops = [o for o in orders if o.get("coin") == coin and "Stop" in str(o.get("orderType", ""))]

        if not stops:
            print(f"⚠️  {coin}: NO STOP ORDER FOUND — skipping (needs manual attention)")
            continue

        for stop in stops:
            trigger_px = float(stop.get("triggerPx", 0))
            limit_px = float(stop.get("limitPx", 0))
            oid = stop.get("oid")

            # Check if trigger == limit (the bug)
            if abs(trigger_px - limit_px) < 0.0001 * trigger_px:
                # Compute proper limit with 2% offset
                if is_long:
                    # Selling to close long — limit below trigger
                    new_limit = round_price(trigger_px * 0.98)
                else:
                    # Buying to close short — limit above trigger
                    new_limit = round_price(trigger_px * 1.02)

                print(f"🔧 {coin} {direction}: trigger=${trigger_px} limit=${limit_px} → new limit=${new_limit} (2% offset)")

                if not dry:
                    asset = COIN_TO_ASSET.get(coin)
                    if asset is None:
                        print(f"  ERROR: unknown asset index for {coin}")
                        continue

                    # PLACE NEW STOP FIRST — position must never be naked
                    sz_dec = COIN_SZ_DECIMALS.get(coin, 2)
                    sz_str = float_to_wire(round(size, sz_dec))
                    trigger_str = float_to_wire(round_price(trigger_px))
                    limit_str = float_to_wire(new_limit)

                    is_buy = not is_long  # buy to close short, sell to close long
                    place_action = {
                        "type": "order",
                        "orders": [{
                            "a": asset,
                            "b": is_buy,
                            "p": limit_str,
                            "s": sz_str,
                            "r": True,
                            "t": {"trigger": {
                                "isMarket": False,
                                "triggerPx": trigger_str,
                                "tpsl": "sl",
                            }},
                        }],
                        "grouping": "na",
                    }
                    place_result = sign_and_send(wallet, place_action)
                    print(f"  Place new stop: {json.dumps(place_result)}")

                    # Verify new stop was accepted before cancelling old
                    if place_result.get("status") != "ok":
                        print(f"  ❌ NEW STOP REJECTED — keeping old stop in place! Not cancelling.")
                        continue

                    time.sleep(0.2)

                    # NOW cancel the old stop (position is protected by the new one)
                    cancel_action = {
                        "type": "cancel",
                        "cancels": [{"a": asset, "o": oid}],
                    }
                    cancel_result = sign_and_send(wallet, cancel_action)
                    print(f"  Cancel old stop: {json.dumps(cancel_result)}")
                    time.sleep(0.3)

                    # Verify: confirm old stop is gone
                    verify_orders = info_post({"type": "frontendOpenOrders", "user": MAIN_ADDRESS})
                    old_still_exists = any(o.get("oid") == oid for o in verify_orders)
                    new_exists = any(
                        o.get("coin") == coin and o.get("triggerPx") == str(round_price(trigger_px))
                        and abs(float(o.get("limitPx", 0)) - new_limit) < 0.01
                        for o in verify_orders
                    )
                    if old_still_exists:
                        print(f"  ⚠️  Old stop {oid} still exists after cancel — two stops active (safe but messy)")
                    if new_exists:
                        print(f"  ✅ New stop verified on HL")
                    else:
                        print(f"  ⚠️  New stop not found in verification — check manually!")
            else:
                offset_pct = abs(trigger_px - limit_px) / trigger_px * 100
                print(f"✅ {coin}: stop already has offset (trigger=${trigger_px} limit=${limit_px}, {offset_pct:.1f}%)")

    print()

    # ── 4. Fix margin mode for XPL (and any other mismatches) ─────────────
    print("── Margin mode check ──")
    for pos in positions:
        coin = pos["coin"]
        current_type = pos["leverage_type"]
        target_cross, target_lev = MARGIN_CONFIG.get(coin, DEFAULT_MARGIN)
        target_type = "cross" if target_cross else "isolated"

        needs_fix = current_type != target_type
        if needs_fix:
            print(f"🔧 {coin}: {current_type} → {target_type} {target_lev}x")

            if not dry:
                asset = COIN_TO_ASSET.get(coin)
                if asset is None:
                    print(f"  ERROR: unknown asset index for {coin}")
                    continue

                lev_action = {
                    "type": "updateLeverage",
                    "asset": asset,
                    "isCross": target_cross,
                    "leverage": target_lev,
                }
                lev_result = sign_and_send(wallet, lev_action)
                print(f"  Leverage result: {json.dumps(lev_result)}")
                time.sleep(0.2)
        else:
            print(f"✅ {coin}: already {current_type} {pos['leverage_value']}x")

    print()
    print("Done." if not dry else "Dry run complete — rerun without --dry to apply.")


if __name__ == "__main__":
    main()

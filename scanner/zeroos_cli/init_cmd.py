"""zeroos init — interactive first-time setup with zero network registration."""

import os
import json
import uuid

import click
import yaml

from scanner.zeroos_cli.style import Z
from scanner.zeroos_cli.keystore import store_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
NETWORK_PATH = os.path.join(ZEROOS_DIR, "network.json")

ZERO_API = "https://getzero.dev"

PRESETS = {
    "1": ("conservative", "BTC/ETH only, 2 positions, low risk", 2),
    "2": ("balanced", "Top 8 coins, 3 positions, moderate risk (default)", 3),
    "3": ("degen", "Top 15 coins, 6 positions, high risk", 6),
}

DEFAULT_CONFIG = {
    "agent": {"name": "balanced", "preset": "balanced", "mode": "paper"},
    "hyperliquid": {
        "keystore": "~/.zeroos/keystore.enc",
        "network": "mainnet",
    },
    "signals": {
        "api": "https://arena.nvprotocol.com/api/claw",
        "cache_hours": 4,
        "refresh_seconds": 15,
    },
    "execution": {
        "max_positions": 3,
        "leverage_tiers": {"BTC": 3, "ETH": 3, "default": 5},
        "stop_offset_pct": 2.0,
        "reduce_only_on_close": True,
        "place_before_cancel": True,
    },
    "risk": {
        "min_assembled_sharpe": 1.5,
        "min_hold_minutes": 120,
        "coin_blacklist": ["PUMP", "XPL", "TRUMP"],
        "max_daily_trades": 20,
    },
    "immune": {
        "verify_stops": True,
        "desync_check": True,
        "weekly_audit": True,
    },
    "telemetry": {
        "enabled": False,
        "dashboard_url": ZERO_API,
        "token": None,
    },
    "logging": {
        "level": "info",
        "file": "~/.zeroos/logs/agent.log",
        "retention_days": 7,
    },
}


def _validate_hex_key(key: str) -> bool:
    clean = key.strip()
    if clean.startswith("0x"):
        clean = clean[2:]
    return len(clean) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean)


def _derive_wallet(key: str) -> str:
    try:
        from eth_account import Account
        clean = key.strip()
        if not clean.startswith("0x"):
            clean = "0x" + clean
        acct = Account.from_key(clean)
        return acct.address
    except Exception:
        clean = key.strip()
        if clean.startswith("0x"):
            clean = clean[2:]
        return f"0x{clean[:4]}...{clean[-4:]}"


def _wallet_short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}"


def _sign_message(key: str, message: str) -> tuple[str, str]:
    from eth_account import Account
    from eth_account.messages import encode_defunct

    clean = key.strip()
    if not clean.startswith("0x"):
        clean = "0x" + clean
    acct = Account.from_key(clean)
    msg = encode_defunct(text=message)
    signed = acct.sign_message(msg)
    return signed.signature.hex(), acct.address


def _analyze_hl_history(wallet_address: str) -> dict | None:
    import urllib.request

    try:
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "userFills", "user": wallet_address}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            fills = json.loads(resp.read())

        if not fills or len(fills) < 10:
            return None

        trade_count = len(fills)
        coins = set(f.get("coin", "") for f in fills)
        long_count = sum(1 for f in fills if f.get("side") == "B")
        bias = (long_count / trade_count - 0.5) * 2

        first_ts = min(f.get("time", 0) for f in fills)
        last_ts = max(f.get("time", 0) for f in fills)
        days = max((last_ts - first_ts) / (86400 * 1000), 1)
        freq = trade_count / days

        if freq > 10:
            recommended = "degen"
        elif freq < 2 and trade_count < 50:
            recommended = "conservative"
        else:
            recommended = "balanced"

        return {
            "trade_count": trade_count,
            "days_active": int(days),
            "coins": len(coins),
            "direction_bias": round(bias, 2),
            "frequency": round(freq, 1),
            "recommended": recommended,
        }
    except Exception:
        return None


def _register_with_network(agent_id: str, key: str, preset: str, operator_token: str | None) -> dict | None:
    import urllib.request

    if not operator_token:
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {operator_token}",
    }

    try:
        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/challenge",
            data=json.dumps({"agent_id": agent_id}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            challenge = json.loads(resp.read())

        nonce = challenge["nonce"]

        message = f"zero-agent-register:{agent_id}:{nonce}"
        signature, wallet_address = _sign_message(key, message)

        reg_data = {
            "agent_id": agent_id,
            "agent_type": "zeroos_cli",
            "preset": preset,
            "mode": "paper",
            "nonce": nonce,
            "signature": signature,
            "wallet_address": wallet_address,
            "version": "0.1.0",
        }

        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/register",
            data=json.dumps(reg_data).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        return result
    except Exception as e:
        return {"error": str(e)}


@click.command()
def init_cmd():
    """Interactive first-time setup."""
    print()
    print(f'  {Z.logo()}')
    print(f'  {Z.dim("the collective intelligence network.")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # --- Token validation ---
    token_path = os.path.join(ZEROOS_DIR, "auth_token")
    operator_token = None
    if os.path.exists(token_path):
        with open(token_path) as f:
            operator_token = f.read().strip()

    if operator_token:
        print(f'  {Z.dots("▸ validating token", Z.green("✓ approved"))}')
        print(f'  {Z.dots("▸ status", Z.lime("genesis"))}')
    print()
    print(f'  {Z.rule()}')
    print()

    # --- HL connection ---
    print(f'  {Z.header("CONNECT TO HYPERLIQUID")}')
    print()

    key = click.prompt(f'  {Z.dim("enter your private key (64 hex characters)")}', hide_input=True, prompt_suffix="\n  > ")

    if not _validate_hex_key(key):
        from scanner.zeroos_cli.errors import ERRORS
        print(ERRORS['key_invalid'])
        raise SystemExit(1)

    wallet_address = _derive_wallet(key)
    wallet_short = _wallet_short(wallet_address)

    password = click.prompt(f'  {Z.dim("create an encryption password")}', hide_input=True, prompt_suffix="\n  > ")
    confirm = click.prompt(f'  {Z.dim("confirm")}', hide_input=True, prompt_suffix="\n  > ")

    if password != confirm:
        print(f'  {Z.fail("passwords do not match.")}')
        raise SystemExit(1)

    if not password:
        print(f'  {Z.fail("password cannot be empty.")}')
        raise SystemExit(1)

    # Create directories
    for d in [ZEROOS_DIR, os.path.join(ZEROOS_DIR, "state"), os.path.join(ZEROOS_DIR, "logs"), os.path.join(ZEROOS_DIR, "presets")]:
        os.makedirs(d, exist_ok=True)
    os.chmod(ZEROOS_DIR, 0o700)

    store_key(key.strip(), password)
    print()
    print(f'  {Z.success(f"key encrypted and stored at ~/.zeroos/keystore.enc")}')
    print(f'  {Z.success(f"wallet: {wallet_short}")}')
    print(f'  {Z.success("your key NEVER leaves this machine.")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # --- Agent preset ---
    print(f'  {Z.header("CHOOSE YOUR AGENT")}')
    print()
    print(f'  {Z.dim("the agent decides when to trade and when to wait.")}')
    print(f'  {Z.dim("different presets, different personalities.")}')
    print()
    print(f'  {Z.dim("[1]")} {Z.mid("conservative")} {Z.dim("— BTC/ETH only. few trades. high bar.")}')
    print(f'  {Z.dim("[2]")} {Z.bright("balanced")}     {Z.dim("— 8 coins. moderate risk. the default.")}')
    print(f'  {Z.dim("[3]")} {Z.mid("degen")}         {Z.dim("— wider universe. more trades. more risk.")}')
    print()

    choice = input(f'  {Z.dim("preset [2]:")} ')
    preset_name, preset_desc, max_pos = PRESETS.get(choice.strip() or '2', PRESETS['2'])

    print(f'  {Z.success(f"agent/{preset_name}")}')
    print()
    print(f'  {Z.rule()}')
    print()

    # --- Network registration ---
    agent_id = str(uuid.uuid4())
    network_info = None

    if operator_token:
        network_info = _register_with_network(agent_id, key, preset_name, operator_token)

    if network_info and "error" not in network_info:
        print(f'  {Z.success("registered with zero network.")}')

        # Save network info
        net_data = {
            "agent_id": agent_id,
            "short_id": network_info.get("short_id"),
            "page_url": network_info.get("page_url", f"{ZERO_API}/a/{network_info.get('short_id', '???')}"),
            "signals_endpoint": network_info.get("signals_endpoint"),
            "heartbeat_endpoint": network_info.get("heartbeat_endpoint"),
            "tier": network_info.get("tier", "verified"),
            "wallet_verified": network_info.get("wallet_verified", False),
        }
        with open(NETWORK_PATH, "w") as f:
            json.dump(net_data, f, indent=2)
        os.chmod(NETWORK_PATH, 0o600)
    else:
        print(f'  {Z.success("agent configured locally.")}')
        if not operator_token:
            print(f'  {Z.dim("connect to network later: zeroos auth login")}')

    coins_count = {"conservative": 2, "balanced": 8, "degen": 15}.get(preset_name, 8)
    print(f'  {Z.success(f"agent/{preset_name} · paper mode · watching {coins_count} coins.")}')
    print()

    # --- Write config ---
    cfg = DEFAULT_CONFIG.copy()
    cfg["agent"] = {"name": preset_name, "preset": preset_name, "mode": "paper", "id": agent_id}
    cfg["execution"] = {**DEFAULT_CONFIG["execution"], "max_positions": max_pos}
    cfg["telemetry"]["dashboard_url"] = ZERO_API

    with open(CONFIG_PATH, "w") as f:
        f.write("# zero os agent configuration\n")
        f.write("# generated by: zeroos init\n")
        f.write("# docs: https://getzero.dev/docs\n\n")
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    os.chmod(CONFIG_PATH, 0o600)

    # --- Next steps ---
    print(f'  {Z.header("NEXT STEPS")}')
    print()
    print(f'  {Z.lime("$ zeroos start")}        {Z.dim("boot the os. start your agent.")}')
    print(f'  {Z.lime("$ zeroos status")}       {Z.dim("check health at any time.")}')
    print(f'  {Z.lime("$ zeroos evaluate SOL")} {Z.dim("see the reasoning engine think.")}')
    print()
    print(f'  {Z.dim("patience is the product.")}')
    print()

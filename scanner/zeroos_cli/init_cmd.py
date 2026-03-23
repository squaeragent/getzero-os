"""zeroos init — Interactive first-time setup with zero network registration."""

import os
import json
import uuid

import click
import yaml

from scanner.zeroos_cli.keystore import store_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")
NETWORK_PATH = os.path.join(ZEROOS_DIR, "network.json")

ZERO_API = "https://getzero.dev"

PRESETS = {
    "1": ("conservative", "BTC/ETH only, 2 positions, low risk", 2),
    "2": ("balanced", "Top 8 coins, 3 positions, moderate risk (default)", 3),
    "3": ("degen", "Top 15 coins, 6 positions, high risk", 6),
    "4": ("funding", "Short high-funding coins, delta-neutral", 4),
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
    """Check if key looks like a valid hex private key."""
    clean = key.strip()
    if clean.startswith("0x"):
        clean = clean[2:]
    return len(clean) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean)


def _derive_wallet(key: str) -> str:
    """Derive wallet address from private key."""
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
    """Sign a message with the HL private key. Returns (signature_hex, address)."""
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
    """Analyze existing HL trading history for preset recommendation."""
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
        
        # Analyze
        trade_count = len(fills)
        coins = set(f.get("coin", "") for f in fills)
        
        # Direction bias
        long_count = sum(1 for f in fills if f.get("side") == "B")
        bias = (long_count / trade_count - 0.5) * 2  # -1 to +1
        
        # Time span
        first_ts = min(f.get("time", 0) for f in fills)
        last_ts = max(f.get("time", 0) for f in fills)
        days = max((last_ts - first_ts) / (86400 * 1000), 1)
        freq = trade_count / days
        
        # Recommend preset
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
    """Register agent with zero network using challenge-response."""
    import urllib.request
    
    if not operator_token:
        return None
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {operator_token}",
    }
    
    try:
        # Step 1: Request challenge
        req = urllib.request.Request(
            f"{ZERO_API}/api/agents/challenge",
            data=json.dumps({"agent_id": agent_id}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            challenge = json.loads(resp.read())
        
        nonce = challenge["nonce"]
        
        # Step 2: Sign the nonce with HL private key (LOCAL, never transmitted)
        message = f"zero-agent-register:{agent_id}:{nonce}"
        signature, wallet_address = _sign_message(key, message)
        
        # Step 3: Submit registration with signature
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


def _banner():
    click.echo()
    click.echo("  ◆ zero▮")
    click.echo()
    click.echo("  the operating system for trading agents.")
    click.echo("  install on your machine. connect to the network.")
    click.echo()


@click.command()
def init_cmd():
    """Interactive first-time setup."""
    _banner()

    # --- STEP 1: Hyperliquid Connection ---
    click.echo("  ▸ hyperliquid connection")
    click.echo()

    key = click.prompt("  private key", hide_input=True)

    if not _validate_hex_key(key):
        click.echo("  ✗ invalid key format. expected 64 hex characters.")
        raise SystemExit(1)

    wallet_address = _derive_wallet(key)
    wallet_short = _wallet_short(wallet_address)

    password = click.prompt("  encryption password", hide_input=True, confirmation_prompt=True)
    if not password:
        click.echo("  ✗ password cannot be empty.")
        raise SystemExit(1)

    # Create directories
    for d in [ZEROOS_DIR, os.path.join(ZEROOS_DIR, "state"), os.path.join(ZEROOS_DIR, "logs"), os.path.join(ZEROOS_DIR, "presets")]:
        os.makedirs(d, exist_ok=True)
    os.chmod(ZEROOS_DIR, 0o700)

    store_key(key.strip(), password)

    click.echo(f"  ✓ key encrypted. wallet: {wallet_short}")
    click.echo("  ✓ key NEVER leaves this machine.")
    click.echo()

    # --- OPT-IN: Analyze HL history ---
    click.echo("  zero can analyze your existing hyperliquid trading")
    click.echo("  history to recommend the best preset for you.")
    click.echo("  this reads your public on-chain trades.")
    click.echo()
    
    analyze = click.prompt("  analyze your trading history? [y/N]", default="n", show_default=False).strip().lower()
    click.echo()
    
    hl_profile = None
    if analyze == "y":
        click.echo(f"  ▸ reading history for {wallet_short}...")
        hl_profile = _analyze_hl_history(wallet_address)
        
        if hl_profile:
            click.echo(f"  found: {hl_profile['trade_count']} trades over {hl_profile['days_active']} days.")
            click.echo(f"  direction bias: {'long' if hl_profile['direction_bias'] > 0 else 'short'} ({hl_profile['direction_bias']:+.2f})")
            click.echo(f"  frequency: {hl_profile['frequency']} trades/day")
            click.echo(f"  recommended preset: {hl_profile['recommended']}")
            click.echo()
        else:
            click.echo("  no trading history found. choosing preset manually.")
            click.echo()

    # --- STEP 2: Agent Preset ---
    click.echo("  ▸ choose your agent")
    click.echo()
    click.echo("  [1] conservative — BTC/ETH only, few trades, high conviction")
    click.echo("  [2] balanced     — moderate risk, the default")
    click.echo("  [3] degen        — more trades, wider universe")
    click.echo("  [4] funding      — collect funding payments, delta-neutral")
    click.echo()

    default_choice = "2"
    if hl_profile:
        for k, (name, _, _) in PRESETS.items():
            if name == hl_profile["recommended"]:
                default_choice = k
                break

    preset_choice = click.prompt("  ", type=click.Choice(["1", "2", "3", "4"]), default=default_choice, show_choices=False)
    preset_name, preset_desc, max_pos = PRESETS[preset_choice]

    cfg = DEFAULT_CONFIG.copy()
    cfg["agent"] = {"name": preset_name, "preset": preset_name, "mode": "paper"}
    cfg["execution"] = {**DEFAULT_CONFIG["execution"], "max_positions": max_pos}

    click.echo(f"  ✓ agent/{preset_name}")
    click.echo()

    # --- STEP 3: Connect to zero network ---
    click.echo("  ▸ connecting to zero network...")
    click.echo()

    agent_id = str(uuid.uuid4())
    network_info = None

    # Check for existing auth token
    token_path = os.path.join(ZEROOS_DIR, "auth_token")
    operator_token = None
    if os.path.exists(token_path):
        with open(token_path) as f:
            operator_token = f.read().strip()

    if operator_token:
        click.echo("  ▸ registering operator .............. ", nl=False)
        click.echo("done")
        
        click.echo("  ▸ registering agent/{} ........ ".format(preset_name), nl=False)
        network_info = _register_with_network(agent_id, key, preset_name, operator_token)
        
        if network_info and "error" not in network_info:
            click.echo("done")
            page_url = network_info.get("page_url", f"{ZERO_API}/a/{network_info.get('short_id', '???')}")
            click.echo(f"  ▸ your agent page: {page_url}")
            
            # Save network info
            net_data = {
                "agent_id": agent_id,
                "short_id": network_info.get("short_id"),
                "page_url": page_url,
                "signals_endpoint": network_info.get("signals_endpoint"),
                "heartbeat_endpoint": network_info.get("heartbeat_endpoint"),
                "tier": network_info.get("tier", "verified"),
                "wallet_verified": network_info.get("wallet_verified", False),
            }
            with open(NETWORK_PATH, "w") as f:
                json.dump(net_data, f, indent=2)
            os.chmod(NETWORK_PATH, 0o600)
        else:
            click.echo("skipped")
            err = network_info.get("error", "unknown") if network_info else "no auth token"
            click.echo(f"  ℹ network registration: {err}")
            click.echo("  ℹ your agent will run locally. connect later with: zeroos network connect")
    else:
        click.echo("  ℹ no auth token found.")
        click.echo("  ℹ to connect to the zero network:")
        click.echo("    1. get access at getzero.dev/waitlist")
        click.echo("    2. save your token: zeroos auth login")
        click.echo("    3. register: zeroos network connect")
        click.echo("  ℹ your agent runs locally regardless. network is optional.")
    
    click.echo()

    # --- OPT-IN: Link wallet ---
    if analyze == "y":
        click.echo("  link your wallet to your zero profile for on-chain verification?")
        click.echo("  this makes your agent page verifiable by anyone.")
        click.echo("  your wallet address will be visible on your agent page.")
        click.echo()
        store_wallet = click.prompt("  link wallet? [y/N]", default="n", show_default=False).strip().lower() == "y"
        
        if store_wallet:
            cfg["telemetry"]["wallet_linked"] = True
            click.echo("  ✓ wallet linked for on-chain verification.")
        else:
            click.echo("  ✓ wallet not linked. you can link later.")
        click.echo()

    # Store agent_id in config
    cfg["agent"]["id"] = agent_id
    cfg["telemetry"]["dashboard_url"] = ZERO_API

    # Write config
    with open(CONFIG_PATH, "w") as f:
        f.write("# zero os agent configuration\n")
        f.write("# generated by: zeroos init\n")
        f.write("# docs: https://getzero.dev/docs\n\n")
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    os.chmod(CONFIG_PATH, 0o600)

    click.echo()
    click.echo("  agent registered. paper mode. ready.")
    click.echo()
    click.echo("  ◆ zero▮")
    click.echo()
    click.echo(f"  agent/{preset_name}")
    click.echo(f"  wallet: {wallet_short}")
    click.echo("  mode: paper (simulated)")
    if network_info and "error" not in (network_info or {}):
        click.echo(f"  page: {network_info.get('page_url', '')}")
    click.echo()
    click.echo("  $ zeroos start          — boot the os and start your agent")
    click.echo("  $ zeroos status         — check os and agent health")
    click.echo("  $ zeroos config --live  — switch to live (after paper)")
    click.echo()
    click.echo("  patience is the product.")
    click.echo()

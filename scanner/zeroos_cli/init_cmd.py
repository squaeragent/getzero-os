"""zeroos init — Interactive first-time setup."""

import os

import click
import yaml

from scanner.zeroos_cli.keystore import store_key

ZEROOS_DIR = os.path.expanduser("~/.zeroos")
CONFIG_PATH = os.path.join(ZEROOS_DIR, "config.yaml")

PRESETS = {
    "1": ("conservative", "BTC/ETH only, 2 positions, low risk", 2),
    "2": ("balanced", "Top 8 coins, 3 positions, moderate risk", 3),
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
        "x402_wallet": None,
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
        "dashboard_url": "https://getzero.dev",
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
    """Derive wallet address from private key (first/last 4 bytes shown)."""
    try:
        from eth_account import Account
        clean = key.strip()
        if not clean.startswith("0x"):
            clean = "0x" + clean
        acct = Account.from_key(clean)
        addr = acct.address
        return f"{addr[:6]}...{addr[-4:]}"
    except Exception:
        clean = key.strip()
        if clean.startswith("0x"):
            clean = clean[2:]
        return f"0x{clean[:4]}...{clean[-4:]}"


def _banner():
    click.echo()
    click.echo("  ┌─ ZERO OS ─────────────────────────────────────────┐")
    click.echo("  │                                                     │")
    click.echo("  │  Welcome to ZERO OS v1.0                           │")
    click.echo("  │  The operating system for trading agents.          │")
    click.echo("  │                                                     │")
    click.echo("  └─────────────────────────────────────────────────────┘")
    click.echo()


@click.command()
def init_cmd():
    """Interactive first-time setup."""
    _banner()

    # --- STEP 1: Hyperliquid Connection ---
    click.echo("  STEP 1: Hyperliquid Connection")
    click.echo()
    click.echo("  How do you want to connect to Hyperliquid?")
    click.echo()
    click.echo("  [1] Private key (most secure — never leaves this machine)")
    click.echo("  [2] API key (trade-only, no withdrawals)")
    click.echo()

    key_type = click.prompt("  ", type=click.Choice(["1", "2"]), default="1", show_choices=False)
    click.echo()

    if key_type == "1":
        key = click.prompt("  Enter your Hyperliquid private key", hide_input=True)
    else:
        key = click.prompt("  Enter your Hyperliquid API key", hide_input=True)

    if not _validate_hex_key(key):
        click.echo("  ✗ Invalid key format. Expected 64 hex characters (with optional 0x prefix).")
        raise SystemExit(1)

    wallet = _derive_wallet(key)

    password = click.prompt("  Set a password to encrypt your key", hide_input=True, confirmation_prompt=True)
    if not password:
        click.echo("  ✗ Password cannot be empty.")
        raise SystemExit(1)

    # Create directories
    for d in [ZEROOS_DIR, os.path.join(ZEROOS_DIR, "state"), os.path.join(ZEROOS_DIR, "logs"), os.path.join(ZEROOS_DIR, "presets")]:
        os.makedirs(d, exist_ok=True)
    os.chmod(ZEROOS_DIR, 0o700)

    store_key(key.strip(), password)

    click.echo(f"  ✓ Key validated. Wallet: {wallet}")
    click.echo("  ✓ Key encrypted and stored in ~/.zeroos/keystore.enc")
    click.echo("  ✓ Key NEVER leaves this machine.")
    click.echo()

    # --- STEP 2: Agent Preset ---
    click.echo("  STEP 2: Agent Preset")
    click.echo()
    click.echo("  Choose your agent's personality:")
    click.echo()
    click.echo("  [1] Conservative  — BTC/ETH only, 2 positions, low risk")
    click.echo("  [2] Balanced      — Top 8 coins, 3 positions, moderate risk (default)")
    click.echo("  [3] Degen         — Top 15 coins, 6 positions, high risk")
    click.echo("  [4] Funding       — Short high-funding coins, delta-neutral")
    click.echo()

    preset_choice = click.prompt("  ", type=click.Choice(["1", "2", "3", "4"]), default="2", show_choices=False)
    preset_name, preset_desc, max_pos = PRESETS[preset_choice]

    cfg = DEFAULT_CONFIG.copy()
    cfg["agent"] = {"name": preset_name, "preset": preset_name, "mode": "paper"}
    cfg["execution"] = {**DEFAULT_CONFIG["execution"], "max_positions": max_pos}
    cfg["hyperliquid"]["key_type"] = "private_key" if key_type == "1" else "api_key"

    click.echo(f"  ✓ Preset: {preset_name}")
    click.echo()

    # --- STEP 3: Signal API ---
    click.echo("  STEP 3: Signal API")
    click.echo()
    click.echo("  ZERO OS fetches signals from the NVProtocol API.")
    click.echo("  Payment: x402 micropayments (on-chain, per API call).")
    click.echo()

    x402 = click.prompt("  Enter your x402 wallet address (or press Enter to skip)", default="", show_default=False)
    click.echo()

    if x402.strip():
        cfg["signals"]["x402_wallet"] = x402.strip()
        click.echo("  ✓ x402 wallet configured. Full signals enabled.")
    else:
        click.echo("  ⚠ No x402 wallet configured. Agent will use basic signals only.")
        click.echo("    Run `zeroos config --x402 <wallet>` to enable full signals.")
    click.echo()

    # --- STEP 4: Paper Mode ---
    click.echo("  STEP 4: Paper Mode")
    click.echo()
    click.echo("  Your agent starts in PAPER MODE (simulated trades).")
    click.echo("  No real money is used until you explicitly switch to live.")
    click.echo("  Minimum paper period: 24 hours or 50 simulated trades.")
    click.echo()
    click.echo("  ✓ Paper mode active.")
    click.echo()

    # Write config
    with open(CONFIG_PATH, "w") as f:
        f.write("# ZERO OS Agent Configuration\n")
        f.write("# Generated by: zeroos init\n")
        f.write("# Docs: https://docs.getzero.dev/config\n\n")
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    os.chmod(CONFIG_PATH, 0o600)

    click.echo("  ✓ Config saved to ~/.zeroos/config.yaml")
    click.echo()

    # --- Ready banner ---
    signals_status = "full (x402 active)" if x402.strip() else "basic (upgrade with x402)"
    click.echo("  ┌─ READY ────────────────────────────────────────────┐")
    click.echo("  │                                                     │")
    click.echo(f"  │  Agent:    agent/{preset_name:<37s}│")
    click.echo("  │  Mode:     PAPER (simulated)                       │")
    click.echo(f"  │  Wallet:   {wallet:<40s}│")
    click.echo(f"  │  Signals:  {signals_status:<40s}│")
    click.echo("  │                                                     │")
    click.echo("  │  Start:    zeroos start                            │")
    click.echo("  │  Status:   zeroos status                           │")
    click.echo("  │  Live:     zeroos config --live (after paper)      │")
    click.echo("  │  Dashboard: zeroos dashboard --connect             │")
    click.echo("  │                                                     │")
    click.echo("  └─────────────────────────────────────────────────────┘")
    click.echo()

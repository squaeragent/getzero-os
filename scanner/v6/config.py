#!/usr/bin/env python3
"""
V6 Configuration — all constants and env helpers in one place.
"""

import os
from pathlib import Path

# ─── PATHS ────────────────────────────────────────────────────────────────────
V6_DIR        = Path(__file__).parent
BUS_DIR       = V6_DIR / "bus"
DATA_DIR      = V6_DIR / "data"
SCANNER_DIR   = V6_DIR.parent
SIGNALS_CACHE_DIR = SCANNER_DIR / "data" / "signals_cache"  # V5 fallback

# Bus files
STRATEGIES_FILE = BUS_DIR / "strategies.json"
ALLOCATION_FILE = BUS_DIR / "allocation.json"
ENTRIES_FILE    = BUS_DIR / "entries.json"
EXITS_FILE      = BUS_DIR / "exits.json"
APPROVED_FILE   = BUS_DIR / "approved.json"
POSITIONS_FILE  = BUS_DIR / "positions.json"
RISK_FILE       = BUS_DIR / "risk.json"
HEARTBEAT_FILE  = BUS_DIR / "heartbeat.json"

# Data files
TRADES_FILE = DATA_DIR / "trades.jsonl"

# ─── ACCOUNT ──────────────────────────────────────────────────────────────────
CAPITAL           = 750.0
CAPITAL_FLOOR     = 500.0
DAILY_LOSS_LIMIT  = 50.0

# ─── POSITION LIMITS ──────────────────────────────────────────────────────────
MAX_POSITIONS     = 3
MAX_PER_COIN      = 1
MAX_POSITION_USD  = 250.0
MIN_POSITION_USD  = 50.0

# ─── RISK ─────────────────────────────────────────────────────────────────────
STOP_LOSS_PCT     = 0.05   # 5% hard stop (override ENVY's 30%)
MIN_HOLD_MINUTES  = 15     # minimum hold before evaluating exits

# ─── STRATEGY ─────────────────────────────────────────────────────────────────
STRATEGY_REFRESH_HOURS = 6
ACTIVE_COINS_COUNT     = 8   # top coins from portfolio/optimize (or scoring)
STRATEGY_VERSION       = 6

# ─── ENVY API ─────────────────────────────────────────────────────────────────
ENVY_BASE_URL = "https://gate.getzero.dev/api/claw"
ENVY_WS_URL   = "wss://gate.getzero.dev/api/claw/ws/indicators"

# ─── HYPERLIQUID ──────────────────────────────────────────────────────────────
HL_MAIN_ADDRESS  = "0xA5F25E3Bbf7a10EB61EEfA471B61E1dfa5777884"
HL_API_WALLET    = "0xc7b52216e7bc13de0cd010aa12cacb6d774453a2"
HL_INFO_URL      = "https://api.hyperliquid.xyz/info"
HL_EXCHANGE_URL  = "https://api.hyperliquid.xyz/exchange"

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_CHAT_ID        = "133058580"
TELEGRAM_BOT_TOKEN_ENV  = "TELEGRAM_BOT_TOKEN"

# ─── SUPABASE ─────────────────────────────────────────────────────────────────
SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_SERVICE_KEY"

# ─── COIN UNIVERSE ────────────────────────────────────────────────────────────
ALL_COINS = [
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
]

# ─── ENV HELPERS ──────────────────────────────────────────────────────────────
def load_env() -> dict:
    """Load all env vars from ~/.config/openclaw/.env."""
    env_path = Path("~/.config/openclaw/.env").expanduser()
    env = {}
    if not env_path.exists():
        return env
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_env(key: str, default: str = "") -> str:
    """Get env var from process environment or ~/.config/openclaw/.env."""
    val = os.environ.get(key)
    if val:
        return val
    return load_env().get(key, default)


if __name__ == "__main__":
    print("V6 config OK")
    print(f"  V6_DIR:           {V6_DIR}")
    print(f"  CAPITAL:          ${CAPITAL}")
    print(f"  CAPITAL_FLOOR:    ${CAPITAL_FLOOR}")
    print(f"  MAX_POSITIONS:    {MAX_POSITIONS}")
    print(f"  MAX_POSITION_USD: ${MAX_POSITION_USD}")
    print(f"  STOP_LOSS_PCT:    {STOP_LOSS_PCT*100:.0f}%")
    print(f"  MIN_HOLD_MINUTES: {MIN_HOLD_MINUTES}")
    env = load_env()
    print(f"  ENVY_API_KEY:     {'SET' if env.get('ENVY_API_KEY') else 'MISSING'}")
    print(f"  HL_PRIVATE_KEY:   {'SET' if env.get('HL_PRIVATE_KEY') else 'MISSING'}")
    print(f"  SUPABASE_URL:     {'SET' if env.get('SUPABASE_URL') else 'MISSING'}")

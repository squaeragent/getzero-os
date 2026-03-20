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
EQUITY_HISTORY_FILE = BUS_DIR / "equity_history.jsonl"

# ─── ACCOUNT ──────────────────────────────────────────────────────────────────
CAPITAL           = 750.0   # initial deposit — used only for reference
CAPITAL_FLOOR_PCT = 0.60    # halt trading if equity drops below 60% of peak
CAPITAL_FLOOR     = CAPITAL * CAPITAL_FLOOR_PCT  # $450 initially, but risk_guard uses dynamic peak
DAILY_LOSS_LIMIT  = 50.0

# ─── POSITION LIMITS ──────────────────────────────────────────────────────────
MAX_POSITIONS     = 3
MAX_PER_COIN      = 1
MAX_POSITION_USD  = 250.0
MIN_POSITION_USD  = 50.0

# ─── RISK ─────────────────────────────────────────────────────────────────────
STOP_LOSS_PCT     = 0.05   # default fallback — overridden by per-coin volatility stops

# Per-coin stop loss based on typical daily volatility (ATR proxy)
# Updated: derived from 30d realized vol data
COIN_STOP_PCT = {
    "BTC":     0.03,   # ~3% daily vol
    "ETH":     0.04,   # ~4% daily vol
    "SOL":     0.06,   # ~6% daily vol
    "DOGE":    0.08,   # ~8% daily vol
    "XRP":     0.05,   # ~5% daily vol
    "ADA":     0.06,   # ~6% daily vol
    "AVAX":    0.06,   # ~6% daily vol
    "NEAR":    0.07,   # ~7% daily vol
    "SUI":     0.08,   # ~8% daily vol
    "LTC":     0.05,   # ~5% daily vol
    "LINK":    0.06,   # ~6% daily vol
    "DOT":     0.06,   # ~6% daily vol
    "HYPE":    0.10,   # ~10% daily vol (new, volatile)
    "ZEC":     0.06,   # ~6% daily vol
    "PAXG":    0.02,   # ~2% daily vol (gold-pegged)
    "FARTCOIN": 0.12,  # ~12% daily vol (meme)
    "TRUMP":   0.10,   # ~10% daily vol (meme)
    "BONK":    0.10,   # ~10% daily vol (meme)
    "kBONK":   0.10,
    "PUMP":    0.12,   # ~12% daily vol (meme)
}

def get_stop_pct(coin: str, signal_stop: float = 0) -> float:
    """Get stop loss % for a coin. Priority: per-coin vol > signal > default."""
    vol_stop = COIN_STOP_PCT.get(coin, STOP_LOSS_PCT)
    # If signal has a stop and it's tighter than vol-based, use signal's
    if signal_stop > 0 and signal_stop < vol_stop:
        return signal_stop
    return vol_stop
MIN_HOLD_MINUTES  = 15     # minimum hold before evaluating exits

# ─── STRATEGY ─────────────────────────────────────────────────────────────────
STRATEGY_REFRESH_HOURS = 2  # was 6h — too slow to catch new signals
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

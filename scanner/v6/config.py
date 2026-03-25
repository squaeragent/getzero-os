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

# Paper trading state
PAPER_STATE_DIR = Path('~/.zeroos/state').expanduser()
PAPER_STATE_FILE = PAPER_STATE_DIR / 'paper_state.json'

# Paper mode uses isolated bus/data directories to avoid contaminating live state
PAPER_BUS_DIR  = PAPER_STATE_DIR / "bus"
PAPER_DATA_DIR = PAPER_STATE_DIR / "data"

# ─── ACCOUNT ──────────────────────────────────────────────────────────────────
CAPITAL           = 49.0    # ZERO wallet — initial deposit reference
CAPITAL_FLOOR_PCT = 0.60    # halt if equity < 60% of peak
CAPITAL_FLOOR     = CAPITAL * CAPITAL_FLOOR_PCT  # static fallback
DAILY_LOSS_LIMIT_PCT = 0.07  # 7% of equity per day
DAILY_LOSS_LIMIT  = CAPITAL * DAILY_LOSS_LIMIT_PCT  # static fallback

# ─── POSITION LIMITS (% of equity — computed dynamically) ─────────────────────
MAX_PER_COIN      = 1
MAX_POSITION_PCT  = 0.33    # 33% of equity max per position
MIN_POSITION_PCT  = 0.07    # 7% of equity min per position
FEE_RATE          = 0.00045  # 0.045% HL base taker — queried dynamically per trade


def get_dynamic_limits(equity: float) -> dict:
    """All position limits from current equity. Nothing hardcoded."""
    return {
        "max_positions":    2 if equity < 500 else 3 if equity < 1500 else 4 if equity < 3000 else 5,
        "max_position_usd": round(equity * MAX_POSITION_PCT, 2),
        "min_position_usd": round(max(10, equity * MIN_POSITION_PCT), 2),
        "daily_loss_limit": round(equity * DAILY_LOSS_LIMIT_PCT, 2),
    }


# Legacy constants (fallback when no equity available)
MAX_POSITIONS     = 3
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
# Per-asset slippage tolerance (derived from typical spread + vol)
COIN_SLIPPAGE = {
    "BTC":     0.002,   # 0.2% — deep book
    "ETH":     0.003,   # 0.3%
    "SOL":     0.005,   # 0.5%
    "XRP":     0.004,   # 0.4%
    "DOGE":    0.008,   # 0.8%
    "PAXG":    0.003,   # 0.3% — gold, tight
    "FARTCOIN": 0.015,  # 1.5% — meme, thin
    "TRUMP":   0.012,   # 1.2% — meme
    "PUMP":    0.015,   # 1.5% — meme
    "HYPE":    0.010,   # 1.0% — volatile
    "kBONK":   0.012,   # 1.2% — meme
}
DEFAULT_SLIPPAGE = 0.01  # 1% default


def get_slippage(coin: str) -> float:
    """Get per-asset slippage tolerance."""
    return COIN_SLIPPAGE.get(coin, DEFAULT_SLIPPAGE)


MIN_HOLD_MINUTES  = 120    # 2h minimum hold — P0 data: <1h trades avg $0.012, 4-12h avg $0.557

# ─── LEVERAGE (E2: explicit, not HL defaults) ────────────────────────────────
COIN_LEVERAGE = {
    "BTC":      5,   # major, deep book
    "ETH":      5,   # major, deep book
    "SOL":      3,   # L1, volatile
    "XRP":      3,
    "DOGE":     3,
    "LTC":      3,
    "LINK":     3,
    "DOT":      3,
    "ADA":      3,
    "AVAX":     3,
    "NEAR":     3,
    "SUI":      3,
    "OP":       3,
    "UNI":      3,
    "BNB":      3,
    "AAVE":     3,
    "SEI":      3,
    "TIA":      3,
    "WLD":      3,
    "INJ":      3,
    "ZEC":      3,
    "BCH":      3,
    "CRV":      3,
    "ENA":      3,
    "LDO":      3,
    "ONDO":     3,
    "JUP":      3,
    "TON":      3,
    "TRX":      3,
    "HYPE":     2,   # volatile alt
    "TRUMP":    2,   # meme
    "FARTCOIN": 2,   # meme, thin
    "PUMP":     2,   # meme, thin
    "XPL":      2,   # thin book
    "kBONK":    2,
    "kPEPE":    2,
    "kSHIB":    2,
    "PAXG":     3,   # gold, low vol
}
DEFAULT_LEVERAGE = 3

def get_leverage(coin: str) -> int:
    return COIN_LEVERAGE.get(coin, DEFAULT_LEVERAGE)


# ─── TRAILING STOP (H2: ATR-based, not flat 0.3%) ────────────────────────────
def get_trailing_trigger(coin: str) -> float:
    """Trailing stop trigger = stop_pct * 0.5 (half of stop distance)."""
    return get_stop_pct(coin) * 0.5

# ─── STRATEGY ─────────────────────────────────────────────────────────────────
STRATEGY_REFRESH_HOURS = 6  # 6h refresh — 365d backtests don't change hourly. Saves ~2/3 credits.
                             # API audit 2026-03-22: signal check=$1, assemble=$3. At 2h: 864 credits/day (55 days).
                             # At 6h: 288 credits/day (164 days). WebSocket evaluator runs continuously regardless.
ACTIVE_COINS_COUNT     = 16  # top coins from portfolio/optimize (or scoring)
STRATEGY_VERSION       = 6

# ─── ENVY API ─────────────────────────────────────────────────────────────────
ENVY_BASE_URL = "https://gate.getzero.dev/api/claw"
ENVY_WS_URL   = "wss://gate.getzero.dev/api/claw/ws/indicators"

# ─── HYPERLIQUID ──────────────────────────────────────────────────────────────
HL_MAIN_ADDRESS  = os.environ.get("HYPERLIQUID_MAIN_ADDRESS", "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e")
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
    print(f"  NVARENA_API_KEY:  {'SET' if env.get('NVARENA_API_KEY') or env.get('ENVY_API_KEY') else 'MISSING'}")
    print(f"  HL_PRIVATE_KEY:   {'SET' if env.get('HL_PRIVATE_KEY') else 'MISSING'}")

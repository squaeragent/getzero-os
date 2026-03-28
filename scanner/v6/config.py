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
CAPITAL_FLOOR_PCT = 0.40    # halt if equity < 40% of peak (degen: wider runway)
CAPITAL_FLOOR     = CAPITAL * CAPITAL_FLOOR_PCT  # static fallback
DAILY_LOSS_LIMIT_PCT = 0.12  # 12% of equity per day (degen: more room)
DAILY_LOSS_LIMIT  = CAPITAL * DAILY_LOSS_LIMIT_PCT  # static fallback

# ─── POSITION LIMITS (% of equity — computed dynamically) ─────────────────────
# PRESET: DEGEN — more trades, bigger sizes, wider universe
MAX_PER_COIN      = 1
MAX_POSITION_PCT  = 0.50    # 50% of equity max per position (degen)
MIN_POSITION_PCT  = 0.05    # 5% of equity min per position (degen: lower floor)
FEE_RATE          = 0.00045  # 0.045% HL base taker — queried dynamically per trade


def get_dynamic_limits(equity: float) -> dict:
    """All position limits from current equity. DEGEN preset: more aggressive."""
    return {
        "max_positions":    3 if equity < 200 else 4 if equity < 1000 else 5 if equity < 3000 else 6,
        "max_position_usd": round(equity * MAX_POSITION_PCT, 2),
        "min_position_usd": round(max(10, equity * MIN_POSITION_PCT), 2),
        "daily_loss_limit": round(equity * DAILY_LOSS_LIMIT_PCT, 2),
    }


# Legacy constants (fallback when no equity available)
MAX_POSITIONS     = 4
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
# PRESET: DEGEN — higher leverage across the board
COIN_LEVERAGE = {
    "BTC":      7,   # major, deep book
    "ETH":      7,   # major, deep book
    "SOL":      5,   # L1
    "XRP":      5,
    "DOGE":     5,
    "LTC":      5,
    "LINK":     5,
    "DOT":      5,
    "ADA":      5,
    "AVAX":     5,
    "NEAR":     5,
    "SUI":      5,
    "OP":       5,
    "UNI":      5,
    "BNB":      5,
    "AAVE":     5,
    "SEI":      5,
    "TIA":      5,
    "WLD":      5,
    "INJ":      5,
    "ZEC":      5,
    "BCH":      5,
    "CRV":      5,
    "ENA":      5,
    "LDO":      5,
    "ONDO":     5,
    "JUP":      5,
    "TON":      5,
    "TRX":      5,
    "HYPE":     3,   # volatile alt
    "TRUMP":    3,   # meme
    "FARTCOIN": 3,   # meme, thin
    "PUMP":     3,   # meme, thin
    "XPL":      3,   # thin book
    "kBONK":    3,
    "kPEPE":    3,
    "kSHIB":    3,
    "PAXG":     5,   # gold, low vol
}
DEFAULT_LEVERAGE = 5

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
ACTIVE_COINS_COUNT     = 24  # degen: wider universe, more opportunities
STRATEGY_VERSION       = 6

# ─── HYPERLIQUID ──────────────────────────────────────────────────────────────
HL_MAIN_ADDRESS  = os.environ.get("HYPERLIQUID_MAIN_ADDRESS", "0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e")
HL_API_WALLET    = os.environ.get("HYPERLIQUID_API_WALLET", "0xc7b52216e7bc13de0cd010aa12cacb6d774453a2")
HL_INFO_URL      = "https://api.hyperliquid.xyz/info"
HL_EXCHANGE_URL  = "https://api.hyperliquid.xyz/exchange"

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_CHAT_ID        = "133058580"
TELEGRAM_BOT_TOKEN_ENV  = "TELEGRAM_BOT_TOKEN"


# ─── CONTROLLER TIMING (overridable via environment) ─────────────────────────
CYCLE_SECONDS              = int(os.environ.get("ZERO_CYCLE_SECONDS", "5"))
RECONCILE_INTERVAL         = int(os.environ.get("ZERO_RECONCILE_INTERVAL", "300"))
HEARTBEAT_INTERVAL         = int(os.environ.get("ZERO_HEARTBEAT_INTERVAL", "60"))
FAILED_ENTRY_COOLDOWN      = int(os.environ.get("ZERO_FAILED_ENTRY_COOLDOWN", "900"))
ALERT_COOLDOWN             = int(os.environ.get("ZERO_ALERT_COOLDOWN", "300"))

# ─── HARD SAFETY CAPS (controller-level, not strategy-configurable) ──────────
HARD_MAX_POSITION_PCT      = int(os.environ.get("ZERO_HARD_MAX_POSITION_PCT", "25"))
HARD_MAX_EXPOSURE_PCT      = int(os.environ.get("ZERO_HARD_MAX_EXPOSURE_PCT", "80"))
HARD_MAX_ORDERS_PER_MIN    = int(os.environ.get("ZERO_HARD_MAX_ORDERS_PER_MIN", "10"))
HARD_MAX_ORDERS_PER_SESSION = int(os.environ.get("ZERO_HARD_MAX_ORDERS_PER_SESSION", "100"))

# ─── MONITOR TIMING ─────────────────────────────────────────────────────────
PRICE_STALE_MS             = int(os.environ.get("ZERO_PRICE_STALE_MS", "120000"))
SOURCE_STALE_MS            = int(os.environ.get("ZERO_SOURCE_STALE_MS", "30000"))
FEAR_GREED_TTL_S           = int(os.environ.get("ZERO_FEAR_GREED_TTL_S", "300"))

# ─── IMMUNE SYSTEM ──────────────────────────────────────────────────────────
IMMUNE_CYCLE_SECONDS       = int(os.environ.get("ZERO_IMMUNE_CYCLE_SECONDS", "60"))
CONTROLLER_STALE_THRESHOLD_S = int(os.environ.get("ZERO_CONTROLLER_STALE_THRESHOLD", "300"))
DEAD_MAN_STOP_PCT_CFG      = float(os.environ.get("ZERO_DEAD_MAN_STOP_PCT", "0.01"))

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
    """Load all env vars from ~/getzero-os/.env (engine-owned)."""
    env_path = Path("~/getzero-os/.env").expanduser()
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
    """Get env var from process environment, key file, or ~/getzero-os/.env.

    For sensitive keys (HL_PRIVATE_KEY, HYPERLIQUID_SECRET_KEY), prefers
    reading from the HL_KEY_FILE path to avoid /proc/pid/environ exposure.
    """
    # For private keys, prefer secure key file over env var
    if key in ("HL_PRIVATE_KEY", "HYPERLIQUID_SECRET_KEY"):
        key_file = os.environ.get("HL_KEY_FILE")
        if key_file and os.path.isfile(key_file):
            try:
                with open(key_file) as f:
                    val = f.read().strip()
                if val:
                    return val
            except OSError:
                pass
    val = os.environ.get(key)
    if val:
        return val
    return load_env().get(key, default)


API_VERSION = "6.1.0"


def validate_config() -> list[str]:
    """Validate all config constants are within safe ranges.

    Returns list of errors (empty = all OK). Called at import time
    so misconfigurations are caught early.
    """
    errors = []

    # Account
    if not (0.0 < CAPITAL_FLOOR_PCT < 1.0):
        errors.append(f"CAPITAL_FLOOR_PCT={CAPITAL_FLOOR_PCT} must be (0, 1)")
    if not (0.0 < DAILY_LOSS_LIMIT_PCT < 1.0):
        errors.append(f"DAILY_LOSS_LIMIT_PCT={DAILY_LOSS_LIMIT_PCT} must be (0, 1)")

    # Position limits
    if not (0.0 < MAX_POSITION_PCT <= 1.0):
        errors.append(f"MAX_POSITION_PCT={MAX_POSITION_PCT} must be (0, 1]")
    if not (0.0 < MIN_POSITION_PCT <= MAX_POSITION_PCT):
        errors.append(f"MIN_POSITION_PCT={MIN_POSITION_PCT} must be (0, MAX_POSITION_PCT]")
    if MAX_PER_COIN < 1:
        errors.append(f"MAX_PER_COIN={MAX_PER_COIN} must be >= 1")
    if not (0.0 < FEE_RATE < 0.01):
        errors.append(f"FEE_RATE={FEE_RATE} must be (0, 0.01)")

    # Risk
    if not (0.0 < STOP_LOSS_PCT < 1.0):
        errors.append(f"STOP_LOSS_PCT={STOP_LOSS_PCT} must be (0, 1)")
    for coin, pct in COIN_STOP_PCT.items():
        if not (0.0 < pct < 1.0):
            errors.append(f"COIN_STOP_PCT[{coin}]={pct} must be (0, 1)")
    for coin, slip in COIN_SLIPPAGE.items():
        if not (0.0 < slip < 0.1):
            errors.append(f"COIN_SLIPPAGE[{coin}]={slip} must be (0, 0.1)")

    # Leverage
    for coin, lev in COIN_LEVERAGE.items():
        if not (1 <= lev <= 10):
            errors.append(f"COIN_LEVERAGE[{coin}]={lev} must be [1, 10]")
    if not (1 <= DEFAULT_LEVERAGE <= 10):
        errors.append(f"DEFAULT_LEVERAGE={DEFAULT_LEVERAGE} must be [1, 10]")

    # Timing
    if CYCLE_SECONDS < 1:
        errors.append(f"CYCLE_SECONDS={CYCLE_SECONDS} must be >= 1")
    if RECONCILE_INTERVAL < 1:
        errors.append(f"RECONCILE_INTERVAL={RECONCILE_INTERVAL} must be >= 1")

    # Hard caps
    if not (1 <= HARD_MAX_POSITION_PCT <= 100):
        errors.append(f"HARD_MAX_POSITION_PCT={HARD_MAX_POSITION_PCT} must be [1, 100]")
    if not (1 <= HARD_MAX_EXPOSURE_PCT <= 100):
        errors.append(f"HARD_MAX_EXPOSURE_PCT={HARD_MAX_EXPOSURE_PCT} must be [1, 100]")
    if HARD_MAX_ORDERS_PER_MIN < 1:
        errors.append(f"HARD_MAX_ORDERS_PER_MIN={HARD_MAX_ORDERS_PER_MIN} must be >= 1")

    # Coin universe
    if len(ALL_COINS) < 1:
        errors.append("ALL_COINS is empty")
    if len(ALL_COINS) != len(set(ALL_COINS)):
        errors.append("ALL_COINS has duplicates")

    return errors


# Validate at import time — fail fast
_config_errors = validate_config()
if _config_errors:
    import warnings
    for err in _config_errors:
        warnings.warn(f"Config validation: {err}", stacklevel=2)


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
    print(f"  HL_PRIVATE_KEY:   {'SET' if env.get('HL_PRIVATE_KEY') else 'MISSING'}")

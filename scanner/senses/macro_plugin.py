#!/usr/bin/env python3
"""
ZERO OS — Macro Intelligence Sense Plugin
Fetches free macro data every cycle and writes to scanner/bus/macro_intel.json.

Data sources (all free, no API key):
  1. Alternative.me Fear & Greed Index
  2. CoinGecko Global Market Data
  3. Deribit DVOL (BTC implied volatility)
  4. Deribit Options Expiry OI by date
  5. Hardcoded FOMC + quarterly options expiry calendar

Outputs:
  scanner/bus/macro_intel.json   — macro intelligence snapshot
  scanner/data/live/macro.log    — rotating log

Usage:
  python3 scanner/senses/macro_plugin.py           # single run
  python3 scanner/senses/macro_plugin.py --loop    # continuous 15-min cycle
"""

import json
import logging
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

SCANNER_DIR = Path(__file__).parent.parent
BUS_DIR = SCANNER_DIR / "bus"
DATA_DIR = SCANNER_DIR / "data" / "live"
LOG_FILE = DATA_DIR / "macro.log"
OUTPUT_FILE = BUS_DIR / "macro_intel.json"

BUS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MACRO] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("macro")

# ── Constants ──────────────────────────────────────────────────────────────────

CYCLE_SEC = 15 * 60  # 15 minutes

# FOMC meeting dates 2026
FOMC_DATES_2026 = [
    date(2026, 1, 29),
    date(2026, 3, 19),
    date(2026, 5, 7),
    date(2026, 6, 18),
    date(2026, 7, 30),
    date(2026, 9, 17),
    date(2026, 10, 29),
    date(2026, 12, 10),
]

# Quarterly options expiry: last Friday of Mar, Jun, Sep, Dec 2025-2027
def _last_friday(year: int, month: int) -> date:
    """Return the last Friday of a given month."""
    # Start from the last day of the month
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    # Walk back to Friday (weekday 4)
    offset = (last_day.weekday() - 4) % 7
    return last_day - timedelta(days=offset)


QUARTERLY_EXPIRY_DATES = sorted([
    _last_friday(y, m)
    for y in range(2025, 2028)
    for m in [3, 6, 9, 12]
])

# Signal thresholds
EXTREME_FEAR_THRESHOLD = 25
EXTREME_GREED_THRESHOLD = 75
HIGH_VOL_THRESHOLD = 65.0
LOW_VOL_THRESHOLD = 30.0
IMMINENT_DAYS = 2  # days before event = "imminent"

# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 15) -> dict | list | None:
    """Fetch JSON from a URL. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZERO-OS/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"HTTP error fetching {url}: {e}")
        return None


# ── Data Sources ───────────────────────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    """Source 1: Alternative.me Fear & Greed Index."""
    data = _fetch_json("https://api.alternative.me/fng/?limit=1")
    if not data:
        return {}
    try:
        entry = data["data"][0]
        return {
            "fear_greed": int(entry["value"]),
            "fear_greed_class": entry["value_classification"],
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning(f"Fear & Greed parse error: {e}")
        return {}


def fetch_coingecko_global() -> dict:
    """Source 2: CoinGecko Global market data."""
    data = _fetch_json("https://api.coingecko.com/api/v3/global")
    if not data:
        return {}
    try:
        gdata = data["data"]
        total_mcap = gdata["total_market_cap"].get("usd", 0)
        total_vol = gdata["total_volume"].get("usd", 0)
        btc_dom = gdata["market_cap_percentage"].get("btc", 0)
        return {
            "total_mcap_usd": float(total_mcap),
            "total_vol_24h_usd": float(total_vol),
            "btc_dominance": round(float(btc_dom), 2),
            "total_mcap_trillion": round(float(total_mcap) / 1e12, 3),
            "total_vol_24h_billion": round(float(total_vol) / 1e9, 1),
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"CoinGecko global parse error: {e}")
        return {}


def fetch_deribit_dvol() -> dict:
    """Source 3: Deribit BTC DVOL (implied volatility index)."""
    url = (
        "https://deribit.com/api/v2/public/get_volatility_index_data"
        "?currency=BTC&resolution=3600&start_timestamp=1&end_timestamp=9999999999999"
    )
    data = _fetch_json(url)
    if not data:
        return {}
    try:
        rows = data["result"]["data"]
        if not rows:
            return {}
        # Each row: [timestamp, open, high, low, close]
        latest = rows[-1]
        close_val = float(latest[4])
        return {"btc_dvol": round(close_val, 2)}
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning(f"Deribit DVOL parse error: {e}")
        return {}


def fetch_deribit_options_expiry() -> dict:
    """Source 4: Deribit BTC options OI grouped by expiry date."""
    url = (
        "https://deribit.com/api/v2/public/get_book_summary_by_currency"
        "?currency=BTC&kind=option"
    )
    data = _fetch_json(url, timeout=30)
    if not data:
        return {}
    try:
        summaries = data["result"]
        oi_by_expiry: dict[str, float] = defaultdict(float)
        for item in summaries:
            instrument = item.get("instrument_name", "")
            # Format: BTC-27MAR26-50000-C  → parts[1] = 27MAR26
            parts = instrument.split("-")
            if len(parts) < 2:
                continue
            expiry = parts[1]
            oi = float(item.get("open_interest", 0))
            oi_by_expiry[expiry] += oi

        # Sort by OI descending, take top 5
        sorted_expiry = sorted(oi_by_expiry.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "options_expiry": [
                {"expiry": exp, "oi": round(oi, 2)}
                for exp, oi in sorted_expiry
            ]
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"Deribit options expiry parse error: {e}")
        return {}


def compute_calendar() -> dict:
    """Source 5: Compute days to next FOMC and quarterly options expiry."""
    today = datetime.now(timezone.utc).date()

    # Days to next FOMC
    future_fomc = [d for d in FOMC_DATES_2026 if d >= today]
    days_to_fomc = (future_fomc[0] - today).days if future_fomc else 999

    # Days to next quarterly options expiry
    future_expiry = [d for d in QUARTERLY_EXPIRY_DATES if d >= today]
    days_to_options_expiry = (future_expiry[0] - today).days if future_expiry else 999

    macro_event_imminent = (
        days_to_fomc < IMMINENT_DAYS or days_to_options_expiry < IMMINENT_DAYS
    )

    return {
        "days_to_fomc": days_to_fomc,
        "days_to_options_expiry": days_to_options_expiry,
        "macro_event_imminent": macro_event_imminent,
    }


# ── Signal computation ─────────────────────────────────────────────────────────

def compute_signals(intel: dict) -> dict:
    """Derive boolean signals from the intel snapshot."""
    fg = intel.get("fear_greed")
    dvol = intel.get("btc_dvol")
    days_fomc = intel.get("days_to_fomc", 999)
    days_exp = intel.get("days_to_options_expiry", 999)

    return {
        "extreme_fear": fg is not None and fg <= EXTREME_FEAR_THRESHOLD,
        "extreme_greed": fg is not None and fg >= EXTREME_GREED_THRESHOLD,
        "high_vol": dvol is not None and dvol >= HIGH_VOL_THRESHOLD,
        "low_vol": dvol is not None and dvol <= LOW_VOL_THRESHOLD,
        "options_expiry_near": days_exp < IMMINENT_DAYS,
        "fomc_near": days_fomc < IMMINENT_DAYS,
    }


# ── Main cycle ─────────────────────────────────────────────────────────────────

def run_once() -> dict:
    """Fetch all sources and write macro_intel.json. Returns the snapshot."""
    logger.info("Starting macro intelligence fetch")
    intel: dict = {}

    # Fetch all sources, tolerating individual failures
    for name, fn in [
        ("fear_greed", fetch_fear_greed),
        ("coingecko", fetch_coingecko_global),
        ("dvol", fetch_deribit_dvol),
        ("options_expiry", fetch_deribit_options_expiry),
        ("calendar", compute_calendar),
    ]:
        try:
            result = fn()
            if result:
                intel.update(result)
                logger.info(f"  [{name}] OK → {list(result.keys())}")
            else:
                logger.warning(f"  [{name}] returned empty data")
        except Exception as e:
            logger.warning(f"  [{name}] exception: {e}")

    # Attach metadata and signals
    intel["timestamp"] = datetime.now(timezone.utc).isoformat()
    intel["signals"] = compute_signals(intel)

    # Reorder keys for readability (non-critical)
    ordered_keys = [
        "timestamp", "fear_greed", "fear_greed_class",
        "btc_dominance", "total_mcap_trillion", "total_vol_24h_billion",
        "total_mcap_usd", "total_vol_24h_usd",
        "btc_dvol", "options_expiry",
        "days_to_fomc", "days_to_options_expiry", "macro_event_imminent",
        "signals",
    ]
    snapshot = {k: intel[k] for k in ordered_keys if k in intel}
    # Append any remaining keys not in the ordered list
    for k, v in intel.items():
        if k not in snapshot:
            snapshot[k] = v

    OUTPUT_FILE.write_text(json.dumps(snapshot, indent=2))
    logger.info(
        f"Wrote macro_intel.json — FG={intel.get('fear_greed','?')} "
        f"DVOL={intel.get('btc_dvol','?')} "
        f"FOMC_in={intel.get('days_to_fomc','?')}d "
        f"OE_in={intel.get('days_to_options_expiry','?')}d"
    )
    return snapshot


def main():
    loop_mode = "--loop" in sys.argv

    if not loop_mode:
        run_once()
        return

    logger.info(f"Loop mode: fetching every {CYCLE_SEC // 60} minutes")
    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"Unhandled error in run_once: {e}", exc_info=True)
        logger.info(f"Sleeping {CYCLE_SEC}s until next fetch")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()

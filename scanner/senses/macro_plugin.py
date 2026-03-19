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

# ── HL Liquidation cluster config ──────────────────────────────────────────────

HL_ENRICHMENT_FILE = BUS_DIR / "hl_enrichment.json"

# Default max leverage per coin on Hyperliquid (conservative estimates)
HL_MAX_LEVERAGE: dict = {
    "BTC": 50.0, "ETH": 50.0, "SOL": 20.0, "BNB": 20.0,
    "DOGE": 20.0, "XRP": 20.0, "ADA": 20.0, "AVAX": 20.0,
    "LINK": 20.0, "DOT": 20.0,
}
HL_DEFAULT_MAX_LEVERAGE = 20.0  # fallback for coins not in the map

# Core coins to analyse for liquidation clusters
CORE_COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "DOT"]

# Liquidation proximity threshold (within 5% = "near")
LIQ_NEAR_PCT = 5.0

# Funding rate threshold: above this = longs overleveraged
FUNDING_LONG_HEAVY = 0.0001  # ~0.01% per 8h funding

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
    """Source 2: CoinGecko Global market data (enhanced with DeFi + mcap change)."""
    data = _fetch_json("https://api.coingecko.com/api/v3/global")
    if not data:
        return {}
    try:
        gdata = data["data"]
        total_mcap = gdata["total_market_cap"].get("usd", 0)
        total_vol = gdata["total_volume"].get("usd", 0)
        btc_dom = gdata["market_cap_percentage"].get("btc", 0)

        # Enhanced: mcap change and DeFi dominance
        mcap_change_24h_pct = gdata.get("market_cap_change_percentage_24h_usd", None)
        defi_mcap = gdata.get("defi_market_cap", 0)
        defi_mcap_pct = None
        if total_mcap and total_mcap > 0:
            try:
                defi_mcap_pct = round(float(defi_mcap) / float(total_mcap) * 100, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        result = {
            "total_mcap_usd": float(total_mcap),
            "total_vol_24h_usd": float(total_vol),
            "btc_dominance": round(float(btc_dom), 2),
            "total_mcap_trillion": round(float(total_mcap) / 1e12, 3),
            "total_vol_24h_billion": round(float(total_vol) / 1e9, 1),
        }
        if mcap_change_24h_pct is not None:
            result["mcap_change_24h_pct"] = round(float(mcap_change_24h_pct), 4)
        if defi_mcap_pct is not None:
            result["defi_mcap_pct"] = defi_mcap_pct
        return result
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


def compute_liquidation_clusters() -> dict:
    """Source 6: Derive liquidation cluster levels from HL enrichment data.

    For each core coin:
      - Long liq zone  = price × (1 − 1/max_leverage)
      - Short liq zone = price × (1 + 1/max_leverage)
    Flags 'liquidation_magnet' if one zone is closer AND OI is non-trivial.
    """
    if not HL_ENRICHMENT_FILE.exists():
        logger.warning("hl_enrichment.json not found — skipping liquidation clusters")
        return {}

    try:
        hl_data = json.loads(HL_ENRICHMENT_FILE.read_text())
        coins_data = hl_data.get("coins", {})
    except Exception as e:
        logger.warning(f"HL enrichment read error: {e}")
        return {}

    clusters = []

    for coin in CORE_COINS:
        coin_info = coins_data.get(coin)
        if not coin_info:
            continue

        price = float(coin_info.get("mark_px") or coin_info.get("mid_px") or 0)
        funding = float(coin_info.get("funding_rate") or 0)
        oi_usd = float(coin_info.get("oi_usd") or 0)

        if price <= 0:
            continue

        max_lev = HL_MAX_LEVERAGE.get(coin, HL_DEFAULT_MAX_LEVERAGE)

        long_liq_price = price * (1.0 - 1.0 / max_lev)
        short_liq_price = price * (1.0 + 1.0 / max_lev)

        dist_long_pct = ((price - long_liq_price) / price) * 100.0
        dist_short_pct = ((short_liq_price - price) / price) * 100.0

        # Determine magnet direction
        # Longs are overleveraged when funding is very positive (longs paying shorts)
        # Shorts are overleveraged when funding is very negative
        if funding >= FUNDING_LONG_HEAVY and oi_usd > 0:
            magnet = "long"
        elif funding <= -FUNDING_LONG_HEAVY and oi_usd > 0:
            magnet = "short"
        else:
            # Default: whichever zone is closer gets the magnet label
            if dist_long_pct <= dist_short_pct and oi_usd > 0:
                magnet = "long"
            elif dist_short_pct < dist_long_pct and oi_usd > 0:
                magnet = "short"
            else:
                magnet = "none"

        clusters.append({
            "coin": coin,
            "price": round(price, 6),
            "funding_rate": round(funding, 8),
            "oi_usd": round(oi_usd, 2),
            "long_liq_price": round(long_liq_price, 6),
            "short_liq_price": round(short_liq_price, 6),
            "distance_to_long_liq_pct": round(dist_long_pct, 4),
            "distance_to_short_liq_pct": round(dist_short_pct, 4),
            "liquidation_magnet": magnet,
        })

    logger.info(f"  [liq_clusters] computed {len(clusters)} coins")
    return {"liquidation_clusters": clusters}


def fetch_btc_network() -> dict:
    """Source 7: Blockchain.info BTC network health stats."""
    data = _fetch_json("https://api.blockchain.info/stats", timeout=20)
    if not data:
        return {}
    try:
        hash_rate_ths = float(data.get("hash_rate", 0))
        hash_rate_ehs = round(hash_rate_ths / 1e6, 4)

        result = {
            "btc_network": {
                "btc_hash_rate_ehs": hash_rate_ehs,
                "btc_n_tx_24h": int(data.get("n_tx", 0)),
                "btc_difficulty": float(data.get("difficulty", 0)),
                "btc_avg_block_time_min": round(float(data.get("minutes_between_blocks", 0)), 4),
            }
        }

        # Optional field — not always present
        mempool = data.get("n_tx_total_mem_pool") or data.get("mempool_size")
        if mempool is not None:
            result["btc_network"]["btc_mempool_size"] = int(mempool)

        return result
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"Blockchain.info stats parse error: {e}")
        return {}


# ── Signal computation ─────────────────────────────────────────────────────────

def compute_signals(intel: dict) -> dict:
    """Derive boolean signals from the intel snapshot."""
    fg = intel.get("fear_greed")
    dvol = intel.get("btc_dvol")
    days_fomc = intel.get("days_to_fomc", 999)
    days_exp = intel.get("days_to_options_expiry", 999)

    # Liquidation magnet: any coin within LIQ_NEAR_PCT of its closer liq zone
    liq_clusters = intel.get("liquidation_clusters", [])
    liquidation_magnet_near = False
    for c in liq_clusters:
        magnet = c.get("liquidation_magnet", "none")
        if magnet == "long":
            dist = c.get("distance_to_long_liq_pct", 999)
        elif magnet == "short":
            dist = c.get("distance_to_short_liq_pct", 999)
        else:
            dist = min(
                c.get("distance_to_long_liq_pct", 999),
                c.get("distance_to_short_liq_pct", 999),
            )
        if dist <= LIQ_NEAR_PCT:
            liquidation_magnet_near = True
            break

    # BTC network health: slow blocks = miners going offline
    btc_network = intel.get("btc_network", {})
    avg_block_time = btc_network.get("btc_avg_block_time_min")
    hash_rate_declining = bool(avg_block_time is not None and avg_block_time > 12)

    return {
        "extreme_fear": fg is not None and fg <= EXTREME_FEAR_THRESHOLD,
        "extreme_greed": fg is not None and fg >= EXTREME_GREED_THRESHOLD,
        "high_vol": dvol is not None and dvol >= HIGH_VOL_THRESHOLD,
        "low_vol": dvol is not None and dvol <= LOW_VOL_THRESHOLD,
        "options_expiry_near": days_exp < IMMINENT_DAYS,
        "fomc_near": days_fomc < IMMINENT_DAYS,
        "liquidation_magnet_near": liquidation_magnet_near,
        "hash_rate_declining": hash_rate_declining,
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
        ("liq_clusters", compute_liquidation_clusters),
        ("btc_network", fetch_btc_network),
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
        "mcap_change_24h_pct", "defi_mcap_pct",
        "btc_dvol", "options_expiry",
        "days_to_fomc", "days_to_options_expiry", "macro_event_imminent",
        "liquidation_clusters", "btc_network",
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

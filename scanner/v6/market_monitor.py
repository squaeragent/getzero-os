#!/usr/bin/env python3
"""
Tier 2: Hyperliquid All-Market Monitor

Fetches ALL listed markets from HL (150+ perps including crypto, equities,
commodities, indices). Computes basic regime from price data alone (no ENVY
credits). Writes results to Supabase market_regimes table.

Data collected per market (all FREE):
  - price (mid)
  - 24h volume
  - 24h price change %
  - open interest
  - funding rate
  - basic regime classification

Usage:
  python3 market_monitor.py           # one-shot update
  python3 market_monitor.py --loop    # continuous 5-minute cycle
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
CYCLE_SECONDS = 300  # 5 minutes
DATA_DIR = Path(__file__).parent / "data"
MARKET_REGIMES_FILE = DATA_DIR / "market_regimes.json"
KNOWN_MARKETS_FILE = DATA_DIR / "known_markets.json"

# Supabase (optional — falls back to local file)
_env_file = Path.home() / ".config" / "openclaw" / ".env"
SUPABASE_URL = os.environ.get("SUPABASE_URL", os.environ.get("NEXT_PUBLIC_SUPABASE_URL", ""))
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", "")))

if (not SUPABASE_URL or not SUPABASE_KEY) and _env_file.exists():
    try:
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "SUPABASE_URL" and not SUPABASE_URL:
                SUPABASE_URL = v
            elif k == "NEXT_PUBLIC_SUPABASE_URL" and not SUPABASE_URL:
                SUPABASE_URL = v
            elif k in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_KEY") and not SUPABASE_KEY:
                SUPABASE_KEY = v
    except Exception:
        pass

# ENVY-covered coins (Tier 1) — can get full evaluation
TIER1_COINS = {
    "AAVE", "ADA", "APT", "ARB", "AVAX", "BCH", "BNB", "BTC", "CRV",
    "DOGE", "DOT", "ENA", "ETH", "FARTCOIN", "HYPE", "INJ", "JUP",
    "LDO", "LINK", "LTC", "NEAR", "ONDO", "OP", "PAXG", "PUMP",
    "SEI", "SOL", "SUI", "TIA", "TON", "TRUMP", "TRX", "UNI", "WLD",
    "XPL", "XRP", "ZEC", "kBONK", "kPEPE", "kSHIB",
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [MARKET] {msg}", flush=True)


# ─── HL API ───────────────────────────────────────────────────────────────────

def hl_post(payload: dict, timeout: int = 15) -> dict | list:
    """POST to Hyperliquid info API."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_meta() -> list[dict]:
    """Fetch all listed perpetual markets with metadata."""
    result = hl_post({"type": "meta"})
    if isinstance(result, dict) and "universe" in result:
        return result["universe"]
    return []


def fetch_all_mids() -> dict[str, float]:
    """Fetch current mid prices for all markets."""
    result = hl_post({"type": "allMids"})
    if isinstance(result, dict):
        return {k: float(v) for k, v in result.items() if v}
    return {}


def fetch_clearinghouse_state() -> list[dict]:
    """Fetch market stats — volume, OI, funding."""
    # Use metaAndAssetCtxs for combined data
    result = hl_post({"type": "metaAndAssetCtxs"})
    if isinstance(result, list) and len(result) >= 2:
        return result[1]  # asset contexts
    return []


# ─── REGIME CLASSIFICATION ────────────────────────────────────────────────────

def classify_basic_regime(ctx: dict) -> str:
    """Classify market into basic regime from HL data alone.
    
    Returns one of: trending_up, trending_down, ranging, volatile, quiet
    """
    try:
        mark = float(ctx.get("markPx", 0))
        mid = float(ctx.get("midPx", mark))
        funding = float(ctx.get("funding", 0))
        open_interest = float(ctx.get("openInterest", 0))
        day_ntl_vlm = float(ctx.get("dayNtlVlm", 0))
        prev_day_px = float(ctx.get("prevDayPx", mid))

        if prev_day_px <= 0 or mid <= 0:
            return "unknown"

        # Price change %
        change_pct = ((mid - prev_day_px) / prev_day_px) * 100

        # Volume relative check (use raw volume — no historical average available
        # from this endpoint, so use OI as proxy for "normal" activity)
        vol_oi_ratio = day_ntl_vlm / open_interest if open_interest > 0 else 0

        # Classification
        abs_change = abs(change_pct)

        if abs_change < 0.5 and vol_oi_ratio < 0.3:
            return "quiet"
        elif abs_change > 5.0 or vol_oi_ratio > 2.0:
            return "volatile"
        elif change_pct > 1.5:
            return "trending_up"
        elif change_pct < -1.5:
            return "trending_down"
        else:
            return "ranging"

    except (ValueError, TypeError, ZeroDivisionError):
        return "unknown"


# ─── DATA PROCESSING ─────────────────────────────────────────────────────────

def process_markets() -> list[dict]:
    """Fetch all HL markets and compute basic regimes. Returns market list."""
    log("Fetching HL meta + asset contexts...")
    
    try:
        result = hl_post({"type": "metaAndAssetCtxs"})
    except Exception as e:
        log(f"  ERROR fetching HL data: {e}")
        return []

    if not isinstance(result, list) or len(result) < 2:
        log("  ERROR: unexpected HL response format")
        return []

    universe = result[0].get("universe", [])
    contexts = result[1]

    if len(universe) != len(contexts):
        log(f"  WARN: universe ({len(universe)}) != contexts ({len(contexts)})")

    markets = []
    now = datetime.now(timezone.utc).isoformat()

    for i, meta in enumerate(universe):
        if i >= len(contexts):
            break

        ctx = contexts[i]
        name = meta.get("name", "???")

        try:
            mid_px = float(ctx.get("midPx", 0))
            mark_px = float(ctx.get("markPx", 0))
            prev_day_px = float(ctx.get("prevDayPx", 0))
            day_ntl_vlm = float(ctx.get("dayNtlVlm", 0))
            open_interest = float(ctx.get("openInterest", 0))
            funding = float(ctx.get("funding", 0))

            change_pct = 0.0
            if prev_day_px > 0:
                change_pct = round(((mid_px - prev_day_px) / prev_day_px) * 100, 2)

            regime = classify_basic_regime(ctx)
            tier = 1 if name in TIER1_COINS else 2

            markets.append({
                "symbol": name,
                "price": round(mid_px, 6),
                "mark_price": round(mark_px, 6),
                "change_pct": change_pct,
                "volume_24h": round(day_ntl_vlm, 2),
                "open_interest": round(open_interest, 2),
                "funding_rate": round(funding, 6),
                "basic_regime": regime,
                "tier": tier,
                "max_leverage": meta.get("maxLeverage", 0),
                "sz_decimals": meta.get("szDecimals", 0),
                "updated_at": now,
            })

        except (ValueError, TypeError) as e:
            log(f"  WARN: error processing {name}: {e}")
            continue

    log(f"  Processed {len(markets)} markets")

    # Regime summary
    regimes = {}
    for m in markets:
        r = m["basic_regime"]
        regimes[r] = regimes.get(r, 0) + 1
    regime_str = " | ".join(f"{r}: {c}" for r, c in sorted(regimes.items(), key=lambda x: -x[1]))
    log(f"  Regimes: {regime_str}")
    log(f"  Tier 1 (ENVY): {sum(1 for m in markets if m['tier'] == 1)} | Tier 2 (HL only): {sum(1 for m in markets if m['tier'] == 2)}")

    return markets


# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def save_local(markets: list[dict]):
    """Save market regimes to local JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(markets),
        "tier1_count": sum(1 for m in markets if m["tier"] == 1),
        "tier2_count": sum(1 for m in markets if m["tier"] == 2),
        "markets": {m["symbol"]: m for m in markets},
    }
    tmp = MARKET_REGIMES_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(MARKET_REGIMES_FILE)


def save_supabase(markets: list[dict]):
    """Upsert market regimes to Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    url = f"{SUPABASE_URL}/rest/v1/market_regimes"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    # Batch upsert (Supabase supports bulk)
    rows = []
    for m in markets:
        rows.append({
            "symbol": m["symbol"],
            "price": m["price"],
            "mark_price": m["mark_price"],
            "change_pct": m["change_pct"],
            "volume_24h": m["volume_24h"],
            "open_interest": m["open_interest"],
            "funding_rate": m["funding_rate"],
            "basic_regime": m["basic_regime"],
            "tier": m["tier"],
            "max_leverage": m["max_leverage"],
            "updated_at": m["updated_at"],
        })

    try:
        data = json.dumps(rows).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                log(f"  Supabase: upserted {len(rows)} market regimes")
            else:
                log(f"  Supabase: HTTP {resp.status}")
    except Exception as e:
        log(f"  Supabase: upsert failed — {e}")


# ─── AUTO-DISCOVERY (TIER 3) ─────────────────────────────────────────────────

def check_new_listings(markets: list[dict]) -> list[str]:
    """Compare current markets against known list. Return new symbols."""
    known_file = KNOWN_MARKETS_FILE
    known = set()
    if known_file.exists():
        try:
            known = set(json.loads(known_file.read_text()))
        except Exception:
            pass

    current = {m["symbol"] for m in markets}
    new_symbols = sorted(current - known)

    # Update known list
    if current:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = known_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(sorted(current), f)
        tmp.replace(known_file)

    return new_symbols


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_cycle() -> dict:
    """Run one monitoring cycle. Returns summary."""
    markets = process_markets()
    if not markets:
        return {"error": "no data"}

    save_local(markets)
    save_supabase(markets)

    # Tier 3: Auto-discovery
    new_listings = check_new_listings(markets)
    if new_listings:
        log(f"  🆕 NEW LISTINGS DETECTED: {', '.join(new_listings)}")
        # Future: alert Igor via message tool

    # Top movers
    sorted_by_change = sorted(markets, key=lambda m: abs(m["change_pct"]), reverse=True)
    top5 = sorted_by_change[:5]
    log("  Top movers:")
    for m in top5:
        arrow = "▲" if m["change_pct"] > 0 else "▼"
        log(f"    {m['symbol']:10s} {arrow} {abs(m['change_pct']):6.2f}%  regime={m['basic_regime']}  vol=${m['volume_24h']:,.0f}")

    return {
        "total_markets": len(markets),
        "tier1": sum(1 for m in markets if m["tier"] == 1),
        "tier2": sum(1 for m in markets if m["tier"] == 2),
        "new_listings": new_listings,
    }


def main():
    log("=== Hyperliquid Market Monitor starting ===")
    loop = "--loop" in sys.argv

    summary = run_cycle()
    log(f"  Total: {summary.get('total_markets', 0)} markets ({summary.get('tier1', 0)} Tier 1, {summary.get('tier2', 0)} Tier 2)")

    if loop:
        log(f"  Looping every {CYCLE_SECONDS}s...")
        while True:
            time.sleep(CYCLE_SECONDS)
            try:
                run_cycle()
            except Exception as e:
                log(f"  ERROR in cycle: {e}")
                time.sleep(30)


if __name__ == "__main__":
    main()

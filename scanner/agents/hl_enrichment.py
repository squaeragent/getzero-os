#!/usr/bin/env python3
"""
ZERO OS — HL Enrichment Agent
Single-call replacement for multiple HL API calls.

Fetches ALL 229 coins in one metaAndAssetCtxs request and extracts:
  - premium        : mark-oracle divergence (>0 = longs overleveraged)
  - day_volume_usd : from dayNtlVlm
  - day_volume_base: from dayBaseVlm
  - impact_spread  : (impactPxs[1] - impactPxs[0]) / midPx — market depth indicator
  - prev_day_px    : for daily change calculation
  - oi_usd         : openInterest * midPx
  - funding_rate   : from funding field
  - daily_change_pct: (midPx - prevDayPx) / prevDayPx * 100

Outputs:
  scanner/bus/hl_enrichment.json  — per-coin enrichment data + market summary

Usage:
  python3 scanner/agents/hl_enrichment.py           # single run
  python3 scanner/agents/hl_enrichment.py --loop    # continuous 120s cycle
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from scanner.utils import (
    save_json, make_logger, update_heartbeat,
    BUS_DIR,
)

# ── PATHS ──
ENRICHMENT_FILE = BUS_DIR / "hl_enrichment.json"

# ── CONFIG ──
HL_INFO_URL   = "https://api.hyperliquid.xyz/info"
CYCLE_SECONDS = 120   # 2-min cycle, matches perception.py
RETRY_BACKOFF = 5     # seconds to wait after 429


# ==========================================================================
# LOGGING
# ==========================================================================

log = make_logger("HL_ENRICHMENT")


# ==========================================================================
# HEARTBEAT
# ==========================================================================

def write_heartbeat():
    update_heartbeat("hl_enrichment")


# ==========================================================================
# HYPERLIQUID API
# ==========================================================================

def fetch_meta_and_asset_ctxs():
    """
    Single call to metaAndAssetCtxs — returns ALL coins in one request.
    Returns (meta_universe_list, asset_ctxs_list).
    Raises urllib.error.HTTPError on 429 (rate limit).
    """
    payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
    req = urllib.request.Request(
        HL_INFO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = json.loads(resp.read().decode())

    meta_dict  = raw[0]
    asset_ctxs = raw[1]
    universe   = meta_dict.get("universe", [])
    return universe, asset_ctxs


# ==========================================================================
# ENRICHMENT LOGIC
# ==========================================================================

def safe_float(val, default=0.0):
    """Convert to float safely, returning default on None/empty/invalid."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def compute_enrichment(universe, asset_ctxs):
    """
    Extract per-coin enrichment data from a single metaAndAssetCtxs response.
    Returns (coins_dict, summary_dict).
    """
    coins = {}
    ts_iso = datetime.now(timezone.utc).isoformat()

    for i, ctx in enumerate(asset_ctxs):
        if i >= len(universe):
            break

        coin = universe[i].get("name", f"UNKNOWN_{i}")

        mid_px       = safe_float(ctx.get("midPx"))
        mark_px      = safe_float(ctx.get("markPx"))
        oracle_px    = safe_float(ctx.get("oraclePx"))
        prev_day_px  = safe_float(ctx.get("prevDayPx"))
        day_ntl_vlm  = safe_float(ctx.get("dayNtlVlm"))
        day_base_vlm = safe_float(ctx.get("dayBaseVlm"))
        open_interest= safe_float(ctx.get("openInterest"))
        funding_rate = safe_float(ctx.get("funding"))
        premium_raw  = ctx.get("premium")
        impact_pxs   = ctx.get("impactPxs")  # [bid_impact, ask_impact]

        # Premium: use API field if present, else compute from mark-oracle
        if premium_raw is not None and premium_raw != "":
            premium = safe_float(premium_raw)
        elif oracle_px > 0:
            premium = (mark_px - oracle_px) / oracle_px
        else:
            premium = 0.0

        # Impact spread: (ask_impact - bid_impact) / midPx
        impact_spread = 0.0
        if impact_pxs and len(impact_pxs) >= 2 and mid_px > 0:
            bid_impact = safe_float(impact_pxs[0])
            ask_impact = safe_float(impact_pxs[1])
            if bid_impact > 0 and ask_impact > 0:
                impact_spread = (ask_impact - bid_impact) / mid_px

        # OI in USD
        ref_px = mid_px if mid_px > 0 else mark_px
        oi_usd = open_interest * ref_px if ref_px > 0 else 0.0

        # Daily change %
        daily_change_pct = 0.0
        if prev_day_px > 0 and ref_px > 0:
            daily_change_pct = (ref_px - prev_day_px) / prev_day_px * 100.0

        coins[coin] = {
            "premium":           round(premium, 8),
            "day_volume_usd":    round(day_ntl_vlm, 2),
            "day_volume_base":   round(day_base_vlm, 4),
            "impact_spread":     round(impact_spread, 8),
            "prev_day_px":       round(prev_day_px, 8),
            "oi_usd":            round(oi_usd, 2),
            "funding_rate":      round(funding_rate, 10),
            "daily_change_pct":  round(daily_change_pct, 4),
            # Raw prices for downstream use
            "mid_px":            round(mid_px, 8),
            "mark_px":           round(mark_px, 8),
            "oracle_px":         round(oracle_px, 8),
        }

    # ── Summary ──
    premiums   = [c["premium"] for c in coins.values()]
    volumes    = [c["day_volume_usd"] for c in coins.values()]
    oi_values  = [c["oi_usd"] for c in coins.values()]

    n = len(premiums) or 1
    avg_premium        = sum(premiums) / n
    coins_pos_premium  = sum(1 for p in premiums if p > 0)
    coins_neg_premium  = sum(1 for p in premiums if p < 0)
    total_oi_usd       = sum(oi_values)
    total_volume_usd   = sum(volumes)

    # Premium skew: (positive - negative) / total — positive = market overleveraged long
    skew_denom         = coins_pos_premium + coins_neg_premium
    premium_skew       = (coins_pos_premium - coins_neg_premium) / skew_denom if skew_denom else 0.0

    summary = {
        "total_oi_usd":            round(total_oi_usd, 2),
        "total_volume_usd":        round(total_volume_usd, 2),
        "avg_premium":             round(avg_premium, 8),
        "coins_positive_premium":  coins_pos_premium,
        "coins_negative_premium":  coins_neg_premium,
        "premium_skew":            round(premium_skew, 4),
        "coins_count":             len(coins),
    }

    return coins, summary


# ==========================================================================
# RUN CYCLE
# ==========================================================================

def run_cycle():
    cycle_start = time.time()
    ts_iso      = datetime.now(timezone.utc).isoformat()

    log(f"Fetching metaAndAssetCtxs (single call, all coins)...")

    # Attempt with one retry on failure
    universe = asset_ctxs = None
    for attempt in range(2):
        try:
            universe, asset_ctxs = fetch_meta_and_asset_ctxs()
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"  Rate limited (429) — backing off {RETRY_BACKOFF}s")
                time.sleep(RETRY_BACKOFF)
                if attempt == 1:
                    log("  Rate limit retry failed — skipping cycle")
                    write_heartbeat()
                    return
            else:
                log(f"  HTTP error {e.code}: {e} — attempt {attempt+1}/2")
                if attempt == 1:
                    log("  All retries exhausted — skipping cycle")
                    write_heartbeat()
                    return
                time.sleep(2)
        except Exception as e:
            log(f"  Fetch error: {e} — attempt {attempt+1}/2")
            if attempt == 1:
                log("  All retries exhausted — skipping cycle")
                write_heartbeat()
                return
            time.sleep(2)

    coins, summary = compute_enrichment(universe, asset_ctxs)

    output = {
        "timestamp": ts_iso,
        "coins":     coins,
        "summary":   summary,
    }

    save_json(ENRICHMENT_FILE, output)
    write_heartbeat()

    elapsed_ms = int((time.time() - cycle_start) * 1000)
    log(
        f"Written hl_enrichment.json — {len(coins)} coins | "
        f"total_oi=${summary['total_oi_usd']/1e9:.1f}B | "
        f"total_vol=${summary['total_volume_usd']/1e9:.1f}B | "
        f"avg_premium={summary['avg_premium']:.6f} | "
        f"skew={summary['premium_skew']:.3f} | "
        f"{elapsed_ms}ms"
    )


# ==========================================================================
# ENTRY POINT
# ==========================================================================

def main():
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        log(f"HL Enrichment Agent starting — loop every {CYCLE_SECONDS}s")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"[error] Cycle failed: {e}")
                write_heartbeat()
            time.sleep(CYCLE_SECONDS)
    else:
        run_cycle()


if __name__ == "__main__":
    main()

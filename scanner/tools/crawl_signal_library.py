#!/usr/bin/env python3
"""
ENVY Signal Library Crawler
Systematically pulls ALL signal packs for all coins to build a complete library.
Run once, then maintain with pack_refresher.py periodic updates.

Usage:
  python3 scanner/tools/crawl_signal_library.py           # crawl all
  python3 scanner/tools/crawl_signal_library.py --coin BTC # single coin
  python3 scanner/tools/crawl_signal_library.py --stats    # show library stats
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

SCANNER_DIR = Path(__file__).parent.parent
CACHE_DIR = SCANNER_DIR / "data" / "signals_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Load API key
def load_api_key():
    env_path = os.path.expanduser("~/.config/openclaw/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("ENVY_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ENVY_API_KEY", "")

API_KEY = load_api_key()
BASE_URL = "https://gate.getzero.dev/api/claw"
PACK_TYPES = ["common", "rare", "trump"]
RATE_LIMIT = 0.4  # seconds between API calls


def fetch_coins():
    """Get list of all available coins."""
    url = f"{BASE_URL}/coins"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return [c.get("symbol", c.get("name", "")) for c in data.get("coins", [])]


def fetch_pack(coin, pack_type):
    """Fetch one batch of signals for a coin/type."""
    url = f"{BASE_URL}/paid/signals/pack?coins={coin}&pack_type={pack_type}"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        signals = data.get("signals", [])
        return signals
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"  Rate limited on {coin}/{pack_type}, waiting 5s...")
            time.sleep(5)
            return fetch_pack(coin, pack_type)  # retry once
        print(f"  HTTP {e.code} for {coin}/{pack_type}")
        return []
    except Exception as e:
        print(f"  Error for {coin}/{pack_type}: {e}")
        return []


def load_cache(coin):
    """Load existing cached signals for a coin."""
    path = CACHE_DIR / f"{coin}.json"
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else data.get("signals", [])
        except Exception:
            return []
    return []


def save_cache(coin, signals):
    """Save signals to cache file."""
    path = CACHE_DIR / f"{coin}.json"
    with open(path, "w") as f:
        json.dump(signals, f)
    os.chmod(path, 0o600)


def crawl_coin(coin, max_rounds=5):
    """Crawl all signal packs for a single coin."""
    existing = load_cache(coin)
    existing_names = {s.get("name", "") for s in existing}
    new_total = 0

    for pack_type in PACK_TYPES:
        consecutive_empty = 0
        for round_num in range(max_rounds):
            signals = fetch_pack(coin, pack_type)
            new_signals = [s for s in signals if s.get("name", "") not in existing_names]
            
            if new_signals:
                for s in new_signals:
                    s["_pack_type"] = pack_type
                    s["_crawled_at"] = datetime.now(timezone.utc).isoformat()
                    existing.append(s)
                    existing_names.add(s.get("name", ""))
                new_total += len(new_signals)
                consecutive_empty = 0
            else:
                consecutive_empty += 1

            time.sleep(RATE_LIMIT)
            
            # Stop if 2 consecutive rounds return no new signals
            if consecutive_empty >= 2:
                break

    if new_total > 0:
        save_cache(coin, existing)
    
    return len(existing), new_total


def show_stats():
    """Show library statistics."""
    total = 0
    tier1 = 0
    tier2 = 0
    coins_covered = 0
    
    for fn in sorted(CACHE_DIR.iterdir()):
        if fn.suffix != ".json":
            continue
        coin = fn.stem
        signals = load_cache(coin)
        if not signals:
            continue
        coins_covered += 1
        total += len(signals)
        for s in signals:
            sharpe = s.get("sharpe", 0)
            wr = s.get("win_rate", 0)
            tc = s.get("trade_count", 0)
            if sharpe >= 2.0 and wr >= 60 and tc >= 10:
                tier1 += 1
            elif sharpe >= 1.5 and wr >= 55 and tc >= 5:
                tier2 += 1
        
        t1_coin = sum(1 for s in signals if s.get("sharpe",0)>=2.0 and s.get("win_rate",0)>=60 and s.get("trade_count",0)>=10)
        t2_coin = sum(1 for s in signals if s.get("sharpe",0)>=1.5 and s.get("win_rate",0)>=55 and s.get("trade_count",0)>=5) - t1_coin
        print(f"  {coin:6s}: {len(signals):4d} signals ({t1_coin} T1, {t2_coin} T2)")
    
    print(f"\n{'='*50}")
    print(f"Total: {total} signals across {coins_covered} coins")
    print(f"Tier 1 (Sharpe≥2.0, WR≥60%, N≥10): {tier1}")
    print(f"Tier 2 (Sharpe≥1.5, WR≥55%, N≥5):  {tier2}")
    print(f"Tier 3 (archive): {total - tier1 - tier2}")


def main():
    if "--stats" in sys.argv:
        show_stats()
        return

    target_coin = None
    if "--coin" in sys.argv:
        idx = sys.argv.index("--coin")
        if idx + 1 < len(sys.argv):
            target_coin = sys.argv[idx + 1].upper()

    if not API_KEY:
        print("ERROR: ENVY_API_KEY not found")
        sys.exit(1)

    print(f"ENVY Signal Library Crawler")
    print(f"{'='*50}")

    if target_coin:
        coins = [target_coin]
    else:
        print("Fetching coin list...")
        coins = fetch_coins()
        print(f"Found {len(coins)} coins")

    total_new = 0
    for i, coin in enumerate(coins):
        existing_count = len(load_cache(coin))
        print(f"[{i+1}/{len(coins)}] {coin} (existing: {existing_count})...", end=" ", flush=True)
        total, new = crawl_coin(coin)
        print(f"→ {total} total ({new} new)")
        total_new += new

    print(f"\n{'='*50}")
    print(f"Crawl complete. {total_new} new signals added.")
    print()
    show_stats()


if __name__ == "__main__":
    main()

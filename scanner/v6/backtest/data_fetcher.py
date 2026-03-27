"""Historical data downloader for backtesting — fetches candles + funding from Hyperliquid."""

import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"

TOP_COINS = [
    "BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK", "ADA", "DOT", "MATIC",
    "UNI", "AAVE", "MKR", "SNX", "CRV", "LDO", "ARB", "OP", "TIA", "SEI",
    "JUP", "WLD", "PEPE", "WIF", "BONK", "NEAR", "ATOM", "FTM", "APT", "SUI",
]

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


class HistoricalDataFetcher:
    """Download and cache historical candle + funding data from HL."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── candles ───────────────────────────────────────────────────

    def fetch_candles(
        self, coin: str, interval: str = "1h", days: int = 90, *, force: bool = False
    ) -> list[dict]:
        cache_path = self.cache_dir / f"{coin}_{interval}_{days}d_candles.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 86_400 * 1000

        all_candles: list[dict] = []
        cursor = start_ms

        while cursor < now_ms:
            body = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": now_ms,
                },
            }
            resp = requests.post(HL_INFO_URL, json=body, timeout=15)
            resp.raise_for_status()
            batch = resp.json()

            if not batch:
                break

            all_candles.extend(batch)

            # Move cursor past last candle
            last_t = batch[-1].get("t", 0)
            if isinstance(last_t, str):
                last_t = int(last_t)
            if last_t <= cursor:
                break
            cursor = last_t + 1

            time.sleep(0.2)  # respect rate limits

        # Deduplicate by timestamp
        seen: set[int] = set()
        deduped: list[dict] = []
        for c in all_candles:
            t = int(c["t"]) if isinstance(c["t"], str) else c["t"]
            if t not in seen:
                seen.add(t)
                deduped.append(c)
        deduped.sort(key=lambda c: int(c["t"]) if isinstance(c["t"], str) else c["t"])

        cache_path.write_text(json.dumps(deduped))
        return deduped

    # ── funding ──────────────────────────────────────────────────

    def fetch_funding(self, coin: str, days: int = 90, *, force: bool = False) -> list[dict]:
        cache_path = self.cache_dir / f"{coin}_{days}d_funding.json"
        if cache_path.exists() and not force:
            return json.loads(cache_path.read_text())

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 86_400 * 1000

        all_funding: list[dict] = []
        cursor = start_ms

        while cursor < now_ms:
            body = {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor,
            }
            resp = requests.post(HL_INFO_URL, json=body, timeout=15)
            resp.raise_for_status()
            batch = resp.json()

            if not batch:
                break

            all_funding.extend(batch)

            last_t = batch[-1].get("time", 0)
            if isinstance(last_t, str):
                last_t = int(last_t)
            if last_t <= cursor:
                break
            cursor = last_t + 1

            time.sleep(0.2)

        # Deduplicate
        seen: set[int] = set()
        deduped: list[dict] = []
        for f in all_funding:
            t = int(f["time"]) if isinstance(f["time"], str) else f["time"]
            if t not in seen:
                seen.add(t)
                deduped.append(f)
        deduped.sort(key=lambda f: int(f["time"]) if isinstance(f["time"], str) else f["time"])

        cache_path.write_text(json.dumps(deduped))
        return deduped

    # ── batch fetch ──────────────────────────────────────────────

    def fetch_all(
        self,
        coins: list[str] | None = None,
        interval: str = "1h",
        days: int = 90,
        *,
        force: bool = False,
    ):
        coins = coins or TOP_COINS
        total = len(coins)
        for i, coin in enumerate(coins, 1):
            print(f"  [{i}/{total}] Fetching {coin}...")
            try:
                candles = self.fetch_candles(coin, interval, days, force=force)
                funding = self.fetch_funding(coin, days, force=force)
                print(f"           {len(candles)} candles, {len(funding)} funding records")
            except Exception as exc:
                print(f"           ERROR: {exc}")
            time.sleep(0.2)
        print("  Done.")

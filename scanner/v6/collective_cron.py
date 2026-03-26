"""
collective_cron.py — Runs collective.compute() every 5 minutes.

Usage:
    python -m scanner.v6.collective_cron          # foreground
    python -m scanner.v6.collective_cron &         # background
"""

import time
from datetime import datetime, timezone

INTERVAL = 300  # 5 minutes


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [collective-cron] [{ts}] {msg}", flush=True)


def main():
    from scanner.v6.collective import compute

    _log("started — running every 5 minutes")

    while True:
        try:
            result = compute()
            ac = result.get("agent_count", 0)
            nc = len(result.get("consensus", {}))
            _log(f"tick complete — {ac} agents, {nc} coins with consensus")
        except Exception as e:
            _log(f"error: {e}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Refresh historical candle data from Hyperliquid. Run weekly via cron."""
import subprocess
import sys

from scanner.utils import SCANNER_DIR

FETCH_SCRIPT = SCANNER_DIR / "fetch_history.py"

if not FETCH_SCRIPT.exists():
    print(f"fetch_history.py not found at {FETCH_SCRIPT}")
    sys.exit(1)

# Run the existing fetch_history.py
result = subprocess.run(
    ["/opt/homebrew/bin/python3", str(FETCH_SCRIPT)],
    cwd=str(SCANNER_DIR),
    capture_output=True, text=True, timeout=1800  # 30 min max
)

print(result.stdout[-500:] if result.stdout else "no output")
if result.returncode != 0:
    print(f"FAILED: {result.stderr[-500:]}")
    sys.exit(1)

print("History refresh complete")

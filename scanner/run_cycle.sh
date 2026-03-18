#!/bin/bash
# ZERO OS — Scanner + Executor cycle
# Runs scanner first, then executor in LIVE mode
set -euo pipefail

PYTHON=/opt/homebrew/bin/python3
DIR="$(cd "$(dirname "$0")" && pwd)"

# Run scanner (generates signals)
$PYTHON "$DIR/signal_scanner.py" 2>&1

# Run executor in LIVE mode (trades on signals)
$PYTHON "$DIR/hyperliquid_executor.py" --live 2>&1

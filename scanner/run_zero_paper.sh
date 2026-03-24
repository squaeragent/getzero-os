#!/bin/bash
# ZERO OS — Paper Trading Agent
# Runs the ZERO agent in paper mode (no real orders)
# Uses ZERO's HL credentials, isolated state from live agent
set -euo pipefail

PYTHON=/opt/homebrew/bin/python3
DIR="$(cd "$(dirname "$0")" && pwd)"

# Source credentials
source ~/.config/openclaw/.env

# ZERO agent credentials (override the live agent's)
export HYPERLIQUID_MAIN_ADDRESS="$ZERO_HL_MAIN_ADDRESS"
export HYPERLIQUID_ACCOUNT_ADDRESS="$ZERO_HL_API_WALLET"
export HYPERLIQUID_SECRET_KEY="$ZERO_HL_SECRET_KEY"

# Paper mode — no real orders, isolated state
export PAPER_MODE=1

echo "[$(date -u +%H:%M:%S\ UTC)] Starting ZERO paper agent..."
echo "  Main: $HYPERLIQUID_MAIN_ADDRESS"
echo "  API:  $HYPERLIQUID_ACCOUNT_ADDRESS"
echo "  Mode: PAPER"

exec $PYTHON "$DIR/run_agents.py" --once 2>&1

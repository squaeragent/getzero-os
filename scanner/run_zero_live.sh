#!/bin/bash
# ZERO OS — Live Trading Agent (ZERO account)
# Runs alongside Gleb's agent — separate credentials, shared signal pipeline
set -euo pipefail

PYTHON=/opt/homebrew/bin/python3
DIR="$(cd "$(dirname "$0")" && pwd)"

# Source credentials
source ~/.config/openclaw/.env

# ZERO agent credentials (override the live agent's)
export HYPERLIQUID_MAIN_ADDRESS="$ZERO_HL_MAIN_ADDRESS"
export HYPERLIQUID_ACCOUNT_ADDRESS="$ZERO_HL_API_WALLET"
export HYPERLIQUID_SECRET_KEY="$ZERO_HL_SECRET_KEY"

# Live mode
export PAPER_MODE=0

echo "[$(date -u +%H:%M:%S\ UTC)] Starting ZERO live agent..."
echo "  Main: $HYPERLIQUID_MAIN_ADDRESS"
echo "  API:  $HYPERLIQUID_ACCOUNT_ADDRESS"
echo "  Mode: LIVE"

exec $PYTHON "$DIR/run_agents.py" --once 2>&1

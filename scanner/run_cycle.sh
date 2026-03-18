#!/bin/bash
# ZERO OS — Agent Supervisor launcher
# Replaces old scanner+executor cycle with 5-agent system
set -euo pipefail

PYTHON=/opt/homebrew/bin/python3
DIR="$(cd "$(dirname "$0")" && pwd)"

# Run the supervisor (it manages all 5 agents internally)
exec $PYTHON "$DIR/run_agents.py" --once 2>&1

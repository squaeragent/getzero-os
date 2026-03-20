#!/usr/bin/env bash
# Weekly Self-Audit Cron Script
# Runs every Sunday at midnight UTC (07:00 Monday BKK time)
# Cron entry: 0 0 * * 0 /Users/forge/getzero-os/scanner/tools/cron_weekly_audit.sh
set -euo pipefail

# ── Source env ────────────────────────────────────────────────────────────────
ENV_FILE="$HOME/.config/openclaw/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# ── Paths ─────────────────────────────────────────────────────────────────────
SCANNER_DIR="$HOME/getzero-os/scanner"
AUDIT_SCRIPT="$SCANNER_DIR/tools/weekly_audit.py"
AUDIT_DIR="$SCANNER_DIR/data/audit"
DATE=$(date -u +"%Y-%m-%d")
OUTPUT="$AUDIT_DIR/$DATE.json"
LOG_FILE="$AUDIT_DIR/cron.log"

mkdir -p "$AUDIT_DIR"

echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Starting weekly audit..." >> "$LOG_FILE"

# ── Run audit script ─────────────────────────────────────────────────────────
if python3 "$AUDIT_SCRIPT" --output "$OUTPUT" >> "$LOG_FILE" 2>&1; then
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Audit completed: $OUTPUT" >> "$LOG_FILE"
else
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Audit script failed (exit $?)" >> "$LOG_FILE"
fi

# ── Trigger OpenClaw agent for judgment analysis ──────────────────────────────
if command -v openclaw &>/dev/null; then
    openclaw system event \
        --text "Weekly audit data ready: scanner/data/audit/$DATE.json — run Part 7 judgment analysis and post to Telegram" \
        --mode now \
        >> "$LOG_FILE" 2>&1 || echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] openclaw event failed" >> "$LOG_FILE"
else
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] WARNING: openclaw not found in PATH" >> "$LOG_FILE"
fi

echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Done." >> "$LOG_FILE"

#!/usr/bin/env bash
# heartbeat_bridge.sh — Sync local agent state to Supabase every 5 minutes
# Reads bus/heartbeat.json, bus/risk.json, bus/positions.json + HL on-chain data
# Updates the agents table so getzero.dev/app shows live status
set -uo pipefail

AGENT_ID="4802c6f8-f862-42f1-b248-45679e1517e7"
SCANNER_DIR="$HOME/getzero-os/scanner/v6"
ENV_FILE="$HOME/.config/openclaw/.env"
HL_WALLET="0xA5F25E3Bbf7a10EB61EEfA471B61E1dfa5777884"
SUPABASE_URL="https://fzzotmxxrcnmrqtmsesi.supabase.co"
LOOP_INTERVAL=300  # 5 minutes

# Load secrets
if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
else
  echo "ERROR: $ENV_FILE not found"
  exit 1
fi

if [[ -z "${SUPABASE_SERVICE_KEY:-}" ]]; then
  echo "ERROR: SUPABASE_SERVICE_KEY not set"
  exit 1
fi

sync_heartbeat() {
  local now
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Read local heartbeat
  local hb_file="$SCANNER_DIR/bus/heartbeat.json"
  if [[ ! -f "$hb_file" ]]; then
    echo "[$now] heartbeat.json missing — agent likely not running"
    return 1
  fi

  # Check if evaluator beat is fresh (within 5 min)
  local eval_beat
  eval_beat=$(python3 -c "
import json
from datetime import datetime, timezone, timedelta
hb = json.load(open('$hb_file'))
beat = datetime.fromisoformat(hb.get('evaluator', '2000-01-01T00:00:00+00:00'))
age = (datetime.now(timezone.utc) - beat).total_seconds()
print(f'{age:.0f}')
" 2>/dev/null)

  if [[ -z "$eval_beat" ]] || (( eval_beat > 600 )); then
    echo "[$now] evaluator stale (${eval_beat}s) — marking stopped"
    curl -s -X PATCH "$SUPABASE_URL/rest/v1/agents?id=eq.$AGENT_ID" \
      -H "apikey: $SUPABASE_SERVICE_KEY" \
      -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" \
      -H "Content-Type: application/json" \
      -H "Prefer: return=minimal" \
      -d "{\"status\": \"stopped\", \"stopped_at\": \"$now\", \"last_heartbeat\": \"$now\"}" \
      -o /dev/null -w "HTTP %{http_code}\n"
    return 0
  fi

  # Get on-chain data
  local hl_data
  hl_data=$(curl -s "$HL_API" -X POST -H "Content-Type: application/json" \
    -d "{\"type\":\"clearinghouseState\",\"user\":\"$HL_WALLET\"}" 2>/dev/null)

  local equity positions
  equity=$(echo "$hl_data" | python3 -c "import json,sys; d=json.load(sys.stdin); print(float(d['marginSummary']['accountValue']))" 2>/dev/null || echo "0")
  positions=$(echo "$hl_data" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len([p for p in d.get('assetPositions',[]) if float(p.get('position',{}).get('szi',0))!=0]))" 2>/dev/null || echo "0")

  # Get trade count
  local trades
  trades=$(curl -s "$HL_API" -X POST -H "Content-Type: application/json" \
    -d "{\"type\":\"userFills\",\"user\":\"$HL_WALLET\"}" 2>/dev/null | \
    python3 -c "import json,sys; fills=json.load(sys.stdin); print(len([f for f in fills if not f['coin'].startswith('@') and not f['coin'][0].isdigit() and float(f.get('closedPnl','0'))!=0]))" 2>/dev/null || echo "0")

  # Read risk state
  local halted immune_status
  halted=$(python3 -c "import json; d=json.load(open('$SCANNER_DIR/bus/risk.json')); print('true' if d.get('halted') else 'false')" 2>/dev/null || echo "false")
  
  if [[ "$halted" == "true" ]]; then
    immune_status="critical"
  else
    immune_status="healthy"
  fi

  # Calculate uptime (seconds since started_at in Supabase — approximate with file age)
  local uptime
  uptime=$(python3 -c "
from datetime import datetime, timezone
import os
created = os.path.getctime('$SCANNER_DIR/bus/heartbeat.json')
uptime = (datetime.now(timezone.utc) - datetime.fromtimestamp(created, tz=timezone.utc)).total_seconds()
# Cap at reasonable value
print(int(min(uptime, 86400 * 30)))
" 2>/dev/null || echo "0")

  # Calculate total PnL
  local total_pnl
  total_pnl=$(python3 -c "print(round($equity - 746.51, 2))" 2>/dev/null || echo "0")

  echo "[$now] equity=\$$equity positions=$positions trades=$trades halted=$halted uptime=${uptime}s"

  # System snapshot (enrichment pipeline)
  python3 -c "
import sys
sys.path.insert(0, '$SCANNER_DIR')
try:
    from enrichment import record_system_snapshot
    import json, time
    risk = json.load(open('$SCANNER_DIR/bus/risk.json'))
    immune = 'critical' if risk.get('halted') else 'healthy'
    record_system_snapshot(
        equity=$equity,
        positions=[],  # simplified — full positions in Supabase
        signal_mode='smart',
        immune_status=immune,
    )
    time.sleep(1)  # wait for async write
except Exception as e:
    echo \"snapshot skipped: \$e\"
" 2>/dev/null || true

  # Push to Supabase
  curl -s -X PATCH "$SUPABASE_URL/rest/v1/agents?id=eq.$AGENT_ID" \
    -H "apikey: $SUPABASE_SERVICE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" \
    -H "Content-Type: application/json" \
    -H "Prefer: return=minimal" \
    -d "{
      \"status\": \"running\",
      \"stopped_at\": null,
      \"last_heartbeat\": \"$now\",
      \"equity_current\": $equity,
      \"current_positions\": $positions,
      \"total_trades\": $trades,
      \"total_pnl\": $total_pnl,
      \"immune_status\": \"$immune_status\",
      \"uptime_seconds\": $uptime
    }" -o /dev/null -w "HTTP %{http_code}\n"
}

HL_API="https://api.hyperliquid.xyz/info"

echo "=== Heartbeat Bridge started ==="
echo "Agent: $AGENT_ID"
echo "Wallet: $HL_WALLET"
echo "Interval: ${LOOP_INTERVAL}s"

while true; do
  sync_heartbeat || true
  sleep "$LOOP_INTERVAL"
done

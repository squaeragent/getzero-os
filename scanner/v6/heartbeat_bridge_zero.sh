#!/bin/bash
# Heartbeat bridge for ZERO agent (0xCb842e...)
# Pushes equity/position data to Supabase
set -euo pipefail

source ~/.config/openclaw/.env

ZERO_MAIN="0xCb842e38B510a855Ff4E5d65028247Bc8Fd16e5e"
AGENT_ID="zero-live-01"
HL_API="https://api.hyperliquid.xyz/info"
SUPABASE_URL="${SUPABASE_URL}"
SUPABASE_KEY="${SUPABASE_SERVICE_KEY}"

# Fetch perps state
PERPS=$(curl -s -m 10 "$HL_API" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"clearinghouseState\",\"user\":\"$ZERO_MAIN\"}" 2>/dev/null)

if [ -z "$PERPS" ]; then
  echo "[$(date -u +%H:%M:%S\ UTC)] Failed to fetch HL data"
  exit 1
fi

# Extract equity
EQUITY=$(echo "$PERPS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(float(d.get('marginSummary',{}).get('accountValue',0)))" 2>/dev/null)
MARGIN=$(echo "$PERPS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(float(d.get('marginSummary',{}).get('totalMarginUsed',0)))" 2>/dev/null)
POSITIONS=$(echo "$PERPS" | python3 -c "
import json,sys
d=json.load(sys.stdin)
pos=[p for p in d.get('assetPositions',[]) if float(p.get('position',{}).get('szi','0'))!=0]
print(len(pos))
" 2>/dev/null)

# Count trades
TRADES=$(echo "$PERPS" | python3 -c "
import json,sys
# Fetch fills separately
import urllib.request
req = urllib.request.Request('https://api.hyperliquid.xyz/info',
  data=json.dumps({'type':'userFills','user':'$ZERO_MAIN'}).encode(),
  headers={'Content-Type':'application/json'})
fills = json.loads(urllib.request.urlopen(req, timeout=10).read())
print(len(fills))
" 2>/dev/null || echo "0")

echo "[$(date -u +%H:%M:%S\ UTC)] ZERO: equity=\$$EQUITY margin=\$$MARGIN positions=$POSITIONS trades=$TRADES"

# Push to Supabase
curl -s -o /dev/null -X POST "${SUPABASE_URL}/rest/v1/agent_heartbeats" \
  -H "apikey: ${SUPABASE_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_KEY}" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=minimal" \
  -d "{
    \"agent_id\": \"$AGENT_ID\",
    \"equity\": $EQUITY,
    \"margin_used\": $MARGIN,
    \"open_positions\": $POSITIONS,
    \"total_trades\": $TRADES,
    \"status\": \"running\",
    \"mode\": \"live\"
  }" 2>/dev/null

echo "[$(date -u +%H:%M:%S\ UTC)] Heartbeat pushed to Supabase"

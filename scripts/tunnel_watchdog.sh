#!/bin/zsh
# zero▮ tunnel watchdog — checks local API then external, restarts what's needed
LOG=~/getzero-os/logs/tunnel_watchdog.log
mkdir -p ~/getzero-os/logs

# 1. Check local API (port 8420)
LOCAL=$(curl -s --max-time 4 http://localhost:8420/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
if [[ "$LOCAL" != "operational" ]]; then
  echo "$(date): intelligence-api down, restarting..." >> $LOG
  launchctl stop com.zero.intelligence-api
  sleep 3
  launchctl start com.zero.intelligence-api
  sleep 10
fi

# 2. Check external tunnel
EXTERNAL=$(curl -s --max-time 6 https://api.getzero.dev/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
if [[ "$EXTERNAL" != "operational" ]]; then
  echo "$(date): tunnel down (local=$LOCAL external=$EXTERNAL), restarting cloudflared..." >> $LOG
  launchctl stop com.zero.cloudflare-tunnel
  sleep 3
  launchctl start com.zero.cloudflare-tunnel
  sleep 15
  FINAL=$(curl -s --max-time 8 https://api.getzero.dev/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  echo "$(date): after restart: $FINAL" >> $LOG
fi

#!/bin/bash
source /Users/forge/.config/openclaw/.env
export SUPABASE_SERVICE_KEY
cd /Users/forge/getzero-os
/opt/homebrew/bin/python3 -m scanner.v6.sync_to_supabase 2>&1

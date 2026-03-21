# ZERO OS — Trading Agent

## What This Is
Self-hosted trading agent OS for Hyperliquid. Python 3.10+. Runs locally on user's machine.
Keys never leave the hardware. Paper mode by default, live mode gated.

## Architecture
```
scanner/
  zeroos_cli/         — CLI entrypoint (zeroos init/start/stop/status/logs/emergency-close)
  v6/                 — Core trading engine
    executor.py       — Trade execution (paper + live modes)
    evaluator.py      — Signal evaluation via WebSocket stream
    strategy_manager.py — Strategy refresh from signal API (6h cycles)
    immune.py         — 13-check monitoring system (stops, positions, equity)
    supervisor.py     — Process watchdog (spawns evaluator, immune, executor)
    risk_guard.py     — Pre-trade risk checks
    paper_executor.py — Paper trading (virtual positions, real prices)
    signal_provider.py — Abstract interface for signal sources
    signal_manager.py  — Graceful degradation (FULL→CACHED→BASIC→PROTECTION)
    signal_cache.py   — Local signal cache with freshness rules
    basic_signals.py  — Fallback RSI/EMA/MACD engine (no API needed)
    x402.py           — x402 micropayment integration
    telemetry_client.py — Opt-in dashboard telemetry
    x402_monitor.py   — Credit balance monitoring
    config.py         — All constants and env helpers
    bus/              — Runtime state (atomic JSON file I/O)
    data/             — Persistent data (trades.jsonl, etc.)
```

## Key Patterns
- All user state in ~/.zeroos/ (config.yaml, keystore.enc, cache/, logs/)
- Encrypted keystore: AES-256-GCM, Argon2 key derivation
- Signal providers are swappable (SignalProvider interface)
- 4 signal modes: FULL → CACHED → BASIC → PROTECTION (graceful degradation)
- Paper mode by default: 24h + 50 trades minimum before live
- Immune system runs independently of signal mode
- Every position must have an on-chain stop (invariant — never naked)
- Bus files use atomic writes (write temp → rename)
- Supervisor runs evaluator + immune as subprocesses, executor inline

## When Working on This Codebase
- Never log private keys or API credentials, even in debug mode
- Never hardcode thresholds in public-facing code or comments
- Test paper executor changes against live executor interface (same HLClient API)
- The immune system must work in ALL 4 signal modes
- Use atomic file writes for any bus state changes
- The CLI is the user's control plane — dashboard is read-only
- Paper mode must use isolated bus/data dirs (~/.zeroos/state/bus/, ~/.zeroos/state/data/)
- PAPER_MODE=1 env var suppresses all Telegram alerts
- Python 3.14 requires `global` declarations at top of function before any code

## Config
- ~/.zeroos/config.yaml — user configuration (preset, mode, API keys)
- ~/.zeroos/keystore.enc — encrypted HL private key
- ~/.zeroos/cache/ — signal cache (survives restarts)
- ~/.zeroos/logs/agent.log — unified log file

## Dependencies
hyperliquid-python-sdk, click, cryptography, websockets, pyyaml, requests

## Testing
```bash
pip install -e .
zeroos init          # interactive setup
zeroos start --paper # paper trading mode
zeroos status        # check state
zeroos logs          # tail log
zeroos stop          # graceful shutdown
```

# Architecture Consistency Audit
## Date: 2026-03-22
## Scope: Self-hosted pivot verification

### C1: Keys Never Transmitted ✅
- HL private key: loaded from encrypted keystore, passed to `EthAccount.from_key()`, used only for signing
- Never logged (even in debug mode)
- Never sent to any external service
- Never transmitted to Supabase, getzero.dev, or any API
- ENVY_API_KEY: sent to NVProtocol API only (correct — that's the signal service)

### C2: Dashboard Read-Only ✅ (with note)
- `/api/agents/[id]/lifecycle/route.ts` exists — can pause/resume/stop agents via Supabase status update
- In self-hosted model, this route is INERT — local agent reads from ~/.zeroos/config.yaml, not Supabase
- **NOTE**: Remove this route when self-hosted ships to avoid confusion
- No route sends commands directly to the user's local agent
- No route accesses HL API on behalf of users

### C3: Signal Provider Interface ✅
- Abstract base class: `SignalProvider` (signal_provider.py)
- 3 implementations:
  - `NVProtocolProvider` (FULL mode — live API)
  - `CachedProvider` (CACHED mode — local cache)
  - `BasicProvider` (BASIC mode — local RSI/EMA/MACD)
- `SignalManager` orchestrates mode transitions
- Evaluator uses interface, not direct API calls

### C4: Self-Protection Modes ✅
- FULL → CACHED → BASIC → PROTECTION degradation chain
- Mode transitions logged with quality scores (10 → 7 → 5 → 0)
- PROTECTION mode: no new trades, manages existing positions
- Bus file signal_mode.json tracks current mode
- x402 balance monitoring triggers BASIC mode on depletion

### C5: Paper Executor ✅
- Interface matches live executor: get_balance, get_positions, get_price, market_buy, market_sell, place_stop_loss
- Virtual balance tracking ($10K default)
- Real prices from HL REST API (no NVArena dependency for prices)
- Paper trades stored in ~/.zeroos/state/paper_state.json
- Paper bus isolation at ~/.zeroos/state/bus/ (no live state contamination)
- Telegram alerts suppressed in paper mode

### C6: CLI Wrapping ✅
- `zeroos init` → interactive key + preset + config setup ✅
- `zeroos start` → launches supervisor daemon ✅
- `zeroos stop` → graceful SIGTERM shutdown ✅
- `zeroos status` → reads paper bus state, shows equity/positions/uptime ✅
- `zeroos logs` → tails agent.log ✅
- `zeroos emergency-close` → force-close all positions ✅

## Result: Architecture consistent with self-hosted model ✅
One item noted for future cleanup (lifecycle route).

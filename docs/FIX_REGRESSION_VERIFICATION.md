# Fix Regression Verification Report
## Date: 2026-03-22
## Scope: 15 deployed fixes verified against zeroos codebase

| # | Fix | File | Status | Notes |
|---|-----|------|--------|-------|
| 1 | Position reconciliation on startup | executor.py:1088 `_reconcile_positions()` + L1283 startup guard | ✅ VERIFIED | Queries clearinghouseState, rebuilds local from HL truth |
| 2 | Stop offset (trigger ≠ limit) | executor.py:434 `place_stop_order()` | ✅ VERIFIED | `limit_offset_pct=0.02`, limit = trigger × 0.98 (sell) / 1.02 (buy) |
| 3 | Safe write (no empty overwrite) | executor.py:225 `_safe_save_positions()` | ✅ VERIFIED | Checks HL before writing 0 positions, auto-reconciles if mismatch |
| 4 | Immune desync detector | immune.py:474 `check_position_desync()` | ✅ VERIFIED | Compares local vs HL position counts, auto-reconciles + alerts |
| 5 | Assembled Sharpe as selection | strategy_manager.py:457-487 assembly parsing | ✅ VERIFIED | Ranks by assembled Sharpe from tournament endpoint, not signal Sharpe |
| 6 | 1.5 assembled Sharpe floor | strategy_manager.py:59 `MIN_ASSEMBLED_SHARPE=1.5`, L858 enforcement | ✅ VERIFIED | Coins below floor excluded with log |
| 7 | Negative-edge blacklist | evaluator.py:229 `COIN_BLACKLIST = {"PUMP", "XPL", "TRUMP"}` | ✅ VERIFIED | Checked in entry eval loop, skips blacklisted coins |
| 8 | Signal family blacklist | evaluator.py:225 `SIGNAL_FAMILY_BLACKLIST` | ✅ VERIFIED | SOCIAL, INFLUENCER, ICHIMOKU, ARCH, CHAOS — substring match |
| 9 | GTC entries for non-urgent signals | executor.py:738 GTC/IOC logic | ✅ VERIFIED | Age >10min → GTC at mid-spread, 60s timeout, IOC fallback |
| 10 | Minimum hold time (2h) | config.py:124 `MIN_HOLD_MINUTES=120`, evaluator.py:337 | ✅ VERIFIED | Stops fire anytime, expression exits wait 120min |
| 11 | Book depth fail-safe | executor.py:690 | ✅ VERIFIED | l2Book failure → skip trade, depth=0 → skip with log_rejection |
| 12 | reduce_only on SHORT close | executor.py:912, 932 | ✅ VERIFIED | Both close paths use `reduce_only=True` for both LONG and SHORT |
| 13 | Rejection reason logging | executor.py:67 `log_rejection()` | ✅ VERIFIED | JSONL + Supabase telemetry, specific reasons per gate |
| 14 | NameError fix (signal_time) | executor.py:739 | ✅ VERIFIED | `trade.get("signal_time") or trade.get("fired_at", "")` — no crash |
| 15 | Equity source (spot + perp) | executor.py:310 `get_balance()` | ✅ VERIFIED | spotClearinghouseState for USDC total + perp uPnL only |

## Result: 15/15 VERIFIED ✅
All fixes present and correctly implemented in the zeroos codebase.

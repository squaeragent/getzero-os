-- ZERO OS — Supabase Schema
-- Run this in the Supabase SQL editor to set up all tables.
-- Replaces: closed.jsonl, positions.json, equity_history.jsonl, counterfactual_log.jsonl

-- ─── TRADES ───────────────────────────────────────────────────────────────────
-- Replaces scanner/data/live/closed.jsonl
CREATE TABLE IF NOT EXISTS trades (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price NUMERIC NOT NULL,
    exit_price NUMERIC,
    size_usd NUMERIC NOT NULL,
    pnl_dollars NUMERIC,
    pnl_pct NUMERIC,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    exit_reason TEXT,
    signal TEXT,
    sharpe NUMERIC,
    win_rate NUMERIC,
    strategy_version INTEGER DEFAULT 3,
    adversary_verdict TEXT,
    survival_score NUMERIC,
    regime TEXT,
    session TEXT,
    hl_order_id TEXT,
    stop_loss_pct NUMERIC,
    fees_usd NUMERIC DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── POSITIONS ────────────────────────────────────────────────────────────────
-- Replaces scanner/data/live/positions.json
CREATE TABLE IF NOT EXISTS positions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    coin TEXT NOT NULL UNIQUE,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price NUMERIC NOT NULL,
    size_usd NUMERIC NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    signal TEXT,
    sharpe NUMERIC,
    win_rate NUMERIC,
    stop_loss_pct NUMERIC,
    trailing_stop_price NUMERIC,
    peak_price NUMERIC,
    adversary_verdict TEXT,
    survival_score NUMERIC,
    exit_expression TEXT,
    max_hold_hours NUMERIC,
    hl_order_id TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── EQUITY SNAPSHOTS ─────────────────────────────────────────────────────────
-- Replaces scanner/bus/equity_history.jsonl
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    equity_usd NUMERIC NOT NULL,
    unrealized_pnl NUMERIC DEFAULT 0,
    realized_pnl NUMERIC DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    strategy_version INTEGER DEFAULT 3,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── SIGNALS ──────────────────────────────────────────────────────────────────
-- Adversary evaluations — log of all signal verdicts
CREATE TABLE IF NOT EXISTS signals (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL,
    signal_name TEXT,
    sharpe NUMERIC,
    win_rate NUMERIC,
    adversary_verdict TEXT,
    survival_score NUMERIC,
    attacks JSONB DEFAULT '[]'::jsonb,
    regime TEXT,
    was_approved BOOLEAN DEFAULT FALSE,
    was_traded BOOLEAN DEFAULT FALSE,
    trade_id UUID REFERENCES trades(id),
    evaluated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── COUNTERFACTUAL LOG ───────────────────────────────────────────────────────
-- Replaces scanner/memory/counterfactual_log.jsonl
CREATE TABLE IF NOT EXISTS counterfactual_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    episode_id TEXT UNIQUE NOT NULL,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL,
    adversary_correct BOOLEAN,
    resolution TEXT CHECK (resolution IN ('correct_kill', 'false_kill', 'inconclusive')),
    would_have_won BOOLEAN,
    pnl_at_hold_pct NUMERIC,
    max_hold_hours NUMERIC,
    killing_attacks JSONB DEFAULT '[]'::jsonb,
    dominant_attack TEXT,
    kill_time TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── AGENT HEARTBEATS ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    agent TEXT PRIMARY KEY,
    last_heartbeat TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── INDEXES ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_trades_coin ON trades(coin);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_version ON trades(strategy_version);
CREATE INDEX IF NOT EXISTS idx_signals_coin ON signals(coin);
CREATE INDEX IF NOT EXISTS idx_signals_evaluated_at ON signals(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_equity_recorded_at ON equity_snapshots(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_counterfactual_dominant_attack ON counterfactual_log(dominant_attack);

-- Migration 004: Backtest results storage
-- Run: psql -d karsa -f scripts/migrations/004_backtest_results.sql

CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    regime VARCHAR(20),
    score NUMERIC(5,2),
    entry_price NUMERIC(20,8) NOT NULL,
    exit_price NUMERIC(20,8),
    exit_reason VARCHAR(50),
    sl_price NUMERIC(20,8),
    tp_price NUMERIC(20,8),
    amount NUMERIC(20,8) NOT NULL,
    size_multiplier NUMERIC(5,2),
    pnl_gross NUMERIC(20,8),
    pnl_net NUMERIC(20,8),
    total_fees NUMERIC(20,8),
    total_funding NUMERIC(20,8),
    bars_held INTEGER,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    risk_profile_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- For Phase 3.2 schema match to avoid tracking 20 columns
    config_snapshot JSONB,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    total_trades INTEGER,
    win_rate NUMERIC(6,4),
    profit_factor NUMERIC(10,4),
    net_pnl_usdt NUMERIC(20,8),
    max_drawdown_pct NUMERIC(6,4),
    regime_breakdown JSONB,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS idx_backtest_results_job_id ON backtest_results(job_id);
CREATE INDEX IF NOT EXISTS idx_backtest_results_symbol ON backtest_results(symbol);

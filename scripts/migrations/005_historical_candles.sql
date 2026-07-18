-- Migration 005: Historical candles cache for backtesting
-- Run: psql -d karsa -f scripts/migrations/005_historical_candles.sql

CREATE TABLE IF NOT EXISTS historical_candles (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT '1h',
    ts TIMESTAMPTZ NOT NULL,
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume NUMERIC(20,8) NOT NULL,
    UNIQUE(symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_historical_candles_lookup
    ON historical_candles(symbol, timeframe, ts);

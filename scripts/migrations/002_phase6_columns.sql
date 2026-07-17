-- Migration 002: Phase 6 Adaptive Multi-Strategy columns
-- Adds regime-aware position tracking to trades table
-- Run: psql -d karsa -f scripts/migrations/002_phase6_columns.sql

BEGIN;

ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_regime VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS initial_risk_per_unit NUMERIC(20,8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS moved_to_breakeven BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_sl NUMERIC(20,8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS risk_profile_json JSONB;

COMMIT;

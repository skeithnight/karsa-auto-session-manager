-- Rollback Migration 002: Remove Phase 6 columns from trades table
-- Run: psql -d karsa -f scripts/migrations/002_phase6_columns_down.sql

BEGIN;

ALTER TABLE trades DROP COLUMN IF EXISTS entry_regime;
ALTER TABLE trades DROP COLUMN IF EXISTS initial_risk_per_unit;
ALTER TABLE trades DROP COLUMN IF EXISTS moved_to_breakeven;
ALTER TABLE trades DROP COLUMN IF EXISTS current_sl;
ALTER TABLE trades DROP COLUMN IF EXISTS risk_profile_json;

COMMIT;

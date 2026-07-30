-- Migration 006: Add hybrid intelligence columns to backtest_results
-- Run: psql -d karsa -f scripts/migrations/006_backtest_ai_columns.sql

-- Add AI-related columns for hybrid intelligence metrics
ALTER TABLE backtest_results
ADD COLUMN IF NOT EXISTS ai_confidence NUMERIC(5,2),
ADD COLUMN IF NOT EXISTS beta NUMERIC(5,3),
ADD COLUMN IF NOT EXISTS correlation NUMERIC(5,4),
ADD COLUMN IF NOT EXISTS volume_spike NUMERIC(10,4),
ADD COLUMN IF NOT EXISTS guardrail_type VARCHAR(20),
ADD COLUMN IF NOT EXISTS guardrail_action VARCHAR(30),
ADD COLUMN IF NOT EXISTS monthly_bucket VARCHAR(7);

-- Index for monthly returns analysis
CREATE INDEX IF NOT EXISTS idx_backtest_results_monthly
ON backtest_results(monthly_bucket) WHERE monthly_bucket IS NOT NULL;

-- Index for AI confidence analysis
CREATE INDEX IF NOT EXISTS idx_backtest_results_ai_confidence
ON backtest_results(ai_confidence) WHERE ai_confidence IS NOT NULL;

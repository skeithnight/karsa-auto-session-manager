-- Migration: Add edge_family, expected_value, and holding_time fields
-- This addresses audit finding #4: family-aware ranking cannot work yet

-- Add edge_family column (which strategy family produced this trade)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS edge_family VARCHAR(50);

-- Add expected_value column (EV at time of entry)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS expected_value DECIMAL(20,8);

-- Add holding_time_bucket column (SCALP/SHORT/SWING/POSITIONAL)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS holding_time_bucket VARCHAR(20);

-- Add holding_time_minutes column (precise tracking)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS holding_time_minutes INTEGER;

-- Add win_rate_at_entry column (historical win rate when signal was generated)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS win_rate_at_entry DECIMAL(5,4);

-- Index for family-based queries
CREATE INDEX IF NOT EXISTS idx_trades_edge_family ON trades(edge_family);
CREATE INDEX IF NOT EXISTS idx_trades_holding_bucket ON trades(holding_time_bucket);

-- Function to calculate holding time bucket
CREATE OR REPLACE FUNCTION calculate_holding_bucket(entry TIMESTAMPTZ, exit_t TIMESTAMPTZ)
RETURNS VARCHAR(20) AS $$
DECLARE
    minutes_held INTEGER;
BEGIN
    IF entry IS NULL OR exit_t IS NULL THEN
        RETURN 'OPEN';
    END IF;
    minutes_held := EXTRACT(EPOCH FROM (exit_t - entry)) / 60;
    IF minutes_held < 60 THEN
        RETURN 'SCALP';
    ELSIF minutes_held < 240 THEN
        RETURN 'SHORT';
    ELSIF minutes_held < 1440 THEN
        RETURN 'SWING';
    ELSE
        RETURN 'POSITIONAL';
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Backfill existing trades with holding time
UPDATE trades
SET
    holding_time_minutes = EXTRACT(EPOCH FROM (exit_time - entry_time)) / 60,
    holding_time_bucket = calculate_holding_bucket(entry_time, exit_time)
WHERE exit_time IS NOT NULL AND holding_time_bucket IS NULL;

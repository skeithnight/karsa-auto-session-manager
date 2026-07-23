-- Add strategy_type to trades and signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(50) DEFAULT 'SWING';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_type VARCHAR(50) DEFAULT 'SWING';

-- Add AI Shadow Scoring columns to signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS ai_confidence_score INTEGER;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS ai_reasoning TEXT;

-- Add Macro Context to signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS macro_context JSONB;

-- Add Micro-Scalper columns to trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS is_micro_scalper BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(50);

-- Create sniper_traps table for AI Node 1 metadata
CREATE TABLE IF NOT EXISTS sniper_traps (
    trap_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(20) NOT NULL,
    target_price DECIMAL(20,8) NOT NULL,
    ai_thesis TEXT,
    ai_confidence_score INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'FILLED', 'CANCELLED', 'EXPIRED')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sniper_traps_symbol_status ON sniper_traps(symbol, status);

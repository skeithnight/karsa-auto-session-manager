-- Shadow trades table — mirrors `trades` + shadow-specific columns
-- Called by: main.py on startup when SHADOW_MODE_ENABLED=true

CREATE TABLE IF NOT EXISTS shadow_trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    amount DECIMAL(20,8) NOT NULL,
    entry_price DECIMAL(20,8) NOT NULL,
    exit_price DECIMAL(20,8),
    pnl DECIMAL(20,8),
    regime VARCHAR(30),
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    exit_reason VARCHAR(50),
    ai_confidence INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- Phase 6 columns (mirror trades)
    entry_regime VARCHAR(20),
    initial_risk_per_unit NUMERIC(20,8),
    moved_to_breakeven BOOLEAN DEFAULT FALSE,
    current_sl NUMERIC(20,8),
    risk_profile_json JSONB,
    -- Shadow-specific
    is_shadow BOOLEAN DEFAULT TRUE,
    slippage_applied NUMERIC(20,8),
    fees_applied NUMERIC(20,8)
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_symbol ON shadow_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_entry_time ON shadow_trades(entry_time);

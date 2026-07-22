-- Karsa Auto Session Manager — DB Schema
-- Mounted to postgres docker-entrypoint-initdb.d
-- Callers: docker-compose.yml mounts to /docker-entrypoint-initdb.d/
-- Tables: trades (trade history), ai_decisions (AI audit trail)

CREATE TABLE IF NOT EXISTS trades (
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
    -- Phase 6: Adaptive Multi-Strategy columns
    entry_regime VARCHAR(20),
    initial_risk_per_unit NUMERIC(20,8),
    moved_to_breakeven BOOLEAN DEFAULT FALSE,
    current_sl NUMERIC(20,8),
    risk_profile_json JSONB,
    -- Sprint 8: Validation & Analytics
    mae DECIMAL(20,8),
    mfe DECIMAL(20,8),
    peak_r_multiple DECIMAL(20,8),
    evidence_trend BOOLEAN DEFAULT FALSE,
    evidence_momentum BOOLEAN DEFAULT FALSE,
    evidence_orderbook BOOLEAN DEFAULT FALSE,
    evidence_funding BOOLEAN DEFAULT FALSE,
    evidence_ai BOOLEAN DEFAULT FALSE,
    ai_confidence_before INTEGER,
    ai_confidence_after INTEGER,
    ai_latency_ms INTEGER,
    trace_id VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    decision_type VARCHAR(20) NOT NULL,
    model VARCHAR(50),
    input_hash VARCHAR(64),
    output_json JSONB,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_symbol ON ai_decisions(symbol, created_at);

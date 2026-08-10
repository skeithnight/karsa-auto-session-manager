-- Migration 004: DeFi & Hybrid Intelligence (v3.0) tables

CREATE TABLE IF NOT EXISTS treasury_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    venue VARCHAR(50) NOT NULL,
    amount_usdc DECIMAL(20,8) NOT NULL,
    entry_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exit_timestamp TIMESTAMPTZ,
    expected_apy DECIMAL(10,6),
    realized_yield DECIMAL(20,8),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'PENDING_EXIT', 'CLOSED', 'FAILED')),
    tx_hash VARCHAR(66),
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS defi_interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    protocol VARCHAR(50) NOT NULL,
    chain_id INTEGER NOT NULL,
    action VARCHAR(30) NOT NULL,
    tx_hash VARCHAR(66),
    gas_used INTEGER,
    gas_cost_usd DECIMAL(20,8),
    status VARCHAR(20) NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'REVERTED', 'FAILED')),
    details JSONB
);

CREATE TABLE IF NOT EXISTS lvr_opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    cex_mid_price DECIMAL(20,8) NOT NULL,
    dex_price DECIMAL(20,8) NOT NULL,
    spread_bps DECIMAL(10,4) NOT NULL,
    gas_cost_usd DECIMAL(20,8),
    net_ev DECIMAL(20,8),
    was_actionable BOOLEAN NOT NULL,
    was_executed BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_treasury_allocations_status ON treasury_allocations(status);
CREATE INDEX IF NOT EXISTS idx_defi_interactions_protocol ON defi_interactions(protocol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_lvr_opportunities_symbol ON lvr_opportunities(symbol, timestamp DESC);

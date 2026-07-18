"""initial_schema: create all tables (trades, shadow_trades, backtest_results, historical_candles)

Revision ID: 001
Revises:
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- trades table ---
    op.execute("""
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
            entry_regime VARCHAR(20),
            initial_risk_per_unit NUMERIC(20,8),
            moved_to_breakeven BOOLEAN DEFAULT FALSE,
            current_sl NUMERIC(20,8),
            risk_profile_json JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)")

    # --- shadow_trades table ---
    op.execute("""
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
            entry_regime VARCHAR(20),
            initial_risk_per_unit NUMERIC(20,8),
            moved_to_breakeven BOOLEAN DEFAULT FALSE,
            current_sl NUMERIC(20,8),
            risk_profile_json JSONB,
            is_shadow BOOLEAN DEFAULT TRUE,
            slippage_applied NUMERIC(20,8),
            fees_applied NUMERIC(20,8)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_shadow_trades_symbol ON shadow_trades(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_shadow_trades_entry_time ON shadow_trades(entry_time)")

    # --- backtest_results table ---
    op.execute("""
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
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_backtest_results_job_id ON backtest_results(job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_backtest_results_symbol ON backtest_results(symbol)")

    # --- historical_candles table ---
    op.execute("""
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
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_historical_candles_lookup ON historical_candles(symbol, timeframe, ts)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS historical_candles")
    op.execute("DROP TABLE IF EXISTS backtest_results")
    op.execute("DROP TABLE IF EXISTS shadow_trades")
    op.execute("DROP TABLE IF EXISTS trades")

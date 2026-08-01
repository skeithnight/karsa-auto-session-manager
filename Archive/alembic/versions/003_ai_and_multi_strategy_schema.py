"""ai_and_multi_strategy_schema

Revision ID: 003
Revises: 002
Create Date: 2026-07-23

"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    # --- signals table ---
    op.create_table(
        "signals",
        sa.Column("id", UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("alpha_metrics", JSONB(), nullable=False),
        sa.Column("risk_passed", sa.Boolean(), nullable=False),
        sa.Column("risk_reason", sa.Text(), nullable=True),
        sa.Column("executed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True),
        sa.Column("trade_id", sa.Integer(), sa.ForeignKey("trades.id"), nullable=True),
        sa.Column("strategy_type", sa.String(length=50), server_default="SWING", nullable=True),
        sa.Column("ai_confidence_score", sa.Integer(), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("macro_context", JSONB(), nullable=True),
    )
    op.create_index("idx_signals_timestamp", "signals", ["timestamp"])
    op.create_index("idx_signals_direction_risk", "signals", ["direction", "risk_passed"])

    # --- trades table ---
    op.add_column("trades", sa.Column("strategy_type", sa.String(length=50), server_default="SWING", nullable=True))
    op.add_column("trades", sa.Column("is_micro_scalper", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))

    # --- shadow_trades table ---
    op.add_column("shadow_trades", sa.Column("strategy_type", sa.String(length=50), server_default="SWING", nullable=True))
    op.add_column("shadow_trades", sa.Column("is_micro_scalper", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    # Note: shadow_trades already has exit_reason

    # --- sniper_traps table ---
    op.create_table(
        "sniper_traps",
        sa.Column("trap_id", UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("target_price", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("ai_thesis", sa.Text(), nullable=True),
        sa.Column("ai_confidence_score", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_sniper_traps_symbol_status", "sniper_traps", ["symbol", "status"])


def downgrade() -> None:
    # --- signals table ---
    op.drop_index("idx_signals_direction_risk", table_name="signals")
    op.drop_index("idx_signals_timestamp", table_name="signals")
    op.drop_table("signals")

    # --- trades table ---
    op.drop_column("trades", "is_micro_scalper")
    op.drop_column("trades", "strategy_type")

    # --- shadow_trades table ---
    op.drop_column("shadow_trades", "is_micro_scalper")
    op.drop_column("shadow_trades", "strategy_type")

    # --- sniper_traps table ---
    op.drop_index("idx_sniper_traps_symbol_status", table_name="sniper_traps")
    op.drop_table("sniper_traps")

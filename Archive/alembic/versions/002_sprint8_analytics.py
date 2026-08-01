"""sprint8_analytics: add trace, evidence, ai, and lifecycle columns to trades and shadow_trades

Revision ID: 002
Revises: 001
Create Date: 2026-07-22

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- trades table ---
    op.add_column("trades", sa.Column("mae", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("trades", sa.Column("mfe", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("trades", sa.Column("peak_r_multiple", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("trades", sa.Column("evidence_trend", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("trades", sa.Column("evidence_momentum", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("trades", sa.Column("evidence_orderbook", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("trades", sa.Column("evidence_funding", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("trades", sa.Column("evidence_ai", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("trades", sa.Column("ai_confidence_before", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("ai_confidence_after", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("ai_latency_ms", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("trace_id", sa.String(length=100), nullable=True))

    # --- shadow_trades table ---
    op.add_column("shadow_trades", sa.Column("mae", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("shadow_trades", sa.Column("mfe", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("shadow_trades", sa.Column("peak_r_multiple", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("shadow_trades", sa.Column("evidence_trend", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("shadow_trades", sa.Column("evidence_momentum", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("shadow_trades", sa.Column("evidence_orderbook", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("shadow_trades", sa.Column("evidence_funding", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("shadow_trades", sa.Column("evidence_ai", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True))
    op.add_column("shadow_trades", sa.Column("ai_confidence_before", sa.Integer(), nullable=True))
    op.add_column("shadow_trades", sa.Column("ai_confidence_after", sa.Integer(), nullable=True))
    op.add_column("shadow_trades", sa.Column("ai_latency_ms", sa.Integer(), nullable=True))
    op.add_column("shadow_trades", sa.Column("trace_id", sa.String(length=100), nullable=True))


def downgrade() -> None:
    # --- trades table ---
    op.drop_column("trades", "trace_id")
    op.drop_column("trades", "ai_latency_ms")
    op.drop_column("trades", "ai_confidence_after")
    op.drop_column("trades", "ai_confidence_before")
    op.drop_column("trades", "evidence_ai")
    op.drop_column("trades", "evidence_funding")
    op.drop_column("trades", "evidence_orderbook")
    op.drop_column("trades", "evidence_momentum")
    op.drop_column("trades", "evidence_trend")
    op.drop_column("trades", "peak_r_multiple")
    op.drop_column("trades", "mfe")
    op.drop_column("trades", "mae")

    # --- shadow_trades table ---
    op.drop_column("shadow_trades", "trace_id")
    op.drop_column("shadow_trades", "ai_latency_ms")
    op.drop_column("shadow_trades", "ai_confidence_after")
    op.drop_column("shadow_trades", "ai_confidence_before")
    op.drop_column("shadow_trades", "evidence_ai")
    op.drop_column("shadow_trades", "evidence_funding")
    op.drop_column("shadow_trades", "evidence_orderbook")
    op.drop_column("shadow_trades", "evidence_momentum")
    op.drop_column("shadow_trades", "evidence_trend")
    op.drop_column("shadow_trades", "peak_r_multiple")
    op.drop_column("shadow_trades", "mfe")
    op.drop_column("shadow_trades", "mae")

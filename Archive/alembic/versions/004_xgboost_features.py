"""xgboost_features

Revision ID: 004
Revises: 003
Create Date: 2026-07-24

"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- shadow_trades table ---
    op.add_column("shadow_trades", sa.Column("cvd_slope", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("shadow_trades", sa.Column("spread_bps", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("shadow_trades", sa.Column("session_mult", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("shadow_trades", sa.Column("regime_encoded", sa.Integer(), nullable=True))
    op.add_column("shadow_trades", sa.Column("atr_pct", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("shadow_trades", sa.Column("vol_factor", sa.Numeric(precision=10, scale=4), nullable=True))

    # --- trades table ---
    op.add_column("trades", sa.Column("cvd_slope", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("trades", sa.Column("spread_bps", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("trades", sa.Column("session_mult", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("trades", sa.Column("regime_encoded", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("atr_pct", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("trades", sa.Column("vol_factor", sa.Numeric(precision=10, scale=4), nullable=True))


def downgrade() -> None:
    # --- trades table ---
    op.drop_column("trades", "vol_factor")
    op.drop_column("trades", "atr_pct")
    op.drop_column("trades", "regime_encoded")
    op.drop_column("trades", "session_mult")
    op.drop_column("trades", "spread_bps")
    op.drop_column("trades", "cvd_slope")

    # --- shadow_trades table ---
    op.drop_column("shadow_trades", "vol_factor")
    op.drop_column("shadow_trades", "atr_pct")
    op.drop_column("shadow_trades", "regime_encoded")
    op.drop_column("shadow_trades", "session_mult")
    op.drop_column("shadow_trades", "spread_bps")
    op.drop_column("shadow_trades", "cvd_slope")

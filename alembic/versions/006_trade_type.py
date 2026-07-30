"""Add trade_type column for metric isolation.

Revision ID: 006
Revises: 005
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add trade_type column with default STRATEGY."""
    op.add_column(
        "trades",
        sa.Column("trade_type", sa.String(20), nullable=False, server_default="STRATEGY"),
    )
    # Create index for efficient querying by trade type
    op.create_index("idx_trades_trade_type", "trades", ["trade_type"])


def downgrade() -> None:
    """Remove trade_type column."""
    op.drop_index("idx_trades_trade_type")
    op.drop_column("trades", "trade_type")

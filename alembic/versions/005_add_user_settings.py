"""add_user_settings: persist Telegram bot settings to Postgres

Revision ID: 005
Revises: 004
Create Date: 2026-07-29

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            setting_key VARCHAR(100) NOT NULL,
            setting_value VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, setting_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON user_settings(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_settings")

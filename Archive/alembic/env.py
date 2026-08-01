"""Alembic environment config — async target for PostgreSQL.

Reads DATABASE_URL from app settings at runtime, not the ini placeholder.
Uses asyncpg driver for async migration support.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override DB URL with the one from app Settings — must use asyncpg driver prefix
settings = get_settings()
# postgres_url already has +asyncpg driver prefix; asyncpg_dsn strips it
config.set_main_option("sqlalchemy.url", settings.postgres_url)

target_metadata = None  # No declarative Base — using raw SQL migrations


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without connecting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations on the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create async engine and run migrations."""
    url = config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

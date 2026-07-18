"""Singleton factories for asyncpg pool and redis.asyncio client.

Usage:
    await startup()   # at app boot — creates pool + client
    pool = get_pool()
    redis = get_redis()
    await shutdown()  # at app exit — drains and closes
"""

from __future__ import annotations

import logging

import asyncpg
import redis.asyncio as aioredis

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


async def startup(settings: Settings | None = None) -> None:
    """Create asyncpg connection pool and Redis client.

    Called once at application startup. Idempotent — calling twice is a no-op
    if pool and client already exist.
    """
    global _pool, _redis  # noqa: PLW0603

    if settings is None:
        settings = get_settings()

    if _pool is None:
        dsn = settings.asyncpg_dsn
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("asyncpg pool created (dsn=%s)", dsn.split("@")[-1])

    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
        # Verify connectivity
        await _redis.ping()
        logger.info("redis client connected (%s)", settings.redis_url)


async def shutdown() -> None:
    """Drain and close asyncpg pool and Redis client.

    Called once at application exit. Safe to call even if startup() was never called.
    """
    global _pool, _redis  # noqa: PLW0603

    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")

    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("redis client closed")


def get_pool() -> asyncpg.Pool:
    """Return the asyncpg connection pool.

    Raises:
        RuntimeError: If startup() has not been called yet.
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call startup() first")
    return _pool


def get_redis() -> aioredis.Redis:
    """Return the Redis client.

    Raises:
        RuntimeError: If startup() has not been called yet.
    """
    if _redis is None:
        raise RuntimeError("Redis client not initialized — call startup() first")
    return _redis

"""Settings Store — Postgres-backed persistence for Telegram bot settings.

Provides a DB backup of user settings that currently live in Redis.
On startup, syncs DB -> Redis when Redis is empty (survives Redis restarts).
On every user action, writes through to both Redis and DB.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger
from sqlalchemy import text

from app.core.database import DatabaseEngine

# Redis keys that map to user settings
REDIS_SETTINGS_KEYS: dict[str, str] = {
    "risk_pct": "karsa:settings:risk_pct",
    "max_positions": "karsa:settings:max_positions",
    "trade_alerts": "karsa:settings:alerts:trade",
    "daily_summary": "karsa:settings:alerts:daily_summary",
    "guardrail_alerts": "karsa:settings:alerts:guardrail",
    "mute_until": "karsa:settings:alerts:mute_until",
}

# Default values for settings when neither Redis nor DB has a value
SETTING_DEFAULTS: dict[str, str] = {
    "risk_pct": "30",
    "max_positions": "5",
    "trade_alerts": "1",
    "daily_summary": "1",
    "guardrail_alerts": "1",
    "mute_until": "",
}


class SettingsStore:
    """Postgres CRUD for user_settings table.

    Wraps raw SQL via SQLAlchemy text() — no ORM.  Follows the same pattern
    as TradeStore.
    """

    def __init__(self, db: DatabaseEngine) -> None:
        self.db = db

    # ── Single setting ───────────────────────────────────────────────────

    async def get_setting(self, user_id: int, key: str) -> str | None:
        """Get a single setting from DB. Returns None if not found."""
        try:
            assert self.db.engine is not None  # noqa: S101
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT setting_value FROM user_settings "
                        "WHERE user_id = :user_id AND setting_key = :key"
                    ),
                    {"user_id": user_id, "key": key},
                )
                row = result.fetchone()
                return str(row[0]) if row else None
        except Exception as exc:
            logger.error(
                "settings_store.get_setting failed: user_id={} key={} error={}",
                user_id,
                key,
                exc,
            )
            return None

    async def set_setting(self, user_id: int, key: str, value: str) -> None:
        """Upsert a single setting into DB."""
        try:
            assert self.db.engine is not None  # noqa: S101
            async with self.db.engine.connect() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at) "
                        "VALUES (:user_id, :key, :value, NOW()) "
                        "ON CONFLICT (user_id, setting_key) "
                        "DO UPDATE SET setting_value = :value, updated_at = NOW()"
                    ),
                    {"user_id": user_id, "key": key, "value": value},
                )
                await conn.commit()
        except Exception as exc:
            logger.error(
                "settings_store.set_setting failed: user_id={} key={} value={} error={}",
                user_id,
                key,
                value,
                exc,
            )

    # ── Bulk operations ──────────────────────────────────────────────────

    async def get_all_settings(self, user_id: int) -> dict[str, str]:
        """Get all settings for a user from DB."""
        try:
            assert self.db.engine is not None  # noqa: S101
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT setting_key, setting_value FROM user_settings "
                        "WHERE user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
                return {row[0]: str(row[1]) for row in result.fetchall()}
        except Exception as exc:
            logger.error(
                "settings_store.get_all_settings failed: user_id={} error={}",
                user_id,
                exc,
            )
            return {}

    async def set_all_settings(self, user_id: int, settings: dict[str, str]) -> None:
        """Bulk upsert all settings for a user."""
        for key, value in settings.items():
            await self.set_setting(user_id, key, value)

    # ── Sync helpers ─────────────────────────────────────────────────────

    async def sync_redis_to_db(self, redis: aioredis.Redis, user_id: int) -> None:
        """Read all settings from Redis and persist them to DB.

        Called after user changes a setting (write-through).
        """
        for setting_key, redis_key in REDIS_SETTINGS_KEYS.items():
            try:
                raw = await redis.get(redis_key)
                if raw is not None:
                    value = str(raw) if not isinstance(raw, str) else raw
                    await self.set_setting(user_id, setting_key, value)
            except Exception as exc:
                logger.warning(
                    "sync_redis_to_db: failed for key={}: {}",
                    setting_key,
                    exc,
                )

    async def sync_db_to_redis(self, redis: aioredis.Redis, user_id: int) -> int:
        """Load settings from DB into Redis (only if Redis key is missing).

        Returns the number of settings restored.
        Called on bot startup to survive Redis restarts.
        """
        restored = 0
        db_settings = await self.get_all_settings(user_id)

        for setting_key, redis_key in REDIS_SETTINGS_KEYS.items():
            try:
                # Only populate if Redis doesn't have the key
                existing = await redis.get(redis_key)
                if existing is None and setting_key in db_settings:
                    value = db_settings[setting_key]
                    if value:  # skip empty mute_until
                        await redis.set(redis_key, value)
                        restored += 1
                        logger.debug(
                            "sync_db_to_redis: restored {}={} for user {}",
                            redis_key,
                            value,
                            user_id,
                        )
            except Exception as exc:
                logger.warning(
                    "sync_db_to_redis: failed for key={}: {}",
                    setting_key,
                    exc,
                )

        if restored > 0:
            logger.info(
                "sync_db_to_redis: restored {} settings from DB for user {}",
                restored,
                user_id,
            )
        return restored

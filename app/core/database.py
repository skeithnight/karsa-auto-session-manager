"""Database Engine — shared async SQLAlchemy engine with lifecycle."""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class DatabaseEngine:
    """Shared async SQLAlchemy engine — init once, dispose on shutdown."""

    def __init__(self) -> None:
        logger.debug("DatabaseEngine.__init__: entering")
        self.engine: AsyncEngine | None = None
        logger.debug("DatabaseEngine.__init__: returning")

    async def connect(self, url: str) -> None:
        """Create engine and verify connectivity."""
        logger.debug(
            f"connect: entering url={url.split('@')[-1] if '@' in url else url}"
        )
        self.engine = create_async_engine(url, pool_pre_ping=True)
        async with self.engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
        logger.info("Database connected and verified")
        logger.debug("connect: returning None")

    async def dispose(self) -> None:
        """Dispose engine — release all connections."""
        logger.debug("dispose: entering")
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            logger.info("Database engine disposed")
        logger.debug("dispose: returning None")

    async def check(self) -> bool:
        """Health check — returns True if DB is reachable."""
        logger.debug("check: entering")
        if not self.engine:
            logger.debug("check: returning False (no engine)")
            return False
        try:
            from sqlalchemy import text

            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.debug("check: returning True")
            return True
        except Exception as e:
            logger.error(f"check: error={e}")
            return False

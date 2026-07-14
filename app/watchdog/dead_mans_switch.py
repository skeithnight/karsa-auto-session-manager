"""Dead Man's Switch — periodic HTTP ping to external service."""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from loguru import logger

from app.core import metrics
from app.core.config import get_settings


class DeadMansSwitch:
    """Sends periodic pings to confirm bot is alive."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.url = self.settings.dead_mans_switch_url
        self.interval = self.settings.dead_mans_switch_interval
        self.running = False

    async def start(self) -> None:
        """Start ping loop."""
        if not self.url:
            logger.info("Dead Man's Switch disabled — no URL configured")
            return

        self.running = True
        logger.info(f"Dead Man's Switch started — pinging every {self.interval}s")

        while self.running:
            try:
                await self._ping()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Dead Man's Switch error: {e}")
                await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        """Stop ping loop."""
        self.running = False
        logger.info("Dead Man's Switch stopped")

    async def _ping(self) -> None:
        """Send single ping with retry on failure."""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            metrics.dms_ping_success.inc()
                            logger.debug("Dead Man's Switch ping OK")
                            return
                        else:
                            metrics.dms_ping_failure.inc()
                            logger.warning(f"Dead Man's Switch ping failed: {resp.status}")
            except Exception as e:
                metrics.dms_ping_failure.inc()
                logger.error(f"Dead Man's Switch ping error: {e}")

            if attempt < max_retries:
                logger.debug(f"DMS retry {attempt}/{max_retries}")
                await asyncio.sleep(2)

"""DeFiTelemetry — polls protocol TVL and smart money telemetry.

Uses aiohttp REST calls for non-blocking asynchronous I/O.
Degrades gracefully on API rate limits or network drops.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
from loguru import logger

from app.core import metrics
from app.core.redis_client import RedisClient


DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"


class DeFiTelemetry:
    """Telemetry collector for on-chain TVL velocity and protocol health."""

    def __init__(
        self,
        redis_client: RedisClient,
        poll_interval_s: float = 300.0,
    ) -> None:
        self._redis = redis_client
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._tvl_data: dict[str, Decimal] = {}

    @property
    def tvl_data(self) -> dict[str, Decimal]:
        return dict(self._tvl_data)

    async def fetch_tvl_velocity(self, target_protocols: list[str] | None = None) -> dict[str, Decimal]:
        """Fetch protocol TVL from DeFiLlama API."""
        targets = target_protocols or ["uniswap", "aave", "pendle", "hyperliquid", "eigenlayer"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    DEFILLAMA_PROTOCOLS_URL, timeout=aiohttp.ClientTimeout(total=10.0)
                ) as resp:
                    if resp.status == 200:
                        protocols: list[dict[str, Any]] = await resp.json()
                        res: dict[str, Decimal] = {}
                        for p in protocols:
                            name_slug = str(p.get("slug", "")).lower()
                            if any(target in name_slug for target in targets):
                                tvl = Decimal(str(p.get("tvl", 0)))
                                res[name_slug] = tvl
                        self._tvl_data = res
                        for slug, tvl_val in res.items():
                            await self._redis.set(f"defi:tvl:{slug}", str(tvl_val), ex=600)
                        return res
        except Exception as e:
            logger.warning(f"DeFiTelemetry: failed to fetch TVL velocity: {e}")
        return self._tvl_data

    async def start(self) -> None:
        """Start periodic telemetry collector loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DeFiTelemetry started")

    async def stop(self) -> None:
        """Stop periodic telemetry collector loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DeFiTelemetry stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.fetch_tvl_velocity()
            except Exception as e:
                logger.error(f"DeFiTelemetry loop error: {e}")
            await asyncio.sleep(self._poll_interval_s)

"""GasTracker — monitors Ethereum gas price in gwei.

Periodically fetches gasPrice via EVM RPC, converts to Decimal gwei,
and caches in Redis key 'onchain:gas:gwei' (15s TTL).

Runs in an isolated asyncio task to ensure failure isolation.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import aiohttp
from loguru import logger

from app.core import metrics
from app.core.config import get_settings
from app.core.redis_client import RedisClient


class GasTracker:
    """Monitors EVM gas price asynchronously."""

    def __init__(
        self,
        redis_client: RedisClient,
        rpc_url: str | None = None,
        poll_interval_s: float = 15.0,
    ) -> None:
        self._redis = redis_client
        self._rpc_url = rpc_url or get_settings().evm_rpc_url
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._current_gwei: Decimal | None = None

    @property
    def current_gwei(self) -> Decimal | None:
        return self._current_gwei

    async def fetch_gas_price_gwei(self) -> Decimal | None:
        """Fetch current gas price in gwei using eth_gasPrice RPC call."""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_gasPrice",
            "params": [],
            "id": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5.0)
                ) as resp:
                    if resp.status == 200:
                        data: dict[str, Any] = await resp.json()
                        result_hex = data.get("result")
                        if result_hex and isinstance(result_hex, str):
                            wei_val = int(result_hex, 16)
                            gwei_val = Decimal(wei_val) / Decimal("1000000000")
                            gwei_val = gwei_val.quantize(Decimal("0.1"))
                            self._current_gwei = gwei_val
                            await self._redis.set("onchain:gas:gwei", str(gwei_val), ex=30)
                            return gwei_val
        except Exception as e:
            logger.warning(f"GasTracker: failed to fetch gas price from {self._rpc_url}: {e}")
        return self._current_gwei

    async def start(self) -> None:
        """Start periodic gas tracking background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"GasTracker started (RPC: {self._rpc_url})")

    async def stop(self) -> None:
        """Stop periodic gas tracking loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("GasTracker stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.fetch_gas_price_gwei()
            except Exception as e:
                logger.error(f"GasTracker loop error: {e}")
            await asyncio.sleep(self._poll_interval_s)

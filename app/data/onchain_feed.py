"""OnChainFeed — subscribes to and polls EVM DEX pool prices.

Enforces failure isolation: node disconnections do NOT affect CEX pipeline.
Writes price updates to Redis key 'onchain:price:{pool_address}' (30s TTL).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
from loguru import logger

from app.core import metrics
from app.core.config import get_settings
from app.core.redis_client import RedisClient
from app.data.onchain_normalizer import OnChainNormalizer, OnChainPriceState


# Default major pools (Ethereum mainnet)
DEFAULT_POOLS = [
    {
        "symbol": "ETH/USDT",
        "pool_address": "0x11b815efB8B581d91e513082050220265691f93f",  # Uniswap v3 ETH/USDT 0.05%
        "token0_decimals": 18,  # WETH
        "token1_decimals": 6,   # USDT
        "fee_tier": 500,
    },
    {
        "symbol": "BTC/USDT",
        "pool_address": "0xCBCdBF66710431a4B13bc89f660dE261d7a3A65f",  # Uniswap v3 WBTC/ETH
        "token0_decimals": 8,   # WBTC
        "token1_decimals": 18,  # WETH
        "fee_tier": 3000,
    },
]


class OnChainFeed:
    """Subscriber and scanner for EVM DEX pool states."""

    def __init__(
        self,
        redis_client: RedisClient,
        rpc_url: str | None = None,
        pools: list[dict[str, Any]] | None = None,
        poll_interval_s: float = 15.0,
    ) -> None:
        self._redis = redis_client
        self._rpc_url = rpc_url or get_settings().evm_rpc_url
        self._pools = pools or get_settings().evm_pools or DEFAULT_POOLS
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._latest_prices: dict[str, OnChainPriceState] = {}

    @property
    def latest_prices(self) -> dict[str, OnChainPriceState]:
        return dict(self._latest_prices)

    async def fetch_pool_slot0(self, pool: dict[str, Any]) -> OnChainPriceState | None:
        """Fetch slot0() from Uniswap v3/v4 pool contract via eth_call RPC."""
        # slot0() function selector: 0x3850c7bd
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": pool["pool_address"],
                    "data": "0x3850c7bd",
                },
                "latest",
            ],
            "id": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5.0)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result_hex = data.get("result")
                        if result_hex and isinstance(result_hex, str) and len(result_hex) >= 66:
                            raw_data = result_hex[2:]
                            sqrt_price_x96 = int(raw_data[0:64], 16)
                            tick = int(raw_data[64:128], 16)
                            # Convert signed 24-bit tick if needed
                            if tick >= 2**23:
                                tick -= 2**24

                            dex_price = OnChainNormalizer.sqrt_price_x96_to_price(
                                sqrt_price_x96,
                                pool["token0_decimals"],
                                pool["token1_decimals"],
                            )
                            now_str = datetime.now(timezone.utc).isoformat()
                            state = OnChainPriceState(
                                symbol=pool["symbol"],
                                pool_address=pool["pool_address"],
                                dex_price=dex_price,
                                tick=tick,
                                fee_tier=pool.get("fee_tier", 3000),
                                timestamp_iso=now_str,
                            )
                            self._latest_prices[pool["pool_address"]] = state
                            redis_key = f"onchain:price:{pool['pool_address']}"
                            await self._redis.set(redis_key, state.model_dump_json(), ex=30)
                            symbol_key = f"onchain:symbol:{pool['symbol']}"
                            await self._redis.set(symbol_key, state.model_dump_json(), ex=30)
                            return state
        except Exception as e:
            logger.warning(
                f"OnChainFeed: failed to fetch pool {pool['pool_address']} ({pool['symbol']}): {e}"
            )
        return self._latest_prices.get(pool["pool_address"])

    async def start(self) -> None:
        """Start background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"OnChainFeed started for {len(self._pools)} pools")

    async def stop(self) -> None:
        """Stop background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("OnChainFeed stopped")

    async def _run_loop(self) -> None:
        while self._running:
            for pool in self._pools:
                try:
                    await self.fetch_pool_slot0(pool)
                except Exception as e:
                    logger.error(f"OnChainFeed loop error for {pool.get('symbol')}: {e}")
            await asyncio.sleep(self._poll_interval_s)

"""Volatility Surface Loop.

Publishes BTC/ETH volatility term structure every 30 minutes.
Writes to Redis: karsa:vol_surface:*
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def vol_surface_loop(
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 1800,
) -> None:
    """Publish volatility surface every 30 minutes.

    Args:
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 1800).
    """
    while not shutdown_event.is_set():
        try:
            from app.risk.volatility_surface import VolatilitySurface
            surface = VolatilitySurface()

            # Calculate BTC vol surface
            btc_surface = await surface.calculate("BTC/USDT")
            if btc_surface:
                import json as _json
                await redis_client.set("karsa:vol_surface:btc", _json.dumps(btc_surface))

            # Calculate ETH vol surface
            eth_surface = await surface.calculate("ETH/USDT")
            if eth_surface:
                import json as _json
                await redis_client.set("karsa:vol_surface:eth", _json.dumps(eth_surface))

            # Calculate composite
            composite = await surface.calculate_composite()
            if composite:
                import json as _json
                await redis_client.set("karsa:vol_surface:composite", _json.dumps(composite))

            logger.debug("vol_surface: updated")

        except Exception as e:
            logger.error("vol_surface_loop failed: %s", e)

        await asyncio.sleep(interval_s)

"""Ranking Refresh Loop.

Refreshes strategy ranking every hour by reading recent trades
and computing ranking decision via RankingEngine.

Writes to Redis: karsa:ranking:decision
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ranking_refresh_loop(
    trade_store: Any,
    ranking_engine: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 3600,
) -> None:
    """Refresh strategy ranking every hour.

    Reads recent trades and computes ranking decision.
    Writes to Redis: karsa:ranking:decision

    Args:
        trade_store: TradeStore instance for reading trades.
        ranking_engine: RankingEngine instance for computing ranking.
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            # Get recent trades
            trades = await trade_store.get_recent_trades(count=100)
            if trades:
                # Compute ranking decision
                decision = ranking_engine.evaluate(trades)
                await redis_client.set("karsa:ranking:decision", decision.value)
                logger.debug("ranking_refresh: decision=%s", decision.value)
            else:
                logger.debug("ranking_refresh: no trades, skipping")
        except Exception as e:
            logger.error("ranking_refresh_loop failed: %s", e)

        await asyncio.sleep(interval_s)

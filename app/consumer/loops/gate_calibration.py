"""Gate Calibration Loop.

Calibrates dynamic gate threshold from historical EV every hour.
Uses rolling average of winning trade EVs to set threshold.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def gate_calibration_loop(
    trade_store: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 3600,
) -> None:
    """Calibrate dynamic gate threshold every hour.

    Reads recent trades and computes dynamic threshold from winning trade EVs.
    Writes to Redis: karsa:gate:dynamic_threshold

    Args:
        trade_store: TradeStore instance for reading trades.
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            trades = await trade_store.get_recent_trades(count=100)
            if not trades:
                await asyncio.sleep(interval_s)
                continue

            # Filter to winning trades with EV data
            winning_trades = [
                t for t in trades
                if t.get("realized_pnl", 0) > 0 and t.get("expected_value") is not None
            ]

            if len(winning_trades) < 5:
                logger.debug("gate_calibration: not enough winning trades (%d)", len(winning_trades))
                await asyncio.sleep(interval_s)
                continue

            # Calculate median EV of winning trades
            evs = [t["expected_value"] for t in winning_trades]
            evs.sort()
            median_ev = evs[len(evs) // 2]

            # Set threshold at 80% of median EV
            threshold = max(50.0, min(90.0, median_ev * 0.8))

            # Count winning/total trades
            winning_count = len(winning_trades)
            total_count = len(trades)

            # Write to Redis
            import json as _json
            await redis_client.set(
                "karsa:gate:dynamic_threshold",
                _json.dumps({
                    "threshold": threshold,
                    "median_ev": median_ev,
                    "winning_trades": winning_count,
                    "total_trades": total_count,
                })
            )
            logger.debug(
                "gate_calibration: threshold=%.1f median_ev=%.4f (%d/%d trades)",
                threshold, median_ev, winning_count, total_count,
            )

        except Exception as e:
            logger.error("gate_calibration_loop failed: %s", e)

        await asyncio.sleep(interval_s)

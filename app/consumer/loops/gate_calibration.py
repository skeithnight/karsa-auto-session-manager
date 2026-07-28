"""Gate Calibration Loop.

Calibrates dynamic gate threshold from historical EV every hour.
Uses rolling average of winning trade PnL to set threshold.

Writes to Redis: karsa:gate:dynamic_threshold
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

    Reads recent trades and computes dynamic threshold from winning trade PnL.
    Writes to Redis: karsa:gate:dynamic_threshold

    Args:
        trade_store: TradeStore instance for reading trades.
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            # Get recent trades (TradeStore uses `limit` parameter)
            trades = await trade_store.get_recent_trades(limit=200)
            if not trades or len(trades) < 20:
                logger.debug("gate_calibration: insufficient trades (%d)", len(trades) if trades else 0)
                await asyncio.sleep(interval_s)
                continue

            # Filter to winning trades
            # Note: TradeStore returns `realized_pnl`, not `expected_value`
            # We use realized_pnl as a proxy for EV
            winning_trades = [
                t for t in trades
                if float(t.get("realized_pnl", 0)) > 0
            ]

            if len(winning_trades) < 5:
                logger.debug("gate_calibration: too few winning trades (%d)", len(winning_trades))
                await asyncio.sleep(interval_s)
                continue

            # Calculate median PnL of winning trades (as proxy for EV)
            import statistics
            winning_pnls = [float(t.get("realized_pnl", 0)) for t in winning_trades]
            median_pnl = statistics.median(winning_pnls)

            # Scale to 0-100 range for gate threshold
            # Assuming typical PnL range is -0.1 to 0.1 (10%)
            # Scale: 0.01 PnL → 50 threshold, 0.1 PnL → 90 threshold
            dynamic_threshold = max(50.0, min(90.0, 50.0 + median_pnl * 400))

            # Write to Redis
            import json as _json
            await redis_client.set(
                "karsa:gate:dynamic_threshold",
                _json.dumps({
                    "threshold": round(dynamic_threshold, 2),
                    "median_pnl": round(median_pnl, 6),
                    "winning_trades": len(winning_trades),
                    "total_trades": len(trades),
                })
            )
            logger.info(
                "gate_calibration: threshold=%.1f (median_pnl=%.4f, wins=%d/%d)",
                dynamic_threshold, median_pnl, len(winning_trades), len(trades),
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("gate_calibration_loop failed: %s", e)

        await asyncio.sleep(interval_s)

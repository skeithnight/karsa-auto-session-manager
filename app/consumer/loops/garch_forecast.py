"""GARCH Forecast Loop.

Runs GARCH volatility forecasting every hour.
Writes to Redis: system:garch:forecast
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def garch_forecast_loop(
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 3600,
) -> None:
    """Run GARCH forecast every hour.

    Args:
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            from app.risk.garch_volatility_forecaster import GARCHVolatilityForecaster

            forecaster = GARCHVolatilityForecaster()

            # Get BTC 1H candles
            from app.data.ohlcv_fetcher import OHLCVFetcher
            fetcher = OHLCVFetcher()
            candles = await fetcher.fetch("BTC/USDT", "1h", limit=200)

            if candles:
                import numpy as np
                arr = np.array(candles, dtype=np.float64)
                closes = arr[:, 4]

                # Calculate returns
                returns = np.diff(np.log(closes))

                # Fit GARCH model
                forecast = forecaster.forecast(returns)

                if forecast:
                    # Write to Redis
                    import json as _json
                    await redis_client.set(
                        "system:garch:forecast",
                        _json.dumps({
                            "forecasted_vol": forecast.get("forecasted_vol"),
                            "historical_vol": forecast.get("historical_vol"),
                            "confidence": forecast.get("confidence"),
                        })
                    )
                    logger.debug("garch_forecast: forecasted_vol=%.4f", forecast.get("forecasted_vol", 0))

        except Exception as e:
            logger.error("garch_forecast_loop failed: %s", e)

        await asyncio.sleep(interval_s)

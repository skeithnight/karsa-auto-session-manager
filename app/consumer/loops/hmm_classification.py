"""HMM Classification Loop.

Runs HMM regime classification every hour.
Writes to Redis: system:hmm:regime
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def hmm_classification_loop(
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 3600,
) -> None:
    """Run HMM classification every hour.

    Args:
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            from app.alpha.hmm_regime_classifier import HMMRegimeClassifier
            from app.core.config import get_settings

            settings = get_settings()
            classifier = HMMRegimeClassifier()

            # Get BTC 1H candles
            from app.data.ohlcv_fetcher import OHLCVFetcher
            fetcher = OHLCVFetcher()
            candles = await fetcher.fetch("BTC/USDT", "1h", limit=100)

            if candles:
                import numpy as np
                arr = np.array(candles, dtype=np.float64)
                closes = arr[:, 4]

                # Classify
                result = classifier.classify(closes)

                # Write to Redis
                import json as _json
                await redis_client.set(
                    settings.hmm_redis_key,
                    _json.dumps({
                        "state": result.state,
                        "state_name": result.state_name,
                        "signal": result.signal,
                        "probabilities": result.probabilities,
                        "confidence": result.confidence,
                    })
                )
                logger.debug("hmm_classification: state=%s signal=%s", result.state_name, result.signal)

        except Exception as e:
            logger.error("hmm_classification_loop failed: %s", e)

        await asyncio.sleep(interval_s)

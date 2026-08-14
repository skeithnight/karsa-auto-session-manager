"""MLHarvester — logs feature vectors and signal telemetry for ML dataset curation."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.core.redis_client import RedisClient


class MLHarvester:
    """Harvests signal features and dumps to Redis for offline ML model training."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def log_signal_features(
        self,
        symbol: str,
        side: str,
        regime: str,
        score: float,
        cvd_slope: float = 0.0,
        spread_bps: float = 0.0,
        **extra_features: Any,
    ) -> str:
        """Serialize feature vector and push to Redis feature list."""
        feature_id = f"ML-{uuid.uuid4().hex[:12].upper()}"
        payload = {
            "feature_id": feature_id,
            "symbol": symbol,
            "side": side,
            "regime": regime,
            "score": score,
            "cvd_slope": cvd_slope,
            "spread_bps": spread_bps,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra_features,
        }
        raw = json.dumps(payload)
        await self._redis.set(f"ml:feature:{feature_id}", raw)
        await self._redis.rpush("ml:features:queue", raw)
        logger.debug(f"MLHarvester: recorded feature vector {feature_id} for {symbol}")
        return feature_id

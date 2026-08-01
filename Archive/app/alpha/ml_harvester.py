"""Phase 1 ML Feature Harvester — records feature vectors for signal outcome dataset.

Logs signal feature vectors (CVD slope, spread_bps, session_multiplier, regime, spoofing_flags,
ATR, RSI, final_pnl) to Redis/Postgres for local ML model training (Phase 2 XGBoost).
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger


class MLHarvester:
    """Harvests signal features and outcomes for ML training dataset."""

    def __init__(self, redis_client: Any = None, postgres_pool: Any = None) -> None:
        self._redis = redis_client
        self._db = postgres_pool

    async def log_signal_features(
        self,
        symbol: str,
        side: str,
        regime: str,
        score: float,
        cvd_slope: float = 0.0,
        spread_bps: float = 0.0,
        session_multiplier: float = 1.0,
        spoofing_bid: bool = False,
        spoofing_ask: bool = False,
        atr: float = 0.0,
        rsi: float = 50.0,
        outcome_pnl: float | None = None,
    ) -> str:
        """Log signal feature vector."""
        feature_id = f"ML-{int(time.time()*1000)}-{symbol.replace('/', '')}"
        feature_vector = {
            "feature_id": feature_id,
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "regime": regime,
            "score": score,
            "cvd_slope": cvd_slope,
            "spread_bps": spread_bps,
            "session_multiplier": session_multiplier,
            "spoofing_bid": spoofing_bid,
            "spoofing_ask": spoofing_ask,
            "atr": atr,
            "rsi": rsi,
            "outcome_pnl": outcome_pnl,
        }

        try:
            if self._redis:
                key = f"shadow:ml_features:{feature_id}"
                await self._redis.set(key, json.dumps(feature_vector), ex=86400 * 30)  # 30 day TTL
                await self._redis.rpush("shadow:ml_features_list", key)
            logger.debug(f"MLHarvester: Logged feature vector {feature_id} for {symbol}")
        except Exception as e:
            logger.warning(f"MLHarvester error logging feature vector for {symbol}: {e}")

        return feature_id

    async def record_outcome(self, feature_id: str, final_pnl: float) -> None:
        """Update outcome PnL once virtual/live position closes."""
        try:
            if self._redis:
                key = f"shadow:ml_features:{feature_id}"
                raw = await self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    data["outcome_pnl"] = final_pnl
                    await self._redis.set(key, json.dumps(data), ex=86400 * 30)
                    logger.debug(f"MLHarvester: Recorded outcome PnL={final_pnl:.4f} for {feature_id}")
        except Exception as e:
            logger.warning(f"MLHarvester error recording outcome for {feature_id}: {e}")

"""Dynamic EV Threshold — calibrates gate threshold from historical performance.

Phase 1 (Filter Collapse): Replaces static _GATE_THRESHOLD (75.0) with adaptive
threshold that adjusts based on drawdown, session liquidity, and recent win rate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Bounds
_MIN_THRESHOLD = 0.40
_MAX_THRESHOLD = 0.85
_BASE_THRESHOLD = 0.55  # Much lower than current 75.0 effective gate

# Drawdown adjustments
_DD_SEVERE_PCT = 0.10   # >10% drawdown
_DD_MODERATE_PCT = 0.05  # >5% drawdown
_DD_SEVERE_ADD = 0.15
_DD_MODERATE_ADD = 0.08

# Session adjustments
_SESSION_ADJUSTMENTS = {
    "LDN_NY_OVERLAP": -0.03,  # Best liquidity → more aggressive
    "LDN": 0.0,
    "NY": 0.0,
    "ASIA": 0.05,             # Thin liquidity → more selective
    "PACIFIC": 0.08,          # Dead zone → most selective
    "DEFAULT": 0.0,
}

# Cold streak
_COLD_STREAK_THRESHOLD = 0.35  # Win rate below this → raise threshold
_COLD_STREAK_ADD = 0.10


def _get_session_label(hour_utc: int) -> str:
    """Map UTC hour to session label."""
    if 12 <= hour_utc < 16:
        return "LDN_NY_OVERLAP"
    if 7 <= hour_utc < 13:
        return "LDN"
    if 13 <= hour_utc < 21:
        return "NY"
    if 0 <= hour_utc < 7:
        return "ASIA"
    return "PACIFIC"


class DynamicThreshold:
    """Calibrates EV gate threshold from historical performance.

    Falls back to static _BASE_THRESHOLD if no Redis or history.
    """

    def __init__(self, base_threshold: float = _BASE_THRESHOLD) -> None:
        self._base = base_threshold

    async def get_threshold(
        self,
        redis_client: object | None = None,
        drawdown_pct: float = 0.0,
        recent_win_rate: float = 0.5,
        hour_utc: int | None = None,
    ) -> float:
        """Compute dynamic EV threshold.

        Args:
            redis_client: Optional Redis for cached calibration.
            drawdown_pct: Current account drawdown (0.0 = at peak).
            recent_win_rate: Win rate over last N trades.
            hour_utc: Current UTC hour (default: now).

        Returns:
            Threshold between _MIN_THRESHOLD and _MAX_THRESHOLD.
        """
        # Try Redis-cached threshold first
        if redis_client is not None:
            cached = await self._read_cached(redis_client)
            if cached is not None:
                return cached

        # Compute from parameters
        threshold = self._base

        # Drawdown adjustment
        if drawdown_pct > _DD_SEVERE_PCT:
            threshold += _DD_SEVERE_ADD
            logger.info("Threshold: SEVERE drawdown %.1f%% → +%.2f", drawdown_pct * 100, _DD_SEVERE_ADD)
        elif drawdown_pct > _DD_MODERATE_PCT:
            threshold += _DD_MODERATE_ADD
            logger.info("Threshold: MODERATE drawdown %.1f%% → +%.2f", drawdown_pct * 100, _DD_MODERATE_ADD)

        # Session adjustment
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour
        session = _get_session_label(hour_utc)
        session_adj = _SESSION_ADJUSTMENTS.get(session, 0.0)
        threshold += session_adj

        # Cold streak
        if recent_win_rate < _COLD_STREAK_THRESHOLD:
            threshold += _COLD_STREAK_ADD
            logger.info("Threshold: COLD STREAK (wr=%.2f) → +%.2f", recent_win_rate, _COLD_STREAK_ADD)

        # Clamp
        threshold = max(_MIN_THRESHOLD, min(_MAX_THRESHOLD, threshold))

        # Cache in Redis (1h TTL)
        if redis_client is not None:
            await self._cache(redis_client, threshold)

        return threshold

    async def _read_cached(self, redis_client: object) -> float | None:
        """Read cached threshold from Redis."""
        try:
            import json as _json
            raw = await redis_client.get("karsa:gate:ev_threshold")
            if raw:
                data = _json.loads(raw)
                return data.get("threshold")
        except Exception:
            pass
        return None

    async def _cache(self, redis_client: object, threshold: float) -> None:
        """Cache threshold in Redis (1h TTL)."""
        try:
            import json as _json
            import time
            await redis_client.set(
                "karsa:gate:ev_threshold",
                _json.dumps({
                    "threshold": threshold,
                    "updated_at": time.time(),
                }),
                ex=3600,
            )
        except Exception:
            pass

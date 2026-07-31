"""Asset Calibrator — per-symbol microstructure normalization from rolling data.

Phase 4 (Sizing & Edge): BTC/USDT has $500M daily volume, HEMI/USDT has $2M.
Using the same normalization constants for both is meaningless. This module computes
per-symbol 95th percentiles for skew, lead-lag, funding from rolling 30-day data.

Redis key: karsa:calibration:{symbol} (TTL: 4h)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from loguru import logger

CALIBRATION_TTL = 3600 * 4  # 4 hours


@dataclass
class CalibrationProfile:
    """Per-symbol normalization constants from rolling data."""

    symbol: str
    skew_95pct: float       # 95th percentile of |skew|
    lead_lag_95pct: float   # 95th percentile of |lead_lag_delta|
    funding_95pct: float    # 95th percentile of |funding_rate|
    spread_median: float    # Median spread (for quality scoring)
    atr_median: float       # Median ATR (for volatility normalization)
    volume_24h_avg: float   # Avg 24h volume (for liquidity scoring)
    updated_at: str         # ISO timestamp

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> CalibrationProfile:
        return cls(**json.loads(raw))


# Default profiles for common assets
_DEFAULTS = {
    "BTC/USDT": CalibrationProfile(
        symbol="BTC/USDT", skew_95pct=0.3, lead_lag_95pct=0.0001,
        funding_95pct=0.001, spread_median=0.0001, atr_median=0.015,
        volume_24h_avg=500_000_000, updated_at="",
    ),
    "ETH/USDT": CalibrationProfile(
        symbol="ETH/USDT", skew_95pct=0.4, lead_lag_95pct=0.0002,
        funding_95pct=0.001, spread_median=0.0002, atr_median=0.02,
        volume_24h_avg=200_000_000, updated_at="",
    ),
}

# Generic fallback for unknown symbols
_GENERIC_DEFAULT = CalibrationProfile(
    symbol="", skew_95pct=0.8, lead_lag_95pct=0.0003,
    funding_95pct=0.0005, spread_median=0.001, atr_median=0.01,
    volume_24h_avg=10_000_000, updated_at="",
)


class AssetCalibrator:
    """Computes per-symbol normalization constants from rolling data.

    Usage:
        calibrator = AssetCalibrator(redis_client)
        profile = await calibrator.get_profile("BTC/USDT")
        # Use profile.skew_95pct instead of hardcoded 0.8
    """

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client

    async def get_profile(self, symbol: str) -> CalibrationProfile:
        """Get cached calibration or use defaults."""
        if self._redis is not None:
            try:
                raw = await self._redis.get(f"karsa:calibration:{symbol}")  # type: ignore[attr-defined]
                if raw:
                    data = raw.decode() if isinstance(raw, bytes) else str(raw)
                    return CalibrationProfile.from_json(data)
            except Exception as e:
                logger.debug("Calibration read failed for %s: %s", symbol, e)

        # Return default
        return _DEFAULTS.get(symbol, _GENERIC_DEFAULT)

    async def update_profile(
        self,
        symbol: str,
        skews: list[float] | None = None,
        lead_lags: list[float] | None = None,
        funding_rates: list[float] | None = None,
        spreads: list[float] | None = None,
        atrs: list[float] | None = None,
        volume_24h: float = 0.0,
    ) -> CalibrationProfile:
        """Update calibration profile from collected data."""
        import numpy as np

        now = datetime.now(timezone.utc).isoformat()

        profile = CalibrationProfile(
            symbol=symbol,
            skew_95pct=float(np.percentile(np.abs(skews), 95)) if skews else 0.8,
            lead_lag_95pct=float(np.percentile(np.abs(lead_lags), 95)) if lead_lags else 0.0003,
            funding_95pct=float(np.percentile(np.abs(funding_rates), 95)) if funding_rates else 0.0005,
            spread_median=float(np.median(spreads)) if spreads else 0.001,
            atr_median=float(np.median(atrs)) if atrs else 0.01,
            volume_24h_avg=volume_24h,
            updated_at=now,
        )

        # Cache in Redis
        if self._redis is not None:
            try:
                await self._redis.set(  # type: ignore[attr-defined]
                    f"karsa:calibration:{symbol}",
                    profile.to_json(),
                    ex=CALIBRATION_TTL,
                )
            except Exception as e:
                logger.debug("Calibration write failed for %s: %s", symbol, e)

        return profile

"""Cross-Asset Volatility Surface — term structure and skew analysis.

Builds a volatility surface from historical realized vol across multiple
timeframes (1H, 4H, 1D) for BTC and ETH. Used for:
  - Regime classification input (vol regime detection)
  - Position sizing calibration (high vol = smaller sizes)
  - Spread trade risk evaluation (vol divergence = opportunity)

Redis keys written:
  - karsa:vol_surface:btc — BTC volatility term structure
  - karsa:vol_surface:eth — ETH volatility term structure
  - karsa:vol_surface:spread — BTC-ETH vol spread (divergence signal)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Timeframes for term structure
TIMEFRAMES = {
    "1h": 24,      # 1 day of 1H candles
    "4h": 42,      # 7 days of 4H candles
    "1d": 30,      # 30 days of 1D candles
}


class VolatilitySurface:
    """Builds and maintains a volatility term structure for BTC/ETH.

    Computes realized vol at multiple timeframes and publishes to Redis
    for downstream consumers (DecisionEngine, APM, BacktestEngine).
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._last_update: float = 0.0
        self._surface: dict[str, dict[str, float]] = {}

    def compute_realized_vol(self, closes: np.ndarray, window: int) -> float:
        """Compute annualized realized volatility from close prices.

        Args:
            closes: Array of close prices.
            window: Number of periods to use for vol calculation.

        Returns:
            Annualized volatility (e.g., 0.65 = 65% annualized vol).
        """
        if len(closes) < window + 1:
            return 0.0

        returns = np.diff(np.log(closes[-window - 1:]))
        returns = returns[np.isfinite(returns)]
        if len(returns) < 5:
            return 0.0

        daily_vol = float(np.std(returns))
        # Annualize: multiply by sqrt(periods_per_year)
        annualization = np.sqrt(24 * 365)  # 1H candles
        return daily_vol * annualization

    def build_surface(
        self,
        btc_closes: dict[str, np.ndarray],
        eth_closes: dict[str, np.ndarray],
    ) -> dict[str, dict[str, float]]:
        """Build volatility surface from BTC and ETH close prices.

        Args:
            btc_closes: Dict mapping timeframe to close price arrays.
            eth_closes: Dict mapping timeframe to close price arrays.

        Returns:
            Dict with 'btc', 'eth', 'spread' sub-dicts containing vol at each timeframe.
        """
        surface: dict[str, dict[str, float]] = {"btc": {}, "eth": {}, "spread": {}}

        for tf, lookback in TIMEFRAMES.items():
            btc_closes_tf = btc_closes.get(tf, np.array([]))
            eth_closes_tf = eth_closes.get(tf, np.array([]))

            btc_vol = self.compute_realized_vol(btc_closes_tf, lookback)
            eth_vol = self.compute_realized_vol(eth_closes_tf, lookback)

            surface["btc"][tf] = round(btc_vol, 4)
            surface["eth"][tf] = round(eth_vol, 4)
            surface["spread"][tf] = round(abs(btc_vol - eth_vol), 4)

        # Add composite vol (weighted average across timeframes)
        btc_vols = list(surface["btc"].values())
        eth_vols = list(surface["eth"].values())
        if btc_vols:
            surface["btc"]["composite"] = round(float(np.mean(btc_vols)), 4)
        if eth_vols:
            surface["eth"]["composite"] = round(float(np.mean(eth_vols)), 4)

        self._surface = surface
        self._last_update = time.time()

        return surface

    async def publish_to_redis(self) -> None:
        """Write volatility surface to Redis for downstream consumers."""
        if self._redis is None or not self._surface:
            return

        try:
            payload = json.dumps({
                "surface": self._surface,
                "timestamp": self._last_update,
            })
            await self._redis.set("karsa:vol_surface:composite", payload, ex=1800)

            for asset in ("btc", "eth"):
                if asset in self._surface:
                    await self._redis.set(
                        f"karsa:vol_surface:{asset}",
                        json.dumps(self._surface[asset]),
                        ex=1800,
                    )

            if "spread" in self._surface:
                spread_1h = self._surface["spread"].get("1h", 0)
                spread_4h = self._surface["spread"].get("4h", 0)
                if spread_1h > 0.15 or spread_4h > 0.10:
                    logger.info(
                        "VolSurface: BTC-ETH vol spread elevated "
                        "(1h=%.3f, 4h=%.3f) — potential divergence signal",
                        spread_1h, spread_4h,
                    )
                await self._redis.set(
                    "karsa:vol_surface:spread",
                    json.dumps(self._surface.get("spread", {})),
                    ex=1800,
                )

            logger.debug("VolSurface: published to Redis")

        except Exception:
            logger.debug("VolSurface: Redis publish failed")

    def get_vol_regime(self, asset: str = "btc") -> str:
        """Classify current vol regime from surface data.

        Returns:
            'LOW' (composite vol < 0.40), 'NORMAL' (0.40-0.70), 'HIGH' (>0.70)
        """
        if not self._surface:
            return "NORMAL"

        composite = self._surface.get(asset, {}).get("composite", 0.5)
        if composite < 0.40:
            return "LOW"
        elif composite > 0.70:
            return "HIGH"
        return "NORMAL"

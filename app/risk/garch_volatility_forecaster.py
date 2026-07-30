"""GARCH Volatility Forecasting — Sprint 3 predictive volatility sizing.

Uses GARCH(1,1) model to forecast next-period volatility from historical
1H returns of BTC/USDT (market proxy). The forecasted vol is compared to
historical average vol to produce a volatility targeting multiplier:

  - forecast > 1.5x historical → 0.5x Kelly (high incoming vol)
  - forecast < 0.7x historical → 1.2x Kelly (low incoming vol)
  - otherwise → 1.0x (no adjustment)

Fail-closed: if GARCH fit fails, returns Decimal("1.0") (no adjustment).
"""

from __future__ import annotations

import logging
import time

import numpy as np

logger = logging.getLogger(__name__)

_MIN_RETURNS = 100  # ~4 days of 1H candles


class GARCHVolatilityForecaster:
    """GARCH(1,1) volatility forecaster for position sizing.

    Reads 1H returns from Redis price history, fits GARCH(1,1), and
    produces a volatility targeting multiplier for Kelly sizing.
    """

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client
        self._last_forecast: float | None = None
        self._last_forecast_time: float = 0.0

    async def fit_and_forecast(self, returns: np.ndarray) -> float | None:
        """Fit GARCH(1,1) and forecast next-period annualized volatility.

        Args:
            returns: Array of 1H log returns.

        Returns:
            Annualized volatility forecast (float) or None if fit fails.
        """
        try:
            from arch import arch_model

            if len(returns) < _MIN_RETURNS:
                logger.warning(
                    "GARCH: insufficient returns (%d < %d), skipping",
                    len(returns), _MIN_RETURNS,
                )
                return None

            # Scale returns to percentage (arch library works better with pct)
            scaled_returns = returns * 100.0

            # Remove any NaN/Inf
            mask = np.isfinite(scaled_returns)
            clean_returns = scaled_returns[mask]
            if len(clean_returns) < _MIN_RETURNS:
                logger.warning("GARCH: too few finite returns after filtering (%d)", len(clean_returns))
                return None

            # Fit GARCH(1,1) with constant mean
            model = arch_model(
                clean_returns,
                vol="Garch",
                p=1,
                q=1,
                mean="Constant",
                dist="normal",
            )
            result = model.fit(disp="off", show_warning=False)

            # Forecast next period conditional variance
            forecast = result.forecast(horizon=1)
            next_period_var = forecast.variance.values[-1, 0]

            # Convert to annualized vol (sqrt of variance * sqrt(8760) for 1H candles)
            # 8760 = 24 * 365 hours in a year
            forecasted_vol = float(np.sqrt(next_period_var)) / 100.0  # back to decimal
            annualized_vol = forecasted_vol * np.sqrt(8760)

            # Also calculate historical average vol for comparison
            historical_vol = float(np.std(clean_returns)) / 100.0 * np.sqrt(8760)

            self._last_forecast = annualized_vol
            self._last_forecast_time = time.time()

            logger.info(
                "GARCH: forecasted_annual_vol=%.4f historical_annual_vol=%.4f ratio=%.2f",
                annualized_vol, historical_vol,
                annualized_vol / historical_vol if historical_vol > 0 else 0,
            )

            # Store both for comparison
            self._historical_vol = historical_vol
            return annualized_vol

        except ImportError:
            logger.error("GARCH: arch library not installed")
            return None
        except Exception:
            logger.exception("GARCH: fit_and_forecast failed")
            return None

    def get_volatility_multiplier(
        self, forecasted_vol: float | None = None, historical_vol: float | None = None
    ) -> float:
        """Get the volatility targeting multiplier based on forecast vs historical.

        Args:
            forecasted_vol: Annualized vol forecast (or use last stored).
            historical_vol: Historical average vol (or use last stored).

        Returns:
            Multiplier: 0.5 for high vol, 1.2 for low vol, 1.0 for normal.
        """
        try:
            from decimal import Decimal
            from app.core.config import get_settings

            settings = get_settings()

            f_vol = forecasted_vol if forecasted_vol is not None else self._last_forecast
            h_vol = historical_vol if historical_vol is not None else getattr(self, '_historical_vol', None)

            if f_vol is None or h_vol is None or h_vol <= 0:
                return 1.0

            ratio = f_vol / h_vol

            high_threshold = Decimal(settings.garch_high_vol_threshold)
            low_threshold = Decimal(settings.garch_low_vol_threshold)
            high_mult = Decimal(settings.garch_high_vol_multiplier)
            low_mult = Decimal(settings.garch_low_vol_multiplier)

            if Decimal(str(ratio)) > high_threshold:
                logger.info(
                    "GARCH VOL TARGETING: ratio=%.2f > %s → multiplier=%s (reduce size)",
                    ratio, settings.garch_high_vol_threshold, settings.garch_high_vol_multiplier,
                )
                return float(high_mult)
            elif Decimal(str(ratio)) < low_threshold:
                logger.info(
                    "GARCH VOL TARGETING: ratio=%.2f < %s → multiplier=%s (increase size)",
                    ratio, settings.garch_low_vol_threshold, settings.garch_low_vol_multiplier,
                )
                return float(low_mult)

            return 1.0

        except Exception:
            logger.debug("GARCH: volatility multiplier calculation failed")
            return 1.0

    async def run_forecast_loop(
        self,
        ohlcv_fetcher: object | None = None,
        symbol: str = "BTC/USDT",
        interval_seconds: int = 3600,
    ) -> None:
        """Background task: periodically refit GARCH and update forecast.

        Args:
            ohlcv_fetcher: Object with async fetch(symbol, interval, limit) method.
            symbol: Symbol to fetch candles for.
            interval_seconds: How often to run (default 1h).
        """
        import asyncio

        while True:
            try:
                if ohlcv_fetcher is not None:
                    from app.core.config import get_settings
                    settings = get_settings()
                    limit = settings.garch_rolling_window_days * 24 + 50

                    candles_raw = await ohlcv_fetcher.fetch(symbol, "1h", limit=limit)  # type: ignore[attr-defined]
                    if candles_raw and len(candles_raw) >= _MIN_RETURNS:
                        closes = np.array([c[4] for c in candles_raw], dtype=float)
                        returns = np.diff(np.log(closes[closes > 0]))
                        forecast = await self.fit_and_forecast(returns)

                        # Publish forecast to Redis for decision engine
                        if forecast is not None and self._redis is not None:
                            try:
                                import json as _json
                                payload = _json.dumps({
                                    "forecasted_vol": self._last_forecast,
                                    "historical_vol": getattr(self, '_historical_vol', None),
                                    "timestamp": self._last_forecast_time,
                                })
                                await self._redis.set("system:garch:forecast", payload, ex=3600)
                            except Exception:
                                logger.debug("GARCH: Redis publish failed")

                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GARCH: forecast loop error")
                await asyncio.sleep(30)

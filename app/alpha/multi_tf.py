"""Multi-timeframe confirmation filter.

Checks 4H EMA(20) trend against 1H signal direction.
If they contradict, applies 0.5x confidence penalty.
Graceful degradation: no penalty if 4H data unavailable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from loguru import logger

from prometheus_client import Counter

from app.alpha.ta_tools import calculate_ema
from app.data.ohlcv_fetcher import OHLCVFetcher

multi_tf_penalty_total = Counter(
    "karsa_multi_tf_penalty_applied_total",
    "4H trend contradiction penalties applied",
    ["symbol"],
)


class MultiTFFilter:
    """4H trend confirmation for 1H signals."""

    def __init__(
        self,
        ohlcv_fetcher: OHLCVFetcher,
        ema_period: int = 20,
        penalty: Decimal = Decimal("0.5"),
    ) -> None:
        self.fetcher = ohlcv_fetcher
        self.ema_period = ema_period
        self.penalty = penalty

    async def check(self, symbol: str, direction: Literal["LONG", "SHORT"]) -> dict:
        """Check if 4H trend agrees with signal direction.

        Returns dict with:
            direction_agrees: bool
            ema_4h: Optional[Decimal]
            penalty_applied: Decimal (0.5 if contradicts, 1.0 if agrees)
            data_available: bool
        """
        try:
            candles = await self.fetcher.fetch(
                symbol, timeframe="4h", limit=self.ema_period + 5, ttl_seconds=3600
            )
        except Exception as e:
            logger.warning(f"Multi-TF: failed to fetch 4H for {symbol}: {e}")
            return self._no_data_result()

        if not candles or len(candles) < self.ema_period:
            logger.debug(f"Multi-TF: insufficient 4H data for {symbol} ({len(candles) if candles else 0} candles)")
            return self._no_data_result()

        closes = [Decimal(str(c[4])) for c in candles]  # index 4 = close
        ema_4h = calculate_ema(closes, period=self.ema_period)
        if ema_4h is None:
            return self._no_data_result()

        current_price = closes[-1]
        # LONG agrees if price > EMA, SHORT agrees if price < EMA
        if direction == "LONG":
            agrees = current_price > ema_4h
        else:
            agrees = current_price < ema_4h

        penalty_applied = Decimal("1.0") if agrees else self.penalty

        if not agrees:
            logger.info(f"Multi-TF: {symbol} {direction} contradicts 4H EMA={ema_4h}, penalty={penalty_applied}")
            multi_tf_penalty_total.labels(symbol=symbol).inc()

        return {
            "direction_agrees": agrees,
            "ema_4h": ema_4h,
            "penalty_applied": penalty_applied,
            "data_available": True,
        }

    def _no_data_result(self) -> dict:
        return {
            "direction_agrees": True,  # assume agree when no data (no penalty)
            "ema_4h": None,
            "penalty_applied": Decimal("1.0"),
            "data_available": False,
        }

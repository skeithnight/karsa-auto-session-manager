"""Multi-timeframe confirmation filter.

Checks 4H EMA(20) trend against 1H signal direction.
If they contradict and block_contradictions=True (default), the signal is BLOCKED outright.
If block_contradictions=False, applies 0.5x confidence penalty instead.
Graceful degradation: no block/penalty if 4H data unavailable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from loguru import logger
from prometheus_client import Counter

from app.alpha.ta_tools import calculate_ema
from app.data.ohlcv_fetcher import OHLCVFetcher

multi_tf_penalty_total = Counter(
    "karsa_multi_tf_penalty_applied_total",
    "4H trend contradiction penalties applied",
    ["symbol"],
)

multi_tf_blocked_total = Counter(
    "karsa_multi_tf_blocked_total",
    "4H trend contradiction hard blocks",
    ["symbol"],
)


class MultiTFFilter:
    """4H trend confirmation for 1H signals."""

    def __init__(
        self,
        ohlcv_fetcher: OHLCVFetcher,
        ema_period: int = 20,
        penalty: Decimal = Decimal("0.5"),
        block_contradictions: bool = True,
    ) -> None:
        self.fetcher = ohlcv_fetcher
        self.ema_period = ema_period
        self.penalty = penalty
        self.block_contradictions = block_contradictions

    async def check(self, symbol: str, direction: Literal["LONG", "SHORT"]) -> dict:
        """Check if 4H trend agrees with signal direction.

        Returns dict with:
            direction_agrees: bool
            ema_4h: Optional[Decimal]
            penalty_applied: Decimal (0.5 if contradicts & not blocking, 1.0 if agrees)
            data_available: bool
            blocked: bool — True when 4H contradicts and block_contradictions=True
        """
        try:
            candles = await self.fetcher.fetch(
                symbol, timeframe="4h", limit=self.ema_period + 5, ttl_seconds=3600
            )
        except Exception as e:
            logger.warning(f"Multi-TF: failed to fetch 4H for {symbol}: {e}")
            return self._no_data_result()

        if not candles or len(candles) < self.ema_period:
            logger.debug(
                f"Multi-TF: insufficient 4H data for {symbol} "
                f"({len(candles) if candles else 0} candles)"
            )
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

        if not agrees:
            if self.block_contradictions:
                logger.info(
                    f"Multi-TF BLOCKED: {symbol} {direction} contradicts 4H EMA={ema_4h:.6f} "
                    f"(price={current_price:.6f})"
                )
                multi_tf_blocked_total.labels(symbol=symbol).inc()
                return {
                    "direction_agrees": False,
                    "ema_4h": ema_4h,
                    "penalty_applied": Decimal("0.0"),
                    "data_available": True,
                    "blocked": True,
                }
            else:
                logger.info(
                    f"Multi-TF: {symbol} {direction} contradicts 4H EMA={ema_4h}, penalty={self.penalty}"
                )
                multi_tf_penalty_total.labels(symbol=symbol).inc()

        penalty_applied = Decimal("1.0") if agrees else self.penalty

        return {
            "direction_agrees": agrees,
            "ema_4h": ema_4h,
            "penalty_applied": penalty_applied,
            "data_available": True,
            "blocked": False,
        }

    def _no_data_result(self) -> dict:
        return {
            "direction_agrees": True,  # assume agree when no data (no penalty/block)
            "ema_4h": None,
            "penalty_applied": Decimal("1.0"),
            "data_available": False,
            "blocked": False,
        }

    async def get_macro_anchor_penalty(self, direction: Literal["LONG", "SHORT"], anchors: list[str] | None = None) -> float:
        """Check if macro anchors (e.g. BTC, ETH) confirm the direction.
        
        Returns a penalty factor (e.g. 0.8) if the macro trend contradicts the signal,
        meaning it's a headwind, but doesn't hard-block it if the individual setup is exceptionally strong.
        Returns 1.0 if approved or no data.
        """
        if anchors is None:
            anchors = ["BTC/USDT", "ETH/USDT"]
            
        contradictions = 0
        valid_anchors = 0
        
        for anchor in anchors:
            res = await self.check(anchor, direction)
            if res.get("data_available"):
                valid_anchors += 1
                if not res.get("direction_agrees"):
                    contradictions += 1
                    
        # If >= 50% of anchors contradict, apply a 20% penalty to the score
        if valid_anchors > 0 and contradictions >= (valid_anchors / 2):
            logger.info(f"Macro Anchor Penalty: {contradictions}/{valid_anchors} anchors contradict {direction}. Applying 0.8x penalty.")
            return 0.80
            
        return 1.0


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

macro_momentum_blocks_total = Counter(
    "karsa_macro_momentum_blocks_total",
    "BTC/ETH momentum crash or surge hard blocks",
    ["symbol", "reason"],
)


class MultiTFFilter:
    """4H trend confirmation for 1H signals."""

    def __init__(
        self,
        ohlcv_fetcher: OHLCVFetcher,
        ema_period: int = 20,
        penalty: Decimal = Decimal("0.5"),
        block_contradictions: bool = False,
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
            "blocked": self.block_contradictions,
        }

    async def get_macro_anchor_penalty(
        self, direction: Literal["LONG", "SHORT"], anchors: list[str] | None = None
    ) -> float:
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

        # If >= 50% of anchors contradict, apply a 10% penalty to the score
        if valid_anchors > 0 and contradictions >= (valid_anchors / 2):
            logger.info(
                f"Macro Anchor Penalty: {contradictions}/{valid_anchors} anchors contradict {direction}. Applying 0.9x penalty."
            )
            return 0.90

        return 1.0

    async def check_macro_momentum_block(
        self,
        symbol: str,
        direction: Literal["LONG", "SHORT"],
        anchors: list[str] | None = None,
        crash_threshold_pct: float = -1.0,
    ) -> dict:
        """Hard-block altcoin signals when BTC/ETH are in short-term crash/surge.

        Computes the 1H return (last close vs 4-candles-ago close) for each anchor.
        If BTC/ETH is crashing (return < -1%), all altcoin LONGs are hard-blocked.
        If BTC/ETH is surging (return > +1%), all altcoin SHORTs are hard-blocked.

        Returns:
            dict with: blocked (bool), reason (str), btc_return (float), eth_return (float)
        """
        # Never block BTC/ETH themselves
        if anchors is None:
            anchors = ["BTC/USDT", "ETH/USDT"]
        if symbol in anchors:
            return {"blocked": False, "reason": "is_anchor", "anchor_returns": {}}

        anchor_returns: dict[str, float] = {}
        crash_threshold = crash_threshold_pct / 100.0  # e.g. -1.0% → -0.01
        surge_threshold = abs(crash_threshold)  # +1.0% → +0.01

        for anchor in anchors:
            try:
                candles = await self.fetcher.fetch(
                    anchor, timeframe="1h", limit=5, ttl_seconds=300
                )
                if not candles or len(candles) < 4:
                    continue
                close_now = float(candles[-1][4])
                close_4ago = float(candles[-4][4])
                if close_4ago > 0:
                    ret = (close_now - close_4ago) / close_4ago
                    anchor_returns[anchor] = ret
            except Exception as e:
                logger.debug(f"Macro momentum check failed for {anchor}: {e}")
                continue

        if not anchor_returns:
            return {"blocked": False, "reason": "no_data", "anchor_returns": {}}

        # Check for crash / surge across anchors
        for anchor, ret in anchor_returns.items():
            if direction == "LONG" and ret < crash_threshold:
                logger.warning(
                    f"MACRO MOMENTUM HARD BLOCK: {symbol} LONG blocked — "
                    f"{anchor} is crashing ({ret*100:.2f}% in last 4 candles)"
                )
                macro_momentum_blocks_total.labels(
                    symbol=symbol, reason=f"{anchor}_crash"
                ).inc()
                return {
                    "blocked": True,
                    "reason": f"{anchor}_crash",
                    "anchor_returns": anchor_returns,
                }
            if direction == "SHORT" and ret > surge_threshold:
                logger.warning(
                    f"MACRO MOMENTUM HARD BLOCK: {symbol} SHORT blocked — "
                    f"{anchor} is surging ({ret*100:+.2f}% in last 4 candles)"
                )
                macro_momentum_blocks_total.labels(
                    symbol=symbol, reason=f"{anchor}_surge"
                ).inc()
                return {
                    "blocked": True,
                    "reason": f"{anchor}_surge",
                    "anchor_returns": anchor_returns,
                }

        return {"blocked": False, "reason": "ok", "anchor_returns": anchor_returns}


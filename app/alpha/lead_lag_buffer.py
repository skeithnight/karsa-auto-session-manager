"""Lead-Lag Buffer — tracks price return deltas between lead/lag exchanges.

Binance leads, Bybit lags. Rolling 15-min window per symbol.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

from loguru import logger


class LeadLagBuffer:
    """Rolling 15-min price return buffer per exchange per symbol.

    ponytail: in-process deque, no Redis. Ephemeral working state.
    """

    def __init__(self, window_seconds: int = 900) -> None:
        logger.debug("LeadLagBuffer.__init__: entering")
        self.window_seconds = window_seconds
        # {symbol: {exchange: deque[(timestamp, price)]}}
        self._buffers: dict[str, dict[str, deque[tuple[float, float]]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        logger.debug("LeadLagBuffer.__init__: returning")

    def update(self, symbol: str, exchange: str, price: float) -> None:
        """Record a price tick for a symbol/exchange pair."""
        now = time.time()
        buf = self._buffers[symbol][exchange]
        buf.append((now, price))
        # Evict old entries
        while buf and buf[0][0] < now - self.window_seconds:
            buf.popleft()

    def get_lead_lag_delta(self, symbol: str, lead: str = "binance", lag: str = "bybit") -> Optional[float]:
        """Return lead 15m return minus lag 15m return.

        Positive = lead outperforming → lag likely to catch up.
        None if insufficient data on either side.
        """
        logger.debug(f"get_lead_lag_delta: entering symbol={symbol}")
        lead_ret = self._return(symbol, lead)
        lag_ret = self._return(symbol, lag)

        if lead_ret is None or lag_ret is None:
            logger.debug("get_lead_lag_delta: returning None (insufficient data)")
            return None

        delta = lead_ret - lag_ret
        logger.debug(f"get_lead_lag_delta: returning {delta:.6f}")
        return delta

    def _return(self, symbol: str, exchange: str) -> Optional[float]:
        """15-min return for a symbol/exchange pair."""
        buf = self._buffers[symbol][exchange]
        if len(buf) < 2:
            return None
        old_price = buf[0][1]
        new_price = buf[-1][1]
        if old_price == 0:
            return None
        return (new_price - old_price) / old_price

    def clear(self) -> None:
        self._buffers.clear()

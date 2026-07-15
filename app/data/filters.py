"""Bad Tick Filter — reject price spikes >5% in <1s."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict

from loguru import logger

from app.data.normalizer import ExchangeData


class BadTickFilter:
    """Filters out obvious exchange API glitches."""

    def __init__(self, max_price_change_pct: Decimal = Decimal("0.05")) -> None:
        logger.debug("BadTickFilter.__init__: entering")
        self.max_price_change_pct = max_price_change_pct
        self.last_prices: Dict[str, Decimal] = {}
        self.last_timestamps: Dict[str, datetime] = {}
        logger.debug("BadTickFilter.__init__: returning")

    def is_bad_tick(self, data: ExchangeData) -> bool:
        """Check if a tick is a bad tick (price spike >5% in <1s)."""
        logger.debug(f"is_bad_tick: entering exchange={data.exchange} symbol={data.symbol}")
        if data.last_price is None:
            logger.debug("is_bad_tick: returning False (no last_price)")
            return False

        key = f"{data.exchange}:{data.symbol}"
        last_price = self.last_prices.get(key)
        last_ts = self.last_timestamps.get(key)

        if last_price is None or last_ts is None:
            # First tick — record and accept
            self.last_prices[key] = data.last_price
            self.last_timestamps[key] = data.timestamp
            logger.debug("is_bad_tick: returning False (first tick)")
            return False

        # Calculate price change percentage
        price_change = abs(data.last_price - last_price) / last_price
        time_delta = (data.timestamp - last_ts).total_seconds()

        # Reject if price changed >5% in <1 second
        if price_change > self.max_price_change_pct and time_delta < 1.0:
            logger.warning(
                f"Bad tick rejected: {key} price changed {price_change:.2%} in {time_delta:.3f}s"
            )
            logger.debug("is_bad_tick: returning True (rejected)")
            return True

        # Update tracking
        self.last_prices[key] = data.last_price
        self.last_timestamps[key] = data.timestamp
        logger.debug("is_bad_tick: returning False (within threshold)")
        return False

    def filter_orderbook(self, data: ExchangeData) -> ExchangeData:
        """Filter orderbook data — mark as stale if bad tick detected."""
        logger.debug(f"filter_orderbook: entering exchange={data.exchange}")
        if self.is_bad_tick(data):
            data.is_stale = True
        logger.debug("filter_orderbook: returning ExchangeData")
        return data

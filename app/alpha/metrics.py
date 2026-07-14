"""Alpha Metrics — VWAP, Order Book Skew, Lead-Lag, Funding Rate, Open Interest."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger


def calculate_vwap(prices: List[Decimal], volumes: List[Decimal]) -> Optional[Decimal]:
    """Calculate Volume Weighted Average Price."""
    logger.debug("calculate_vwap: entering")
    if not prices or not volumes or len(prices) != len(volumes):
        logger.debug("calculate_vwap: returning None (empty or mismatched)")
        return None

    total_volume = sum(volumes)
    if total_volume == 0:
        logger.debug("calculate_vwap: returning None (zero volume)")
        return None

    weighted_sum = sum(p * v for p, v in zip(prices, volumes))
    result = weighted_sum / total_volume
    logger.debug(f"calculate_vwap: returning Decimal")
    return result


def calculate_skew(bid_volume: Decimal, ask_volume: Decimal) -> Decimal:
    """
    Calculate order book skew.

    Returns Decimal between -1.0 (all asks) and 1.0 (all bids).
    Positive = buy pressure, Negative = sell pressure.
    """
    logger.debug("calculate_skew: entering")
    total = bid_volume + ask_volume
    if total == 0:
        logger.debug("calculate_skew: returning Decimal 0 (zero total)")
        return Decimal("0")

    result = (bid_volume - ask_volume) / total
    logger.debug("calculate_skew: returning Decimal")
    return result


def calculate_lead_lag(
    reference_price: Decimal,
    follower_price: Decimal,
    window_seconds: int = 900,
) -> Optional[Decimal]:
    """
    Calculate lead-lag between two exchanges.

    Returns price difference (reference - follower).
    Positive = reference leads upward.
    """
    logger.debug("calculate_lead_lag: entering")
    if reference_price == 0:
        logger.debug("calculate_lead_lag: returning None (zero reference)")
        return None

    result = reference_price - follower_price
    logger.debug(f"calculate_lead_lag: returning Decimal diff={result}")
    return result


class AlphaMetrics:
    """Aggregates market data into alpha signals."""

    def __init__(self, lead_exchange: str = "binance", lag_exchange: str = "bybit", exchange: Any = None) -> None:
        logger.debug("AlphaMetrics.__init__: entering")
        self.lead_exchange = lead_exchange
        self.lag_exchange = lag_exchange
        self.exchange = exchange
        self.price_history: Dict[str, List[Decimal]] = {}
        self.volume_history: Dict[str, List[Decimal]] = {}
        self._funding_cache: dict[str, tuple[float, Decimal]] = {}
        self._oi_cache: dict[str, tuple[float, Decimal]] = {}
        logger.debug("AlphaMetrics.__init__: returning")

    def update(self, exchange: str, price: Decimal, volume: Decimal) -> None:
        """Add price/volume observation."""
        logger.debug(f"update: entering exchange={exchange}")
        key = exchange
        if key not in self.price_history:
            self.price_history[key] = []
            self.volume_history[key] = []

        self.price_history[key].append(price)
        self.volume_history[key].append(volume)

        # Keep last 100 observations
        if len(self.price_history[key]) > 100:
            self.price_history[key] = self.price_history[key][-100:]
            self.volume_history[key] = self.volume_history[key][-100:]
        logger.debug("update: returning None")

    def get_vwap(self, exchange: str) -> Optional[Decimal]:
        """Get VWAP for an exchange."""
        logger.debug(f"get_vwap: entering exchange={exchange}")
        prices = self.price_history.get(exchange, [])
        volumes = self.volume_history.get(exchange, [])
        result = calculate_vwap(prices, volumes)
        logger.debug(f"get_vwap: returning result_type={type(result).__name__}")
        return result

    def get_skew(self, bid_volume: Decimal, ask_volume: Decimal) -> Decimal:
        """Get order book skew."""
        logger.debug("get_skew: entering")
        result = calculate_skew(bid_volume, ask_volume)
        logger.debug("get_skew: returning Decimal")
        return result

    def get_lead_lag(self) -> Optional[Decimal]:
        """Get lead-lag between lead and lag exchanges."""
        logger.debug("get_lead_lag: entering")
        lead_prices = self.price_history.get(self.lead_exchange, [])
        lag_prices = self.price_history.get(self.lag_exchange, [])

        if not lead_prices or not lag_prices:
            logger.debug("get_lead_lag: returning None (no prices)")
            return None

        result = calculate_lead_lag(lead_prices[-1], lag_prices[-1])
        logger.debug(f"get_lead_lag: returning result_type={type(result).__name__}")
        return result

    # --- Funding Rate (Phase 2B) ---

    async def get_funding_rate(self, symbol: str) -> Optional[Decimal]:
        """Fetch funding rate from Bybit. Cached 5 min. Contrarian signal."""
        logger.debug(f"get_funding_rate: entering symbol={symbol}")
        now = time.time()
        if symbol in self._funding_cache:
            cached_time, cached_val = self._funding_cache[symbol]
            if now - cached_time < 300:
                logger.debug(f"get_funding_rate: cache hit {cached_val}")
                return cached_val

        if not self.exchange:
            return None

        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            rate = Decimal(str(funding.get("fundingRate", 0)))
            self._funding_cache[symbol] = (now, rate)
            logger.info(f"Funding rate {symbol}: {rate}")
            logger.debug(f"get_funding_rate: returning {rate}")
            return rate
        except Exception as e:
            logger.error(f"get_funding_rate failed: {symbol}: {e}")
            return None

    async def get_open_interest(self, symbol: str) -> Optional[Decimal]:
        """Fetch open interest from Bybit. Cached 5 min."""
        logger.debug(f"get_open_interest: entering symbol={symbol}")
        now = time.time()
        if symbol in self._oi_cache:
            cached_time, cached_val = self._oi_cache[symbol]
            if now - cached_time < 300:
                logger.debug(f"get_open_interest: cache hit {cached_val}")
                return cached_val

        if not self.exchange:
            return None

        try:
            oi = await self.exchange.fetch_open_interest(symbol)
            val = Decimal(str(oi.get("openInterestAmount", 0)))
            self._oi_cache[symbol] = (now, val)
            logger.info(f"Open interest {symbol}: {val}")
            logger.debug(f"get_open_interest: returning {val}")
            return val
        except Exception as e:
            logger.error(f"get_open_interest failed: {symbol}: {e}")
            return None

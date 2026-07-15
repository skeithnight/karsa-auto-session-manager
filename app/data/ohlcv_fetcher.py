"""OHLCV Fetcher — cached REST fetcher for candle data via ccxt."""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger


class OHLCVFetcher:
    """Fetches and caches OHLCV candle data via ccxt REST (not WebSocket).

    Used by RegimeEngine (BTC 1H) and signal components (per-symbol 1H for ATR/funding).
    ponytail: in-memory TTL cache, not Redis — transient working state.
    """

    def __init__(self, exchange: Any, default_ttl_seconds: int = 300) -> None:
        logger.debug("OHLCVFetcher.__init__: entering")
        self.exchange = exchange
        self.default_ttl_seconds = default_ttl_seconds
        self._cache: dict[str, tuple[float, list]] = {}
        logger.debug("OHLCVFetcher.__init__: returning")

    def _cache_key(self, symbol: str, timeframe: str, limit: int) -> str:
        return f"{symbol}:{timeframe}:{limit}"

    async def fetch(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
        ttl_seconds: Optional[int] = None,
    ) -> list[list]:
        """Fetch OHLCV candles. Returns list of [timestamp, open, high, low, close, volume].

        Cached in-memory with TTL to avoid excessive REST calls.
        """
        logger.debug(f"fetch: entering symbol={symbol} timeframe={timeframe} limit={limit}")
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        key = self._cache_key(symbol, timeframe, limit)

        # Check cache
        if key in self._cache:
            cached_time, cached_data = self._cache[key]
            if time.time() - cached_time < ttl:
                logger.debug(f"fetch: cache hit for {key}")
                return cached_data

        # Fetch from exchange
        try:
            target = symbol
            if hasattr(self.exchange, "markets") and self.exchange.markets:
                if symbol not in self.exchange.markets:
                    swap_symbol = f"{symbol}:USDT"
                    if swap_symbol in self.exchange.markets:
                        target = swap_symbol
            candles = await self.exchange.fetch_ohlcv(target, timeframe, limit=limit)
            self._cache[key] = (time.time(), candles)
            logger.info(f"OHLCV fetched: {symbol} {timeframe} {len(candles)} candles")
            logger.debug(f"fetch: returning list_len={len(candles)}")
            return candles
        except Exception as e:
            logger.error(f"OHLCV fetch failed: {symbol} {timeframe}: {e}")
            logger.debug(f"fetch: error={e}")
            # Return stale cache if available
            if key in self._cache:
                logger.warning(f"Returning stale OHLCV cache for {key}")
                return self._cache[key][1]
            return []

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        logger.debug("clear_cache: cache cleared")

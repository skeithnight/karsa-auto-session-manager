"""Tests for OHLCV Fetcher."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.ohlcv_fetcher import OHLCVFetcher


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        [1000000, 100.0, 105.0, 95.0, 102.0, 1000.0],
        [1000001, 102.0, 108.0, 100.0, 106.0, 1200.0],
    ])
    return exchange


@pytest.fixture
def fetcher(mock_exchange):
    return OHLCVFetcher(mock_exchange, default_ttl_seconds=5)


class TestOHLCVFetcher:
    @pytest.mark.asyncio
    async def test_fetch_returns_candles(self, fetcher):
        candles = await fetcher.fetch("BTC/USDT", "1h", 200)
        assert len(candles) == 2
        assert candles[0][4] == 102.0

    @pytest.mark.asyncio
    async def test_cache_hit(self, fetcher, mock_exchange):
        await fetcher.fetch("BTC/USDT", "1h", 200)
        await fetcher.fetch("BTC/USDT", "1h", 200)
        mock_exchange.fetch_ohlcv.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expired(self, fetcher, mock_exchange):
        await fetcher.fetch("BTC/USDT", "1h", 200, ttl_seconds=0)
        await fetcher.fetch("BTC/USDT", "1h", 200, ttl_seconds=0)
        assert mock_exchange.fetch_ohlcv.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_error_returns_stale(self, fetcher, mock_exchange):
        await fetcher.fetch("BTC/USDT", "1h", 200)
        mock_exchange.fetch_ohlcv.side_effect = Exception("network error")
        candles = await fetcher.fetch("BTC/USDT", "1h", 200, ttl_seconds=0)
        assert len(candles) == 2

    @pytest.mark.asyncio
    async def test_fetch_error_no_cache(self, mock_exchange):
        mock_exchange.fetch_ohlcv.side_effect = Exception("network error")
        fetcher = OHLCVFetcher(mock_exchange)
        candles = await fetcher.fetch("BTC/USDT", "1h", 200)
        assert candles == []

    def test_clear_cache(self, fetcher):
        fetcher._cache["key"] = (time.time(), [[1, 2, 3]])
        fetcher.clear_cache()
        assert len(fetcher._cache) == 0

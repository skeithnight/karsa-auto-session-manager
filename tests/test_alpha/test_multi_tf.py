"""Tests for MultiTFFilter — 4H trend confirmation filter."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.alpha.multi_tf import MultiTFFilter


def _make_candles(closes: list[float]) -> list[list]:
    """Build fake 4H OHLCV candles: [timestamp_ms, open, high, low, close, volume]."""
    candles = []
    for i, c in enumerate(closes):
        candles.append([1700000000000 + i * 14400000, c - 1, c + 1, c - 2, c, 1000.0])
    return candles


class TestMultiTFFilter:
    def setup_method(self):
        self.fetcher = AsyncMock()
        self.filter = MultiTFFilter(self.fetcher)

    @pytest.mark.asyncio
    async def test_long_agrees_above_ema(self):
        """Rising prices, LONG -> agrees=True, penalty=1.0."""
        closes = [float(100 + i) for i in range(25)]
        self.fetcher.fetch.return_value = _make_candles(closes)

        result = await self.filter.check("BTCUSDT", "LONG")

        assert result["direction_agrees"] is True
        assert result["penalty_applied"] == Decimal("1.0")
        assert result["data_available"] is True
        assert result["ema_4h"] is not None

    @pytest.mark.asyncio
    async def test_long_disagrees_below_ema(self):
        """Falling prices, LONG -> agrees=False, penalty=0.5."""
        closes = [float(200 - i) for i in range(25)]
        self.fetcher.fetch.return_value = _make_candles(closes)

        result = await self.filter.check("BTCUSDT", "LONG")

        assert result["direction_agrees"] is False
        assert result["penalty_applied"] == Decimal("0.5")
        assert result["data_available"] is True
        assert result["ema_4h"] is not None

    @pytest.mark.asyncio
    async def test_short_agrees_below_ema(self):
        """Falling prices, SHORT -> agrees=True, penalty=1.0."""
        closes = [float(200 - i) for i in range(25)]
        self.fetcher.fetch.return_value = _make_candles(closes)

        result = await self.filter.check("ETHUSDT", "SHORT")

        assert result["direction_agrees"] is True
        assert result["penalty_applied"] == Decimal("1.0")
        assert result["data_available"] is True

    @pytest.mark.asyncio
    async def test_short_disagrees_above_ema(self):
        """Rising prices, SHORT -> agrees=False, penalty=0.5."""
        closes = [float(100 + i) for i in range(25)]
        self.fetcher.fetch.return_value = _make_candles(closes)

        result = await self.filter.check("ETHUSDT", "SHORT")

        assert result["direction_agrees"] is False
        assert result["penalty_applied"] == Decimal("0.5")
        assert result["data_available"] is True

    @pytest.mark.asyncio
    async def test_insufficient_data_no_penalty(self):
        """Fewer than 20 candles -> penalty=1.0, data_available=False."""
        closes = [float(100 + i) for i in range(15)]
        self.fetcher.fetch.return_value = _make_candles(closes)

        result = await self.filter.check("BTCUSDT", "LONG")

        assert result["direction_agrees"] is True
        assert result["penalty_applied"] == Decimal("1.0")
        assert result["data_available"] is False
        assert result["ema_4h"] is None

    @pytest.mark.asyncio
    async def test_fetcher_error_no_penalty(self):
        """Fetcher raises exception -> penalty=1.0, data_available=False."""
        self.fetcher.fetch.side_effect = ConnectionError("network down")

        result = await self.filter.check("BTCUSDT", "LONG")

        assert result["direction_agrees"] is True
        assert result["penalty_applied"] == Decimal("1.0")
        assert result["data_available"] is False
        assert result["ema_4h"] is None

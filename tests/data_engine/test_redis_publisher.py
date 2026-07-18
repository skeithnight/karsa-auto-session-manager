"""Tests for app.data_engine.redis_publisher.

Mock Redis to verify channel naming convention and JSON payload structure
with strict Decimal-to-string serialization.
"""

from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.data_engine.redis_publisher import (
    DecimalEncoder,
    RedisPublisher,
    _normalize_ohlcv,
)


class TestDecimalEncoder:
    def test_decimal_to_string(self) -> None:
        assert json.dumps({"price": Decimal("100.123")}, cls=DecimalEncoder) == '{"price": "100.123"}'

    def test_datetime_to_iso(self) -> None:
        from datetime import datetime

        dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert json.dumps({"ts": dt}, cls=DecimalEncoder) == '{"ts": "2025-01-15T12:00:00+00:00"}'

    def test_int_passthrough(self) -> None:
        assert json.dumps({"x": 42}, cls=DecimalEncoder) == '{"x": 42}'


class TestNormalizeOhlcv:
    def test_basic_conversion(self) -> None:
        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        result = _normalize_ohlcv(candle, "BTC/USDT", "1h", "bybit")

        assert result["exchange"] == "bybit"
        assert result["symbol"] == "BTC/USDT"
        assert result["timeframe"] == "1h"
        assert result["open"] == "100.0"
        assert result["high"] == "102.0"
        assert result["low"] == "99.0"
        assert result["close"] == "101.0"
        assert result["volume"] == "1000.0"

    def test_precision_preserved(self) -> None:
        candle = [1700000000000, 99999.12345678, 100000.0, 99000.0, 99500.0, 12345.67]
        result = _normalize_ohlcv(candle, "BTC/USDT", "1h", "binance")

        assert result["open"] == "99999.12345678"
        assert result["close"] == "99500.0"

    def test_timestamp_is_epoch_ms(self) -> None:
        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        result = _normalize_ohlcv(candle, "BTC/USDT", "1h", "okx")
        assert result["ts"] == 1700000000000


class TestRedisPublisher:
    @pytest.mark.asyncio
    async def test_publish_candle_channel_naming(self) -> None:
        mock_redis = AsyncMock()
        publisher = RedisPublisher(mock_redis)

        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        await publisher.publish_candle("bybit", "BTC/USDT", "1h", candle)

        mock_redis.publish.assert_awaited_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == "karsa:candles:bybit:BTCUSDT:1h"

    @pytest.mark.asyncio
    async def test_publish_candle_payload_is_valid_json(self) -> None:
        mock_redis = AsyncMock()
        publisher = RedisPublisher(mock_redis)

        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        await publisher.publish_candle("binance", "ETH/USDT", "15m", candle)

        json_str = mock_redis.publish.call_args[0][1]
        payload = json.loads(json_str)

        assert payload["exchange"] == "binance"
        assert payload["symbol"] == "ETH/USDT"
        assert payload["timeframe"] == "15m"
        # All prices are strings (Decimal serialized)
        assert isinstance(payload["open"], str)
        assert isinstance(payload["close"], str)

    @pytest.mark.asyncio
    async def test_publish_candles_returns_count(self) -> None:
        mock_redis = AsyncMock()
        publisher = RedisPublisher(mock_redis)

        candles = [
            [1700000000000 + i * 3600000, 100.0 + i, 102.0, 99.0, 101.0, 1000.0]
            for i in range(5)
        ]
        count = await publisher.publish_candles("okx", "SOL/USDT", "1h", candles)
        assert count == 5
        assert mock_redis.publish.call_count == 5

    @pytest.mark.asyncio
    async def test_publish_candle_channel_slash_stripped(self) -> None:
        mock_redis = AsyncMock()
        publisher = RedisPublisher(mock_redis)

        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        await publisher.publish_candle("bybit", "BTC/USDT", "4h", candle)

        channel = mock_redis.publish.call_args[0][0]
        assert "/" not in channel
        assert "BTCUSDT" in channel

    @pytest.mark.asyncio
    async def test_publish_empty_candles(self) -> None:
        mock_redis = AsyncMock()
        publisher = RedisPublisher(mock_redis)

        count = await publisher.publish_candles("bybit", "BTC/USDT", "1h", [])
        assert count == 0
        mock_redis.publish.assert_not_awaited()

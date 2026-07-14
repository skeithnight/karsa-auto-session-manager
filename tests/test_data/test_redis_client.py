"""Tests for Redis Client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal

import pytest

from app.core.redis_client import RedisClient, DecimalEncoder


@pytest.fixture
def mock_settings():
    """Mock get_settings to avoid requiring real env vars."""
    with patch("app.core.redis_client.get_settings") as mock:
        settings = MagicMock()
        settings.redis_url = "redis://localhost:6379/0"
        mock.return_value = settings
        yield


@pytest.fixture
def client(mock_settings):
    """Create RedisClient with mocked settings and mock redis."""
    c = RedisClient()
    c.redis = AsyncMock()
    return c


class TestDecimalEncoder:
    """Test suite for DecimalEncoder."""

    def test_encode_decimal(self) -> None:
        encoder = DecimalEncoder()
        result = encoder.default(Decimal("64000.50"))
        assert result == "64000.50"

    def test_encode_non_decimal(self) -> None:
        encoder = DecimalEncoder()
        with pytest.raises(TypeError):
            encoder.default("not a decimal")


class TestRedisClient:
    """Test suite for RedisClient class."""

    @pytest.mark.asyncio
    async def test_connect(self, client: RedisClient) -> None:
        with patch("app.core.redis_client.aioredis.from_url") as mock_from_url:
            mock_redis = AsyncMock()
            mock_from_url.return_value = mock_redis
            await client.connect()
            assert client.redis is mock_redis

    @pytest.mark.asyncio
    async def test_disconnect(self, client: RedisClient) -> None:
        await client.disconnect()
        client.redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_global_state(self, client: RedisClient) -> None:
        state = {"symbol": "BTC/USDT:USDT", "price": "64000"}
        await client.set_global_state("BTC/USDT:USDT", state)
        client.redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_global_state(self, client: RedisClient) -> None:
        client.redis.get.return_value = '{"symbol": "BTC/USDT:USDT", "price": "64000"}'
        result = await client.get_global_state("BTC/USDT:USDT")
        assert result is not None
        assert result["symbol"] == "BTC/USDT:USDT"

    @pytest.mark.asyncio
    async def test_get_global_state_missing(self, client: RedisClient) -> None:
        client.redis.get.return_value = None
        result = await client.get_global_state("BTC/USDT:USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_heartbeat(self, client: RedisClient) -> None:
        await client.set_heartbeat()
        client.redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_heartbeat(self, client: RedisClient) -> None:
        client.redis.get.return_value = "2024-01-15T14:30:00+00:00"
        result = await client.get_heartbeat()
        assert result == "2024-01-15T14:30:00+00:00"

    @pytest.mark.asyncio
    async def test_set_circuit_breaker(self, client: RedisClient) -> None:
        await client.set_circuit_breaker("TRIGGERED", "Drawdown exceeded")
        client.redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_circuit_breaker(self, client: RedisClient) -> None:
        client.redis.get.return_value = json.dumps({"status": "ACTIVE", "reason": None})
        result = await client.get_circuit_breaker()
        assert result is not None
        assert result["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_not_connected_raises(self, mock_settings) -> None:
        c = RedisClient()
        c.redis = None
        with pytest.raises(RuntimeError, match="Redis not connected"):
            await c.set_heartbeat()

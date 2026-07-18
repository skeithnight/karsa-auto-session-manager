"""Tests for Crypto Analyst."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.analyst import CryptoAnalyst


@pytest.fixture
def mock_ai():
    client = MagicMock()
    client.model = "claude-haiku-3-5"
    client.complete = AsyncMock()
    return client


@pytest.fixture
def mock_fetcher():
    fetcher = MagicMock()
    fetcher.fetch = AsyncMock()
    return fetcher


@pytest.fixture
def mock_redis():
    client = MagicMock()
    client.get_ai_cache = AsyncMock(return_value=None)
    client.set_ai_cache = AsyncMock()
    return client


@pytest.fixture
def analyst(mock_ai, mock_fetcher):
    return CryptoAnalyst(mock_ai, mock_fetcher, cache_ttl=300)


class TestCryptoAnalyst:
    @pytest.mark.asyncio
    async def test_analyze_returns_result(self, analyst, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "direction": "LONG", "confidence": 75, "reasoning": "strong trend"
        })
        candles = [[i * 3600000, 100.0, 105.0, 95.0, 100.0 + (i % 10), 1000.0] for i in range(200)]
        analyst.fetcher.fetch = AsyncMock(return_value=candles)

        result = await analyst.analyze(
            symbol="BTC/USDT", direction="LONG", confidence=0.70,
            regime="TREND_BULL", spread_pct=0.001, funding_rate=0.0001,
            oi_change=0.05, price=Decimal("50000"),
        )
        assert result is not None
        assert result.direction == "LONG"
        assert result.ai_confidence == 75

    @pytest.mark.asyncio
    async def test_cache_hit(self, analyst, mock_ai, mock_redis):
        mock_ai.complete.return_value = json.dumps({
            "direction": "SHORT", "confidence": 60, "reasoning": "reversal"
        })
        candles = [[i * 3600000, 100.0, 105.0, 95.0, 100.0, 1000.0] for i in range(200)]
        analyst.fetcher.fetch = AsyncMock(return_value=candles)
        analyst.redis = mock_redis

        await analyst.analyze("ETH/USDT", "SHORT", 0.60, "TREND_BEAR", 0.001, 0.0, 0.0, Decimal("3000"))
        # Second call hits Redis cache
        mock_redis.get_ai_cache.return_value = {"direction": "SHORT", "ai_confidence": 60, "reasoning": "reversal", "model_used": "test"}
        await analyst.analyze("ETH/USDT", "SHORT", 0.60, "TREND_BEAR", 0.001, 0.0, 0.0, Decimal("3000"))
        # AI called once (first call), cache hit on second
        analyst.ai_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_ai_unavailable_returns_none(self, analyst, mock_ai):
        mock_ai.complete.return_value = None
        candles = [[i * 3600000, 100.0, 105.0, 95.0, 100.0, 1000.0] for i in range(200)]
        analyst.fetcher.fetch = AsyncMock(return_value=candles)

        result = await analyst.analyze("BTC/USDT", "LONG", 0.70, "TREND_BULL", 0.001, 0.0, 0.0, Decimal("50000"))
        assert result is None

    @pytest.mark.asyncio
    async def test_insufficient_candles(self, analyst):
        analyst.fetcher.fetch = AsyncMock(return_value=[[0, 100, 105, 95, 100, 1000]] * 10)
        result = await analyst.analyze("BTC/USDT", "LONG", 0.70, "TREND_BULL", 0.001, 0.0, 0.0, Decimal("50000"))
        assert result is None

    @pytest.mark.asyncio
    async def test_parse_failure_returns_flat(self, analyst, mock_ai):
        """Unparseable AI text falls back to FLAT direction via free-form regex."""
        mock_ai.complete.return_value = "not json at all"
        candles = [[i * 3600000, 100.0, 105.0, 95.0, 100.0, 1000.0] for i in range(200)]
        analyst.fetcher.fetch = AsyncMock(return_value=candles)

        result = await analyst.analyze("BTC/USDT", "LONG", 0.70, "TREND_BULL", 0.001, 0.0, 0.0, Decimal("50000"))
        assert result is not None
        assert result.direction == "FLAT"

    def test_parse_json_with_markdown_fences(self, analyst):
        result = analyst._parse_response('```json\n{"direction": "FLAT", "confidence": 50, "reasoning": "test"}\n```')
        assert result is not None
        assert result.direction == "FLAT"

    def test_parse_invalid_direction_defaults_flat(self, analyst):
        result = analyst._parse_response('{"direction": "INVALID", "confidence": 50, "reasoning": "test"}')
        assert result.direction == "FLAT"

    def test_parse_confidence_clamped(self, analyst):
        result = analyst._parse_response('{"direction": "LONG", "confidence": 150, "reasoning": "test"}')
        assert result.ai_confidence == 100

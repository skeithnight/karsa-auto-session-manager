"""Tests for TradeMemory."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.trade_memory import TradeMemory, MAX_ENTRIES_PER_SYMBOL, RETRIEVE_COUNT


class TestTradeMemory:
    def setup_method(self):
        self.mock_redis_client = MagicMock()
        self.mock_redis_client.redis = MagicMock()
        self.mock_redis_client.redis.zadd = AsyncMock()
        self.mock_redis_client.redis.zremrangebyrank = AsyncMock()
        self.mock_redis_client.redis.zrevrange = AsyncMock()
        self.memory = TradeMemory(self.mock_redis_client)

    @pytest.mark.asyncio
    async def test_store_writes_to_redis(self):
        await self.memory.store(
            symbol="BTCUSDT",
            pnl_pct=Decimal("1.5"),
            hold_duration_min=30,
            regime="trending",
            exit_reason="tp_hit",
            entry_confidence=Decimal("0.85"),
        )

        self.mock_redis_client.redis.zadd.assert_awaited_once()
        call_args = self.mock_redis_client.redis.zadd.call_args
        assert call_args[0][0] == "karsa:memory:BTCUSDT"

        payload = call_args[0][1]
        key = list(payload.keys())[0]
        entry = json.loads(key)
        assert entry["pnl_pct"] == 1.5
        assert entry["hold_min"] == 30
        assert entry["regime"] == "trending"
        assert entry["exit"] == "tp_hit"
        assert entry["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_store_fifo_eviction(self):
        await self.memory.store(
            symbol="BTCUSDT",
            pnl_pct=Decimal("1.0"),
            hold_duration_min=10,
            regime="trending",
            exit_reason="tp_hit",
            entry_confidence=Decimal("0.9"),
        )

        self.mock_redis_client.redis.zremrangebyrank.assert_awaited_once_with(
            "karsa:memory:BTCUSDT", 0, -(MAX_ENTRIES_PER_SYMBOL + 1)
        )

    @pytest.mark.asyncio
    async def test_get_recent_returns_decoded(self):
        trade = {"pnl_pct": 2.0, "hold_min": 15, "regime": "trending", "exit": "tp_hit", "confidence": 0.8}
        self.mock_redis_client.redis.zrevrange.return_value = [
            json.dumps(trade).encode("utf-8"),
        ]

        result = await self.memory.get_recent("BTCUSDT")

        assert len(result) == 1
        assert result[0]["pnl_pct"] == 2.0
        assert result[0]["regime"] == "trending"
        self.mock_redis_client.redis.zrevrange.assert_awaited_once_with(
            "karsa:memory:BTCUSDT", 0, RETRIEVE_COUNT * 3 - 1
        )

    @pytest.mark.asyncio
    async def test_get_recent_regime_filter(self):
        trending = {"pnl_pct": 1.0, "hold_min": 10, "regime": "trending", "exit": "tp_hit", "confidence": 0.8}
        ranging = {"pnl_pct": -0.5, "hold_min": 5, "regime": "ranging", "exit": "sl_hit", "confidence": 0.6}
        trending2 = {"pnl_pct": 0.3, "hold_min": 8, "regime": "trending", "exit": "manual", "confidence": 0.7}

        self.mock_redis_client.redis.zrevrange.return_value = [
            json.dumps(trending).encode(),
            json.dumps(ranging).encode(),
            json.dumps(trending2).encode(),
        ]

        result = await self.memory.get_recent("BTCUSDT", regime="trending")

        assert len(result) == 2
        assert all(e["regime"] == "trending" for e in result)

    @pytest.mark.asyncio
    async def test_get_recent_empty(self):
        self.mock_redis_client.redis.zrevrange.return_value = []

        result = await self.memory.get_recent("BTCUSDT")

        assert result == []

    def test_format_prompt_with_trades(self):
        trades = [
            {"pnl_pct": 1.5, "hold_min": 30, "exit": "tp_hit", "confidence": 0.85},
            {"pnl_pct": -0.8, "hold_min": 12, "exit": "sl_hit", "confidence": 0.60},
        ]

        result = self.memory.format_prompt("BTCUSDT", trades)

        assert "Recent trades for BTCUSDT:" in result
        assert "+1.5%" in result
        assert "-0.8%" in result
        assert "30min" in result
        assert "tp_hit" in result
        assert "sl_hit" in result
        assert "conf=0.85" in result
        assert "conf=0.60" in result

    def test_format_prompt_empty(self):
        result = self.memory.format_prompt("BTCUSDT", [])

        assert result == ""

    @pytest.mark.asyncio
    async def test_get_prompt_context(self):
        trades = [{"pnl_pct": 1.0, "hold_min": 20, "regime": "trending", "exit": "tp_hit", "confidence": 0.9}]
        self.mock_redis_client.redis.zrevrange.return_value = [
            json.dumps(trades[0]).encode(),
        ]

        result = await self.memory.get_prompt_context("BTCUSDT", regime="trending")

        assert "BTCUSDT" in result
        assert "+1.0%" in result
        assert "tp_hit" in result

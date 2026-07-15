"""Tests for Position Judge."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.position_judge import PositionJudge


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
def judge(mock_ai, mock_fetcher):
    return PositionJudge(mock_ai, mock_fetcher, cheap_timeout=5.0, escalated_timeout=15.0)


class TestPositionJudge:
    @pytest.mark.asyncio
    async def test_hold_on_profit(self, judge, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "action": "HOLD", "confidence": 85, "reasoning": "trend intact"
        })
        result = await judge.judge(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), current_price=Decimal("51000"),
            peak_price=Decimal("51500"), atr=Decimal("500"),
            regime="TREND_BULL", elapsed_seconds=3600,
        )
        assert result is not None
        assert result.action == "HOLD"

    @pytest.mark.asyncio
    async def test_exit_on_ai_signal(self, judge, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "action": "EXIT", "confidence": 80, "reasoning": "reversal"
        })
        result = await judge.judge(
            symbol="ETH/USDT", side="buy",
            entry_price=Decimal("3000"), current_price=Decimal("2900"),
            peak_price=Decimal("3100"), atr=Decimal("50"),
            regime="TREND_BEAR", elapsed_seconds=7200,
        )
        assert result is not None
        assert result.action == "EXIT"

    @pytest.mark.asyncio
    async def test_forced_exit_after_3_holds(self, judge, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "action": "HOLD", "confidence": 50, "reasoning": "unclear"
        })
        judge._hold_counters["BTC/USDT:buy"] = 3

        result = await judge.judge(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), current_price=Decimal("49000"),
            peak_price=Decimal("50500"), atr=Decimal("500"),
            regime="CHOP", elapsed_seconds=10800,
        )
        assert result is not None
        assert result.action == "EXIT"
        assert result.tier_used == "forced"

    @pytest.mark.asyncio
    async def test_hold_counter_increments_on_loss(self, judge, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "action": "HOLD", "confidence": 50, "reasoning": "wait"
        })
        await judge.judge(
            symbol="SOL/USDT", side="buy",
            entry_price=Decimal("100"), current_price=Decimal("98"),
            peak_price=Decimal("102"), atr=Decimal("2"),
            regime="MEAN_REVERSION", elapsed_seconds=3600,
        )
        assert judge._hold_counters.get("SOL/USDT:buy") == 1

    @pytest.mark.asyncio
    async def test_hold_counter_resets_on_exit(self, judge, mock_ai):
        mock_ai.complete.return_value = json.dumps({
            "action": "EXIT", "confidence": 90, "reasoning": "done"
        })
        judge._hold_counters["BTC/USDT:buy"] = 2
        await judge.judge(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), current_price=Decimal("49000"),
            peak_price=Decimal("50500"), atr=Decimal("500"),
            regime="TREND_BEAR", elapsed_seconds=3600,
        )
        assert judge._hold_counters.get("BTC/USDT:buy") == 0

    @pytest.mark.asyncio
    async def test_ai_unavailable_returns_conservative_hold(self, judge, mock_ai):
        mock_ai.complete.return_value = None
        result = await judge.judge(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), current_price=Decimal("49000"),
            peak_price=Decimal("50500"), atr=Decimal("500"),
            regime="TREND_BEAR", elapsed_seconds=3600,
        )
        assert result is not None
        assert result.action == "HOLD"

    @pytest.mark.asyncio
    async def test_parse_failure_returns_none(self, judge, mock_ai):
        mock_ai.complete.return_value = "not json"
        result = await judge._cheap_pass(
            "BTC/USDT", "buy", Decimal("50000"), Decimal("51000"),
            Decimal("500"), "TREND_BULL", 3600,
        )
        assert result is None

    def test_reset_hold_counter(self, judge):
        judge._hold_counters["BTC/USDT:buy"] = 3
        judge.reset_hold_counter("BTC/USDT", "buy")
        assert "BTC/USDT:buy" not in judge._hold_counters

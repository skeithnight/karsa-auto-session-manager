"""Tests for Smart Order Router."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.execution.sor import SmartOrderRouter
from app.execution.bybit_client import BybitClient


@pytest.fixture
def mock_bybit():
    with patch("app.execution.bybit_client.get_settings"):
        client = BybitClient()
        client.connected = True
        client.exchange = AsyncMock()
        return client


@pytest.fixture
def sor(mock_bybit):
    return SmartOrderRouter(mock_bybit, max_reprice_attempts=1, reprice_delay_seconds=0.01)


class TestSmartOrderRouter:
    """Test suite for SmartOrderRouter."""

    @pytest.mark.asyncio
    async def test_post_only_fills(self, sor, mock_bybit):
        """Step 1 succeeds — Post-Only fills."""
        mock_bybit.create_limit_order = AsyncMock(return_value={"id": "ord1", "status": "open"})

        result = await sor.execute("BTC/USDT:USDT", "buy", Decimal("0.001"), Decimal("64000"))

        assert result["id"] == "ord1"
        mock_bybit.create_limit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_reprice_fills(self, sor, mock_bybit):
        """Step 1 fails, Step 2 reprice fills."""
        mock_bybit.create_limit_order = AsyncMock(side_effect=[
            Exception("Post-Only rejected"),
            {"id": "ord2", "status": "open"},
        ])
        mock_bybit.cancel_order = AsyncMock(return_value={})

        result = await sor.execute("BTC/USDT:USDT", "buy", Decimal("0.001"), Decimal("64000"))

        assert result["id"] == "ord2"

    @pytest.mark.asyncio
    async def test_market_fallback(self, sor, mock_bybit):
        """Steps 1+2 fail, Step 3 market fallback."""
        mock_bybit.create_limit_order = AsyncMock(side_effect=Exception("rejected"))
        mock_bybit.create_market_order = AsyncMock(return_value={"id": "ord3", "status": "filled"})
        mock_bybit.cancel_order = AsyncMock(return_value={})

        result = await sor.execute("BTC/USDT:USDT", "buy", Decimal("0.001"), Decimal("64000"))

        assert result["id"] == "ord3"
        mock_bybit.create_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_steps_fail_returns_none(self, sor, mock_bybit):
        """All steps fail — returns None."""
        mock_bybit.create_limit_order = AsyncMock(side_effect=Exception("rejected"))
        mock_bybit.create_market_order = AsyncMock(side_effect=Exception("rejected"))
        mock_bybit.cancel_order = AsyncMock(return_value={})

        result = await sor.execute("BTC/USDT:USDT", "buy", Decimal("0.001"), Decimal("64000"))

        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_all(self, sor, mock_bybit):
        """cancel_all cancels all open orders for symbol."""
        mock_bybit.fetch_open_orders = AsyncMock(return_value=[
            {"id": "o1", "symbol": "BTC/USDT:USDT"},
            {"id": "o2", "symbol": "ETH/USDT:USDT"},
        ])
        mock_bybit.cancel_order = AsyncMock(return_value={})

        await sor.cancel_all("BTC/USDT:USDT")

        mock_bybit.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT")

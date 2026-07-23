"""Unit tests for fee-aware order routing in SmartOrderRouter."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.execution.sor import SmartOrderRouter


@pytest.fixture
def mock_bybit_client():
    client = AsyncMock()
    client.create_limit_order = AsyncMock(
        return_value={"id": "order123", "status": "open", "average": "50000.0"}
    )
    client.create_market_order = AsyncMock(
        return_value={"id": "mkt123", "status": "closed", "average": "50000.0"}
    )
    client.get_order_status = AsyncMock(
        return_value={"id": "order123", "status": "open", "average": "50000.0"}
    )
    client.set_trading_stop = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_fee_aware_thin_edge_cancels_post_only(mock_bybit_client):
    """EM < 1.5 * CoA -> Enforces strict Post-Only, cancels after 10s if unfilled."""
    mock_bybit_client.create_limit_order.return_value = {"id": "order123", "status": "open"}
    mock_bybit_client.get_order_status.return_value = {"id": "order123", "status": "open"}

    sor = SmartOrderRouter(bybit_client=mock_bybit_client)

    # Price = 50000, Conf = 0.3 (30%), ATR = 1.0 -> EM = $0.30
    # Taker fee 0.00055 * 50000 = $27.50 -> 1.5 * CoA = $41.25 -> EM << 1.5 * CoA
    result = await sor.execute_fee_aware(
        symbol="BTC/USDT",
        side="LONG",
        amount=Decimal("0.1"),
        price=Decimal("50000.0"),
        ai_confidence=0.3,
        atr=Decimal("1.0"),
    )

    # Order should be cancelled and return None (abandoned, no Taker fee)
    mock_bybit_client.create_limit_order.assert_called_once()
    mock_bybit_client.cancel_order.assert_called_once()
    assert result is None



@pytest.mark.asyncio
async def test_fee_aware_high_expectancy_aggressively_chases(mock_bybit_client):
    """EM > 3.0 * CoA -> High Expectancy setup, aggressively chases order."""
    sor = SmartOrderRouter(bybit_client=mock_bybit_client)

    # Price = 100.0, Conf = 0.9 (90%), ATR = 10.0 -> EM = $9.00
    # CoA = (0.00055 + 0.0001) * 100 = $0.065 -> 3 * CoA = $0.195 -> EM >> 3 * CoA
    result = await sor.execute_fee_aware(
        symbol="SOL/USDT",
        side="LONG",
        amount=Decimal("1.0"),
        price=Decimal("100.0"),
        ai_confidence=0.9,
        atr=Decimal("10.0"),
    )

    assert result is not None
    assert mock_bybit_client.create_limit_order.called

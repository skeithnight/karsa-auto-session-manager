"""Unit tests for Winner Cutting / Dynamic Capital Reallocation & Idempotency."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.risk.portfolio_risk_manager import PortfolioRiskManager
from app.execution.position_manager import ActivePositionManager


@pytest.fixture
def mock_position_store():
    store = AsyncMock()
    store.list_all = AsyncMock(
        return_value=[
            {
                "symbol": "ETH/USDT",
                "side": "LONG",
                "entry_price": "2000.0",
                "live_price": "2020.0",  # +1% PnL (winning, consolidating)
                "take_profit": "2200.0",
                "current_sl": "1980.0",
                "proactive_scale_out_executed": False,
            }
        ]
    )
    store.get = AsyncMock(
        return_value={
            "symbol": "ETH/USDT",
            "side": "LONG",
            "entry_price": "2000.0",
            "amount": "1.0",
            "proactive_scale_out_executed": False,
        }
    )
    return store


@pytest.mark.asyncio
async def test_prm_evaluates_capital_reallocation_trigger(mock_position_store):
    """Verify PRM recommends proactive scale-out when incoming signal EV > open position EV * 1.5."""
    prm = PortfolioRiskManager(
        redis_client=AsyncMock(),
        position_store=mock_position_store,
        trade_store=AsyncMock(),
        sector_mapping=AsyncMock(),
        bybit_client=AsyncMock(),
    )

    new_signal = MagicMock()
    new_signal.confidence = 0.9  # 90% confidence
    new_signal.tp_distance_pct = 0.05  # 5% target
    new_signal.sl_distance_pct = 0.01  # 1% risk -> 5:1 R/R -> EV = 0.9 * 5 = 4.5

    result = await prm.evaluate_capital_reallocation(new_signal)
    assert result is not None
    assert result["symbol"] == "ETH/USDT"
    assert result["new_signal_ev"] > result["open_position_ev"] * 1.5


@pytest.mark.asyncio
async def test_apm_proactive_scale_out_idempotency(mock_position_store):
    """Verify APM executes proactive scale-out once and sets proactive_scale_out_executed = True."""
    client = AsyncMock()
    client.reduce_position = AsyncMock()
    client.amend_stop_loss = AsyncMock()

    apm = ActivePositionManager(
        bybit_client=client,
        position_store=mock_position_store,
        redis_client=AsyncMock(),
        regime_classifier=AsyncMock(),
        alert_service=AsyncMock(),
    )

    # 1st call -> should execute scale-out
    success = await apm.proactive_scale_out("ETH/USDT", "LONG")
    assert success is True
    client.reduce_position.assert_called_once()

    # 2nd call -> should be blocked by idempotency guard
    mock_position_store.get = AsyncMock(
        return_value={
            "symbol": "ETH/USDT",
            "side": "LONG",
            "entry_price": "2000.0",
            "amount": "0.5",
            "proactive_scale_out_executed": True,
        }
    )

    success_again = await apm.proactive_scale_out("ETH/USDT", "LONG")
    assert success_again is False

"""Tests for State Manager — reconciliation, position tracking."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.state import StateManager, Position
from app.core.redis_client import RedisClient
from app.execution.bybit_client import BybitClient


@pytest.fixture
def mock_redis():
    with patch("app.core.redis_client.get_settings"):
        client = RedisClient()
        client.redis = AsyncMock()
        return client


@pytest.fixture
def mock_bybit():
    with patch("app.execution.bybit_client.get_settings"):
        client = BybitClient()
        client.connected = True
        client.exchange = AsyncMock()
        return client


@pytest.fixture
def state_manager(mock_redis, mock_bybit):
    return StateManager(mock_redis, mock_bybit)


class TestStateManager:
    """Test suite for StateManager."""

    @pytest.mark.asyncio
    async def test_reconcile_clean(self, state_manager, mock_bybit):
        """Scenario A: Clean — exchange matches local."""
        mock_bybit.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "side": "LONG", "contracts": Decimal("0.001"), "entry_price": Decimal("64000"), "unrealized_pnl": Decimal("0")},
        ])
        mock_bybit.fetch_open_orders = AsyncMock(return_value=[])

        result = await state_manager.reconcile()

        assert result is True
        assert state_manager.reconciled is True
        assert "BTC/USDT:USDT" in state_manager.positions

    @pytest.mark.asyncio
    async def test_reconcile_cancels_orphaned_orders(self, state_manager, mock_bybit):
        """Scenario B: Orphaned orders — cancelled."""
        state_manager.open_orders = {"old_order": {"id": "old_order", "symbol": "BTC/USDT:USDT"}}
        mock_bybit.fetch_positions = AsyncMock(return_value=[])
        mock_bybit.fetch_open_orders = AsyncMock(return_value=[
            {"id": "orphan1", "symbol": "BTC/USDT:USDT", "side": "buy", "price": 64000, "amount": 0.001, "status": "open"},
        ])
        mock_bybit.cancel_order = AsyncMock(return_value={})

        await state_manager.reconcile()

        mock_bybit.cancel_order.assert_called_once_with("orphan1", "BTC/USDT:USDT")

    @pytest.mark.asyncio
    async def test_reconcile_removes_ghost_positions(self, state_manager, mock_bybit):
        """Scenario C: Ghost position — removed."""
        state_manager.positions["ETH/USDT:USDT"] = Position("ETH/USDT:USDT", "LONG", Decimal("0.1"), Decimal("3000"))
        mock_bybit.fetch_positions = AsyncMock(return_value=[])  # Exchange says flat
        mock_bybit.fetch_open_orders = AsyncMock(return_value=[])

        await state_manager.reconcile()

        assert "ETH/USDT:USDT" not in state_manager.positions

    @pytest.mark.asyncio
    async def test_reconcile_bybit_unreachable(self, state_manager, mock_bybit):
        """Bybit unreachable — degraded startup, allows continue."""
        mock_bybit.fetch_positions = AsyncMock(side_effect=Exception("Connection refused"))

        result = await state_manager.reconcile()

        # ponytail: allow startup without Bybit private API (return True, reconciled stays False)
        assert result is True
        assert state_manager.reconciled is False

    def test_update_position_new(self, state_manager):
        """New position created."""
        state_manager.update_position("BTC/USDT:USDT", "LONG", Decimal("0.001"), Decimal("64000"))

        pos = state_manager.get_position("BTC/USDT:USDT")
        assert pos is not None
        assert pos.side == "LONG"
        assert pos.size == Decimal("0.001")

    def test_close_position_pnl_long(self, state_manager):
        """Close LONG position — profit."""
        state_manager.update_position("BTC/USDT:USDT", "LONG", Decimal("0.001"), Decimal("64000"))

        pnl = state_manager.close_position("BTC/USDT:USDT", Decimal("65000"))

        assert pnl == Decimal("1.0")  # (65000-64000) * 0.001
        assert state_manager.get_position("BTC/USDT:USDT") is None

    def test_close_position_pnl_short(self, state_manager):
        """Close SHORT position — profit."""
        state_manager.update_position("BTC/USDT:USDT", "SHORT", Decimal("0.001"), Decimal("64000"))

        pnl = state_manager.close_position("BTC/USDT:USDT", Decimal("63000"))

        assert pnl == Decimal("1.0")  # (64000-63000) * 0.001

    def test_close_no_position_returns_none(self, state_manager):
        """Close non-existent position — None."""
        pnl = state_manager.close_position("BTC/USDT:USDT", Decimal("64000"))
        assert pnl is None

    @pytest.mark.asyncio
    async def test_store_trade(self, state_manager, mock_redis):
        """Store trade to Redis."""
        state_manager.redis.set_global_state = AsyncMock()
        trade_id = await state_manager.store_trade(
            "BTC/USDT:USDT", "BUY", Decimal("0.001"), Decimal("64000"),
        )
        assert trade_id is not None
        state_manager.redis.set_global_state.assert_called_once()

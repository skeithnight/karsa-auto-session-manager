"""Integration tests for ActivePositionManager — Phase 6.5."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.regime_classifier import MarketRegime
from app.execution.position_manager import (
    APM_BREAKEVEN_FEE_PCT,
    REGIME_SHIFT_CONFIRM_COUNT,
    ActivePositionManager,
)


def _make_pos(
    symbol: str = "SOL/USDT",
    side: str = "LONG",
    entry_price: str = "100.0",
    live_price: str = "105.0",
    initial_risk: str = "5.0",
    atr: str = "2.0",
    moved_to_be: bool = False,
    sl_order_id: str = "SL-001",
    amount: str = "1.0",
    entry_regime: str = "TREND_BULL",
    max_hold: int = 1440,
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "live_price": live_price,
        "initial_risk_per_unit": initial_risk,
        "atr": atr,
        "moved_to_breakeven": moved_to_be,
        "sl_order_id": sl_order_id,
        "amount": amount,
        "entry_regime": entry_regime,
        "entry_time": datetime.now(timezone.utc) - timedelta(minutes=10),
        "max_hold_time_mins": max_hold,
        "current_sl": "95.0",
    }


def _make_apm() -> tuple:
    client = AsyncMock()
    store = AsyncMock()
    regime = AsyncMock()
    alert = AsyncMock()
    store.list_all = AsyncMock(return_value=[])
    store.remove = AsyncMock()
    store.update_sl = AsyncMock()
    client.fetch_open_orders = AsyncMock(return_value=[])
    client.fetch_positions = AsyncMock(return_value=[])
    client.fetch_tickers = AsyncMock(return_value=[])
    client.cancel_order = AsyncMock()
    client.create_market_order = AsyncMock()
    client.amend_stop_loss = AsyncMock()
    client.place_stop_loss = AsyncMock()
    client.place_take_profit = AsyncMock(return_value={"orderId": "TP-001"})
    client.reduce_position = AsyncMock(return_value={"orderId": "RED-001"})
    apm = ActivePositionManager(client, store, regime, alert)
    return apm, client, store, regime, alert


class TestRMultiple:
    def test_long_profit(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "LONG", Decimal("100"), Decimal("105"), Decimal("5")
        )
        assert r == Decimal("1")

    def test_short_profit(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "SHORT", Decimal("100"), Decimal("95"), Decimal("5")
        )
        assert r == Decimal("1")

    def test_zero_risk(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "LONG", Decimal("100"), Decimal("105"), Decimal("0")
        )
        assert r == Decimal("0")


class TestBreakeven:
    @pytest.mark.asyncio
    async def test_breakeven_lock_at_1r(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(moved_to_be=False)
        # live=105, entry=100, risk=5 → R=1.0 → triggers breakeven
        await apm._manage_single_position(pos)
        client.amend_stop_loss.assert_called_once()

    @pytest.mark.asyncio
    async def test_breakeven_not_if_already_moved(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(moved_to_be=True)
        await apm._manage_single_position(pos)
        # Should not call amend — moved_to_breakeven=True
        client.amend_stop_loss.assert_not_called()


class TestTrailingStop:
    @pytest.mark.asyncio
    async def test_trailing_activates_above_1_5r(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        # entry=100, live=108, risk=5 → R=1.6 → above 1.5R threshold
        pos = _make_pos(
            entry_price="100.0",
            live_price="108.0",
            initial_risk="5.0",
            atr="2.0",
            entry_regime="TREND_BULL",
        )
        await apm._manage_single_position(pos)
        # trailing should have fired (live_price=108, atr=2, trail_dist=6, new_sl=102 > current_sl=95)
        client.amend_stop_loss.assert_called()


class TestTimeExit:
    @pytest.mark.asyncio
    async def test_time_exit_chop_30min(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(entry_regime="CHOP", max_hold=30)
        # Position held for 60 min
        pos["entry_time"] = datetime.now(timezone.utc) - timedelta(minutes=60)
        await apm._manage_single_position(pos)
        client.create_market_order.assert_called()


class TestRegimeShift:
    @pytest.mark.asyncio
    async def test_regime_shift_close_after_hysteresis(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        regime.get_current_regime = AsyncMock(return_value=MarketRegime.RANGE)
        pos = _make_pos(entry_regime="TREND_BULL")
        for _ in range(REGIME_SHIFT_CONFIRM_COUNT):
            await apm._check_regime_shift(pos, "SOL/USDT", "TREND_BULL")
        client.create_market_order.assert_called()


class TestForceClose:
    @pytest.mark.asyncio
    async def test_force_close_cancels_orders_and_sells(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        client.fetch_open_orders.return_value = [
            {"id": "SL-001", "symbol": "SOL/USDT"},
            {"id": "TP-002", "symbol": "SOL/USDT"},
        ]
        pos = _make_pos()
        await apm._force_close_position(pos, "test")
        assert client.cancel_order.call_count == 2
        client.create_market_order.assert_called_once_with(
            "SOL/USDT", "SELL", Decimal("1.0"), {"reduceOnly": True}
        )
        store.remove.assert_called_once_with("SOL/USDT", "buy")

    @pytest.mark.asyncio
    async def test_force_close_short_side(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        client.fetch_open_orders.return_value = []
        pos = _make_pos(side="SHORT")
        await apm._force_close_position(pos, "test")
        client.create_market_order.assert_called_once_with(
            "SOL/USDT", "BUY", Decimal("1.0"), {"reduceOnly": True}
        )
        store.remove.assert_called_once_with("SOL/USDT", "sell")


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_ghost_position_removed(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        store.list_all = AsyncMock(
            return_value=[
                {"symbol": "SOL/USDT", "side": "buy"},
                {"symbol": "ADA/USDT", "side": "buy"},
            ]
        )
        client.fetch_positions = AsyncMock(
            return_value=[{"symbol": "SOL/USDT"}]
        )
        client.fetch_open_orders = AsyncMock(return_value=[])
        await apm._reconcile_positions()
        store.remove.assert_called_once_with("ADA/USDT", "buy")

    @pytest.mark.asyncio
    async def test_no_ghosts_no_removal(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        store.list_all = AsyncMock(
            return_value=[{"symbol": "SOL/USDT", "side": "buy"}]
        )
        client.fetch_positions = AsyncMock(
            return_value=[{"symbol": "SOL/USDT"}]
        )
        client.fetch_open_orders = AsyncMock(return_value=[])
        await apm._reconcile_positions()
        store.remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_sl_replaced(self) -> None:
        """When SL order disappeared from exchange, re-place it."""
        apm, client, store, regime, alert = _make_apm()
        store.list_all = AsyncMock(
            return_value=[{
                "symbol": "SOL/USDT",
                "side": "buy",
                "sl_order_id": "SL-GONE",
                "entry_price": "100.0",
                "amount": "1.0",
            }]
        )
        client.fetch_positions = AsyncMock(
            return_value=[{"symbol": "SOL/USDT"}]
        )
        client.fetch_open_orders = AsyncMock(return_value=[])
        client.place_stop_loss = AsyncMock(
            return_value={"orderId": "SL-NEW"}
        )
        await apm._reconcile_positions()
        client.place_stop_loss.assert_called_once_with(
            "SOL/USDT", "buy", Decimal("100.0"), Decimal("1.0")
        )
        store.update_sl.assert_called_once_with("SOL/USDT", "buy", "SL-NEW")


class TestTakeProfit:
    @pytest.mark.asyncio
    async def test_tp_placed_for_range_regime(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(entry_regime="RANGE", moved_to_be=False, initial_risk="5.0")
        await apm._manage_single_position(pos)
        client.place_take_profit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tp_for_trend_regime(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(entry_regime="TREND_BULL", moved_to_be=False)
        await apm._manage_single_position(pos)
        client.place_take_profit.assert_not_called()

    @pytest.mark.asyncio
    async def test_tp_not_replaced_once_placed(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        pos = _make_pos(entry_regime="CHOP", moved_to_be=False)
        pos["tp_placed"] = True
        await apm._manage_single_position(pos)
        client.place_take_profit.assert_not_called()


class TestScaleOut:
    @pytest.mark.asyncio
    async def test_scale_out_range_at_1r(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        # entry=100, live=105, risk=5 → R=1.0 → triggers 50% scale-out for RANGE
        pos = _make_pos(entry_regime="RANGE", entry_price="100.0", live_price="105.0", initial_risk="5.0")
        await apm._manage_single_position(pos)
        client.reduce_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_scale_out_trend_at_2r(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        # entry=100, live=110, risk=5 → R=2.0 → triggers 30% scale-out for TREND
        pos = _make_pos(entry_regime="TREND_BULL", entry_price="100.0", live_price="110.0", initial_risk="5.0")
        await apm._manage_single_position(pos)
        client.reduce_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_scale_out_below_threshold(self) -> None:
        apm, client, store, regime, alert = _make_apm()
        # entry=100, live=102, risk=5 → R=0.4 → below 1R threshold
        pos = _make_pos(entry_regime="RANGE", entry_price="100.0", live_price="102.0", initial_risk="5.0")
        await apm._manage_single_position(pos)
        client.reduce_position.assert_not_called()

"""Integration tests for ActivePositionManager — Phase 6.5."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

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
    current_sl: str = "95.0",
    entry_regime: str = "TREND_BULL",
    moved_to_be: bool = False,
    entry_time: datetime | None = None,
    max_hold_mins: int = 1440,
    atr: str = "2.0",
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "live_price": live_price,
        "initial_risk_per_unit": initial_risk,
        "current_sl": current_sl,
        "entry_regime": entry_regime,
        "moved_to_breakeven": moved_to_be,
        "entry_time": entry_time,
        "max_hold_time_mins": max_hold_mins,
        "atr": atr,
        "amount": "1.0",
    }


def _make_apm() -> (
    tuple[ActivePositionManager, AsyncMock, AsyncMock, AsyncMock, AsyncMock]
):
    client = AsyncMock()
    state = AsyncMock()
    regime = AsyncMock()
    alert = AsyncMock()
    apm = ActivePositionManager(client, state, regime, alert)
    return apm, client, state, regime, alert


class TestRMultiple:
    def test_long_profit(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "LONG", Decimal("100"), Decimal("110"), Decimal("5")
        )
        assert r == Decimal("2")

    def test_short_profit(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "SHORT", Decimal("100"), Decimal("90"), Decimal("5")
        )
        assert r == Decimal("2")

    def test_zero_risk_returns_zero(self) -> None:
        r = ActivePositionManager._calculate_r_multiple(
            "LONG", Decimal("100"), Decimal("110"), Decimal("0")
        )
        assert r == Decimal("0")


class TestBreakeven:
    @pytest.mark.asyncio
    async def test_at_1r_triggers_breakeven(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        pos = _make_pos(live_price="105.0", initial_risk="5.0", moved_to_be=False)
        await apm._manage_single_position(pos)
        client.amend_stop_loss.assert_called_once()
        state.set_breakeven.assert_called_once_with("SOL/USDT", True)

    @pytest.mark.asyncio
    async def test_at_0_9r_no_breakeven(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        pos = _make_pos(live_price="104.5", initial_risk="5.0", moved_to_be=False)
        await apm._manage_single_position(pos)
        client.amend_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_moved_no_repeat(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        # Use RANGE regime to prevent trailing stop from firing
        pos = _make_pos(
            live_price="110.0",
            initial_risk="5.0",
            moved_to_be=True,
            entry_regime="RANGE",
        )
        await apm._manage_single_position(pos)
        client.amend_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_breakeven_sl_correct_for_long(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        pos = _make_pos(
            entry_price="100.0", side="LONG", live_price="105.0", initial_risk="5.0"
        )
        await apm._move_stop_to_breakeven(pos, Decimal("100.0"), "LONG")
        call_args = client.amend_stop_loss.call_args[0]
        expected_sl = Decimal("100.0") + Decimal("100.0") * APM_BREAKEVEN_FEE_PCT
        assert call_args[1] == str(expected_sl)


class TestTimeExit:
    @pytest.mark.asyncio
    async def test_time_exit_triggered(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=250)
        pos = _make_pos(entry_time=entry_time, max_hold_mins=240)
        await apm._manage_time_exit(pos, entry_time, 240)
        client.cancel_all_orders.assert_called_once()
        client.place_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_time_exit_not_triggered(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=100)
        pos = _make_pos(entry_time=entry_time, max_hold_mins=240)
        await apm._manage_time_exit(pos, entry_time, 240)
        client.cancel_all_orders.assert_not_called()


class TestRegimeShift:
    @pytest.mark.asyncio
    async def test_regime_shift_triggers_after_n_checks(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        regime.get_current_regime.return_value = MarketRegime.RANGE

        pos = _make_pos(entry_regime="TREND_BULL")
        for _ in range(REGIME_SHIFT_CONFIRM_COUNT - 1):
            await apm._check_regime_shift(pos, "SOL/USDT", "TREND_BULL")
            client.cancel_all_orders.assert_not_called()

        await apm._check_regime_shift(pos, "SOL/USDT", "TREND_BULL")
        client.cancel_all_orders.assert_called_once_with("SOL/USDT")
        client.place_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_regime_match_resets_counter(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        regime.get_current_regime.return_value = MarketRegime.TREND_BULL

        pos = _make_pos(entry_regime="TREND_BULL")
        apm._regime_shift_counts["SOL/USDT"] = 2
        await apm._check_regime_shift(pos, "SOL/USDT", "TREND_BULL")
        assert "SOL/USDT" not in apm._regime_shift_counts


class TestForceClose:
    @pytest.mark.asyncio
    async def test_force_close_calls_cancel_and_market(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        pos = _make_pos(side="LONG")
        await apm._force_close_position(pos, "test_reason")
        client.cancel_all_orders.assert_called_once_with("SOL/USDT")
        client.place_market_order.assert_called_once_with("SOL/USDT", "SELL", "1.0")
        state.remove_position.assert_called_once_with("SOL/USDT")

    @pytest.mark.asyncio
    async def test_force_close_short_uses_buy(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        pos = _make_pos(side="SHORT")
        await apm._force_close_position(pos, "test")
        client.place_market_order.assert_called_once_with("SOL/USDT", "BUY", "1.0")


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_ghost_position_removed(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        state.get_all_positions.return_value = [
            {"symbol": "SOL/USDT"},
            {"symbol": "ADA/USDT"},
        ]
        client.get_positions.return_value = [{"symbol": "SOL/USDT"}]
        await apm._reconcile_positions()
        state.remove_position.assert_called_once_with("ADA/USDT")

    @pytest.mark.asyncio
    async def test_no_ghosts_no_removal(self) -> None:
        apm, client, state, regime, alert = _make_apm()
        state.get_all_positions.return_value = [{"symbol": "SOL/USDT"}]
        client.get_positions.return_value = [{"symbol": "SOL/USDT"}]
        await apm._reconcile_positions()
        state.remove_position.assert_not_called()

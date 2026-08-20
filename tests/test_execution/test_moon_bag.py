"""Tests for Moon Bag Tiered Exit (Phase 4)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.execution.position_manager import ActivePositionManager


from app.execution.constants import APM_BREAKEVEN_FEE_PCT


def _make_position(
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_price: float = 100.0,
    amount: float = 1.0,
    initial_risk: float = 5.0,
    atr: float = 2.0,
    tranche_state: str = "INITIAL",
    live_price: float = 107.5,
) -> dict:
    """Build a test position dict."""
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": str(entry_price),
        "amount": str(amount),
        "initial_risk_per_unit": str(initial_risk),
        "atr": str(atr),
        "tranche_state": tranche_state,
        "live_price": str(live_price),
        "entry_regime": "TREND_BULL",
        "current_sl": str(entry_price - initial_risk),
        "stop_loss": str(entry_price - initial_risk),
        "sl_order_id": "test_order_123",
        "moved_to_breakeven": False,
        "peak_price": str(live_price),
        "last_tick_price": str(live_price),
    }


def _make_apm() -> ActivePositionManager:
    """Build an APM with mocked dependencies."""
    client = MagicMock()
    client.reduce_position = AsyncMock()
    client.place_stop_loss = AsyncMock()
    client.create_order = AsyncMock()
    client.set_stop_loss = AsyncMock()
    client.amend_stop_loss = AsyncMock()
    client.fetch_positions = AsyncMock(
        return_value=[{"symbol": "BTCUSDT", "size": "1.0", "side": "buy", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 115.0}]
    )

    store = MagicMock()
    store.list_all = AsyncMock(return_value=[])
    store.redis = AsyncMock()
    store.redis.get = AsyncMock(return_value=None)
    store.redis.set = AsyncMock()
    store.update_sl = AsyncMock()
    store.update_fields = AsyncMock()
    store.get_missing_fields = AsyncMock(return_value=[])

    regime = MagicMock()
    regime.get_current_regime = AsyncMock(return_value="TREND_BULL")
    alert = MagicMock()
    alert.send = AsyncMock()

    apm = ActivePositionManager(
        bybit_client=client,
        position_store=store,
        redis_client=MagicMock(),
        regime_classifier=regime,
        alert_service=alert,
    )
    return apm


class TestMoonBagTrigger:
    """Test moon bag trigger at +2.0R."""

    @pytest.mark.asyncio
    async def test_moon_bag_triggers_at_1_5r(self):
        """Position at +2.0R should trigger 80/20 split."""
        apm = _make_apm()
        pos = _make_position(entry_price=100.0, live_price=110.0, initial_risk=5.0)
        # +10.0 / 5.0 = +2.0R

        await apm._manage_single_position(pos)

        assert pos["tranche_state"] == "MOON_BAG_ACTIVE"
        assert Decimal(pos["amount"]) == Decimal("0.2")  # 20% of 1.0
        assert Decimal(pos["moon_bag_amount"]) == Decimal("0.2")
        expected_sl = Decimal("100.0") + Decimal("100.0") * APM_BREAKEVEN_FEE_PCT
        assert Decimal(pos["moon_bag_sl"]) == expected_sl  # Breakeven with fee
        apm._client.reduce_position.assert_called_once()
        apm._client.place_stop_loss.assert_called_once()

    @pytest.mark.asyncio
    async def test_moon_bag_no_trigger_below_1_5r(self):
        """Position at +1.0R should NOT trigger moon bag."""
        apm = _make_apm()
        pos = _make_position(entry_price=100.0, live_price=105.0, initial_risk=5.0)
        # +5.0 / 5.0 = +1.0R

        await apm._manage_single_position(pos)

        assert pos.get("tranche_state", "INITIAL") == "INITIAL"
        assert Decimal(pos["amount"]) == Decimal("1.0")  # Unchanged

    @pytest.mark.asyncio
    async def test_moon_bag_already_active_skips(self):
        """Position already in MOON_BAG_ACTIVE should not trigger again."""
        apm = _make_apm()
        pos = _make_position(
            entry_price=100.0, live_price=120.0, initial_risk=5.0,
            tranche_state="MOON_BAG_ACTIVE", amount=0.2,
        )
        pos["highest_since_partial"] = "115.0"

        await apm._manage_single_position(pos)

        # Should not call reduce_position again
        apm._client.reduce_position.assert_not_called()


class TestMoonBagFailSafe:
    """Test emergency close when SL placement fails."""

    @pytest.mark.asyncio
    async def test_emergency_close_on_sl_failure(self):
        """If SL placement fails, remaining 20% should be emergency closed."""
        apm = _make_apm()
        apm._client.place_stop_loss = AsyncMock(side_effect=Exception("SL failed"))

        pos = _make_position(entry_price=100.0, live_price=110.0, initial_risk=5.0)

        await apm._manage_single_position(pos)

        # Should have called reduce_position twice: once for 80% close, once for emergency 20%
        assert apm._client.reduce_position.call_count == 2
        # Amount should be 0 after emergency close
        assert pos["amount"] == "0"


class TestMoonBagTrailing:
    """Test moon bag ultra-wide trailing (5x ATR)."""

    @pytest.mark.asyncio
    async def test_moon_bag_trailing_updates_sl(self):
        """Moon bag trailing should amend SL to 5x ATR from highest."""
        apm = _make_apm()
        pos = _make_position(
            entry_price=100.0, live_price=120.0, initial_risk=5.0,
            tranche_state="MOON_BAG_ACTIVE", amount=0.2, atr=2.0,
        )
        pos["moon_bag_sl"] = "100.0"
        pos["current_sl"] = "100.0"
        pos["stop_loss"] = "100.0"
        pos["highest_since_partial"] = "115.0"

        await apm._manage_single_position(pos)

        # Highest should update to 120.0, new SL = 120 - (2 * 5) = 110
        assert Decimal(pos["highest_since_partial"]) == Decimal("120.0")
        # SL should be amended to 110 (5x ATR from 120)
        assert Decimal(pos["moon_bag_sl"]) == Decimal("110.0")

    @pytest.mark.asyncio
    async def test_moon_bag_trailing_doesnt_move_down(self):
        """Moon bag trailing should never move SL below current moon_bag_sl."""
        apm = _make_apm()
        pos = _make_position(
            entry_price=100.0, live_price=106.0, initial_risk=5.0,
            tranche_state="MOON_BAG_ACTIVE", amount=0.2, atr=2.0,
        )
        pos["moon_bag_sl"] = "108.0"  # Already higher than 5x ATR would set
        pos["current_sl"] = "108.0"
        pos["stop_loss"] = "108.0"
        pos["highest_since_partial"] = "115.0"

        await apm._manage_single_position(pos)

        # 5x ATR from 115 = 105, which is LOWER than current 108
        # Moon bag trailing should NOT amend (would move SL down)
        # The moon_bag_sl field should remain at 108.0
        assert Decimal(pos["moon_bag_sl"]) == Decimal("108.0")

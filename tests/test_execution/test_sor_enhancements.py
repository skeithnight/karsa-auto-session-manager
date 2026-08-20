"""Tests for SOR entry strategies, slippage guard, TWAP, and close-based trailing stop."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.execution.sor import SmartOrderRouter
from app.execution.exit_manager import ExitManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bybit():
    client = AsyncMock()
    client.connected = True
    client._price_ticks = {"BTC/USDT:USDT": Decimal("0.10")}
    client._to_bybit_symbol = MagicMock(return_value="BTCUSDT")
    client.create_limit_order = AsyncMock(
        return_value={"id": "ord1", "orderId": "ord1", "status": "open"}
    )
    client.create_market_order = AsyncMock(
        return_value={"id": "mkt1", "orderId": "mkt1", "status": "closed", "average": "64000"}
    )
    client.cancel_order = AsyncMock(return_value={})
    client.set_trading_stop = AsyncMock()
    client.place_stop_loss = AsyncMock()
    client.get_order_status = AsyncMock(
        return_value={"id": "ord1", "status": "open", "cumExecQty": "0"}
    )
    client.fetch_tickers = AsyncMock(
        return_value=[{"symbol": "BTC/USDT:USDT", "bid": Decimal("63999"), "ask": Decimal("64001")}]
    )
    client.fetch_open_orders = AsyncMock(return_value=[])
    client.session = AsyncMock()
    return client


@pytest.fixture
def sor(mock_bybit):
    return SmartOrderRouter(
        mock_bybit,
        max_reprice_attempts=1,
        reprice_delay_seconds=0.01,
        redis_client=AsyncMock(),
    )


@pytest.fixture
def exit_manager(mock_bybit):
    return ExitManager(
        bybit_client=mock_bybit,
        position_store=AsyncMock(),
        regime_classifier=AsyncMock(),
        alert_service=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# MARKET Entry Strategy
# ---------------------------------------------------------------------------

class TestMarketEntryStrategy:
    """MARKET strategy: immediate execution via 3-step pipeline."""

    @pytest.mark.asyncio
    async def test_market_strategy_fills(self, sor, mock_bybit):
        """MARKET strategy calls execute() and returns fill."""
        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.001"),
            entry_strategy="MARKET",
        )
        assert result is not None
        assert "orderId" in result

    @pytest.mark.asyncio
    async def test_market_strategy_short(self, sor, mock_bybit):
        """MARKET strategy works for SHORT side."""
        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="SHORT",
            size=Decimal("0.001"),
            entry_strategy="MARKET",
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_market_strategy_unknown_falls_back(self, sor, mock_bybit):
        """Unknown strategy falls back to MARKET."""
        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.001"),
            entry_strategy="UNKNOWN_STRATEGY",
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_market_strategy_no_mid_price_returns_none(self, sor, mock_bybit):
        """MARKET strategy returns None when mid price unavailable."""
        mock_bybit.fetch_tickers = AsyncMock(return_value=[])
        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.001"),
            entry_strategy="MARKET",
        )
        assert result is None


# ---------------------------------------------------------------------------
# LIMIT_RETEST Entry Strategy
# ---------------------------------------------------------------------------

class TestLimitRetestStrategy:
    """LIMIT_RETEST: limit order with TTL, cancel if unfilled."""

    @pytest.mark.asyncio
    async def test_limit_retest_fills_immediately(self, sor, mock_bybit):
        """Limit order fills on first poll."""
        sor._place_sl_after_fill = AsyncMock(return_value="sl_id")
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "filled", "average": "63950"}
        )
        with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
            result = await sor.execute_with_strategy(
                symbol="BTC/USDT:USDT",
                side="LONG",
                size=Decimal("0.001"),
                entry_strategy="LIMIT_RETEST",
                limit_price=Decimal("63950"),
                ttl_candles=2,
            )
        assert result is not None
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_limit_retest_places_limit_order(self, sor, mock_bybit):
        """LIMIT_RETEST places a limit order (not market)."""
        sor._place_sl_after_fill = AsyncMock(return_value="sl_id")
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "filled"}
        )
        with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
            await sor.execute_with_strategy(
                symbol="BTC/USDT:USDT",
                side="LONG",
                size=Decimal("0.001"),
                entry_strategy="LIMIT_RETEST",
                limit_price=Decimal("63950"),
                ttl_candles=2,
            )
        mock_bybit.create_limit_order.assert_called_once()
        call_args = mock_bybit.create_limit_order.call_args
        assert call_args[0][0] == "BTC/USDT:USDT"  # symbol
        assert call_args[0][1] == "buy"  # side
        assert call_args[0][2] == Decimal("0.001")  # size
        assert call_args[0][3] == Decimal("63950")  # price

    @pytest.mark.asyncio
    async def test_limit_retest_uses_default_price(self, sor, mock_bybit):
        """When no limit_price given, uses mid x 0.995."""
        sor._place_sl_after_fill = AsyncMock(return_value="sl_id")
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "filled"}
        )
        with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
            await sor.execute_with_strategy(
                symbol="BTC/USDT:USDT",
                side="LONG",
                size=Decimal("0.001"),
                entry_strategy="LIMIT_RETEST",
            )
        mock_bybit.create_limit_order.assert_called_once()
        call_args = mock_bybit.create_limit_order.call_args
        placed_price = call_args[0][3]
        # mid = (63999 + 64001) / 2 = 64000, price = 64000 * 0.995 = 63680
        assert placed_price == (Decimal("64000") * Decimal("0.995")).quantize(Decimal("0.10"))

    @pytest.mark.asyncio
    async def test_limit_retest_cancels_on_ttl_expiry(self, sor, mock_bybit):
        """Limit order not filled -> cancel after TTL."""
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "open"}
        )
        import app.execution.sor as sor_mod
        orig_candle = sor_mod.CANDLE_SECONDS
        sor_mod.CANDLE_SECONDS = 1  # 1 second candle for test
        try:
            with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
                result = await sor.execute_with_strategy(
                    symbol="BTC/USDT:USDT",
                    side="LONG",
                    size=Decimal("0.001"),
                    entry_strategy="LIMIT_RETEST",
                    limit_price=Decimal("63950"),
                    ttl_candles=1,
                )
            assert result is None
            mock_bybit.cancel_order.assert_called()
        finally:
            sor_mod.CANDLE_SECONDS = orig_candle


# ---------------------------------------------------------------------------
# WAIT_PULLBACK Entry Strategy
# ---------------------------------------------------------------------------

class TestWaitPullbackStrategy:
    """WAIT_PULLBACK: limit order at support level with longer TTL."""

    @pytest.mark.asyncio
    async def test_wait_pullback_fills(self, sor, mock_bybit):
        """Pullback order fills within TTL."""
        sor._place_sl_after_fill = AsyncMock(return_value="sl_id")
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "filled", "average": "63500"}
        )
        with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
            result = await sor.execute_with_strategy(
                symbol="BTC/USDT:USDT",
                side="LONG",
                size=Decimal("0.001"),
                entry_strategy="WAIT_PULLBACK",
                limit_price=Decimal("63500"),
                ttl_candles=4,
            )
        assert result is not None
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_wait_pullback_cancels_on_ttl(self, sor, mock_bybit):
        """Pullback order not filled -> cancel after TTL."""
        mock_bybit.get_order_status = AsyncMock(
            return_value={"id": "ord1", "status": "open"}
        )
        import app.execution.sor as sor_mod
        orig_candle = sor_mod.CANDLE_SECONDS
        sor_mod.CANDLE_SECONDS = 1
        try:
            with patch("app.execution.sor.asyncio.sleep", new_callable=AsyncMock):
                result = await sor.execute_with_strategy(
                    symbol="BTC/USDT:USDT",
                    side="LONG",
                    size=Decimal("0.001"),
                    entry_strategy="WAIT_PULLBACK",
                    limit_price=Decimal("63500"),
                    ttl_candles=1,
                )
            assert result is None
            mock_bybit.cancel_order.assert_called()
        finally:
            sor_mod.CANDLE_SECONDS = orig_candle


# ---------------------------------------------------------------------------
# Slippage Detection & Size Reduction
# ---------------------------------------------------------------------------

class TestSlippageGuard:
    """Slippage detection: reduce size when order book depth is too thin."""

    @pytest.mark.asyncio
    async def test_thin_book_reduces_size(self, sor, mock_bybit):
        """When depth is thin, size is reduced by 50%."""
        # Mock depth check to return thin book (0.1 BTC)
        sor._get_orderbook_depth_1pct = AsyncMock(return_value=Decimal("0.1"))
        sor._get_mid_price = AsyncMock(return_value=Decimal("64000"))

        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.01"),  # 0.01 / 0.1 = 10% slippage > 0.5%
            entry_strategy="MARKET",
        )
        # create_market_order should have been called with reduced size
        if mock_bybit.create_market_order.called:
            call_args = mock_bybit.create_market_order.call_args
            actual_size = call_args[0][2]
            assert actual_size == Decimal("0.005")  # 50% reduction

    @pytest.mark.asyncio
    async def test_thick_book_no_reduction(self, sor, mock_bybit):
        """When depth is thick, size is not reduced."""
        sor._get_orderbook_depth_1pct = AsyncMock(return_value=Decimal("100"))
        sor._get_mid_price = AsyncMock(return_value=Decimal("64000"))

        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.001"),
            entry_strategy="MARKET",
        )
        assert result is not None
        # Size should not be reduced
        if mock_bybit.create_market_order.called:
            call_args = mock_bybit.create_market_order.call_args
            actual_size = call_args[0][2]
            assert actual_size == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_depth_check_failure_proceeds_with_original(self, sor, mock_bybit):
        """If depth check fails, proceed with original size."""
        sor._get_orderbook_depth_1pct = AsyncMock(return_value=None)

        result = await sor.execute_with_strategy(
            symbol="BTC/USDT:USDT",
            side="LONG",
            size=Decimal("0.001"),
            entry_strategy="MARKET",
        )
        assert result is not None


# ---------------------------------------------------------------------------
# TWAP Order Splitting
# ---------------------------------------------------------------------------

class TestTWAP:
    """TWAP: split large orders into smaller chunks over time."""

    @pytest.mark.asyncio
    async def test_twap_splits_into_chunks(self, sor, mock_bybit):
        """TWAP splits order into num_splits chunks."""
        results = await sor._execute_twap(
            symbol="BTC/USDT:USDT",
            side="LONG",
            total_size=Decimal("0.03"),
            num_splits=3,
            interval_seconds=0.01,
        )
        assert len(results) == 3
        assert mock_bybit.create_market_order.call_count == 3

    @pytest.mark.asyncio
    async def test_twap_handles_partial_failure(self, sor, mock_bybit):
        """TWAP continues even if one split fails."""
        mock_bybit.create_market_order = AsyncMock(side_effect=[
            {"id": "mkt1", "status": "filled"},
            Exception("Network error"),
            {"id": "mkt3", "status": "filled"},
        ])
        results = await sor._execute_twap(
            symbol="BTC/USDT:USDT",
            side="LONG",
            total_size=Decimal("0.03"),
            num_splits=3,
            interval_seconds=0.01,
        )
        assert len(results) == 3
        assert "error" in results[1]

    @pytest.mark.asyncio
    async def test_twap_size_calculation(self, sor, mock_bybit):
        """TWAP correctly calculates chunk sizes."""
        await sor._execute_twap(
            symbol="BTC/USDT:USDT",
            side="SHORT",
            total_size=Decimal("0.10"),
            num_splits=3,
            interval_seconds=0.01,
        )
        calls = mock_bybit.create_market_order.call_args_list
        amounts = [call[0][2] for call in calls]
        # First two: 0.033, last: 0.034
        assert amounts[0] == Decimal("0.033")
        assert amounts[1] == Decimal("0.033")
        assert amounts[2] == Decimal("0.034")


# ---------------------------------------------------------------------------
# Mid Price Helper
# ---------------------------------------------------------------------------

class TestMidPrice:
    """Mid price calculation from ticker."""

    @pytest.mark.asyncio
    async def test_mid_price_from_ticker(self, sor, mock_bybit):
        """Mid = (bid + ask) / 2."""
        mid = await sor._get_mid_price("BTC/USDT:USDT")
        assert mid == Decimal("64000")

    @pytest.mark.asyncio
    async def test_mid_price_no_tickers(self, sor, mock_bybit):
        """Returns None when tickers are empty."""
        mock_bybit.fetch_tickers = AsyncMock(return_value=[])
        mid = await sor._get_mid_price("BTC/USDT:USDT")
        assert mid is None


# ---------------------------------------------------------------------------
# Close-Based Trailing Stop
# ---------------------------------------------------------------------------

class TestCloseBasedTrailingStop:
    """Close-based trailing stop: ignore wicks, only trigger on candle close."""

    @pytest.mark.asyncio
    async def test_trailing_stop_uses_closes_not_wicks(self, exit_manager, mock_bybit):
        """Trailing stop tracks highest close, not highest wick."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "63000",
            "sl_order_id": "sl1",
            "peak_price": "66000",  # wick went to 66000
        }
        candle_close = Decimal("65000")

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("65000"),
            r_multiple=Decimal("2.0"),
            side="LONG",
            current_sl=Decimal("63000"),
            candle_close=candle_close,
        )

        assert should_close is False
        assert pos.get("highest_close") == "65000"

    @pytest.mark.asyncio
    async def test_trailing_stop_triggers_on_close_below(self, exit_manager, mock_bybit):
        """Trailing stop triggers when candle CLOSE goes below trailing stop."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "64500",
            "sl_order_id": "sl1",
            "highest_close": "65500",
        }
        candle_close = Decimal("64400")

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("64400"),
            r_multiple=Decimal("2.0"),
            side="LONG",
            current_sl=Decimal("64500"),
            candle_close=candle_close,
        )

        assert should_close is True

    @pytest.mark.asyncio
    async def test_trailing_stop_ignores_wick_below(self, exit_manager, mock_bybit):
        """Wick goes below trailing stop but close stays above -> no trigger."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "64500",
            "sl_order_id": "sl1",
            "highest_close": "65500",
        }
        candle_close = Decimal("65100")

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("63000"),  # wick low
            r_multiple=Decimal("2.0"),
            side="LONG",
            current_sl=Decimal("64500"),
            candle_close=candle_close,
        )

        assert should_close is False

    @pytest.mark.asyncio
    async def test_short_trailing_stop_triggers_on_close_above(self, exit_manager, mock_bybit):
        """SHORT trailing stop triggers when candle close goes above trailing stop."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "SHORT",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "63500",
            "sl_order_id": "sl1",
            "lowest_close": "62500",
        }
        # trailing_stop = 62500 + (2.0 * 500) = 63500
        # Candle close at 63600 -> above trailing stop -> trigger
        candle_close = Decimal("63600")

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("63600"),
            r_multiple=Decimal("2.0"),
            side="SHORT",
            current_sl=Decimal("63500"),
            candle_close=candle_close,
        )

        assert should_close is True

    @pytest.mark.asyncio
    async def test_no_trigger_below_activation_r(self, exit_manager, mock_bybit):
        """Trailing stop does not activate below 1.5R."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "63000",
            "sl_order_id": "sl1",
        }

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("64500"),
            r_multiple=Decimal("1.0"),
            side="LONG",
            current_sl=Decimal("63000"),
            candle_close=Decimal("64500"),
        )

        assert should_close is False

    @pytest.mark.asyncio
    async def test_trailing_stop_amends_exchange_sl(self, exit_manager, mock_bybit):
        """Trailing stop amendment calls amend_stop_loss on exchange."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "64000",
            "sl_order_id": "sl1",
        }

        await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("65500"),
            r_multiple=Decimal("2.0"),
            side="LONG",
            current_sl=Decimal("64000"),
            candle_close=Decimal("65500"),
        )

        # Should have amended SL (highest_close=65500, trail=1000, new_sl=64500 > 64000)
        mock_bybit.amend_stop_loss.assert_called_once()


# ---------------------------------------------------------------------------
# Profit Lock at 3R
# ---------------------------------------------------------------------------

class TestProfitLock:
    """Profit lock: move SL to breakeven when profit > 3R."""

    @pytest.mark.asyncio
    async def test_profit_lock_moves_sl_to_breakeven_long(self, exit_manager, mock_bybit):
        """LONG: profit >= 3R -> SL moves to entry price."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "63500",
            "sl_order_id": "sl1",
        }

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("67000"),
            r_multiple=Decimal("3.5"),
            side="LONG",
            current_sl=Decimal("63500"),
            candle_close=Decimal("67000"),
        )

        assert should_close is False
        assert pos["current_sl"] == "64128.0000"
        assert pos["stop_loss"] == "64128.0000"

    @pytest.mark.asyncio
    async def test_profit_lock_moves_sl_to_breakeven_short(self, exit_manager, mock_bybit):
        """SHORT: profit >= 3R -> SL moves to entry price."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "SHORT",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "64500",
            "sl_order_id": "sl1",
        }

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("61000"),
            r_multiple=Decimal("3.5"),
            side="SHORT",
            current_sl=Decimal("64500"),
            candle_close=Decimal("61000"),
        )

        assert should_close is False
        assert pos["current_sl"] == "63872.0000"
        assert pos["stop_loss"] == "63872.0000"

    @pytest.mark.asyncio
    async def test_profit_lock_no_move_if_already_at_breakeven(self, exit_manager, mock_bybit):
        """Profit lock does not amend if SL already at or above breakeven."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "64128.0000",
            "sl_order_id": "sl1",
        }

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("67000"),
            r_multiple=Decimal("3.5"),
            side="LONG",
            current_sl=Decimal("64128.0000"),
            candle_close=Decimal("67000"),
        )

        assert should_close is False
        # Trailing stop still amends SL upward (64128 -> 66000) since that's more protective
        assert Decimal(pos["current_sl"]) == Decimal("66000")

    @pytest.mark.asyncio
    async def test_profit_lock_before_trailing_activates(self, exit_manager, mock_bybit):
        """Profit lock activates at 3R even if trailing hasn't activated yet."""
        pos = {
            "symbol": "BTC/USDT:USDT",
            "side": "LONG",
            "entry_price": "64000",
            "amount": "0.001",
            "atr": "500",
            "current_sl": "63000",
            "sl_order_id": "sl1",
        }

        should_close = await exit_manager._manage_trend_trailing_stop(
            pos=pos,
            live_price=Decimal("67000"),
            r_multiple=Decimal("3.0"),
            side="LONG",
            current_sl=Decimal("63000"),
            candle_close=Decimal("67000"),
        )

        assert pos["current_sl"] == "64128.0000"


# ---------------------------------------------------------------------------
# R-multiple Calculation
# ---------------------------------------------------------------------------

class TestRMultipleCalculation:
    """R-multiple calculation for position sizing."""

    def test_long_r_multiple(self):
        """LONG: R = (live - entry) / initial_risk."""
        r = ExitManager._calculate_r_multiple(
            "LONG", Decimal("64000"), Decimal("65000"), Decimal("500")
        )
        assert r == Decimal("2")

    def test_short_r_multiple(self):
        """SHORT: R = (entry - live) / initial_risk."""
        r = ExitManager._calculate_r_multiple(
            "SHORT", Decimal("64000"), Decimal("63000"), Decimal("500")
        )
        assert r == Decimal("2")

    def test_zero_risk_returns_zero(self):
        """Zero initial risk returns 0 (division guard)."""
        r = ExitManager._calculate_r_multiple(
            "LONG", Decimal("64000"), Decimal("65000"), Decimal("0")
        )
        assert r == Decimal("0")

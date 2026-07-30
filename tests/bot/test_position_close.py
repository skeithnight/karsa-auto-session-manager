"""Tests for position close from Telegram inline keyboard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.handlers.callback_router import button_callback
from app.bot.handlers.positions import (
    _close_side,
    _execute_close_all_positions,
    _execute_close_position,
    _show_close_all_confirmation,
    _show_close_confirmation,
    view_positions_detail_cmd,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callback_update(data: str, chat_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.edit_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.username = "test_user"
    update.effective_user.id = chat_id
    return update


def _make_context(**extra_bot_data) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {
        "bybit_client": AsyncMock(),
        "redis_client": AsyncMock(),
        **extra_bot_data,
    }
    return ctx


def _pos(symbol="SOL/USDT", side="buy", size="10", entry="98.5", pnl="31.5"):
    return {
        "symbol": symbol,
        "side": side,
        "contracts": size,
        "entry_price": entry,
        "unrealized_pnl": pnl,
        "stopLoss": 0,
        "takeProfit": 0,
        "liquidationPrice": 0,
    }


# ---------------------------------------------------------------------------
# _close_side helper
# ---------------------------------------------------------------------------


class TestCloseSide:
    def test_buy_to_long(self):
        assert _close_side("buy") == "LONG"

    def test_buy_capitalized(self):
        assert _close_side("Buy") == "LONG"

    def test_sell_to_short(self):
        assert _close_side("sell") == "SHORT"

    def test_sell_capitalized(self):
        assert _close_side("Sell") == "SHORT"


# ---------------------------------------------------------------------------
# Close button appears in position details
# ---------------------------------------------------------------------------


class TestCloseButtonInDetail:
    @pytest.mark.asyncio
    async def test_close_button_uses_new_callback_format(self):
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "view_positions_detail"
        update.callback_query.answer = AsyncMock()
        update.callback_query.message = MagicMock()
        update.callback_query.message.edit_text = AsyncMock()
        update.effective_chat = MagicMock()

        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos()]
        )
        ctx.bot_data["bybit_client"].get_wallet_balance = AsyncMock(
            return_value={"balance": 10000, "available": 5000}
        )
        ctx.bot_data["redis_client"].get = AsyncMock(return_value=None)

        await view_positions_detail_cmd(update, ctx)

        # Verify edit_text was called (the view was rendered)
        assert update.callback_query.message.edit_text.called
        call_kwargs = update.callback_query.message.edit_text.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get(
            "reply_markup"
        )
        # Find the close button callback data
        close_data = None
        for row in markup.inline_keyboard:
            for btn in row:
                if "Close" in btn.text and "SL" not in btn.text:
                    close_data = btn.callback_data
                    break
        assert close_data is not None
        assert close_data == "close_position_SOL/USDT_LONG"


# ---------------------------------------------------------------------------
# Confirmation message format
# ---------------------------------------------------------------------------


class TestCloseConfirmation:
    @pytest.mark.asyncio
    async def test_confirmation_shows_position_details(self):
        update = _make_callback_update("close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(side="buy")]
        )
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(
            return_value=[{"last": "101.65"}]
        )

        await _show_close_confirmation(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "SOL/USDT" in text
        assert "LONG" in text
        assert "98.50" in text
        assert "31.50" in text

    @pytest.mark.asyncio
    async def test_confirmation_has_confirm_and_cancel_buttons(self):
        update = _make_callback_update("close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(side="buy")]
        )
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(return_value=[])

        await _show_close_confirmation(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        markup = call_kwargs.kwargs.get("reply_markup")
        all_callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        assert "confirm_close_position_SOL/USDT_LONG" in all_callbacks
        assert "cancel_close" in all_callbacks

    @pytest.mark.asyncio
    async def test_confirmation_no_position_found(self):
        update = _make_callback_update("close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])

        await _show_close_confirmation(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "No open" in text


# ---------------------------------------------------------------------------
# Close execution
# ---------------------------------------------------------------------------


class TestCloseExecution:
    @pytest.mark.asyncio
    async def test_close_calls_market_order(self):
        update = _make_callback_update("confirm_close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(side="buy")]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            return_value={"orderId": "123"}
        )
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(return_value=[])

        await _execute_close_position(update, ctx, "SOL/USDT", "LONG")

        ctx.bot_data["bybit_client"].create_market_order.assert_awaited_once_with(
            symbol="SOL/USDT", side="Sell", amount=10
        )

    @pytest.mark.asyncio
    async def test_close_confirmation_message_format(self):
        update = _make_callback_update("confirm_close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(side="buy")]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            return_value={"orderId": "123"}
        )
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(return_value=[])

        await _execute_close_position(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "Position Closed" in text
        assert "SOL/USDT" in text
        assert "LONG" in text
        assert "98.50" in text
        assert "31.50" in text
        assert "Closed at:" in text

    @pytest.mark.asyncio
    async def test_close_short_position_uses_buy_side(self):
        update = _make_callback_update("confirm_close_position_BTC/USDT_SHORT")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(symbol="BTC/USDT", side="sell")]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            return_value={"orderId": "456"}
        )
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(return_value=[])

        await _execute_close_position(update, ctx, "BTC/USDT", "SHORT")

        ctx.bot_data["bybit_client"].create_market_order.assert_awaited_once_with(
            symbol="BTC/USDT", side="Buy", amount=10
        )

    @pytest.mark.asyncio
    async def test_close_no_position_found(self):
        update = _make_callback_update("confirm_close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])

        await _execute_close_position(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "No open" in text

    @pytest.mark.asyncio
    async def test_close_api_failure_sends_error(self):
        update = _make_callback_update("confirm_close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos(side="buy")]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            side_effect=RuntimeError("Bybit API timeout")
        )

        await _execute_close_position(update, ctx, "SOL/USDT", "LONG")

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "Failed to close" in text
        assert "Bybit API timeout" in text


# ---------------------------------------------------------------------------
# Close All confirmation
# ---------------------------------------------------------------------------


class TestCloseAllConfirmation:
    @pytest.mark.asyncio
    async def test_close_all_shows_all_positions(self):
        update = _make_callback_update("close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[
                _pos(symbol="SOL/USDT", side="buy", pnl="31.50"),
                _pos(symbol="AVAX/USDT", side="sell", pnl="-5.25"),
            ]
        )

        await _show_close_all_confirmation(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "SOL/USDT" in text
        assert "AVAX/USDT" in text
        assert "31.50" in text
        assert "-5.25" in text
        assert "Close ALL" in text

    @pytest.mark.asyncio
    async def test_close_all_has_confirm_button(self):
        update = _make_callback_update("close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[_pos()]
        )

        await _show_close_all_confirmation(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        markup = call_kwargs.kwargs.get("reply_markup")
        all_callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        assert "confirm_close_all_positions" in all_callbacks
        assert "cancel_close" in all_callbacks

    @pytest.mark.asyncio
    async def test_close_all_no_positions(self):
        update = _make_callback_update("close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])

        await _show_close_all_confirmation(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "No open positions" in text


# ---------------------------------------------------------------------------
# Close All execution
# ---------------------------------------------------------------------------


class TestCloseAllExecution:
    @pytest.mark.asyncio
    async def test_close_all_calls_market_order_for_each(self):
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[
                _pos(symbol="SOL/USDT", side="buy"),
                _pos(symbol="AVAX/USDT", side="sell"),
            ]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            return_value={"orderId": "1"}
        )

        await _execute_close_all_positions(update, ctx)

        assert ctx.bot_data["bybit_client"].create_market_order.await_count == 2

    @pytest.mark.asyncio
    async def test_close_all_sends_summary(self):
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[
                _pos(symbol="SOL/USDT", side="buy"),
                _pos(symbol="AVAX/USDT", side="sell"),
            ]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            return_value={"orderId": "1"}
        )

        await _execute_close_all_positions(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "Close All executed" in text
        assert "2/2" in text

    @pytest.mark.asyncio
    async def test_close_all_no_positions(self):
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])

        await _execute_close_all_positions(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "No open positions" in text

    @pytest.mark.asyncio
    async def test_close_all_partial_failure(self):
        """One position fails, others still close."""
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            return_value=[
                _pos(symbol="SOL/USDT", side="buy"),
                _pos(symbol="AVAX/USDT", side="sell"),
            ]
        )
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock(
            side_effect=[
                {"orderId": "1"},
                RuntimeError("Insufficient margin"),
            ]
        )

        await _execute_close_all_positions(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "1/2" in text

    @pytest.mark.asyncio
    async def test_close_all_api_failure(self):
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(
            side_effect=RuntimeError("Connection lost")
        )

        await _execute_close_all_positions(update, ctx)

        call_kwargs = update.callback_query.message.edit_text.call_args
        text = call_kwargs.args[0]
        assert "Close all failed" in text


# ---------------------------------------------------------------------------
# Cancel close
# ---------------------------------------------------------------------------


class TestCancelClose:
    @pytest.mark.asyncio
    async def test_cancel_returns_to_positions_detail(self):
        update = _make_callback_update("cancel_close")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])
        ctx.bot_data["bybit_client"].get_wallet_balance = AsyncMock(
            return_value={"balance": 0, "available": 0}
        )
        ctx.bot_data["redis_client"].get = AsyncMock(return_value=None)

        await button_callback(update, ctx)

        # Should have routed to view_positions_detail_cmd
        update.callback_query.answer.assert_awaited_once()
        update.callback_query.message.edit_text.assert_awaited()


# ---------------------------------------------------------------------------
# Callback routing
# ---------------------------------------------------------------------------


class TestCallbackRouting:
    @pytest.mark.asyncio
    async def test_close_position_routes_to_confirmation(self):
        update = _make_callback_update("close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])
        ctx.bot_data["bybit_client"].fetch_tickers = AsyncMock(return_value=[])

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()
        # Should have attempted to edit the message (confirmation or error)
        assert update.callback_query.message.edit_text.called

    @pytest.mark.asyncio
    async def test_confirm_close_routes_to_execution(self):
        update = _make_callback_update("confirm_close_position_SOL/USDT_LONG")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock()

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()
        assert update.callback_query.message.edit_text.called

    @pytest.mark.asyncio
    async def test_close_all_routes_to_confirmation(self):
        update = _make_callback_update("close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()
        assert update.callback_query.message.edit_text.called

    @pytest.mark.asyncio
    async def test_confirm_close_all_routes_to_execution(self):
        update = _make_callback_update("confirm_close_all_positions")
        ctx = _make_context()
        ctx.bot_data["bybit_client"].fetch_positions = AsyncMock(return_value=[])
        ctx.bot_data["bybit_client"].create_market_order = AsyncMock()

        await button_callback(update, ctx)

        update.callback_query.answer.assert_awaited_once()
        assert update.callback_query.message.edit_text.called

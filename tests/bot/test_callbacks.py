"""Tests for inline keyboard callback routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.handlers import button_callback


def _make_callback_update(data: str, chat_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.username = "test_user"
    update.effective_user.id = chat_id
    return update


class TestCallbackRouting:
    @pytest.mark.asyncio
    async def test_noop_callback_answered(self):
        update = _make_callback_update("noop")
        ctx = MagicMock()
        ctx.bot_data = {"redis_client": AsyncMock()}
        await button_callback(update, ctx)
        update.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_callback_does_not_crash(self):
        update = _make_callback_update("unknown_data_xyz")
        ctx = MagicMock()
        ctx.bot_data = {"redis_client": AsyncMock()}
        await button_callback(update, ctx)
        update.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_asm_stub_callback(self):
        """auto_launch triggers the ASM stub — returns not-yet-available message."""
        update = _make_callback_update("auto_launch")
        ctx = MagicMock()
        ctx.bot_data = {"redis_client": AsyncMock()}
        await button_callback(update, ctx)
        update.callback_query.answer.assert_awaited_once()

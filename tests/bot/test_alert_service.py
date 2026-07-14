"""Tests for AlertService — lazy bot registration + send.

Importers: pytest auto-discovery. Callers: none (test file).
Affected API: AlertService.register_bot, AlertService.send.
Data schemas: none. Verbatim instruction: "b" (set up Telegram alerts).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.alert_service import AlertService


@pytest.fixture
def alert():
    return AlertService("12345")


def test_init_with_chat_id():
    svc = AlertService("999")
    assert svc._chat_id == 999
    assert svc._bot is None


def test_init_empty_chat_id():
    svc = AlertService("")
    assert svc._chat_id == 0
    assert svc._bot is None


def test_register_bot(alert):
    bot = MagicMock()
    alert.register_bot(bot)
    assert alert._bot is bot


@pytest.mark.asyncio
async def test_send_no_bot(alert):
    """No bot registered → silently drops, no error."""
    await alert.send("test")


@pytest.mark.asyncio
async def test_send_no_chat_id():
    svc = AlertService("")
    bot = AsyncMock()
    svc.register_bot(bot)
    await svc.send("test")
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_success(alert):
    bot = AsyncMock()
    alert.register_bot(bot)
    await alert.send("<b>hello</b>")
    bot.send_message.assert_awaited_once_with(12345, "<b>hello</b>", parse_mode="HTML")


@pytest.mark.asyncio
async def test_send_api_error(alert):
    """API error → caught, no crash."""
    bot = AsyncMock()
    bot.send_message.side_effect = Exception("network")
    alert.register_bot(bot)
    await alert.send("boom")
    bot.send_message.assert_awaited_once()

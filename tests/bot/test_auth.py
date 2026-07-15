"""Tests for Telegram bot authorization boundary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from app.bot.handlers import _is_authorized


def _make_update(chat_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    return update


class TestAuthorization:
    @patch("app.bot.handlers.settings")
    def test_authorized_user_passes(self, mock_settings):
        mock_settings.telegram_chat_id = "12345"
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is True

    @patch("app.bot.handlers.settings")
    def test_unauthorized_user_rejected(self, mock_settings):
        mock_settings.telegram_chat_id = "12345"
        update = _make_update(chat_id=99999)
        assert _is_authorized(update) is False

    @patch("app.bot.handlers.settings")
    def test_empty_chat_id_config_rejects_all(self, mock_settings):
        mock_settings.telegram_chat_id = ""
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is False

    @patch("app.bot.handlers.settings")
    def test_none_chat_id_config_rejects_all(self, mock_settings):
        mock_settings.telegram_chat_id = None
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is False

    @patch("app.bot.handlers.settings")
    def test_chat_id_as_integer(self, mock_settings):
        mock_settings.telegram_chat_id = "12345"
        update = MagicMock()
        update.effective_chat.id = 12345
        assert _is_authorized(update) is True

    @patch("app.bot.handlers.settings")
    def test_none_effective_chat_rejected(self, mock_settings):
        mock_settings.telegram_chat_id = "12345"
        update = MagicMock()
        update.effective_chat = None
        assert _is_authorized(update) is False

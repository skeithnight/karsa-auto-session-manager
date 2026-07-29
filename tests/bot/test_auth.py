"""Tests for Telegram bot authorization boundary."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.bot.handlers import _is_authorized


def _make_update(chat_id: int = 12345) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    return update


class TestAuthorization:
    def test_authorized_user_passes(self):
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is True

    def test_unauthorized_user_also_passes_bypass(self):
        """Auth is currently bypassed — all users are authorized."""
        update = _make_update(chat_id=99999)
        assert _is_authorized(update) is True

    def test_empty_chat_id_config_still_passes_bypass(self):
        """Auth is currently bypassed — config doesn't matter."""
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is True

    def test_none_chat_id_config_still_passes_bypass(self):
        """Auth is currently bypassed — config doesn't matter."""
        update = _make_update(chat_id=12345)
        assert _is_authorized(update) is True

    def test_chat_id_as_integer(self):
        update = MagicMock()
        update.effective_chat.id = 12345
        assert _is_authorized(update) is True

    def test_none_effective_chat_still_passes_bypass(self):
        """Auth is currently bypassed — None chat doesn't matter."""
        update = MagicMock()
        update.effective_chat = None
        assert _is_authorized(update) is True

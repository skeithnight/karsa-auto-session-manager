"""Tests for app.commander.main — standalone entrypoint.

Mocks all external dependencies (Redis, DB, Bybit) to verify
wiring logic without real infrastructure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCommanderEntrypoint:
    @pytest.mark.asyncio
    async def test_imports_and_settings(self) -> None:
        """Verify commander entrypoint imports without error."""
        from app.commander import main
        assert main is not None

    @pytest.mark.asyncio
    async def test_configure_logging(self) -> None:
        """Verify structured JSON logging config."""
        from app.commander.main import _configure_logging
        _configure_logging()
        import logging
        root = logging.getLogger()
        assert len(root.handlers) > 0
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)

    @pytest.mark.asyncio
    async def test_connect_services_with_mocked_deps(self) -> None:
        """Verify RedisClient and DatabaseEngine connect calls."""
        from app.commander.main import _connect_services

        with (
            patch("app.commander.main.RedisClient") as mock_redis_cls,
            patch("app.commander.main.DatabaseEngine") as mock_db_cls,
        ):
            mock_redis = MagicMock()
            mock_redis.connect = AsyncMock()
            mock_redis_cls.return_value = mock_redis

            mock_db = MagicMock()
            mock_db.connect = AsyncMock()
            mock_db_cls.return_value = mock_db

            redis_client, db_engine = await _connect_services()

            assert redis_client is not None
            assert db_engine is not None
            mock_redis.connect.assert_awaited_once()
            mock_db.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_services_integration(self) -> None:
        """Verify the function signature returns expected types."""
        from app.commander.main import _connect_services

        with (
            patch("app.commander.main.RedisClient") as mock_redis_cls,
            patch("app.commander.main.DatabaseEngine") as mock_db_cls,
        ):
            mock_redis_cls.return_value.connect = AsyncMock()
            mock_db_cls.return_value.connect = AsyncMock()

            redis_client, db_engine = await _connect_services()

            assert isinstance(redis_client, MagicMock)
            # Verify RedisClient was constructed
            mock_redis_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_entrypoint_returns_when_no_token(self) -> None:
        """Verify main() returns early if telegram token missing."""
        from app.commander.main import main

        with (
            patch("app.commander.main.get_settings") as mock_settings,
            patch("app.commander.main._configure_logging"),
            patch("app.commander.main.asyncio.get_running_loop"),
        ):
            settings = MagicMock()
            settings.telegram_bot_token = ""
            settings.karsa_role = "commander"
            mock_settings.return_value = settings

            result = await main()
            assert result is None


class TestCommanderModuleImports:
    def test_import_alert_service(self) -> None:
        from app.bot.alert_service import AlertService
        assert AlertService is not None

    def test_import_session_manager(self) -> None:
        from app.core.session import AutonomousSessionManager
        assert AutonomousSessionManager is not None

    def test_import_run_bot(self) -> None:
        from app.bot.runner import run_bot
        assert run_bot is not None

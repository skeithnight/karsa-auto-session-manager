"""Tests for Core Config."""

import os
from unittest.mock import patch

from app.core.config import Settings, get_settings


class TestSettings:
    """Test suite for Settings class."""

    def test_settings_from_env(self) -> None:
        """Test that settings load from environment variables."""
        env_vars = {
            "BYBIT_API_KEY": "test_key",
            "BYBIT_API_SECRET": "test_secret",
            "POSTGRES_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
            "REDIS_URL": "redis://localhost:6379/1",
        }

        with patch.dict(os.environ, env_vars):
            settings = Settings(_env_file=None)

            assert settings.bybit_api_key == "test_key"
            assert settings.bybit_api_secret == "test_secret"

    def test_settings_defaults(self) -> None:
        """Test that default values are set correctly."""
        env_vars = {
            "BYBIT_API_KEY": "test_key",
            "BYBIT_API_SECRET": "test_secret",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings(_env_file=None)

            assert settings.postgres_url == "postgresql+asyncpg://karsa:karsa@db:5432/karsa"
            assert settings.redis_url == "redis://redis:6379/0"
            assert settings.daily_drawdown_limit == "-0.02"

    def test_settings_symbols(self) -> None:
        """Test that default symbols are set correctly."""
        env_vars = {
            "BYBIT_API_KEY": "test_key",
            "BYBIT_API_SECRET": "test_secret",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings(_env_file=None)

            assert "BTC/USDT" in settings.symbols
            assert "ETH/USDT" in settings.symbols
            assert len(settings.symbols) >= 1

    def test_get_settings_returns_settings(self) -> None:
        """Test that get_settings returns a Settings instance."""
        env_vars = {
            "BYBIT_API_KEY": "test_key",
            "BYBIT_API_SECRET": "test_secret",
        }
        with patch.dict(os.environ, env_vars):
            settings = get_settings()
            assert isinstance(settings, Settings)

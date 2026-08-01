"""Tests for Autonomous Session Manager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core.session import AutonomousSessionManager


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client."""
    redis = AsyncMock()
    redis._store: dict[str, str] = {}

    async def fake_set(key: str, value: str) -> None:
        redis._store[key] = value

    async def fake_get(key: str) -> str | None:
        return redis._store.get(key)

    async def fake_delete(key: str) -> None:
        redis._store.pop(key, None)

    redis.set = fake_set
    redis.get = fake_get
    redis.delete = fake_delete
    return redis


@pytest.fixture
def kill_switch() -> asyncio.Event:
    return asyncio.Event()


@pytest.fixture
def session_mgr(mock_redis: AsyncMock, kill_switch: asyncio.Event) -> AutonomousSessionManager:
    return AutonomousSessionManager(mock_redis, kill_switch)


class TestAutonomousSessionManager:
    """ASM core logic tests."""

    @pytest.mark.asyncio
    async def test_start_session_writes_redis(self, session_mgr: AutonomousSessionManager, mock_redis: AsyncMock) -> None:
        await session_mgr.start_session(duration_min=30, risk_pct=3, max_pos=5, interval_min=15)
        assert await mock_redis.get("karsa:auto:state:active") == "1"
        config = json.loads(await mock_redis.get("karsa:auto:config"))
        assert config["duration_min"] == 30
        assert config["risk_pct"] == 3
        assert config["max_pos"] == 5
        assert await mock_redis.get("karsa:auto:start_time") is not None

    @pytest.mark.asyncio
    async def test_stop_session_clears_redis(self, session_mgr: AutonomousSessionManager, mock_redis: AsyncMock) -> None:
        await session_mgr.start_session(duration_min=30, risk_pct=3, max_pos=5)
        await session_mgr.stop_session()
        assert await mock_redis.get("karsa:auto:state:active") == "0"
        assert await mock_redis.get("karsa:auto:config") is None
        assert await mock_redis.get("karsa:auto:start_time") is None

    @pytest.mark.asyncio
    async def test_is_active(self, session_mgr: AutonomousSessionManager, mock_redis: AsyncMock) -> None:
        assert await session_mgr.is_active() is False
        await session_mgr.start_session(duration_min=30, risk_pct=3, max_pos=5)
        assert await session_mgr.is_active() is True
        await session_mgr.stop_session()
        assert await session_mgr.is_active() is False

    @pytest.mark.asyncio
    async def test_get_config(self, session_mgr: AutonomousSessionManager, mock_redis: AsyncMock) -> None:
        assert await session_mgr.get_config() is None
        await session_mgr.start_session(duration_min=60, risk_pct=5, max_pos=3)
        config = await session_mgr.get_config()
        assert config is not None
        assert config["duration_min"] == 60
        assert config["risk_pct"] == 5

    @pytest.mark.asyncio
    async def test_duration_expiry(self, session_mgr: AutonomousSessionManager, mock_redis: AsyncMock) -> None:
        """Session with 0 duration (infinite) should not auto-expire."""
        await session_mgr.start_session(duration_min=0, risk_pct=3, max_pos=3)
        session_mgr.kill_switch.set()
        await session_mgr.run_loop()
        assert await session_mgr.is_active() is True

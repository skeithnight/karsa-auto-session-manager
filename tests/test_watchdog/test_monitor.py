"""Tests for Watchdog."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.watchdog.monitor import Watchdog
from app.core.redis_client import RedisClient


@pytest.fixture
def mock_redis():
    with patch("app.core.redis_client.get_settings"):
        client = RedisClient()
        client.redis = AsyncMock()
        return client


@pytest.fixture
def mock_sor():
    sor = MagicMock()
    sor.skip_to_market = False
    sor.cancel_all_positions = AsyncMock()
    return sor


@pytest.fixture
def watchdog(mock_redis):
    return Watchdog(mock_redis, check_interval=1)


@pytest.fixture
def watchdog_full(mock_redis, mock_sor):
    alpha_paused = MagicMock()
    alpha_paused.is_set = MagicMock(return_value=False)
    alpha_paused.set = MagicMock()
    alpha_paused.clear = MagicMock()
    kill_switch = MagicMock()
    kill_switch.is_set = MagicMock(return_value=False)
    kill_switch.set = MagicMock()
    return Watchdog(
        mock_redis,
        alpha_paused=alpha_paused,
        sor=mock_sor,
        kill_switch=kill_switch,
        check_interval=1,
    )


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_start_stop(self, watchdog):
        """Watchdog starts and stops cleanly."""
        watchdog.running = True
        assert watchdog.running is True
        await watchdog.stop()
        assert watchdog.running is False

    @pytest.mark.asyncio
    async def test_get_status(self, watchdog):
        """Status returns expected keys."""
        status = watchdog.get_status()
        assert "running" in status
        assert "last_heartbeat" in status
        assert "check_interval" in status

    @pytest.mark.asyncio
    async def test_check_heartbeat_fresh(self, watchdog, mock_redis):
        """Fresh heartbeat — no warning, no pause."""
        now = datetime.now(timezone.utc).isoformat()
        mock_redis.get_exchange_heartbeats = AsyncMock(
            return_value={"binance": now, "okx": now, "bybit": now}
        )
        await watchdog._check_heartbeat()

    @pytest.mark.asyncio
    async def test_check_heartbeat_stale(self, watchdog_full, mock_redis):
        """Stale heartbeat — alpha_paused set."""
        from datetime import timedelta
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        mock_redis.get_exchange_heartbeats = AsyncMock(
            return_value={"binance": stale, "okx": fresh}
        )
        await watchdog_full._check_heartbeat()
        watchdog_full.alpha_paused.set.assert_called()

    @pytest.mark.asyncio
    async def test_check_heartbeat_none(self, watchdog, mock_redis):
        """No heartbeats — logs warning."""
        mock_redis.get_exchange_heartbeats = AsyncMock(return_value={})
        await watchdog._check_heartbeat()

    @pytest.mark.asyncio
    async def test_record_latency(self, watchdog):
        """Record latency samples."""
        watchdog.record_latency(0.5)
        watchdog.record_latency(1.2)
        assert len(watchdog._latency_samples) == 2

    @pytest.mark.asyncio
    async def test_latency_high_switches_sor(self, watchdog_full):
        """High latency average — SOR switches to market."""
        for _ in range(5):
            watchdog_full.record_latency(2.0)
        watchdog_full._check_latency()
        assert watchdog_full.sor.skip_to_market is True

    @pytest.mark.asyncio
    async def test_latency_low_clears_sor(self, watchdog_full):
        """Low latency — SOR resumes normal."""
        watchdog_full.sor.skip_to_market = True
        for _ in range(5):
            watchdog_full.record_latency(0.3)
        watchdog_full._check_latency()
        assert watchdog_full.sor.skip_to_market is False

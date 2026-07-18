"""Tests for scripts/bootstrap_local.py and scripts/validate_e2e.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.bootstrap_local import _timeframe_to_ms, check_already_bootstrapped
from scripts.validate_e2e import (
    CheckResult,
    ValidationReport,
    check_backtest_queue,
    check_redis_keys,
)


class TestTimeframeConversion:
    def test_1h(self) -> None:
        assert _timeframe_to_ms("1h") == 3_600_000

    def test_4h(self) -> None:
        assert _timeframe_to_ms("4h") == 14_400_000

    def test_1d(self) -> None:
        assert _timeframe_to_ms("1d") == 86_400_000

    def test_15m(self) -> None:
        assert _timeframe_to_ms("15m") == 900_000

    def test_1w(self) -> None:
        assert _timeframe_to_ms("1w") == 604_800_000


class TestCheckAlreadyBootstrapped:
    @pytest.mark.asyncio
    async def test_empty_db(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        assert await check_already_bootstrapped(pool) is False

    @pytest.mark.asyncio
    async def test_has_data(self) -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=1000)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        assert await check_already_bootstrapped(pool) is True


class TestValidationReport:
    def test_empty_passes(self) -> None:
        report = ValidationReport()
        assert report.passed is True
        assert report.failed_count == 0

    def test_all_pass(self) -> None:
        report = ValidationReport(checks=[CheckResult("a", True), CheckResult("b", True)])
        assert report.passed is True

    def test_one_fails(self) -> None:
        report = ValidationReport(checks=[CheckResult("a", True), CheckResult("b", False)])
        assert report.passed is False
        assert report.failed_count == 1


class TestCheckRedisKeys:
    @pytest.mark.asyncio
    async def test_all_keys_present(self) -> None:
        r = AsyncMock()
        r.get = AsyncMock(return_value='{"symbols": ["BTC/USDT"]}')
        r.aclose = AsyncMock()
        with patch("scripts.validate_e2e.aioredis.from_url", return_value=r):
            result = await check_redis_keys("redis://localhost:6379/0")
            assert result.passed is True

    @pytest.mark.asyncio
    async def test_missing_keys(self) -> None:
        r = AsyncMock()
        r.get = AsyncMock(return_value=None)
        r.aclose = AsyncMock()
        with patch("scripts.validate_e2e.aioredis.from_url", return_value=r):
            result = await check_redis_keys("redis://localhost:6379/0")
            assert result.passed is False
            assert "Missing" in result.detail

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        with patch("scripts.validate_e2e.aioredis.from_url", side_effect=Exception("conn refused")):
            result = await check_redis_keys("redis://localhost:6379/0")
            assert result.passed is False
            assert "conn refused" in result.detail


class TestCheckBacktestQueue:
    @pytest.mark.asyncio
    async def test_push_pop_roundtrip(self) -> None:
        r = AsyncMock()
        r.rpush = AsyncMock(return_value=1)
        r.lpop = AsyncMock(return_value='{"job_id": "test"}')
        with patch("scripts.validate_e2e.aioredis.from_url", return_value=r):
            result = await check_backtest_queue("redis://localhost:6379/0")
            assert result.passed is True

    @pytest.mark.asyncio
    async def test_pop_returns_none(self) -> None:
        r = AsyncMock()
        r.rpush = AsyncMock(return_value=1)
        r.lpop = AsyncMock(return_value=None)
        with patch("scripts.validate_e2e.aioredis.from_url", return_value=r):
            result = await check_backtest_queue("redis://localhost:6379/0")
            assert result.passed is False

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        with patch("scripts.validate_e2e.aioredis.from_url", side_effect=Exception("down")):
            result = await check_backtest_queue("redis://localhost:6379/0")
            assert result.passed is False

"""Tests for app.core.telemetry — TelemetryEmitter, health readers, format."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.telemetry import (
    SERVICE_HEARTBEAT_TTL,
    ServiceHealth,
    TelemetryEmitter,
    _read_rss_mb,
    format_health_summary,
    get_all_services_health,
)

# ---------------------------------------------------------------------------
# ServiceHealth dataclass
# ---------------------------------------------------------------------------

class TestServiceHealth:
    def test_defaults(self) -> None:
        h = ServiceHealth(service_name="test")
        assert h.status == "unknown"
        assert h.memory_mb == 0.0
        assert h.candles_ingested == 0


# ---------------------------------------------------------------------------
# _read_rss_mb
# ---------------------------------------------------------------------------

class TestReadRssMb:
    def test_returns_float_or_zero(self) -> None:
        val = _read_rss_mb()
        assert isinstance(val, float)
        assert val >= 0.0


# ---------------------------------------------------------------------------
# TelemetryEmitter lifecycle
# ---------------------------------------------------------------------------

class TestTelemetryEmitter:
    def _make_redis(self) -> AsyncMock:
        r = AsyncMock()
        r.setex = AsyncMock()
        return r

    def test_init(self) -> None:
        r = self._make_redis()
        emitter = TelemetryEmitter(r, "test-svc")
        assert emitter.service_name == "test-svc"
        assert emitter.candles_ingested == 0

    def test_record_candle(self) -> None:
        emitter = TelemetryEmitter(self._make_redis(), "svc")
        emitter.record_candle("1700000000")
        assert emitter.candles_ingested == 1
        assert emitter.last_candle_ts == "1700000000"

    def test_record_candle_no_ts(self) -> None:
        emitter = TelemetryEmitter(self._make_redis(), "svc")
        emitter.record_candle()
        assert emitter.candles_ingested == 1
        assert emitter.last_candle_ts == ""

    def test_record_signal(self) -> None:
        emitter = TelemetryEmitter(self._make_redis(), "svc")
        emitter.record_signal()
        emitter.record_signal()
        assert emitter.signals_fired == 2  # noqa: PLR2004

    def test_record_order(self) -> None:
        emitter = TelemetryEmitter(self._make_redis(), "svc")
        emitter.record_order()
        assert emitter.orders_placed == 1

    def test_record_error(self) -> None:
        emitter = TelemetryEmitter(self._make_redis(), "svc")
        emitter.record_error()
        assert emitter.error_count == 1

    @pytest.mark.asyncio
    async def test_write_heartbeat_calls_setex(self) -> None:
        r = self._make_redis()
        emitter = TelemetryEmitter(r, "svc")
        await emitter._write_heartbeat()
        r.setex.assert_called_once()
        key, ttl, payload = r.setex.call_args[0]
        assert key == "health:svc"
        assert ttl == SERVICE_HEARTBEAT_TTL
        data = json.loads(payload)
        assert data["service_name"] == "svc"
        assert "last_heartbeat" in data

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self) -> None:
        r = self._make_redis()
        emitter = TelemetryEmitter(r, "svc", interval=60)
        await emitter.start()
        assert emitter._task is not None
        # Let first heartbeat tick
        await asyncio.sleep(0)
        # Second start is no-op
        await emitter.start()
        assert emitter._task is not None
        await emitter.heartbeat_now()
        await emitter.stop()
        assert emitter._task is None
        # At least one heartbeat from start + final from stop
        assert r.setex.call_count >= 2

    @pytest.mark.asyncio
    async def test_start_stop_on_error_redis(self) -> None:
        r = self._make_redis()
        r.setex = AsyncMock(side_effect=Exception("connection lost"))
        emitter = TelemetryEmitter(r, "svc", interval=0.01)
        await emitter.start()
        await asyncio.sleep(0.05)  # let heartbeat loop tick
        await emitter.stop()
        # No crash despite Redis error

    @pytest.mark.asyncio
    async def test_heartbeat_now(self) -> None:
        r = self._make_redis()
        emitter = TelemetryEmitter(r, "svc")
        await emitter.heartbeat_now()
        r.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_payload_includes_counters(self) -> None:
        r = self._make_redis()
        emitter = TelemetryEmitter(r, "svc")
        emitter.record_candle("ts1")
        emitter.record_signal()
        emitter.record_order()
        emitter.record_error()
        emitter.positions_open = 3
        await emitter._write_heartbeat()
        payload = json.loads(r.setex.call_args[0][2])
        assert payload["candles_ingested"] == 1
        assert payload["last_candle_ts"] == "ts1"
        assert payload["signals_fired"] == 1
        assert payload["orders_placed"] == 1
        assert payload["error_count"] == 1
        assert payload["positions_open"] == 3
        assert payload["uptime_s"] >= 0


# ---------------------------------------------------------------------------
# get_all_services_health
# ---------------------------------------------------------------------------

class TestGetAllServicesHealth:
    @pytest.mark.asyncio
    async def test_empty_keys(self) -> None:
        r = AsyncMock()
        r.keys = AsyncMock(return_value=[])
        result = await get_all_services_health(r)
        assert result == {}

    @pytest.mark.asyncio
    async def test_fresh_service(self) -> None:
        now = datetime.now(UTC).isoformat()
        payload = {
            "service_name": "data-engine",
            "last_heartbeat": now,
            "last_candle_ts": "1700000000",
            "candles_ingested": 42,
            "positions_open": 0,
            "signals_fired": 0,
            "orders_placed": 0,
            "error_count": 0,
            "memory_mb": 12.5,
            "uptime_s": 300.0,
            "python_version": "3.11.0",
            "service_version": "0.1.0",
            "exchange_state": "connected",
        }
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:data-engine"])
        r.get = AsyncMock(return_value=json.dumps(payload))
        result = await get_all_services_health(r)
        assert "data-engine" in result
        h = result["data-engine"]
        assert h.status == "fresh"
        assert h.candles_ingested == 42
        assert h.memory_mb == 12.5
        assert h.exchange_state == "connected"

    @pytest.mark.asyncio
    async def test_stale_service(self) -> None:
        stale_dt = datetime.now(UTC) - timedelta(seconds=120)
        payload = {
            "service_name": "live",
            "last_heartbeat": stale_dt.isoformat(),
            "last_candle_ts": "",
            "candles_ingested": 0,
            "positions_open": 0,
            "signals_fired": 0,
            "orders_placed": 0,
            "error_count": 0,
            "memory_mb": 0,
            "uptime_s": 0,
            "python_version": "",
            "service_version": "",
            "exchange_state": "",
        }
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:live"])
        r.get = AsyncMock(return_value=json.dumps(payload))
        result = await get_all_services_health(r)
        assert result["live"].status == "stale"

    @pytest.mark.asyncio
    async def test_dead_service(self) -> None:
        dead_dt = datetime.now(UTC) - timedelta(seconds=200)
        payload = {
            "service_name": "shadow",
            "last_heartbeat": dead_dt.isoformat(),
            "last_candle_ts": "",
            "candles_ingested": 0,
            "positions_open": 0,
            "signals_fired": 0,
            "orders_placed": 0,
            "error_count": 0,
            "memory_mb": 0,
            "uptime_s": 0,
            "python_version": "",
            "service_version": "",
            "exchange_state": "",
        }
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:shadow"])
        r.get = AsyncMock(return_value=json.dumps(payload))
        result = await get_all_services_health(r)
        assert result["shadow"].status == "dead"

    @pytest.mark.asyncio
    async def test_none_value(self) -> None:
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:svc"])
        r.get = AsyncMock(return_value=None)
        result = await get_all_services_health(r)
        assert result["svc"].status == "dead"

    @pytest.mark.asyncio
    async def test_bad_json(self) -> None:
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:svc"])
        r.get = AsyncMock(return_value="not-json")
        result = await get_all_services_health(r)
        assert result["svc"].status == "dead"

    @pytest.mark.asyncio
    async def test_keys_error(self) -> None:
        r = AsyncMock()
        r.keys = AsyncMock(side_effect=Exception("connection lost"))
        result = await get_all_services_health(r)
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_error(self) -> None:
        r = AsyncMock()
        r.keys = AsyncMock(return_value=["health:svc"])
        r.get = AsyncMock(side_effect=Exception("timeout"))
        result = await get_all_services_health(r)
        assert result["svc"].status == "unknown"


# ---------------------------------------------------------------------------
# format_health_summary
# ---------------------------------------------------------------------------

class TestFormatHealthSummary:
    def test_empty(self) -> None:
        assert format_health_summary({}) == "No telemetry data."

    def test_single_fresh(self) -> None:
        h = ServiceHealth(
            service_name="data-engine",
            status="fresh",
            memory_mb=42.0,
            candles_ingested=100,
            orders_placed=5,
            positions_open=2,
            last_candle_ts="1700000000",
        )
        result = format_health_summary({"data-engine": h})
        assert "🟢" in result
        assert "DATA" in result
        assert "42M" in result

    def test_stale_and_dead(self) -> None:
        h1 = ServiceHealth(service_name="live", status="stale", memory_mb=10.0)
        h2 = ServiceHealth(service_name="shadow", status="dead", memory_mb=0.0)
        result = format_health_summary({"live": h1, "shadow": h2})
        assert "🟡" in result
        assert "🔴" in result
        assert "LIVE" in result
        assert "SHDW" in result

    def test_unknown_status(self) -> None:
        h = ServiceHealth(service_name="svc", status="unknown")
        result = format_health_summary({"svc": h})
        assert "⚪" in result

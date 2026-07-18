"""Tests for historical candle ingestion script.

Mocks CCXT and asyncpg to test pagination, normalization, and error handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.ingest_historical_candles import (
    _standardize_symbol,
    _timeframe_to_ms,
    fetch_candles_range,
    fetch_all_candles_for_symbol,
    bulk_upsert_candles,
)


# ── Test _standardize_symbol ───────────────────────────────


class TestStandardizeSymbol:
    def test_btcusdt_to_unified(self) -> None:
        assert _standardize_symbol("BTCUSDT") == "BTC/USDT"

    def test_ethusdt_to_unified(self) -> None:
        assert _standardize_symbol("ETHUSDT") == "ETH/USDT"

    def test_already_unified(self) -> None:
        assert _standardize_symbol("BTC/USDT") == "BTC/USDT"

    def test_lowercase_input(self) -> None:
        assert _standardize_symbol("solusdt") == "SOL/USDT"

    def test_unknown_suffix(self) -> None:
        assert _standardize_symbol("BTCUSDC") == "BTCUSDC"


# ── Test _timeframe_to_ms ──────────────────────────────────


class TestTimeframeToMs:
    def test_one_hour(self) -> None:
        assert _timeframe_to_ms("1h") == 3_600_000

    def test_fifteen_minutes(self) -> None:
        assert _timeframe_to_ms("15m") == 900_000

    def test_one_day(self) -> None:
        assert _timeframe_to_ms("1d") == 86_400_000

    def test_one_week(self) -> None:
        assert _timeframe_to_ms("1w") == 604_800_000


# ── Test fetch_candles_range ───────────────────────────────


class TestFetchCandlesRange:
    @pytest.mark.asyncio
    async def test_returns_candles_on_success(self) -> None:
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock(
            return_value=[
                [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0],
                [1700003600000, 101.0, 103.0, 100.0, 102.0, 1100.0],
            ]
        )
        result = await fetch_candles_range(exchange, "BTCUSDT", "1h", 1700000000000)
        assert len(result) == 2
        assert result[0][4] == 101.0

    @pytest.mark.asyncio
    async def test_returns_empty_on_rate_limit_after_retries(self) -> None:
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("rate limit exceeded"))
        with patch("scripts.ingest_historical_candles.RATE_LIMIT_BACKOFF_S", 0):
            result = await fetch_candles_range(exchange, "BTCUSDT", "1h", 1700000000000)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_all_failures(self) -> None:
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock(side_effect=Exception("network error"))
        with patch("scripts.ingest_historical_candles.RETRY_DELAY_S", 0):
            result = await fetch_candles_range(exchange, "BTCUSDT", "1h", 1700000000000)
        assert result == []

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self) -> None:
        exchange = MagicMock()
        exchange.fetch_ohlcv = AsyncMock(
            side_effect=[
                Exception("timeout"),
                [[1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]],
            ]
        )
        with patch("scripts.ingest_historical_candles.RETRY_DELAY_S", 0):
            result = await fetch_candles_range(exchange, "BTCUSDT", "1h", 1700000000000)
        assert len(result) == 1


# ── Test fetch_all_candles_for_symbol ──────────────────────


class TestFetchAllCandlesForSymbol:
    @pytest.mark.asyncio
    async def test_single_batch_when_less_than_max(self) -> None:
        exchange = MagicMock()
        exchange.market = MagicMock(return_value={"id": "BTCUSDT"})
        candles_100 = [
            [1700000000000 + i * 3600000, 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 1000.0]
            for i in range(100)
        ]
        exchange.fetch_ohlcv = AsyncMock(return_value=candles_100)

        with patch("scripts.ingest_historical_candles.RATE_LIMIT_BACKOFF_S", 0):
            result = await fetch_all_candles_for_symbol(exchange, "BTC/USDT", "1h", 90)

        assert len(result) == 100
        assert result[0]["symbol"] == "BTC/USDT"
        assert exchange.fetch_ohlcv.call_count == 1

    @pytest.mark.asyncio
    async def test_multi_batch_pagination(self) -> None:
        exchange = MagicMock()
        exchange.market = MagicMock(return_value={"id": "BTCUSDT"})

        batch_1 = [
            [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(1000)
        ]
        batch_2 = [
            [1700000000000 + 1000 * 3600000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(500)
        ]
        exchange.fetch_ohlcv = AsyncMock(side_effect=[batch_1, batch_2])

        with (
            patch("scripts.ingest_historical_candles.RATE_LIMIT_BACKOFF_S", 0),
            patch("scripts.ingest_historical_candles._now_ms", return_value=9999999999999),
        ):
            result = await fetch_all_candles_for_symbol(exchange, "BTC/USDT", "1h", 90)

        assert len(result) == 1500
        assert exchange.fetch_ohlcv.call_count == 2

    @pytest.mark.asyncio
    async def test_deduplicates_overlapping_candles(self) -> None:
        exchange = MagicMock()
        exchange.market = MagicMock(return_value={"id": "BTCUSDT"})

        base_ts = 1700000000000
        batch_1 = [[base_ts + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0] for i in range(1000)]
        batch_2 = [[base_ts + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0] for i in range(995, 1005)]
        exchange.fetch_ohlcv = AsyncMock(side_effect=[batch_1, batch_2, []])

        with (
            patch("scripts.ingest_historical_candles.RATE_LIMIT_BACKOFF_S", 0),
            patch("scripts.ingest_historical_candles._now_ms", return_value=9999999999999),
        ):
            result = await fetch_all_candles_for_symbol(exchange, "BTC/USDT", "1h", 90)

        assert len(result) == 1005

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_candles(self) -> None:
        exchange = MagicMock()
        exchange.market = MagicMock(return_value={"id": "BTCUSDT"})
        exchange.fetch_ohlcv = AsyncMock(return_value=[])
        result = await fetch_all_candles_for_symbol(exchange, "BTC/USDT", "1h", 90)
        assert result == []

    @pytest.mark.asyncio
    async def test_decimal_normalization(self) -> None:
        exchange = MagicMock()
        exchange.market = MagicMock(return_value={"id": "BTCUSDT"})
        exchange.fetch_ohlcv = AsyncMock(
            return_value=[[1700000000000, 99999.12345678, 100000.0, 99000.0, 99500.0, 12345.67]]
        )
        result = await fetch_all_candles_for_symbol(exchange, "BTC/USDT", "1h", 90)
        assert len(result) == 1
        candle = result[0]
        assert candle["open"] == "99999.12345678"
        assert candle["close"] == "99500.0"  # Decimal preserves input precision
        assert isinstance(candle["ts"], datetime)
        assert candle["ts"].tzinfo is not None


# ── Test bulk_upsert_candles ───────────────────────────────


class TestBulkUpsertCandles:
    @pytest.mark.asyncio
    async def test_upserts_batch_correctly(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="INSERT 0 10")
        candles = [
            {
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "ts": datetime.now(timezone.utc),
                "open": "100.0",
                "high": "102.0",
                "low": "99.0",
                "close": "101.0",
                "volume": "1000.0",
            }
            for _ in range(10)
        ]
        inserted = await bulk_upsert_candles(conn, candles)
        assert inserted == 10
        assert conn.execute.call_count == 1
        call_args = conn.execute.call_args
        assert "ON CONFLICT" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_list(self) -> None:
        conn = AsyncMock()
        inserted = await bulk_upsert_candles(conn, [])
        assert inserted == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_batches_correctly_when_over_batch_size(self) -> None:
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="INSERT 0 500")
        candles = [
            {
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "ts": datetime.now(timezone.utc),
                "open": "100.0",
                "high": "102.0",
                "low": "99.0",
                "close": "101.0",
                "volume": "1000.0",
            }
            for _ in range(750)
        ]
        inserted = await bulk_upsert_candles(conn, candles, batch_size=500)
        assert conn.execute.call_count == 2  # 500 + 250
        assert inserted == 1000  # 500 + 500

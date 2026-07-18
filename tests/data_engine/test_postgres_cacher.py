"""Tests for app.data_engine.postgres_cacher.

Mock asyncpg to verify batch sizing, upsert SQL correctness,
and Decimal normalization of prices.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.data_engine.postgres_cacher import _candle_to_row, bulk_upsert


class TestCandleToRow:
    def test_basic_conversion(self) -> None:
        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        row = _candle_to_row("bybit", "BTC/USDT", "1h", candle)

        assert row[0] == "BTC/USDT"
        assert row[1] == "1h"
        assert isinstance(row[2], datetime)
        assert row[2].tzinfo is not None
        assert row[3] == Decimal("100.0")   # open
        assert row[4] == Decimal("102.0")   # high
        assert row[5] == Decimal("99.0")    # low
        assert row[6] == Decimal("101.0")   # close
        assert row[7] == Decimal("1000.0")  # volume

    def test_precision_preserved(self) -> None:
        candle = [1700000000000, 99999.12345678, 100000.0, 99000.0, 99500.0, 12345.67]
        row = _candle_to_row("binance", "BTC/USDT", "1h", candle)

        assert row[3] == Decimal("99999.12345678")
        assert row[7] == Decimal("12345.67")

    def test_exchange_id_not_in_row(self) -> None:
        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        row = _candle_to_row("bybit", "BTC/USDT", "1h", candle)
        assert "bybit" not in row


class TestBulkUpsert:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_list(self) -> None:
        conn = AsyncMock()
        count = await bulk_upsert(conn, "bybit", "BTC/USDT", "1h", [])
        assert count == 0
        conn.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_batch(self) -> None:
        conn = AsyncMock()
        conn.executemany = AsyncMock(return_value="INSERT 0 10")

        candles = [
            [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(10)
        ]
        count = await bulk_upsert(conn, "bybit", "BTC/USDT", "1h", candles, batch_size=500)

        assert count == 10
        conn.executemany.assert_awaited_once()
        # Verify SQL contains ON CONFLICT
        sql = conn.executemany.call_args[0][0]
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_multi_batch(self) -> None:
        conn = AsyncMock()
        conn.executemany = AsyncMock(return_value="INSERT 0 500")

        candles = [
            [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(750)
        ]
        count = await bulk_upsert(conn, "bybit", "BTC/USDT", "1h", candles, batch_size=500)

        assert conn.executemany.call_count == 2  # 500 + 250
        assert count == 1000  # 500 + 500

    @pytest.mark.asyncio
    async def test_rows_contain_decimal_prices(self) -> None:
        conn = AsyncMock()
        conn.executemany = AsyncMock(return_value="INSERT 0 1")

        candles = [[1700000000000, 64250.50, 64500.0, 64000.0, 64300.75, 100.0]]
        await bulk_upsert(conn, "bybit", "BTC/USDT", "1h", candles)

        rows = conn.executemany.call_args[0][1]
        row = rows[0]
        # Indices 3-7 are open/high/low/close/volume
        assert isinstance(row[3], Decimal)
        assert isinstance(row[6], Decimal)

    @pytest.mark.asyncio
    async def test_executemany_error_count_fallback(self) -> None:
        """When executemany returns None (some asyncpg versions), fall back to batch len."""
        conn = AsyncMock()
        conn.executemany = AsyncMock(return_value=None)

        candles = [
            [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(3)
        ]
        count = await bulk_upsert(conn, "bybit", "BTC/USDT", "1h", candles)
        assert count == 3

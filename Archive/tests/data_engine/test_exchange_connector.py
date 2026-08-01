"""Tests for app.data_engine.exchange_connector.

Mock ccxt to test pagination, rate limits, error handling, and retry logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.data_engine.exchange_connector import ExchangeConnector, _make_exchange


@pytest.fixture(autouse=True)
def _mock_make_exchange(monkeypatch):
    """Patch _make_exchange to avoid real API calls in tests.

    TestMakeExchange uses the local import reference so is unaffected.
    """
    mock_ex = AsyncMock()
    mock_ex.id = "bybit"
    mock_ex.apiKey = ""
    mock_ex.secret = ""
    mock_ex.fetch_ohlcv = AsyncMock(return_value=[])
    mock_ex.close = AsyncMock()
    monkeypatch.setattr(
        "app.data_engine.exchange_connector._make_exchange",
        lambda *a, **kw: mock_ex,
    )


class TestMakeExchange:
    def test_creates_bybit(self) -> None:
        ex = _make_exchange("bybit")
        assert ex.id == "bybit"

    def test_creates_binance(self) -> None:
        ex = _make_exchange("binance")
        assert ex.id == "binance"

    def test_creates_okx(self) -> None:
        ex = _make_exchange("okx")
        assert ex.id == "okx"

    def test_unsupported_exchange_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported exchange"):
            _make_exchange("kraken")

    def test_passes_api_keys(self) -> None:
        ex = _make_exchange("bybit", api_key="k123", api_secret="s456")
        assert ex.apiKey == "k123"
        assert ex.secret == "s456"


class TestFetchOhlcv:
    @pytest.mark.asyncio
    async def test_returns_candles_on_success(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.fetch_ohlcv = AsyncMock(
            return_value=[
                [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0],
                [1700003600000, 101.0, 103.0, 100.0, 102.0, 1100.0],
            ]
        )
        result = await connector.fetch_ohlcv("BTC/USDT", "1h", limit=100)
        assert len(result) == 2
        assert result[0][4] == 101.0  # close
        connector.exchange.fetch_ohlcv.assert_awaited_once_with(
            "BTC/USDT", "1h", since=None, limit=100
        )
        await connector.close()

    @pytest.mark.asyncio
    async def test_returns_empty_on_rate_limit_after_retries(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.fetch_ohlcv = AsyncMock(
            side_effect=Exception("rate limit exceeded")
        )
        with patch("app.data_engine.exchange_connector._RATE_LIMIT_BACKOFF_S", 0):
            result = await connector.fetch_ohlcv("BTC/USDT")
        assert result == []
        assert connector.exchange.fetch_ohlcv.call_count == 3  # _MAX_RETRIES
        await connector.close()

    @pytest.mark.asyncio
    async def test_returns_empty_on_persistent_failure(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.fetch_ohlcv = AsyncMock(
            side_effect=Exception("network down")
        )
        with patch("app.data_engine.exchange_connector._BASE_BACKOFF_S", 0):
            result = await connector.fetch_ohlcv("BTC/USDT")
        assert result == []
        assert connector.exchange.fetch_ohlcv.call_count == 3
        await connector.close()

    @pytest.mark.asyncio
    async def test_succeeds_on_second_retry(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.fetch_ohlcv = AsyncMock(
            side_effect=[
                Exception("timeout"),
                [[1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]],
            ]
        )
        with patch("app.data_engine.exchange_connector._BASE_BACKOFF_S", 0):
            result = await connector.fetch_ohlcv("BTC/USDT")
        assert len(result) == 1
        assert connector.exchange.fetch_ohlcv.call_count == 2
        await connector.close()


class TestFetchAllCandles:
    @pytest.mark.asyncio
    async def test_single_batch_when_small(self) -> None:
        connector = ExchangeConnector("bybit")
        candles_50 = [
            [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(50)
        ]
        connector.exchange.fetch_ohlcv = AsyncMock(return_value=candles_50)
        result = await connector.fetch_all_candles("BTC/USDT", "1h", days=1)
        assert len(result) == 50
        await connector.close()

    @pytest.mark.asyncio
    async def test_concatenates_sequential_batches(self) -> None:
        connector = ExchangeConnector("bybit")
        base_ts = 1700000000000
        batch_1 = [
            [base_ts + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0]
            for i in range(1000)
        ]
        # batch_2 starts right after batch_1 (non-overlapping)
        batch_2 = [
            [base_ts + (1000 + i) * 3600000, 200.0, 202.0, 199.0, 201.0, 2000.0]
            for i in range(500)
        ]
        connector.exchange.fetch_ohlcv = AsyncMock(side_effect=[batch_1, batch_2, []])

        with patch("app.data_engine.exchange_connector._POLL_INTERVAL_S", 0):
            result = await connector.fetch_all_candles("BTC/USDT", "1h", days=1000)

        # 1000 from batch_1 + 500 from batch_2 = 1500
        assert len(result) == 1500
        # Sorted ascending
        assert result[0][0] < result[-1][0]
        await connector.close()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.fetch_ohlcv = AsyncMock(return_value=[])
        result = await connector.fetch_all_candles("BTC/USDT", "1h", days=90)
        assert result == []
        await connector.close()


class TestTimeframeToMs:
    def test_one_hour(self) -> None:
        assert ExchangeConnector._timeframe_to_ms("1h") == 3_600_000

    def test_fifteen_minutes(self) -> None:
        assert ExchangeConnector._timeframe_to_ms("15m") == 900_000

    def test_one_day(self) -> None:
        assert ExchangeConnector._timeframe_to_ms("1d") == 86_400_000

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            ExchangeConnector._timeframe_to_ms("3h")


class TestClose:
    @pytest.mark.asyncio
    async def test_close_calls_exchange_close(self) -> None:
        connector = ExchangeConnector("bybit")
        connector.exchange.close = AsyncMock()
        await connector.close()
        connector.exchange.close.assert_awaited_once()

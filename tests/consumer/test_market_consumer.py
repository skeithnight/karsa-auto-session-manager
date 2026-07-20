"""Tests for app.consumer.market_consumer.MarketConsumer.

Mock Redis Pub/Sub to test channel parsing, symbol normalization,
dedup logic, and signal dispatch.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.consumer.market_consumer import _CHANNEL_RE, MarketConsumer


class TestChannelRegex:
    def test_parses_valid_channel(self) -> None:
        m = _CHANNEL_RE.match("karsa:candles:bybit:BTCUSDT:1h")
        assert m is not None
        assert m.group("exchange") == "bybit"
        assert m.group("symbol") == "BTCUSDT"
        assert m.group("timeframe") == "1h"

    def test_parses_binance_channel(self) -> None:
        m = _CHANNEL_RE.match("karsa:candles:binance:ETHUSDT:15m")
        assert m is not None
        assert m.group("exchange") == "binance"

    def test_no_match_on_wrong_prefix(self) -> None:
        m = _CHANNEL_RE.match("other:prefix:bybit:BTCUSDT:1h")
        assert m is None


class TestNormalizeSymbol:
    def test_btcusdt(self) -> None:
        assert MarketConsumer._normalize_symbol("BTCUSDT") == "BTC/USDT"

    def test_ethusdt(self) -> None:
        assert MarketConsumer._normalize_symbol("ETHUSDT") == "ETH/USDT"

    def test_already_unified(self) -> None:
        assert MarketConsumer._normalize_symbol("BTC/USDT") == "BTC/USDT"

    def test_unknown_suffix(self) -> None:
        result = MarketConsumer._normalize_symbol("BTCUSDC")
        assert result == "BTCUSDC"  # no known quote suffix, return as-is


class TestMarketConsumerProcessMessage:
    @pytest.mark.asyncio
    async def test_processes_valid_candle(self) -> None:
        redis = AsyncMock()
        pubsub_mock = MagicMock()
        pubsub_mock.listen = AsyncMock(return_value=iter([]))
        redis.pubsub.return_value = pubsub_mock

        engine = AsyncMock()
        engine.evaluate.return_value = None
        signals_received = []

        async def on_signal(symbol, sig):
            signals_received.append((symbol, sig))

        consumer = MarketConsumer(redis, engine, on_signal)
        payload = {
            "exchange": "bybit",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": "1700000000000",
            "open": "100.0",
            "high": "102.0",
            "low": "99.0",
            "close": "101.0",
            "volume": "1000.0",
        }

        await consumer._process_message(
            "karsa:candles:bybit:BTCUSDT:1h",
            json.dumps(payload),
        )

        assert consumer._buffer.count("BTC/USDT") == 1

    @pytest.mark.asyncio
    async def test_skips_stale_candle(self) -> None:
        redis = AsyncMock()
        engine = AsyncMock()
        signals = []

        async def on_signal(symbol, sig):
            signals.append(sig)

        consumer = MarketConsumer(redis, engine, on_signal)

        payload = json.dumps({
            "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
            "ts": "1700000000000", "open": "100.0", "high": "102.0",
            "low": "99.0", "close": "101.0", "volume": "1000.0",
        })

        # First message
        await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)
        assert consumer._buffer.count("BTC/USDT") == 1

        # Same timestamp — should be deduped
        await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)
        assert consumer._buffer.count("BTC/USDT") == 1

    @pytest.mark.asyncio
    async def test_skips_invalid_json(self) -> None:
        redis = AsyncMock()
        engine = AsyncMock()
        consumer = MarketConsumer(redis, engine, AsyncMock())
        await consumer._process_message(
            "karsa:candles:bybit:BTCUSDT:1h", "NOT JSON"
        )
        assert consumer._buffer.count("BTC/USDT") == 0

    @pytest.mark.asyncio
    async def test_engine_called_with_50_candles(self) -> None:
        redis = AsyncMock()
        engine = AsyncMock()
        engine.evaluate.return_value = None
        consumer = MarketConsumer(redis, engine, AsyncMock())

        for i in range(100):
            payload = json.dumps({
                "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
                "ts": str(1700000000000 + i * 3600000),
                "open": "100.0", "high": "102.0", "low": "99.0",
                "close": "101.0", "volume": "1000.0",
            })
            await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)

        # Engine called on each candle from 50th onwards (51 calls for 100 candles)
        assert engine.evaluate.call_count == 51

    @pytest.mark.asyncio
    async def test_signal_callback_invoked(self) -> None:
        redis = AsyncMock()

        signal_result = MagicMock()
        engine = AsyncMock()
        engine.evaluate.return_value = signal_result

        signals = []

        async def on_signal(symbol, sig):
            signals.append((symbol, sig))

        consumer = MarketConsumer(redis, engine, on_signal)

        for i in range(60):
            payload = json.dumps({
                "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
                "ts": str(1700000000000 + i * 3600000),
                "open": "100.0", "high": "102.0", "low": "99.0",
                "close": "101.0", "volume": "1000.0",
            })
            await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)

        # Signal on each candle from 50th onwards (11 calls for 60 candles)
        assert len(signals) == 11
        assert signals[0][0] == "BTC/USDT"


class TestMarketConsumerGlobalPrices:
    @pytest.mark.asyncio
    async def test_builds_global_prices_for_trend_scoring(self) -> None:
        redis = AsyncMock()
        engine = AsyncMock()
        engine.evaluate.return_value = None
        consumer = MarketConsumer(redis, engine, AsyncMock())

        # Set up cross-exchange prices
        consumer.global_prices["BTC/USDT"]["binance"] = 100.5
        consumer.global_prices["BTC/USDT"]["okx"] = 100.3

        for i in range(60):
            payload = json.dumps({
                "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
                "ts": str(1700000000000 + i * 3600000),
                "open": "100.0", "high": "102.0", "low": "99.0",
                "close": "101.0", "volume": "1000.0",
            })
            await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)

        call_kwargs = engine.evaluate.call_args
        prices = call_kwargs[1].get("global_prices") or call_kwargs.kwargs.get("global_prices")
        assert prices is not None
        assert "binance" in prices
        assert "okx" in prices

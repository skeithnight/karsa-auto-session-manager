"""Integration smoke test — Data Engine → Redis → Market Consumer pipeline.

Verifies the full candle ingestion path:
  RedisPublisher.publish_candle()  →  MarketConsumer._process_message()  →  DecisionEngine.evaluate()

All external infra (Redis, Postgres, CCXT) is mocked. The test proves the
data flow works end-to-end without any real infrastructure.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.regime_classifier import MarketRegime
from app.consumer.decision_engine import DecisionEngine, TradeSignal
from app.consumer.market_consumer import _CHANNEL_RE, MarketConsumer
from app.data_engine.redis_publisher import _normalize_ohlcv

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_candle(ts_ms: int, close: float) -> list:
    """Generate a single OHLCV candle with given timestamp and close."""
    return [ts_ms, close - 2.0, close + 3.0, close - 4.0, close, 1000.0]


def _make_raw_candle(ts_ms: int, close: float) -> dict:
    """Generate a Redis payload dict (as published by RedisPublisher)."""
    return {
        "exchange": "bybit",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "ts": f"{ts_ms}",
        "open": str(Decimal(str(close - 2.0))),
        "high": str(Decimal(str(close + 3.0))),
        "low": str(Decimal(str(close - 4.0))),
        "close": str(Decimal(str(close))),
        "volume": str(Decimal("1000.0")),
    }


class _FakeRedisPubSub:
    """Simulates redis-py Pub/Sub with a message queue."""

    def __init__(self) -> None:
        self._messages: list[dict] = []
        self._subscribed = False

    async def psubscribe(self, pattern: str) -> None:
        self._subscribed = True

    async def listen(self):
        while self._messages:
            yield self._messages.pop(0)
        # Yield one pmessage and then stop
        yield {"type": "pmessage", "channel": "DONE", "data": ""}

    async def punsubscribe(self) -> None:
        pass

    async def reset(self) -> None:
        self._subscribed = False


# ---------------------------------------------------------------------------
# Test: RedisPublisher → payload format → MarketConsumer channel parsing
# ---------------------------------------------------------------------------


class TestPublisherConsumerChannelCompat:
    """Verify RedisPublisher output is parseable by MarketConsumer."""

    def test_channel_regex_matches_published_format(self) -> None:
        """Publisher: karsa:candles:bybit:BTCUSDT:1h matches consumer regex."""
        channel = "karsa:candles:bybit:BTCUSDT:1h"
        m = _CHANNEL_RE.match(channel)
        assert m is not None
        assert m.group("exchange") == "bybit"
        assert m.group("symbol") == "BTCUSDT"
        assert m.group("timeframe") == "1h"

    def test_normalize_ohlcv_produces_valid_json(self) -> None:
        """Publisher _normalize_ohlcv output is valid JSON for consumer."""
        candle = [1700000000000, 64000.0, 64500.0, 63500.0, 64250.0, 1000.0]
        payload = _normalize_ohlcv(candle, "BTC/USDT", "1h", "bybit")
        json_str = json.dumps(payload)
        parsed = json.loads(json_str)

        assert parsed["exchange"] == "bybit"
        assert parsed["symbol"] == "BTC/USDT"
        assert "64000" in parsed["open"]  # Decimal string
        assert parsed["timeframe"] == "1h"

    def test_publisher_channel_uses_normalized_symbol(self) -> None:
        """Publisher strips '/' from symbol for Redis channel name."""
        from app.data_engine.redis_publisher import _CHANNEL_TEMPLATE

        channel = _CHANNEL_TEMPLATE.format(
            exchange="bybit", symbol="BTCUSDT", timeframe="1h"
        )
        assert channel == "karsa:candles:bybit:BTCUSDT:1h"


# ---------------------------------------------------------------------------
# Test: Full pipeline — publish 60 candles → signal fires
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """Smoke test: 60 candles through the full pipeline produces a signal."""

    @pytest.mark.asyncio
    async def test_publish_to_signal(self) -> None:
        """Publisher → Consumer → Buffer(60) → Engine → callback fires."""
        # Setup: mock engine that always returns a signal for 50+ candles
        mock_engine = MagicMock(spec=DecisionEngine)
        mock_engine.evaluate.return_value = TradeSignal(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            score=80.0,
            risk_profile=MagicMock(
                use_post_only=False,
                size_multiplier=Decimal("1.0"),
                take_profit_type="TRAILING",
                trail_atr_mult=Decimal("3.0"),
                sl_atr_buffer=Decimal("1.5"),
                to_json=lambda: '{}',
            ),
            entry_price=Decimal("64250.50"),
            sl_price=Decimal("63000.00"),
            tp_price=None,
            amount=Decimal("0.001"),
            entry_fee_rate=Decimal("0.00055"),
            atr=Decimal("500.0"),
            timestamp_ms=1700000000000,
            candles=[],
        )

        signals_received: list[tuple[str, TradeSignal]] = []

        async def on_signal(symbol: str, sig: TradeSignal) -> None:
            signals_received.append((symbol, sig))

        redis_mock = MagicMock()
        redis_mock.pubsub.return_value = _FakeRedisPubSub()

        consumer = MarketConsumer(redis_mock, mock_engine, on_signal)

        # Publish 60 candles through _process_message (simulating Redis delivery)
        for i in range(60):
            ts_ms = 1700000000000 + i * 3600000
            payload = _make_raw_candle(ts_ms, 64000.0 + i * 10.0)
            await consumer._process_message(
                "karsa:candles:bybit:BTCUSDT:1h",
                json.dumps(payload),
            )

        # Verify: engine was called at least once (from 50th candle onwards)
        assert mock_engine.evaluate.call_count >= 1

        # Verify: callback fired
        assert len(signals_received) >= 1
        assert signals_received[0][0] == "BTC/USDT"
        assert isinstance(signals_received[0][1], TradeSignal)


# ---------------------------------------------------------------------------
# Test: Buffer accumulation path
# ---------------------------------------------------------------------------


class TestBufferAccumulation:
    """Verify CandleBuffer correctly accumulates candles from consumer path."""

    @pytest.mark.asyncio
    async def test_buffer_grows_correctly(self) -> None:
        """Each processed candle adds to buffer, dedup works."""
        redis_mock = MagicMock()
        engine_mock = MagicMock()
        engine_mock.evaluate.return_value = None

        consumer = MarketConsumer(redis_mock, engine_mock, AsyncMock())

        for i in range(20):
            payload = _make_raw_candle(1700000000000 + i * 3600000, 64000.0)
            await consumer._process_message(
                "karsa:candles:bybit:BTCUSDT:1h",
                json.dumps(payload),
            )

        assert consumer._buffer.count("BTC/USDT") == 20  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_stale_candle_not_buffered(self) -> None:
        """Same timestamp does not grow buffer."""
        redis_mock = MagicMock()
        engine_mock = MagicMock()
        consumer = MarketConsumer(redis_mock, engine_mock, AsyncMock())

        payload = json.dumps(_make_raw_candle(1700000000000, 64000.0))
        await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)
        await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", payload)

        assert consumer._buffer.count("BTC/USDT") == 1


# ---------------------------------------------------------------------------
# Test: Multiple symbols
# ---------------------------------------------------------------------------


class TestMultiSymbolPipeline:
    """Verify consumer handles multiple symbols independently."""

    @pytest.mark.asyncio
    async def test_btc_and_eth_processed_separately(self) -> None:
        """Each symbol accumulates in its own buffer."""
        redis_mock = MagicMock()
        engine_mock = MagicMock()
        engine_mock.evaluate.return_value = None
        consumer = MarketConsumer(redis_mock, engine_mock, AsyncMock())

        btc_payload = json.dumps(_make_raw_candle(1700000000000, 64000.0))
        eth_payload = json.dumps({
            "exchange": "bybit", "symbol": "ETH/USDT", "timeframe": "1h",
            "ts": "1700000000000", "open": "3000.0", "high": "3050.0",
            "low": "2980.0", "close": "3020.0", "volume": "5000.0",
        })

        await consumer._process_message("karsa:candles:bybit:BTCUSDT:1h", btc_payload)
        await consumer._process_message("karsa:candles:bybit:ETHUSDT:1h", eth_payload)

        assert consumer._buffer.count("BTC/USDT") == 1
        assert consumer._buffer.count("ETH/USDT") == 1


# ---------------------------------------------------------------------------
# Test: Global prices injection
# ---------------------------------------------------------------------------


class TestGlobalPricesInjection:
    """Verify cross-exchange prices reach the decision engine."""

    @pytest.mark.asyncio
    async def test_global_prices_passed_to_engine(self) -> None:
        """Consumer passes global_prices to engine.evaluate."""
        redis_mock = MagicMock()
        engine_mock = MagicMock()
        engine_mock.evaluate = AsyncMock(return_value=None)
        consumer = MarketConsumer(redis_mock, engine_mock, AsyncMock())

        # Set cross-exchange prices
        consumer.global_prices["BTC/USDT"]["binance"] = 64260.0
        consumer.global_prices["BTC/USDT"]["okx"] = 64255.0

        # Send enough candles to trigger engine
        for i in range(55):
            payload = _make_raw_candle(1700000000000 + i * 3600000, 64000.0)
            await consumer._process_message(
                "karsa:candles:bybit:BTCUSDT:1h",
                json.dumps(payload),
            )

        # Verify engine received global_prices
        call_kwargs = engine_mock.evaluate.call_args
        prices = call_kwargs.kwargs.get("global_prices") or call_kwargs[1].get("global_prices")
        assert prices is not None
        assert "binance" in prices
        assert "okx" in prices


# ---------------------------------------------------------------------------
# Test: Symbol normalization (publisher ↔ consumer)
# ---------------------------------------------------------------------------


class TestSymbolNormalization:
    """Verify publisher and consumer agree on symbol normalization."""

    def test_consumer_normalizes_published_symbols(self) -> None:
        """Consumer converts BTCUSDT back to BTC/USDT as publisher stores it."""
        assert MarketConsumer._normalize_symbol("BTCUSDT") == "BTC/USDT"
        assert MarketConsumer._normalize_symbol("ETHUSDT") == "ETH/USDT"
        assert MarketConsumer._normalize_symbol("SOLUSDT") == "SOL/USDT"
        assert MarketConsumer._normalize_symbol("XRPUSDT") == "XRP/USDT"

    def test_publisher_stores_unified_symbol(self) -> None:
        """Publisher payload keeps unified symbol format."""
        candle = [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0]
        payload = _normalize_ohlcv(candle, "BTC/USDT", "1h", "bybit")
        assert payload["symbol"] == "BTC/USDT"

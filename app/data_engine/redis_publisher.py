"""Redis Pub/Sub publisher for normalized OHLCV data.

Channel naming convention:  karsa:candles:{exchange}:{symbol}:{timeframe}
Prices are serialized as Decimal → string for precision preservation.
Matches the existing fleet Redis pattern (karsa: namespace).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL_TEMPLATE = "karsa:candles:{exchange}:{symbol}:{timeframe}"


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serialises Decimal objects as strings."""

    def default(self, o: object) -> str:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def _normalize_ohlcv(
    candle: list,
    symbol: str,
    timeframe: str,
    exchange_id: str,
) -> dict[str, Any]:
    """Convert raw ccxt OHLCV list to JSON-safe dict with Decimal prices.

    ccxt returns [timestamp_ms, open, high, low, close, volume] as floats.
    This converts prices to Decimal for serialization as strings, matching
    the DATA_MODEL.md rule: money is always Decimal → string.

    Args:
        candle: Raw ccxt OHLCV list.
        symbol: Unified symbol (e.g. BTC/USDT).
        timeframe: Timeframe string (e.g. 1h).
        exchange_id: Exchange identifier.

    Returns:
        Dict with ts (ISO datetime), open/high/low/close/volume (Decimal as str),
        symbol, timeframe, exchange.
    """
    ts_ms, open_p, high_p, low_p, close_p, volume = candle

    return {
        "exchange": exchange_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "ts": int(ts_ms),
        "open": str(Decimal(str(open_p))),
        "high": str(Decimal(str(high_p))),
        "low": str(Decimal(str(low_p))),
        "close": str(Decimal(str(close_p))),
        "volume": str(Decimal(str(volume))),
    }


class RedisPublisher:
    """Publishes normalized OHLCV candles to Redis Pub/Sub channels.

    Each candle is published to a channel named
    ``karsa:candles:{exchange}:{symbol}:{timeframe}``.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def publish_candle(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        candle: list,
    ) -> None:
        """Publish a single OHLCV candle to Redis Pub/Sub.

        Args:
            exchange_id: Exchange identifier (bybit, binance, okx).
            symbol: Unified symbol (e.g. BTC/USDT).
            timeframe: Candle timeframe (e.g. 1h).
            candle: Raw ccxt OHLCV list.
        """
        channel = _CHANNEL_TEMPLATE.format(
            exchange=exchange_id,
            symbol=symbol.replace("/", ""),
            timeframe=timeframe,
        )
        payload = _normalize_ohlcv(candle, symbol, timeframe, exchange_id)
        json_str = json.dumps(payload, cls=DecimalEncoder)

        await self._redis.publish(channel, json_str)
        logger.debug(
            "published %s %s %s candle: ts=%s close=%s",
            exchange_id,
            symbol,
            timeframe,
            payload["ts"],
            payload["close"],
        )

    async def publish_candles(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        candles: list[list],
    ) -> int:
        """Publish multiple OHLCV candles to Redis Pub/Sub.

        Args:
            exchange_id: Exchange identifier.
            symbol: Unified symbol.
            timeframe: Candle timeframe.
            candles: List of raw ccxt OHLCV lists.

        Returns:
            Number of candles published.
        """
        for candle in candles:
            await self.publish_candle(exchange_id, symbol, timeframe, candle)
        import logging

        logging.getLogger(__name__).info(
            f"published {len(candles)} candles to {exchange_id}:{symbol}:{timeframe}"
        )
        return len(candles)

    async def publish_tick(
        self,
        exchange_id: str,
        symbol: str,
        tick_data: dict[str, Any],
    ) -> None:
        """Publish a single tick/orderbook update to Redis Pub/Sub.

        Args:
            exchange_id: Exchange identifier.
            symbol: Unified symbol.
            tick_data: Dictionary containing tick/orderbook data (best_bid, best_ask, etc).
        """
        channel = f"karsa:ticks:{exchange_id}:{symbol.replace('/', '')}"
        json_str = json.dumps(tick_data, cls=DecimalEncoder)
        await self._redis.publish(channel, json_str)

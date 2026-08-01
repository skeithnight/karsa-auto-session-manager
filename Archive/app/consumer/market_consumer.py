"""Redis Pub/Sub consumer that subscribes to candle channels and
triggers the shared decision pipeline.

Listens on ``karsa:candles:{exchange}:{symbol}:{timeframe}``,
buffers candles per symbol, and runs DecisionEngine when enough
data is available. Dispatches TradeSignals to a caller-provided callback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

from app.consumer.candle_buffer import CandleBuffer
from app.consumer.decision_engine import DecisionEngine, TradeSignal

logger = logging.getLogger(__name__)

_CHANNEL_PATTERN = "karsa:candles:*"
_TICK_CHANNEL_PATTERN = "karsa:ticks:*"
_CHANNEL_RE = re.compile(
    r"^karsa:candles:(?P<exchange>[^:]+):(?P<symbol>[^:]+):(?P<timeframe>[^:]+)$"
)
_TICK_CHANNEL_RE = re.compile(
    r"^karsa:ticks:(?P<exchange>[^:]+):(?P<symbol>[^:]+)$"
)
_POLL_INTERVAL_S = 1.0  # seconds between subscribe polls

# Channel:  karsa:candles:bybit:BTCUSDT:1h
# Redis Pub/Sub wildcard:  karsa:candles:*


class MarketConsumer:
    """Subscribes to Redis candle channels, buffers data, runs decision pipeline.

    Args:
        redis_client: Configured Redis client (from app.core.dependencies).
        decision_engine: Shared DecisionEngine instance.
        on_signal: Async callback invoked when a TradeSignal is generated.
            Receives (symbol, TradeSignal).
        on_candle: Optional async callback for every candle received (for metrics/logging).
    """

    def __init__(
        self,
        redis_client: Any,
        decision_engine: DecisionEngine,
        on_signal: Callable[[str, TradeSignal], Coroutine[Any, Any, None]],
        on_candle: Callable[[str, list], Coroutine[Any, Any, None]] | None = None,
        micro_scalper: Any | None = None,
        on_micro_signal: Callable[[str, Any], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._redis = redis_client
        self._engine = decision_engine
        self._on_signal = on_signal
        self._on_candle = on_candle
        self._micro_scalper = micro_scalper
        self._on_micro_signal = on_micro_signal
        self._buffer = CandleBuffer()
        self._running = False

        # Track last-seen timestamp per channel to avoid reprocessing stale data
        self._last_ts: dict[str, int] = {}

        # External data feeds for CHOP scoring
        self.global_prices: dict[str, dict[str, float]] = defaultdict(dict)
        self.orderbook_delta: dict[str, float] = {}
        self.funding_rate: dict[str, float] = {}
        self.oi_change: dict[str, float] = {}
        self.cvd_slope: dict[str, float] = {}
        self.liquidity_walls: dict[str, dict[str, float | None]] = {}

    async def start(self) -> None:
        """Subscribe to candle channels and start processing loop.

        Runs until cancelled. Uses ``PSUBSCRIBE`` through Redis Pub/Sub.
        Note: redis-py's async Pub/Sub is message-based (not polling).
        """
        self._running = True
        max_backoff = 30

        while self._running:
            backoff = 1
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                await pubsub.psubscribe(_CHANNEL_PATTERN, _TICK_CHANNEL_PATTERN)
                logger.info("subscribed to %s and %s", _CHANNEL_PATTERN, _TICK_CHANNEL_PATTERN)
                backoff = 1

                async for message in pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "pmessage":
                        continue

                    channel: str = message["channel"]
                    data: str = message["data"]

                    try:
                        await self._process_message(channel, data)
                    except Exception:
                        logger.exception("error processing message on %s", channel)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("MarketConsumer: pubsub disconnected: %s", exc)
                if pubsub is not None:
                    try:
                        await pubsub.punsubscribe()
                        await pubsub.reset()
                    except Exception:
                        pass

                if not self._running:
                    break
                logger.info("MarketConsumer: reconnecting in %ds...", backoff)

                # Flush stale candles and timestamps on reconnect to prevent corrupted regime math
                for sym in self._buffer.symbols():
                    self._buffer.clear(sym)
                self._last_ts.clear()

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue

            break

        self._running = False
        logger.info("market consumer stopped")

    def stop(self) -> None:
        """Signal the consumer to stop."""
        self._running = False

    async def _process_message(self, channel: str, data: str) -> None:
        """Process a single candle message from Redis Pub/Sub.

        Args:
            channel: Full channel name (e.g. karsa:candles:bybit:BTCUSDT:1h).
            data: JSON payload with candle data.
        """
        if channel.startswith("karsa:ticks:"):
            await self._process_tick(channel, data)
            return

        match = _CHANNEL_RE.match(channel)
        if not match:
            return

        exchange = match.group("exchange")
        symbol_raw = match.group("symbol")
        timeframe = match.group("timeframe")

        # Normalize symbol: BTCUSDT → BTC/USDT
        symbol = self._normalize_symbol(symbol_raw)

        # Parse JSON payload
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("invalid JSON on %s: %s", channel, data[:100])
            return

        # Reconstruct OHLCV list from payload
        candle = [
            int(payload["ts"]),
            float(payload["open"]),
            float(payload["high"]),
            float(payload["low"]),
            float(payload["close"]),
            float(payload["volume"]),
        ]

        # Dedup check: Buffer every tick data, but only run signal evaluation on new candle close (ts > last_ts)
        dedup_key = f"{exchange}:{symbol}:{timeframe}"
        ts = candle[0]
        last_ts = self._last_ts.get(dedup_key, 0)

        # Always update buffer with latest tick data
        self._buffer.append(symbol, candle)

        if ts <= last_ts:
            return  # Same or older candle tick update, skip signal evaluation

        self._last_ts[dedup_key] = ts

        # Optional per-candle callback
        if self._on_candle:
            await self._on_candle(symbol, candle)

        # Check if we have enough data for decision engine
        if not self._buffer.has_enough(symbol):
            return

        # Build cross-exchange prices for TREND global sync scoring
        prices = self._get_global_prices(exchange, symbol)

        # Run decision pipeline
        signal = await self._engine.evaluate(
            symbol=symbol,
            candles=self._buffer.as_list(symbol),
            global_prices=prices,
            orderbook_delta=self.orderbook_delta.get(symbol),
            funding_rate=self.funding_rate.get(symbol),
            oi_change=self.oi_change.get(symbol),
            cvd_slope=self.cvd_slope.get(symbol),
            liquidity_walls=self.liquidity_walls.get(symbol),
        )

        if signal is not None:
            logger.info(
                "signal: %s %s score=%.1f regime=%s entry=%s sl=%s tp=%s",
                symbol,
                signal.direction,
                signal.score,
                signal.regime.value,
                signal.entry_price,
                signal.sl_price,
                signal.tp_price,
            )
            # Run signal execution (which includes 30s AI analyst call) as a background task 
            # to unblock the Redis pubsub hot path
            asyncio.create_task(self._on_signal(symbol, signal))

    async def _process_tick(self, channel: str, data: str) -> None:
        """Process a single tick message from Redis Pub/Sub for Micro-Scalper."""
        if not self._micro_scalper or not self._on_micro_signal:
            return

        match = _TICK_CHANNEL_RE.match(channel)
        if not match:
            return

        symbol_raw = match.group("symbol")
        symbol = self._normalize_symbol(symbol_raw)

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        signal = await self._micro_scalper.evaluate_tick(
            symbol=symbol,
            best_bid=float(payload.get("best_bid", 0)),
            best_ask=float(payload.get("best_ask", 0)),
            ob_imbalance=float(payload.get("ob_imbalance", 0)),
            recent_trades=payload.get("recent_trades", [])
        )
        if signal:
            asyncio.create_task(self._on_micro_signal(symbol, signal))

    def _get_global_prices(self, exchange: str, symbol: str) -> dict[str, float] | None:
        """Build cross-exchange price dict for TREND scoring.

        Returns a dict with all three exchange prices if at least one other
        exchange is available. Otherwise returns None.
        """
        prices = dict(self.global_prices.get(symbol, {}))
        if not prices:
            return None
        # Ensure the source exchange itself is in the dict
        if exchange not in prices:
            return prices
        # Need at least 2 exchanges for global sync score
        if len(prices) < 2:  # noqa: PLR2004
            return None
        return prices

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """Convert Redis channel symbol back to unified format.

        BTCUSDT → BTC/USDT. If already unified (contains /), return as-is.
        """
        if "/" in raw:
            return raw
        # Find the quote separator — assumes common patterns like *USDT, *USD, *BTC
        for quote in ("USDT", "USD", "BTC", "ETH"):
            if raw.endswith(quote) and len(raw) > len(quote):
                base = raw[: -len(quote)]
                return f"{base}/{quote}"
        return raw

"""Async wrapper around ccxt.async_support for multi-exchange OHLCV fetching.

Supports Bybit, Binance, OKX. Handles rate limits with exponential backoff.
All prices remain as raw floats from ccxt — Decimal conversion happens downstream
in postgres_cacher (matching the existing ingestion pattern).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import ccxt.async_support as ccxt_pro

logger = logging.getLogger(__name__)

# ccxt OHLCV columns: [timestamp_ms, open, high, low, close, volume]
OHLCV_COLS = ("ts", "open", "high", "low", "close", "volume")

_MAX_RETRIES = 3
_BASE_BACKOFF_S = 1.0
_RATE_LIMIT_BACKOFF_S = 30.0
_POLL_INTERVAL_S = 60.0


def _make_exchange(
    exchange_id: str,
    api_key: str = "",
    api_secret: str = "",
    sandbox: bool = False,
) -> Any:
    """Create a ccxt async exchange instance with rate limiting enabled.

    Args:
        exchange_id: One of 'bybit', 'binance', 'okx'.
        api_key: Optional API key.
        api_secret: Optional API secret.
        sandbox: If True, enable testnet/sandbox mode.

    Returns:
        Configured ccxt exchange instance.

    Raises:
        ValueError: If exchange_id is not supported.
    """
    supported = ("bybit", "binance", "okx")
    if exchange_id not in supported:
        raise ValueError(
            f"Unsupported exchange: {exchange_id!r}. Must be one of {supported}"
        )

    exchange_class = getattr(ccxt_pro, exchange_id)
    config: dict[str, Any] = {"enableRateLimit": True}
    if api_key:
        config["apiKey"] = api_key
    if api_secret:
        config["secret"] = api_secret

    exchange = exchange_class(config)

    if sandbox:
        exchange.set_sandbox_mode(True)
        logger.info("ExchangeConnector: %s testnet/sandbox mode ENABLED", exchange_id)

    return exchange


class ExchangeConnector:
    """Unified OHLCV fetcher with exponential backoff and polling support.

    Attributes:
        exchange_id: The exchange identifier (bybit, binance, okx).
        exchange: The underlying ccxt exchange instance.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        api_secret: str = "",
        sandbox: bool = False,
    ) -> None:
        self.exchange_id = exchange_id
        self.exchange = _make_exchange(
            exchange_id, api_key, api_secret, sandbox=sandbox
        )
        self._markets_loaded = False

    async def _ensure_markets(self) -> None:
        """Load exchange markets once on first use for symbol resolution."""
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True
            logger.info(
                "ExchangeConnector: loaded %d markets for %s",
                len(self.exchange.markets),
                self.exchange_id,
            )

    def _resolve_symbol(self, symbol: str) -> str:
        """Resolve short symbol (e.g. 'BTC/USDT') to full perpetual form ('BTC/USDT:USDT').

        Handles the universe scanner output format where :USDT suffix is stripped.
        """
        # Already has swap suffix — return as-is
        if ":USDT" in symbol:
            return symbol

        # Try common perpetual suffixes
        for suffix in (":USDT", ":USDC"):
            candidate = f"{symbol}{suffix}"
            if candidate in self.exchange.markets:
                return candidate

        # Fallback: return original, let ccxt raise if truly invalid
        return symbol

    async def close(self) -> None:
        """Close the underlying exchange connection."""
        await self.exchange.close()
        logger.info("exchange %s closed", self.exchange_id)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int = 1000,
    ) -> list[list]:
        """Fetch OHLCV candles with exponential backoff on transient errors.

        Args:
            symbol: Unified symbol (e.g. 'BTC/USDT').
            timeframe: Candle timeframe (e.g. '1h', '15m', '1d').
            since: Timestamp in ms to fetch candles from. None for latest.
            limit: Max number of candles per request (exchange-specific cap).

        Returns:
            List of [timestamp_ms, open, high, low, close, volume] lists.
            Returns empty list on persistent failure.
        """
        # Load markets once so _resolve_symbol can map short forms to perpetual symbols
        await self._ensure_markets()
        resolved = self._resolve_symbol(symbol)

        for attempt in range(_MAX_RETRIES):
            try:
                candles = await self.exchange.fetch_ohlcv(
                    resolved, timeframe, since=since, limit=limit
                )
                return candles  # type: ignore[no-any-return]
            except Exception as exc:
                is_rate_limit = "rate limit" in str(exc).lower() or "429" in str(exc)
                if is_rate_limit:
                    backoff = _RATE_LIMIT_BACKOFF_S
                    logger.warning(
                        "rate limited on %s %s (attempt %d/%d), backing off %.1fs",
                        self.exchange_id,
                        symbol,
                        attempt + 1,
                        _MAX_RETRIES,
                        backoff,
                    )
                else:
                    backoff = _BASE_BACKOFF_S * (2**attempt)
                    logger.warning(
                        "fetch_ohlcv error on %s %s (attempt %d/%d): %s — retrying in %.1fs",
                        self.exchange_id,
                        resolved,
                        attempt + 1,
                        _MAX_RETRIES,
                        exc,
                        backoff,
                    )
                await asyncio.sleep(backoff)

        logger.error(
            "fetch_ohlcv failed after %d retries for %s %s",
            _MAX_RETRIES,
            self.exchange_id,
            resolved,
        )
        return []

    async def fetch_all_candles(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 90,
    ) -> list[list]:
        """Fetch full historical candle set by backward pagination.

        Walks backward from now, paginating with `since` parameter, deduplicating
        overlapping timestamps. Stops when no more data or target depth reached.

        Args:
            symbol: Unified symbol (e.g. 'BTC/USDT').
            timeframe: Candle timeframe.
            days: How many days of history to fetch.

        Returns:
            Deduplicated list of OHLCV candles sorted by timestamp ascending.
        """
        timeframe_ms = self._timeframe_to_ms(timeframe)
        now_ms = int(time.time() * 1000)
        target_depth_ms = now_ms - (days * 86_400_000)

        all_candles: dict[int, list] = {}
        since = target_depth_ms

        while True:
            batch = await self.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not batch:
                break

            for candle in batch:
                ts = int(candle[0])
                all_candles[ts] = candle

            # Advance to after the last candle
            last_ts = int(batch[-1][0])
            if last_ts <= since:
                break  # No progress — avoid infinite loop
            since = last_ts + timeframe_ms

            # Reached current time?
            if last_ts >= now_ms:
                break

            # Small pause between pagination batches
            await asyncio.sleep(0.2)

        result = sorted(all_candles.values(), key=lambda c: c[0])
        logger.info(
            "fetched %d candles for %s %s (%d days)",
            len(result),
            symbol,
            timeframe,
            days,
        )
        return result

    async def poll_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        callback: Any = None,
        interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        """Poll for new candles at fixed interval, invoking callback for each batch.

        Tracks last-seen timestamp to avoid duplicates. Runs until cancelled.

        Args:
            symbol: Unified symbol.
            timeframe: Candle timeframe.
            callback: Async callable receiving (exchange_id, symbol, timeframe, candles).
            interval_s: Seconds between polls.
        """
        last_ts: int | None = None

        while True:
            try:
                candles = await self.fetch_ohlcv(symbol, timeframe, limit=10)
                new_candles = [
                    c for c in candles if last_ts is None or int(c[0]) > last_ts
                ]

                if new_candles and callback is not None:
                    await callback(self.exchange_id, symbol, timeframe, new_candles)
                    last_ts = int(new_candles[-1][0])

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poll_ohlcv error for %s %s", self.exchange_id, symbol)

            await asyncio.sleep(interval_s)

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string to milliseconds.

        Args:
            timeframe: One of '1m','5m','15m','1h','4h','1d','1w'.

        Returns:
            Millisecond equivalent.
        """
        mapping = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
            "1w": 604_800_000,
        }
        if timeframe not in mapping:
            raise ValueError(
                f"Unsupported timeframe: {timeframe!r}. Must be one of {list(mapping)}"
            )
        return mapping[timeframe]

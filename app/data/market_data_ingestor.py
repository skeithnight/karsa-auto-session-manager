"""Market Data Ingestor — polls Bybit for orderbook, funding rate, OI.

Long-lived async task that:
  1. Fetches L2 orderbook depth -> computes orderbook delta (bid/ask imbalance)
  2. Fetches current funding rate
  3. Fetches open interest -> computes OI change % vs previous poll
  4. Publishes to Redis keys (karsa:market:{symbol}:*) for other consumers
  5. Updates MarketConsumer dicts for CHOP scoring

CHOP scoring thresholds (from StrategyRouter):
  orderbook_delta < 0  -> LONG absorption (+20)
  funding_rate < -0.0005 -> LONG confluence (+30)
  oi_change < 0         -> OI drop / capitulation (+30)
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import ccxt.async_support as ccxt
from loguru import logger

REDIS_KEY_PREFIX = "karsa:market"
ORDERBOOK_DEPTH = 25  # top 25 levels each side
POLL_INTERVAL_S = 30  # default poll interval


class MarketDataIngestor:
    """Polls Bybit for micro-structure data: orderbook, funding, OI.

    Publishes to Redis and maintains in-memory dicts for fast access.
    """

    def __init__(  # noqa: PLR0913
        self,
        redis_client: Any,
        symbols: list[str],
        poll_interval_s: int = POLL_INTERVAL_S,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ) -> None:
        self._redis = redis_client
        self._symbols = symbols
        self._interval = poll_interval_s
        self._running = False

        # In-memory caches
        self.orderbook_delta: dict[str, float] = {}
        self.funding_rate: dict[str, float] = {}
        self.oi_change: dict[str, float] = {}
        self._prev_oi: dict[str, float] = {}

        # ccxt Bybit session for REST polling
        self._session: ccxt.bybit | None = None
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet

    async def start(self) -> None:
        """Main polling loop. Runs until stop() is called."""
        self._running = True
        self._session = ccxt.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {"defaultType": "swap"},
        })
        if self._testnet:
            self._session.set_sandbox_mode(True)
        await self._session.load_markets()

        logger.info(
            "MarketDataIngestor: starting poll loop symbols=%s interval=%ds",
            self._symbols, self._interval,
        )

        while self._running:
            try:
                await asyncio.gather(
                    *[self._fetch_symbol(s) for s in self._symbols],
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MarketDataIngestor: poll cycle failed")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Stop polling and close ccxt session."""
        self._running = False
        if self._session:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None
        logger.info("MarketDataIngestor: stopped")

    def _to_ccxt_symbol(self, symbol: str) -> str:
        if ":" not in symbol and symbol.endswith("USDT"):
            return f"{symbol}:USDT"
        return symbol

    async def _fetch_symbol(self, symbol: str) -> None:
        """Fetch orderbook, funding, OI for a single symbol."""
        if not self._session:
            return
        
        ccxt_sym = self._to_ccxt_symbol(symbol)

        try:
            await self._fetch_orderbook(symbol, ccxt_sym)
        except Exception:
            logger.debug("MarketDataIngestor: orderbook fetch failed %s", symbol)

        try:
            await self._fetch_funding_rate(symbol, ccxt_sym)
        except Exception:
            logger.debug("MarketDataIngestor: funding fetch failed %s", symbol)

        try:
            await self._fetch_oi(symbol, ccxt_sym)
        except Exception:
            logger.debug("MarketDataIngestor: OI fetch failed %s", symbol)

    async def _fetch_orderbook(self, symbol: str, ccxt_sym: str) -> None:
        """Fetch L2 orderbook and compute bid/ask volume imbalance.

        delta = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        Range: [-1.0, 1.0]. CHOP uses contrarian:
          delta < 0 + LONG = bids absorbing sell pressure (+20)
          delta > 0 + SHORT = asks absorbing buy pressure (+20)
        """
        ob = await self._session.fetch_order_book(ccxt_sym, limit=ORDERBOOK_DEPTH)  # type: ignore[union-attr]
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        bid_vol = sum(level[1] for level in bids)
        ask_vol = sum(level[1] for level in asks)
        total = bid_vol + ask_vol
        delta = (bid_vol - ask_vol) / total if total > 0 else 0.0

        self.orderbook_delta[symbol] = delta
        await self._publish(symbol, "orderbook_delta", str(round(delta, 6)))

    async def _fetch_funding_rate(self, symbol: str, ccxt_sym: str) -> None:
        """Fetch current funding rate.

        CHOP thresholds:
          rate < -0.0005 -> shorts paying longs -> LONG confluence (+30)
          rate >  0.0005 -> longs paying shorts -> SHORT confluence (+30)
        """
        funding = await self._session.fetch_funding_rate(ccxt_sym)  # type: ignore[union-attr]
        rate = float(funding.get("fundingRate") or 0.0)
        self.funding_rate[symbol] = rate
        await self._publish(symbol, "funding_rate", str(rate))

    async def _fetch_oi(self, symbol: str, ccxt_sym: str) -> None:
        """Fetch open interest and compute change vs previous poll.

        OI drop (oi_change < 0) = capitulation / liquidation-driven move (+30).
        """
        try:
            oi_data = await self._session.fetch_open_interest(ccxt_sym)  # type: ignore[union-attr]
            current_oi = float(
                oi_data.get("openInterestAmount")
                or oi_data.get("openInterest")
                or 0
            )
        except (AttributeError, TypeError):
            current_oi = 0.0

        if current_oi == 0:
            return

        prev_oi = self._prev_oi.get(symbol, current_oi)
        change = (current_oi - prev_oi) / prev_oi if prev_oi > 0 else 0.0
        self.oi_change[symbol] = change
        self._prev_oi[symbol] = current_oi
        await self._publish(symbol, "oi_change", str(round(change, 6)))

    async def _publish(self, symbol: str, field: str, value: str) -> None:
        """Publish a single value to Redis key."""
        try:
            await self._redis.set(f"{REDIS_KEY_PREFIX}:{symbol}:{field}", value)
        except Exception:
            logger.debug("MarketDataIngestor: redis publish %s failed %s", field, symbol)

    def update_consumer(self, consumer: Any) -> None:
        """Push latest values into a MarketConsumer's dicts.

        Call before each candle evaluate cycle.
        """
        consumer.orderbook_delta.update(self.orderbook_delta)
        consumer.funding_rate.update(self.funding_rate)
        consumer.oi_change.update(self.oi_change)

    def get_all(self, symbol: str) -> dict[str, float | None]:
        """Get all three data points for a symbol."""
        return {
            "orderbook_delta": self.orderbook_delta.get(symbol),
            "funding_rate": self.funding_rate.get(symbol),
            "oi_change": self.oi_change.get(symbol),
        }

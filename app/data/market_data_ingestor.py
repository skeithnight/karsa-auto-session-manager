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
import time
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt
from loguru import logger

from app.core import metrics

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
        self.cvd: dict[str, float] = {}
        self.cvd_history: dict[str, list[float]] = {}
        self.cvd_slope: dict[str, float] = {}
        self.liquidity_walls: dict[str, dict[str, float | None]] = {}

        # Spoofing Detection (CPU-Optimized for Top 5 Levels)
        self.spoofing_bid: dict[str, bool] = {}
        self.spoofing_ask: dict[str, bool] = {}
        self._top_bid_levels: dict[str, dict[float, float]] = {}
        self._top_ask_levels: dict[str, dict[float, float]] = {}
        self._spoof_expiry_bid: dict[str, float] = {}
        self._spoof_expiry_ask: dict[str, float] = {}

        # Failure tracking per symbol: escalate from debug to warning
        self._failure_counts: dict[str, int] = {}
        self._escalation_threshold = 3

        # Freshness tracking: timestamp of last successful fetch per symbol
        self._last_fetch_ts: dict[str, float] = {}
        self.max_staleness_s: float = 2.0

        # ccxt Bybit session for REST polling
        self._session: ccxt.bybit | None = None
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet

    async def start(self) -> None:
        """Main polling loop. Runs until stop() is called."""
        self._running = True
        self._session = ccxt.bybit(
            {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "options": {"defaultType": "swap"},
            }
        )
        if self._testnet:
            self._session.set_sandbox_mode(True)
        await self._session.load_markets()

        logger.info(
            "MarketDataIngestor: starting poll loop symbols=%s interval=%ds",
            self._symbols,
            self._interval,
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
        cycle_failures = 0

        try:
            await self._fetch_orderbook(symbol, ccxt_sym)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="orderbook", result="success"
            ).inc()
        except Exception as e:
            cycle_failures += 1
            self._log_fetch_failure(symbol, "orderbook", e)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="orderbook", result="failure"
            ).inc()

        try:
            await self._fetch_funding_rate(symbol, ccxt_sym)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="funding", result="success"
            ).inc()
        except Exception as e:
            cycle_failures += 1
            self._log_fetch_failure(symbol, "funding", e)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="funding", result="failure"
            ).inc()

        try:
            await self._fetch_oi(symbol, ccxt_sym)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="oi", result="success"
            ).inc()
        except Exception as e:
            cycle_failures += 1
            self._log_fetch_failure(symbol, "OI", e)
            metrics.data_fetch_total.labels(
                symbol=symbol, field="oi", result="failure"
            ).inc()

        if cycle_failures == 0:
            self._failure_counts[symbol] = 0
            self._last_fetch_ts[symbol] = time.time()
            metrics.data_age_seconds.labels(symbol=symbol).set(0.0)
        else:
            last_ts = self._last_fetch_ts.get(symbol)
            if last_ts is not None:
                metrics.data_age_seconds.labels(symbol=symbol).set(
                    time.time() - last_ts
                )

    def _log_fetch_failure(self, symbol: str, field: str, error: Exception) -> None:
        """Log fetch failure with escalation after threshold consecutive misses."""
        self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
        count = self._failure_counts[symbol]
        if count > self._escalation_threshold:
            logger.warning(
                "MarketDataIngestor: persistent %s fetch failure for %s (%d consecutive) — %s",
                field,
                symbol,
                count,
                error,
            )
        else:
            logger.debug(
                "MarketDataIngestor: %s fetch failed %s — %s", field, symbol, error
            )

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

        # Cumulative Volume Delta (CVD) calculation & slope tracking
        current_cvd = self.cvd.get(symbol, 0.0) + (bid_vol - ask_vol)
        self.cvd[symbol] = current_cvd
        hist = self.cvd_history.setdefault(symbol, [])
        hist.append(current_cvd)
        if len(hist) > 5:
            hist.pop(0)

        # CVD Slope: change over recent window normalized by avg volume
        if len(hist) >= 2 and total > 0:
            slope = (hist[-1] - hist[0]) / (total * len(hist))
            slope = max(-1.0, min(1.0, slope))
        else:
            slope = 0.0
        self.cvd_slope[symbol] = slope
        await self._publish(symbol, "cvd_slope", str(round(slope, 6)))

        # Liquidity Wall Detection: >3x average level volume
        wall_above: float | None = None
        wall_below: float | None = None
        if bids:
            avg_bid_vol = bid_vol / len(bids)
            for price, vol in bids:
                if vol > 3.0 * avg_bid_vol:
                    wall_below = float(price)
                    break
        if asks:
            avg_ask_vol = ask_vol / len(asks)
            for price, vol in asks:
                if vol > 3.0 * avg_ask_vol:
                    wall_above = float(price)
                    break

        self.liquidity_walls[symbol] = {"wall_above": wall_above, "wall_below": wall_below}
        if wall_above is not None:
            await self._publish(symbol, "wall_above", str(wall_above))
        if wall_below is not None:
            await self._publish(symbol, "wall_below", str(wall_below))

        # Spoofing Detection: CPU-Optimized for Top 5 Levels (> $500k notional, canceled < 3.0s)
        now_ts = time.time()
        if self._spoof_expiry_bid.get(symbol, 0) < now_ts:
            self.spoofing_bid[symbol] = False
        if self._spoof_expiry_ask.get(symbol, 0) < now_ts:
            self.spoofing_ask[symbol] = False

        prev_bids = self._top_bid_levels.setdefault(symbol, {})
        current_bids: dict[float, float] = {}
        for price_level, vol_level in bids[:5]:
            p_flt = float(price_level)
            v_flt = float(vol_level)
            if p_flt * v_flt > 500_000:
                current_bids[p_flt] = prev_bids.get(p_flt, now_ts)

        for prev_p, added_ts in list(prev_bids.items()):
            if prev_p not in current_bids:
                duration = now_ts - added_ts
                if duration < 3.0:
                    logger.warning(
                        f"SPOOFING DETECTED for {symbol}: Bid level ${prev_p:.2f} (> $500k) disappeared after {duration:.2f}s!"
                    )
                    self.spoofing_bid[symbol] = True
                    self._spoof_expiry_bid[symbol] = now_ts + 30.0
                    await self._publish(symbol, "spoofing_bid", "true")

        self._top_bid_levels[symbol] = current_bids

        prev_asks = self._top_ask_levels.setdefault(symbol, {})
        current_asks: dict[float, float] = {}
        for price_level, vol_level in asks[:5]:
            p_flt = float(price_level)
            v_flt = float(vol_level)
            if p_flt * v_flt > 500_000:
                current_asks[p_flt] = prev_asks.get(p_flt, now_ts)

        for prev_p, added_ts in list(prev_asks.items()):
            if prev_p not in current_asks:
                duration = now_ts - added_ts
                if duration < 3.0:
                    logger.warning(
                        f"SPOOFING DETECTED for {symbol}: Ask level ${prev_p:.2f} (> $500k) disappeared after {duration:.2f}s!"
                    )
                    self.spoofing_ask[symbol] = True
                    self._spoof_expiry_ask[symbol] = now_ts + 30.0
                    await self._publish(symbol, "spoofing_ask", "true")

        self._top_ask_levels[symbol] = current_asks

        # Cache mid price for shadow APM (SL/TP monitoring)
        if bids and asks:
            best_bid = Decimal(str(bids[0][0]))
            best_ask = Decimal(str(asks[0][0]))
            if best_bid > 0 and best_ask > 0:
                mid = str((best_bid + best_ask) / 2)
                await self._redis.set(f"shadow:price:{symbol}", mid, ex=300)

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
                oi_data.get("openInterestAmount") or oi_data.get("openInterest") or 0
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
            logger.debug(
                "MarketDataIngestor: redis publish %s failed %s", field, symbol
            )

    def update_consumer(self, consumer: Any) -> None:
        """Push latest values into a MarketConsumer's dicts.

        Call before each candle evaluate cycle.
        """
        consumer.orderbook_delta.update(self.orderbook_delta)
        consumer.funding_rate.update(self.funding_rate)
        consumer.oi_change.update(self.oi_change)
        if hasattr(consumer, "cvd_slope"):
            consumer.cvd_slope.update(self.cvd_slope)
        if hasattr(consumer, "liquidity_walls"):
            consumer.liquidity_walls.update(self.liquidity_walls)

    def update_symbols(self, new_symbols: list[str]) -> None:
        """Update symbol list at runtime. New symbols picked up on next poll cycle."""
        added = set(new_symbols) - set(self._symbols)
        removed = set(self._symbols) - set(new_symbols)
        if added or removed:
            logger.info(
                f"MarketDataIngestor: universe update +{len(added)} -{len(removed)} "
                f"({len(new_symbols)} total)"
            )
        self._symbols = list(new_symbols)

    def get_all(self, symbol: str) -> dict[str, float | None]:
        """Get all three data points for a symbol."""
        return {
            "orderbook_delta": self.orderbook_delta.get(symbol),
            "funding_rate": self.funding_rate.get(symbol),
            "oi_change": self.oi_change.get(symbol),
        }

    def is_stale(self, symbol: str) -> bool:
        """Check if data for symbol exceeds max staleness threshold."""
        ts = self._last_fetch_ts.get(symbol)
        if ts is None:
            return True
        return (time.time() - ts) > self.max_staleness_s

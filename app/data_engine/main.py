"""Karsa Data Engine — asyncio entrypoint.

Container entrypoint for karsa-data-engine. Initializes config,
connects to DB/Redis, starts polling all configured exchanges for
OHLCV data. Publishes to Redis Pub/Sub and caches to PostgreSQL.

Graceful shutdown on SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
import sys
import time
from typing import Any

from app.alpha.lead_lag_buffer import LeadLagBuffer
from app.alpha.regime import RegimeEngine
from app.core import metrics
from app.core.config import get_settings
from app.core.dependencies import get_pool, get_redis, shutdown, startup
from app.core.redis_client import RedisClient
from app.core.session import AutonomousSessionManager
from app.core.telemetry import TelemetryEmitter
from app.data.ccxt_manager import CCXTManager
from app.data.filters import BadTickFilter
from app.data.normalizer import Normalizer
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.data_engine.exchange_connector import ExchangeConnector
from app.data_engine.postgres_cacher import bulk_upsert
from app.data_engine.redis_publisher import RedisPublisher

logger = logging.getLogger("karsa.data_engine")

_POLL_TIMEFRAME = "1h"
_POLL_INTERVAL_S = 60.0
_HISTORICAL_DAYS = 90


def _configure_logging() -> None:
    """Configure structured JSON logging for container output."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","msg":"%(message)s"}'
        )
    )
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


async def _ingest_historical(
    connector: ExchangeConnector,
    symbol: str,
    timeframe: str,
    pool: Any,
) -> int:
    """Fetch historical candles and upsert to PostgreSQL.

    Args:
        connector: Exchange connector instance.
        symbol: Unified symbol.
        timeframe: Candle timeframe.
        pool: asyncpg connection pool.

    Returns:
        Number of candles upserted.
    """
    candles = await connector.fetch_all_candles(
        symbol, timeframe, days=_HISTORICAL_DAYS
    )
    if not candles:
        return 0

    async with pool.acquire() as conn:
        inserted = await bulk_upsert(
            conn, connector.exchange_id, symbol, timeframe, candles
        )

    logger.info("historical ingest: %d candles for %s %s", inserted, symbol, timeframe)
    return inserted


async def _poll_and_publish(  # noqa: PLR0913
    connector: ExchangeConnector,
    publisher: RedisPublisher,
    pool: Any,
    symbol: str,
    timeframe: str,
    emitter: TelemetryEmitter | None = None,
) -> None:
    """Poll loop: fetch latest candles, publish to Redis, cache to PG.

    Runs continuously until cancelled. Uses connector.fetch_ohlcv with
    exponential backoff (handled inside connector).

    Args:
        connector: Exchange connector instance.
        publisher: Redis Pub/Sub publisher.
        pool: asyncpg connection pool.
        symbol: Unified symbol.
        timeframe: Candle timeframe.
        emitter: Optional telemetry emitter for heartbeat tracking.
    """
    last_ts: int | None = None

    while True:
        try:
            candles = await connector.fetch_ohlcv(symbol, timeframe, limit=10)
            new_candles = [
                c for c in candles if last_ts is None or int(c[0]) >= last_ts
            ]

            if new_candles:
                await publisher.publish_candles(
                    connector.exchange_id, symbol, timeframe, new_candles
                )
                if emitter is not None:
                    last_published = new_candles[-1]
                    emitter.record_candle(str(last_published[0]))
                # Cache new candles to PG (idempotent via ON CONFLICT DO NOTHING)
                async with pool.acquire() as conn:
                    await bulk_upsert(
                        conn, connector.exchange_id, symbol, timeframe, new_candles
                    )
                last_ts = int(new_candles[-1][0])

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "poll error: %s %s %s", connector.exchange_id, symbol, timeframe
            )

        await asyncio.sleep(_POLL_INTERVAL_S)


async def _stream_orderbook(
    symbol: str,
    exchange_id: str,
    ccxt_manager: CCXTManager,
    normalizer: Normalizer,
    bad_tick_filter: BadTickFilter,
    redis_client: RedisClient,
    lead_lag_buffer: LeadLagBuffer,
    publisher: RedisPublisher,
    shutdown_event: asyncio.Event,
) -> None:
    """Stream orderbook for a single (symbol, exchange) pair. Runs until shutdown."""
    logger.info(f"Starting stream {exchange_id}/{symbol}")
    retries = 0
    last_heartbeat = 0.0
    while not shutdown_event.is_set():
        try:
            orderbook = await ccxt_manager.watch_orderbook(symbol, exchange_id)
            retries = 0  # reset backoff on success
            metrics.orderbook_received.labels(exchange=exchange_id, symbol=symbol).inc()
            now = time.time()
            if now - last_heartbeat >= 5.0:
                await redis_client.set_exchange_heartbeat(exchange_id)
                last_heartbeat = now
            exchange_data = normalizer.normalize_orderbook(
                orderbook, exchange_id, symbol
            )
            exchange_data = bad_tick_filter.filter_orderbook(exchange_data)
            if exchange_data.is_stale:
                metrics.bad_tick_rejected.labels(
                    exchange=exchange_id, symbol=symbol
                ).inc()
            metrics.orderbook_normalized.labels(
                exchange=exchange_id, symbol=symbol
            ).inc()

            # Feed lead-lag buffer with mid price
            best_bid = (
                max(exchange_data.bids, key=lambda x: x[0])[0]
                if exchange_data.bids
                else None
            )
            best_ask = (
                min(exchange_data.asks, key=lambda x: x[0])[0]
                if exchange_data.asks
                else None
            )
            if best_bid and best_ask:
                mid = (float(best_bid) + float(best_ask)) / 2
                lead_lag_buffer.update(symbol, exchange_id, mid)


                global_state = normalizer.build_global_state(symbol, [exchange_data])
                if global_state:
                    await redis_client.set_global_state(
                        symbol,
                        {
                            "global_vwap": str(global_state.global_vwap),
                            "aggregate_skew": global_state.aggregate_skew,
                            "best_bid": (
                                str(global_state.best_bid)
                                if global_state.best_bid
                                else None
                            ),
                            "best_ask": (
                                str(global_state.best_ask)
                                if global_state.best_ask
                                else None
                            ),
                            "total_volume": (
                                str(global_state.total_volume)
                                if global_state.total_volume
                                else None
                            ),
                            "updated_at": global_state.updated_at.isoformat(),
                        },
                    )

                    # Publish tick data for Micro-Scalper
                    if best_bid and best_ask:
                        await publisher.publish_tick(
                            exchange_id,
                            symbol,
                            {
                                "ts": int(now * 1000),
                                "best_bid": str(best_bid),
                                "best_ask": str(best_ask),
                                "ob_imbalance": global_state.aggregate_skew if global_state.aggregate_skew else 0.0,
                                "recent_trades": []
                            }
                        )

                    metrics.global_state_written.labels(symbol=symbol).inc()
                    if global_state.global_vwap is not None:
                        metrics.vwap_value.labels(symbol=symbol).set(
                            float(global_state.global_vwap)
                        )
                    if global_state.aggregate_skew is not None:
                        metrics.skew_value.labels(symbol=symbol).set(
                            float(global_state.aggregate_skew)
                        )
        except asyncio.CancelledError:
            logger.info(f"Stream {exchange_id}/{symbol} cancelled")
            raise
        except Exception as e:
            retries += 1
            metrics.orderbook_errors.labels(
                exchange=exchange_id, symbol=symbol, error_type=type(e).__name__
            ).inc()
            logger.warning(
                f"Stream {exchange_id}/{symbol} error (attempt {retries}): {e}"
            )
            if retries >= 10:
                logger.error(
                    f"Stream {exchange_id}/{symbol} gave up after {retries} retries"
                )
                break
            # Exponential backoff with jitter to prevent thundering herd
            delay = min(2 ** min(retries, 6), 30) + random.random()
            logger.info(
                f"Stream {exchange_id}/{symbol} retry {retries} in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
    logger.debug(f"Stream {exchange_id}/{symbol} exiting")



async def _regime_metrics_loop(
    ohlcv_fetcher: OHLCVFetcher,
    shutdown_event: asyncio.Event,
    interval_s: int = 900,
) -> None:
    """Periodically classify BTC regime and update Prometheus metrics."""
    regime_engine = RegimeEngine()
    regime_map = {"CHOP": 0, "MEAN_REVERSION": 1, "TREND_BEAR": 2, "TREND_BULL": 3}

    while not shutdown_event.is_set():
        try:
            candles_1h = await ohlcv_fetcher.fetch(
                "BTC/USDT", "1h", 200, ttl_seconds=900
            )
            candles_4h = await ohlcv_fetcher.fetch(
                "BTC/USDT", "4h", 60, ttl_seconds=3600
            )

            if candles_1h and len(candles_1h) >= 200:
                regime, hurst, adx = await asyncio.to_thread(
                    regime_engine.classify_multi, candles_1h, candles_4h or []
                )
                metrics.regime_state.set(regime_map.get(regime, 0))
                metrics.regime_hurst.set(hurst)
                metrics.regime_adx.set(adx)
                adx_4h = 0.0
                if candles_4h and len(candles_4h) >= 50:
                    _, _, adx_4h = await asyncio.to_thread(
                        regime_engine.classify, candles_4h, 50
                    )
                    metrics.regime_adx_4h.set(adx_4h)
                logger.info(
                    "BTC regime: %s (hurst=%.4f adx=%.2f adx_4h=%.2f)",
                    regime,
                    hurst,
                    adx,
                    adx_4h,
                )
            else:
                logger.warning(
                    "Insufficient BTC candles: %d", len(candles_1h) if candles_1h else 0
                )
        except Exception:
            logger.exception("regime_metrics_loop error")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_s)
        except TimeoutError:
            pass


async def _asm_session_loop(
    redis: Any,
    shutdown_event: asyncio.Event,
) -> None:
    """Run AutonomousSessionManager to toggle asm_session_active metric."""
    session_manager = AutonomousSessionManager(redis, shutdown_event)
    await session_manager.run_loop()


async def _live_equity_monitor_loop(
    redis: Any,
    bybit_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 5,
) -> None:
    """Monitor total live equity (Balance + uPnL) and trigger KILL on -2% drawdown."""
    while not shutdown_event.is_set():
        try:
            if not bybit_client:
                await asyncio.sleep(interval_s)
                continue

            wallet = await bybit_client.get_wallet_balance()
            balance = Decimal(str(wallet.get("balance", "0")))
            if balance <= 0:
                await asyncio.sleep(interval_s)
                continue

            import json as _json
            keys = await redis.keys("karsa:position:*")
            upnl = Decimal("0")

            for k in keys:
                raw = await redis.get(k)
                if raw:
                    pos = _json.loads(raw)
                    entry_price = Decimal(str(pos.get("entry_price", "0")))
                    amount = Decimal(str(pos.get("amount", "0")))
                    side = pos.get("side", "LONG")
                    symbol = pos.get("symbol", "")

                    if entry_price > 0 and amount > 0:
                        state_raw = await redis.get(f"global:state:{symbol}")
                        if state_raw:
                            state = _json.loads(state_raw)
                            best_bid = Decimal(str(state.get("best_bid", "0")))
                            best_ask = Decimal(str(state.get("best_ask", "0")))
                            if best_bid > 0 and best_ask > 0:
                                live_price = (best_bid + best_ask) / 2
                                if side == "LONG":
                                    upnl += (live_price - entry_price) * amount
                                else:
                                    upnl += (entry_price - live_price) * amount

            start_balance_raw = await redis.get("karsa:metrics:daily_start_balance")
            start_balance = Decimal(str(start_balance_raw)) if start_balance_raw else balance

            if not start_balance_raw:
                await redis.set("karsa:metrics:daily_start_balance", str(balance))
                start_balance = balance

            if start_balance > 0:
                live_equity = balance + upnl
                drawdown = (live_equity - start_balance) / start_balance

                if drawdown <= Decimal("-0.02"):
                    logger.critical(
                        f"🚨 LIVE EQUITY DRAWDOWN TRIGGERED: Drawdown is {drawdown:.2%}! "
                        f"Live Equity = {live_equity:.2f}, Start = {start_balance:.2f}. "
                        "INITIATING EMERGENCY FLATTEN."
                    )
                    # Flatten all positions
                    for k in keys:
                        raw = await redis.get(k)
                        if raw:
                            pos = _json.loads(raw)
                            symbol = pos.get("symbol", "")
                            side = pos.get("side", "LONG")
                            amount_str = str(pos.get("amount", "0"))
                            if Decimal(amount_str) > 0 and symbol:
                                try:
                                    api_side = "sell" if side == "LONG" else "buy"
                                    bybit_symbol = symbol.replace("/", "")
                                    await bybit_client.create_order(
                                        symbol=bybit_symbol,
                                        side=api_side,
                                        order_type="Market",
                                        qty=amount_str,
                                        reduce_only=True,
                                    )
                                    logger.critical(f"Flattened {symbol} {side} due to drawdown.")
                                    # Clear from redis
                                    await redis.delete(k)
                                except Exception as e:
                                    logger.error(f"Failed to flatten {symbol}: {e}")

                    # Disable trading
                    await redis.set("karsa:auto:state:active", "0")
                    await redis.set("karsa:auto:drawdown_triggered", "1")
                    logger.critical("TRADING DISABLED DUE TO DAILY DRAWDOWN LIMIT.")
        except Exception as e:
            logger.debug(f"Live equity monitor error: {e}")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_s)
        except TimeoutError:
            pass


async def _run_engine(
    settings: Any,
    emitter: TelemetryEmitter | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:  # noqa: PLR0915, PLR0912
    """Main engine loop — connects exchanges, starts poll tasks.

    Runs DynamicUniverseScanner in background. Reads universe list from
    Redis periodically and dynamically creates/cancels poll tasks.

    Args:
        shutdown_event: Shared event from main() — DO NOT create a local one.
    """
    pool = get_pool()
    redis = get_redis()
    redis_client = RedisClient()
    redis_client.redis = redis
    publisher = RedisPublisher(redis)

    # Determine which exchanges to connect (testnet routing via BYBIT_TESTNET env)
    exchanges: list[ExchangeConnector] = []
    if settings.bybit_api_key:
        exchanges.append(
            ExchangeConnector(
                "bybit",
                settings.bybit_api_key,
                settings.bybit_api_secret,
                sandbox=settings.bybit_testnet,
            )
        )
    if not exchanges:
        logger.error("no exchanges configured — set at least one API key in .env")
        return

    # Start universe scanner
    from app.data.universe_scanner import DynamicUniverseScanner

    scanner = DynamicUniverseScanner(
        redis_client=redis,
        api_key=settings.bybit_api_key or "",
        api_secret=settings.bybit_api_secret or "",
        testnet=settings.bybit_testnet,
        top_n=40,
        fallback_symbols=settings.symbols[:40],
    )
    scanner_task = asyncio.create_task(scanner.start(), name="universe-scanner")

    # Regime metrics + ASM session tasks — use the shutdown_event passed from main()
    # (DO NOT create a new Event or install signal handlers here — main() owns them)
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    # Create OHLCVFetcher from the bybit exchange connector for regime classification
    bybit_connector = exchanges[0] if exchanges else None
    regime_fetcher = OHLCVFetcher(bybit_connector.exchange) if bybit_connector else None

    # Initialize streaming dependencies
    ccxt_manager = CCXTManager()
    await ccxt_manager.start(testnet=settings.bybit_testnet)
    normalizer = Normalizer()
    bad_tick_filter = BadTickFilter()
    lead_lag_buffer = LeadLagBuffer()

    extra_tasks: list[asyncio.Task] = []
    if regime_fetcher:
        extra_tasks.append(
            asyncio.create_task(
                _regime_metrics_loop(regime_fetcher, shutdown_event),
                name="regime-metrics",
            )
        )
    extra_tasks.append(
        asyncio.create_task(
            _asm_session_loop(redis, shutdown_event), name="asm-session"
        )
    )

    if settings.bybit_api_key:
        try:
            from app.execution.bybit_client import BybitClient
            bybit_client = BybitClient()
            await bybit_client.connect()
            extra_tasks.append(
                asyncio.create_task(
                    _live_equity_monitor_loop(redis, bybit_client, shutdown_event),
                    name="live-equity-monitor",
                )
            )
            logger.info("Live Equity Monitor started.")
        except Exception as e:
            logger.warning(f"Failed to start Live Equity Monitor: {e}")

    # Wait for first scan to complete
    await asyncio.sleep(2)
    if not scanner.symbols:
        logger.info("no scanner results yet — waiting for first refresh")
        while not scanner.symbols:
            await asyncio.sleep(5)

    initial_symbols = scanner.get_active_symbols() or settings.symbols[:40]

    logger.info(
        "starting data engine: %d exchanges, %d initial symbols, timeframe=%s",
        len(exchanges),
        len(initial_symbols),
        _POLL_TIMEFRAME,
    )

    logger.info("starting historical ingest for %d symbols...", len(initial_symbols))
    ingest_tasks = []
    for connector in exchanges:
        for symbol in initial_symbols:
            ingest_tasks.append(
                _ingest_historical(connector, symbol, _POLL_TIMEFRAME, pool)
            )
    await asyncio.gather(*ingest_tasks, return_exceptions=True)
    logger.info("historical ingest complete.")

    # Track active poll tasks: key = "exchange:symbol", value = Task
    active_tasks: dict[str, asyncio.Task] = {}
    stream_tasks: dict[str, asyncio.Task] = {}
    for connector in exchanges:
        for symbol in initial_symbols:
            key = f"{connector.exchange_id}:{symbol}"
            active_tasks[key] = asyncio.create_task(
                _poll_and_publish(
                    connector, publisher, pool, symbol, _POLL_TIMEFRAME, emitter
                ),
                name=key,
            )
            # Start websocket stream task
            stream_tasks[key] = asyncio.create_task(
                _stream_orderbook(
                    symbol,
                    connector.exchange_id,
                    ccxt_manager,
                    normalizer,
                    bad_tick_filter,
                    redis_client,
                    lead_lag_buffer,
                    publisher,
                    shutdown_event,
                ),
                name=f"stream-{key}",
            )

    logger.info("started %d poll tasks and %d stream tasks", len(active_tasks), len(stream_tasks))

    # Universe management loop — checks every 5 minutes for symbol changes
    universe_check_interval_s = 300
    try:
        while True:
            await asyncio.sleep(universe_check_interval_s)
            current_symbols = set(scanner.get_active_symbols())
            if not current_symbols:
                continue

            # Cancel tasks for removed symbols
            for connector in exchanges:
                active_symbols = {
                    key.split(":", 1)[1]
                    for key in active_tasks
                    if key.startswith(connector.exchange_id)
                }
                removed = active_symbols - current_symbols
                for symbol in removed:
                    key = f"{connector.exchange_id}:{symbol}"
                    task = active_tasks.pop(key, None)
                    if task and not task.done():
                        task.cancel()
                        logger.info("cancelled poll task for %s", key)
                    s_task = stream_tasks.pop(key, None)
                    if s_task and not s_task.done():
                        s_task.cancel()
                        logger.info("cancelled stream task for %s", key)

                # Start tasks for new symbols
                added = current_symbols - active_symbols
                for symbol in added:
                    key = f"{connector.exchange_id}:{symbol}"
                    if key in active_tasks:
                        continue
                    # Historical ingest for new symbol
                    with contextlib.suppress(Exception):
                        await _ingest_historical(
                            connector, symbol, _POLL_TIMEFRAME, pool
                        )
                    active_tasks[key] = asyncio.create_task(
                        _poll_and_publish(
                            connector,
                            publisher,
                            pool,
                            symbol,
                            _POLL_TIMEFRAME,
                            emitter,
                        ),
                        name=key,
                    )
                    stream_tasks[key] = asyncio.create_task(
                        _stream_orderbook(
                            symbol,
                            connector.exchange_id,
                            ccxt_manager,
                            normalizer,
                            bad_tick_filter,
                            redis_client,
                            lead_lag_buffer,
                            publisher,
                            shutdown_event,
                        ),
                        name=f"stream-{key}",
                    )
                    logger.info("started poll and stream task for new symbol %s", key)

            if len(active_tasks) != len(active_symbols):
                logger.info(
                    "universe updated: %d active poll tasks",
                    len(active_tasks),
                )
    finally:
        shutdown_event.set()
        scanner_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scanner_task
        await scanner.stop()
        for task in active_tasks.values():
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*active_tasks.values())
        for task in stream_tasks.values():
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*stream_tasks.values())
        for task in extra_tasks:
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*extra_tasks)
        for connector in exchanges:
            await connector.close()


async def main() -> None:
    """Entrypoint: configure logging, start DB/Redis, run engine, handle shutdown."""
    _configure_logging()
    settings = get_settings()

    if prom_port := __import__("os").getenv("PROMETHEUS_PORT"):
        from prometheus_client import start_http_server

        start_http_server(int(prom_port))
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    def _sigusr1_handler() -> None:
        logger.info("SIGUSR1 received: dumping tracemalloc and objgraph")
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                tracemalloc.start(10)
                logger.info("started tracemalloc")
            else:
                snapshot = tracemalloc.take_snapshot()
                top_stats = snapshot.statistics('lineno')
                logger.info("[Memory] Top 10 memory allocations:")
                for stat in top_stats[:10]:
                    logger.info(f"  {stat}")
        except Exception as e:
            logger.error(f"Failed to dump tracemalloc: {e}")

        try:
            import objgraph
            logger.info("[Memory] Most common types:")
            types = objgraph.most_common_types(limit=10)
            for t in types:
                logger.info(f"  {t[0]}: {t[1]}")
        except ImportError:
            logger.warning("objgraph not installed")
        except Exception as e:
            logger.error(f"Failed to dump objgraph: {e}")

    if hasattr(signal, "SIGUSR1"):
        loop.add_signal_handler(signal.SIGUSR1, _sigusr1_handler)

    logger.info("karsa-data-engine starting (role=%s)", settings.karsa_role)

    await startup(settings)

    emitter = TelemetryEmitter(get_redis(), "data-engine")
    await emitter.start()

    engine_task = asyncio.create_task(
        _run_engine(settings, emitter, shutdown_event=shutdown_event)
    )

    # Wait for shutdown signal
    await shutdown_event.wait()

    logger.info("cancelling engine tasks...")
    engine_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await engine_task

    await emitter.stop()
    await shutdown()
    logger.info("karsa-data-engine stopped")


if __name__ == "__main__":
    asyncio.run(main())

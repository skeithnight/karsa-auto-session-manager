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
import signal
import sys
from typing import Any

from app.core.config import get_settings
from app.core.dependencies import get_pool, get_redis, shutdown, startup
from app.core.telemetry import TelemetryEmitter
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
    candles = await connector.fetch_all_candles(symbol, timeframe, days=_HISTORICAL_DAYS)
    if not candles:
        return 0

    async with pool.acquire() as conn:
        inserted = await bulk_upsert(conn, connector.exchange_id, symbol, timeframe, candles)

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


async def _run_engine(settings: Any, emitter: TelemetryEmitter | None = None) -> None:  # noqa: PLR0915, PLR0912
    """Main engine loop — connects exchanges, starts poll tasks.

    Runs DynamicUniverseScanner in background. Reads universe list from
    Redis periodically and dynamically creates/cancels poll tasks.
    """
    pool = get_pool()
    redis = get_redis()
    publisher = RedisPublisher(redis)

    # Determine which exchanges to connect (testnet routing via BYBIT_TESTNET env)
    exchanges: list[ExchangeConnector] = []
    if settings.bybit_api_key:
        exchanges.append(ExchangeConnector(
            "bybit",
            settings.bybit_api_key,
            settings.bybit_api_secret,
            sandbox=settings.bybit_testnet,
        ))
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

    # Wait for first scan to complete
    await asyncio.sleep(2)
    if not scanner.symbols:
        logger.info("no scanner results yet — waiting for first refresh")
        while not scanner.symbols:
            await asyncio.sleep(5)

    initial_symbols = scanner.get_active_symbols() or settings.symbols[:40]

    logger.info(
        "starting data engine: %d exchanges, %d initial symbols, timeframe=%s",
        len(exchanges), len(initial_symbols), _POLL_TIMEFRAME,
    )

    logger.info("starting historical ingest for %d symbols...", len(initial_symbols))
    ingest_tasks = []
    for connector in exchanges:
        for symbol in initial_symbols:
            ingest_tasks.append(_ingest_historical(connector, symbol, _POLL_TIMEFRAME, pool))
    await asyncio.gather(*ingest_tasks, return_exceptions=True)
    logger.info("historical ingest complete.")

    # Track active poll tasks: key = "exchange:symbol", value = Task
    active_tasks: dict[str, asyncio.Task] = {}
    for connector in exchanges:
        for symbol in initial_symbols:
            key = f"{connector.exchange_id}:{symbol}"
            active_tasks[key] = asyncio.create_task(
                _poll_and_publish(connector, publisher, pool, symbol, _POLL_TIMEFRAME, emitter),
                name=key,
            )

    logger.info("started %d poll tasks", len(active_tasks))

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

                # Start tasks for new symbols
                added = current_symbols - active_symbols
                for symbol in added:
                    key = f"{connector.exchange_id}:{symbol}"
                    if key in active_tasks:
                        continue
                    # Historical ingest for new symbol
                    with contextlib.suppress(Exception):
                        await _ingest_historical(connector, symbol, _POLL_TIMEFRAME, pool)
                    active_tasks[key] = asyncio.create_task(
                        _poll_and_publish(
                            connector, publisher, pool, symbol, _POLL_TIMEFRAME, emitter,
                        ),
                        name=key,
                    )
                    logger.info("started poll task for new symbol %s", key)

            if len(active_tasks) != len(active_symbols):
                logger.info(
                    "universe updated: %d active poll tasks", len(active_tasks),
                )
    finally:
        scanner_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scanner_task
        await scanner.stop()
        for task in active_tasks.values():
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*active_tasks.values())
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

    logger.info("karsa-data-engine starting (role=%s)", settings.karsa_role)

    await startup(settings)

    emitter = TelemetryEmitter(get_redis(), "data-engine")
    await emitter.start()

    engine_task = asyncio.create_task(_run_engine(settings, emitter))

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

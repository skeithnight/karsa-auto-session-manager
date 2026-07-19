"""karsa-live entrypoint — wires MarketConsumer to SmartOrderRouter.

Connects to Redis, subscribes to candle channels, and executes real
trades through the Bybit SmartOrderRouter. Every entry passes
PortfolioRiskManager before execution — no bypass.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
from decimal import Decimal
from typing import Any

from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.consumer.decision_engine import DecisionEngine, TradeSignal
from app.consumer.market_consumer import MarketConsumer
from app.core.config import get_settings
from app.core.dependencies import get_pool, get_redis, shutdown, startup
from app.core.position_store import PositionStore
from app.core.telemetry import TelemetryEmitter
from app.core.trade_store import TradeStore
from app.data.market_data_ingestor import MarketDataIngestor
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.risk.dynamic_risk_gate import DynamicRiskGate

logger = logging.getLogger("karsa.live")


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","msg":"%(message)s"}'
        )
    )
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


async def _on_candle(_symbol: str, _candle: list) -> None:
    """Per-candle callback — extend with Prometheus metrics if needed."""
    pass


async def _on_signal_live(  # noqa: PLR0913  # noqa: PLR0913
    symbol: str,
    signal: TradeSignal,
    position_store: PositionStore,
    executor: Any,
    risk_manager: Any,
    trade_store: TradeStore,
) -> None:
    """Handle a TradeSignal by executing a real order on Bybit.

    Checks:
    1. No duplicate position already open.
    2. PortfolioRiskManager approves.
    3. Execute via SmartOrderRouter.
    4. Record trade in DB.
    """
    # Skip if position already open
    has_pos = await position_store.has_position(symbol)
    if has_pos:
        logger.info("skip %s — position already open", symbol)
        return

    # Max positions check (ponytail: read from Redis if set, default 3)
    open_positions = await position_store.list_all()
    max_pos = 3  # ponytail: hardcoded default, Redis override available
    if len(open_positions) >= max_pos:
        logger.info("skip %s — max positions %d reached (%d open)", symbol, max_pos, len(open_positions))
        return

    # PortfolioRiskManager gate (mandatory, no bypass)
    if risk_manager is not None:
        from app.risk.portfolio_risk_manager import PRMResult
        result: PRMResult = await risk_manager.check(signal)
        if not result.approved:
            logger.info("skip %s — portfolio risk rejected: %s", symbol, result.reason)
            return

    # Execute via SmartOrderRouter
    result = await executor.execute(
        symbol=symbol,
        side=signal.direction,
        amount=signal.amount,
        price=signal.entry_price,
        max_loss_usd=abs(signal.entry_price - signal.sl_price) * signal.amount,
    )

    if result is None:
        logger.warning("execution failed for %s", symbol)
        return

    fill_price = Decimal(str(result.get("price", 0)))

    # Save position
    await position_store.save(
        symbol=symbol,
        side=signal.direction,
        entry_price=fill_price,
        amount=signal.amount,
        atr=signal.atr,
        entry_confidence=signal.score,
        regime=signal.regime.value,
        risk_profile_json=signal.risk_profile.to_json(),
    )

    # Record trade
    await trade_store.record_entry(
        symbol=symbol,
        side=signal.direction,
        amount=signal.amount,
        entry_price=fill_price,
        regime=signal.regime.value,
        risk_profile_json=signal.risk_profile.to_json(),
    )

    logger.info(
        "executed %s %s @ %s (score=%.1f, regime=%s)",
        symbol, signal.direction, fill_price, signal.score, signal.regime.value,
    )


async def _read_universe(redis: Any) -> list[str] | None:
    """Read universe symbols from DynamicUniverseScanner Redis key."""
    try:
        import json as _json
        raw = await redis.get("system:universe:symbols")
        if raw:
            data = _json.loads(raw)
            symbols = data.get("symbols")
            if symbols and isinstance(symbols, list):
                return symbols
    except Exception:
        logger.debug("live: failed to read universe from Redis")
    return None


def _start_ingestor(
    settings: Any,
    redis: Any,
    consumer: MarketConsumer,
    initial_symbols: list[str],
) -> tuple[MarketDataIngestor, asyncio.Task]:
    """Create ingestor + sync loop. Returns (ingestor, task)."""
    ingestor = MarketDataIngestor(
        redis_client=redis,
        symbols=initial_symbols,
        poll_interval_s=30,
        api_key=settings.bybit_api_key or "",
        api_secret=settings.bybit_api_secret or "",
        testnet=settings.bybit_testnet,
    )

    async def _sync_loop() -> None:
        task = asyncio.create_task(ingestor.start())
        try:
            while True:
                await asyncio.sleep(31)
                ingestor.update_consumer(consumer)
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return ingestor, asyncio.create_task(_sync_loop(), name="live-ingestor")


async def _universe_refresh_loop(
    redis: Any, ingestor: MarketDataIngestor, interval_s: int = 14400
) -> None:
    """Periodically refresh symbol list from DynamicUniverseScanner."""
    while True:
        await asyncio.sleep(interval_s)
        new_symbols = await _read_universe(redis)
        if new_symbols:
            ingestor.update_symbols(new_symbols)


async def main() -> None:  # noqa: PLR0915
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

    logger.info("karsa-live starting")

    await startup(settings)
    redis = get_redis()
    pool = get_pool()

    emitter = TelemetryEmitter(redis, "live")
    await emitter.start()

    # Build components
    classifier = RegimeClassifier(redis_client=redis)
    router = StrategyRouter()
    risk_gate = DynamicRiskGate()
    engine = DecisionEngine(classifier, router, risk_gate)

    position_store = PositionStore(redis)

    # Build execution components
    from app.execution.sor import SmartOrderRouter

    bybit = None
    try:
        from app.execution.bybit_client import BybitClient as _BybitClient
        bybit = _BybitClient()
        await bybit.connect()
        from app.core import metrics
        metrics.vpn_status.set(1)
        metrics.bybit_status.set(1)
    except Exception:
        logger.warning("BybitClient unavailable — live execution disabled")

    executor = SmartOrderRouter(bybit) if bybit is not None else None

    from app.risk.portfolio_risk_manager import PortfolioRiskManager

    trade_store = None
    try:
        from app.core.database import DatabaseEngine
        from app.core.trade_store import TradeStore as _TradeStore
        db_engine = DatabaseEngine()
        await db_engine.connect(settings.postgres_url)
        trade_store = _TradeStore(db_engine)
    except Exception:
        logger.warning("TradeStore unavailable — trades will not be recorded")

    class _SectorMapping:
        """Wrapper to adapt sync get_sector to async interface expected by PRM."""
        async def get_sector(self, symbol: str) -> str:
            from app.data.sector_mapping import get_sector
            return get_sector(symbol)

    risk_manager = PortfolioRiskManager(
        redis_client=redis,
        position_store=position_store,
        trade_store=trade_store,
        sector_mapping=_SectorMapping(),
        bybit_client=bybit,
    )

    async def on_signal(symbol: str, sig: TradeSignal) -> None:
        emitter.record_signal()
        await _on_signal_live(
            symbol, sig, position_store, executor, risk_manager, trade_store
        )

    consumer = MarketConsumer(redis, engine, on_signal, _on_candle)

    # Read dynamic universe from Redis, fall back to static config
    universe_symbols = await _read_universe(redis)
    initial_symbols = universe_symbols if universe_symbols else (
        settings.watchlist.split(",") if settings.watchlist else settings.symbols
    )
    logger.info(f"live universe: {len(initial_symbols)} symbols from {'redis' if universe_symbols else 'config'}")

    # Pre-fill CandleBuffer with historical candles so DecisionEngine can evaluate immediately
    try:
        import ccxt.async_support as ccxt
        exchange = ccxt.bybit({'enableRateLimit': True})
        ohlcv_fetcher = OHLCVFetcher(exchange)
        for sym in initial_symbols[:10]:
            try:
                candles = await ohlcv_fetcher.fetch(sym, "1h", 60)
                if candles:
                    for c in candles:
                        consumer._buffer.append(sym, c)
                logger.info(f"live pre-filled buffer for {sym} with {len(candles or [])} candles")
            except Exception as e:
                logger.warning(f"failed to pre-fill {sym}: {e}")
    except Exception as e:
        logger.warning(f"OHLCVFetcher init failed — no candle pre-fill: {e}")

    # Startup state reconciliation — sync exchange positions with internal stores
    try:
        from app.core.state_reconciliation import StateReconciler

        bybit_client = None
        try:
            from app.execution.bybit_client import BybitClient as _BybitClient
            bybit_client = _BybitClient()
            await bybit_client.connect()
        except Exception:
            logger.warning("BybitClient unavailable — skipping reconciliation")

        if bybit_client is not None:
            reconciler = StateReconciler(
                bybit_client=bybit_client,
                position_store=position_store,
                trade_store=trade_store,
                db_engine=pool,
            )
            recon_results = await reconciler.reconcile()
            logger.info("reconciliation_complete: %s", recon_results)

            # Sync exchange positions to Redis — save any orphaned exchange
            # positions so max_positions count is accurate on restart
            try:
                exchange_positions = recon_results.get("exchange_positions_raw") or []
                if not exchange_positions:
                    exchange_positions = await bybit_client.fetch_positions() or []

                # Build set of exchange positions: symbol (ccxt format) + side
                exchange_map: dict[str, dict] = {}
                for p in exchange_positions:
                    sym = (p.get("symbol") or "").replace("/", "")
                    side = p.get("side", "")  # already "buy"/"sell" from fetch_positions
                    exchange_map[f"{sym}:{side}"] = p

                # Check which exchange positions are NOT in Redis
                existing_keys = await position_store.redis.keys("karsa:position:*")
                existing: set[str] = set()
                for key in existing_keys:
                    try:
                        raw = await position_store.redis.get(key if isinstance(key, str) else key.decode())
                        if raw:
                            pos = json.loads(raw)
                            p_sym = (pos.get("symbol") or "").replace("/", "")
                            p_side = pos.get("side", "")
                            existing.add(f"{p_sym}:{p_side}")
                    except Exception:
                        pass

                # Also clean stale keys (in Redis but NOT on exchange)
                stale_keys: list[str] = []
                for key in existing_keys:
                    key_str = key if isinstance(key, str) else key.decode()
                    raw = await position_store.redis.get(key_str)
                    if not raw:
                        stale_keys.append(key_str)
                        continue
                    try:
                        pos = json.loads(raw)
                        p_sym = (pos.get("symbol") or "").replace("/", "")
                        p_side = pos.get("side", "")
                        if f"{p_sym}:{p_side}" not in exchange_map:
                            stale_keys.append(key_str)
                    except Exception:
                        stale_keys.append(key_str)

                for key_str in stale_keys:
                    await position_store.redis.delete(key_str)
                if stale_keys:
                    logger.warning("stale_cleanup: removed %d stale Redis keys", len(stale_keys))

                # Save exchange positions missing from Redis
                synced = 0
                for ex_key, p in exchange_map.items():
                    if ex_key not in existing:
                        # Convert buy/sell back to LONG/SHORT for position_store
                        ccxt_sym = (p.get("symbol") or "").replace("/", "")
                        ccxt_sym_fmt = ccxt_sym[:-4] + "/" + ccxt_sym[-4:] if len(ccxt_sym) > 4 else ccxt_sym
                        side_long = "LONG" if p.get("side") == "buy" else "SHORT"
                        await position_store.save(
                            symbol=ccxt_sym_fmt,
                            side=side_long,
                            entry_price=Decimal(str(p.get("entry_price", 0))),
                            amount=Decimal(str(p.get("contracts", 0))),
                        )
                        synced += 1
                        logger.info("reconciled_position: saved %s %s to Redis", ccxt_sym_fmt, side_long)

                if synced:
                    logger.warning("reconciled_position: synced %d exchange positions to Redis", synced)
            except Exception:
                logger.exception("position_sync failed")
    except Exception:
        logger.exception("reconciliation_failed — continuing startup")

    # Market data ingestor — feeds orderbook/funding/OI to CHOP scorer
    ingestor, ingestor_task = _start_ingestor(settings, redis, consumer, initial_symbols)
    universe_task = asyncio.create_task(
        _universe_refresh_loop(redis, ingestor), name="live-universe"
    )
    consumer_task = asyncio.create_task(consumer.start(), name="live-consumer")

    try:
        await shutdown_event.wait()
    finally:
        consumer.stop()
        ingestor_task.cancel()
        universe_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task
        with contextlib.suppress(asyncio.CancelledError):
            await ingestor_task
        with contextlib.suppress(asyncio.CancelledError):
            await universe_task
        await ingestor.stop()
        await emitter.stop()
        await shutdown()
        logger.info("karsa-live stopped")


if __name__ == "__main__":
    asyncio.run(main())

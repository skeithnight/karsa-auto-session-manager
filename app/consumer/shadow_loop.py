"""karsa-shadow entrypoint — wires MarketConsumer to ShadowExecutor + ShadowAPM.

Shadow mode simulates trades without real exchange interaction. Shares
the DecisionEngine with karsa-live but diverges at execution layer:
  SmartOrderRouter → ShadowExecutor (virtual fills)
  ActivePositionManager → ShadowAPM (virtual monitoring)
  PositionStore → ShadowPositionStore (shadow:* Redis keys)
  TradeStore → ShadowTradeStore (shadow_trades table)
"""

from __future__ import annotations

import asyncio
import contextlib
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
from app.core.telemetry import TelemetryEmitter
from app.data.market_data_ingestor import MarketDataIngestor
from app.risk.dynamic_risk_gate import DynamicRiskGate

logger = logging.getLogger("karsa.shadow")


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


async def _on_signal_shadow(
    symbol: str,
    signal: TradeSignal,
    shadow_executor: Any,
    shadow_pos_store: Any,
    shadow_trade_store: Any | None,
    crypto_analyst: Any | None = None,
    risk_manager: Any | None = None,
    engine: Any | None = None,
) -> None:
    """Handle a TradeSignal by executing a virtual shadow trade.

    Checks:
    1. No duplicate shadow position open.
    2. Consecutive loss block (3+ losses in same regime).
    3. Execute via ShadowExecutor (virtual fill).
    4. Record shadow trade in DB.
    """
    # Skip if position already open (check both LONG and SHORT)
    has_long = await shadow_pos_store.get(symbol, "LONG")
    has_short = await shadow_pos_store.get(symbol, "SHORT")
    if has_long or has_short:
        existing_side = "LONG" if has_long else "SHORT"
        logger.info(
            "shadow skip %s — position already open (%s)", symbol, existing_side
        )
        return

    # Consecutive loss block
    if engine and await engine.check_consecutive_losses(symbol, signal.regime):
        logger.info("shadow skip %s — consecutive loss block", symbol)
        return

    # AI Analyst gate — skip for high-confidence signals (score >= 75) and CHOP regime
    # CHOP has inherently conflicting indicators, so the AI systematically rejects
    # CHOP signals. Bypass AI entirely for CHOP to get clean signal data.
    AI_CONFIDENCE_BYPASS_THRESHOLD = 75.0
    from app.alpha.regime_classifier import MarketRegime

    is_chop = signal.regime in (MarketRegime.CHOP, MarketRegime.RANGE)
    if (
        crypto_analyst
        and signal.score >= 40.0
        and signal.score < AI_CONFIDENCE_BYPASS_THRESHOLD
        and not is_chop
    ):
        logger.info(f"shadow AI Analyst validating {symbol} signal")
        analyst_result = await crypto_analyst.analyze(
            symbol=symbol,
            direction=signal.direction,
            confidence=signal.score,
            regime=signal.regime.value,
            spread_pct=0.0,
            funding_rate=0.0,
            oi_change=0.0,
            price=signal.entry_price,
            recent_trades="",
        )
        if not analyst_result or analyst_result.direction != signal.direction:
            from app.core import metrics

            reason = "unavailable" if not analyst_result else "direction_mismatch"
            metrics.ai_analyst_rejections.labels(reason=reason).inc()
            logger.info("shadow skip %s - AI analyst rejected (%s)", symbol, reason)
            return

    # PortfolioRiskManager gate
    if risk_manager:
        from app.risk.portfolio_risk_manager import PRMResult

        result: PRMResult = await risk_manager.check(signal)
        if not result.approved:
            from app.core import metrics

            metrics.risk_gate_reject.labels(
                symbol=symbol, reason="portfolio_risk"
            ).inc()
            logger.info(
                "shadow skip %s - PortfolioRiskManager rejected: %s",
                symbol,
                result.reason,
            )
            return

    from app.core import metrics

    metrics.risk_gate_pass.labels(symbol=symbol).inc()

    # Execute virtual trade
    result = await shadow_executor.execute(
        symbol=symbol,
        side=signal.direction,
        amount=signal.amount,
        price=signal.entry_price,
        is_post_only=signal.risk_profile.use_post_only,
    )

    if result is None:
        logger.warning("shadow execution failed for %s", symbol)
        return

    fill_price = Decimal(str(result.get("price", signal.entry_price)))

    # Save to ShadowPositionStore
    await shadow_pos_store.save(
        symbol=symbol,
        side=signal.direction,
        entry_price=fill_price,
        amount=signal.amount,
        atr=signal.atr,
        entry_confidence=signal.score,
        regime=signal.regime.value,
        risk_profile_json=signal.risk_profile.to_json(),
        virtual_sl=str(signal.sl_price) if signal.sl_price else None,
        virtual_tp=str(signal.tp_price) if signal.tp_price else None,
    )

    # Record trade
    if shadow_trade_store is not None:
        await shadow_trade_store.record_entry(
            symbol=symbol,
            side=signal.direction,
            amount=signal.amount,
            entry_price=fill_price,
            regime=signal.regime.value,
            risk_profile_json=signal.risk_profile.to_json(),
        )

    logger.info(
        "shadow executed %s %s @ %s (score=%.1f, regime=%s)",
        symbol,
        signal.direction,
        fill_price,
        signal.score,
        signal.regime.value,
    )


async def _on_candle(_symbol: str, _candle: list) -> None:
    """Per-candle callback — extend with Prometheus metrics if needed."""
    pass


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
        logger.debug("shadow: failed to read universe from Redis")
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

    return ingestor, asyncio.create_task(_sync_loop(), name="shadow-ingestor")


async def _universe_refresh_loop(
    redis: Any, ingestor: MarketDataIngestor, interval_s: int = 14400
) -> None:
    """Periodically refresh symbol list from DynamicUniverseScanner."""
    while True:
        await asyncio.sleep(interval_s)
        new_symbols = await _read_universe(redis)
        if new_symbols:
            ingestor.update_symbols(new_symbols)


async def _orphan_cleanup_loop(
    pos_store: Any, trade_store: Any, interval_s: int = 300
) -> None:
    """Close DB trades with no backing Redis position every 5 minutes."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await pos_store.close_orphans(trade_store)
        except Exception as e:
            logger.debug(f"orphan cleanup loop error: {e}")


async def _build_shadow_components(
    redis: Any,
    settings: Any,
) -> tuple[Any, Any, Any, Any]:
    """Create ShadowPositionStore, ShadowTradeStore, ShadowExecutor, ShadowAPM."""
    from app.core.database import DatabaseEngine
    from app.core.shadow_store import ShadowPositionStore, ShadowTradeStore
    from app.execution.shadow import ShadowAPM, ShadowExecutor

    db_engine = DatabaseEngine()
    await db_engine.connect(settings.postgres_url)

    pos_store = ShadowPositionStore(redis)
    trade_store = ShadowTradeStore(db_engine)
    executor = ShadowExecutor(
        redis_client=redis,
        position_store=pos_store,
        trade_store=trade_store,
    )
    apm = ShadowAPM(
        real_apm=None,
        shadow_executor=executor,
        redis_client=redis,
        position_store=pos_store,
        trade_store=trade_store,
    )
    return pos_store, trade_store, executor, apm


async def main() -> None:
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

    logger.info("karsa-shadow starting")

    await startup(settings)
    redis = get_redis()
    get_pool()

    emitter = TelemetryEmitter(redis, "shadow")
    await emitter.start()

    # Build shared decision engine
    from app.alpha.trade_memory import TradeMemory
    from app.alpha.multi_tf import MultiTFFilter
    from app.data.ohlcv_fetcher import OHLCVFetcher
    import ccxt.async_support as ccxt

    classifier = RegimeClassifier(redis_client=redis)
    router = StrategyRouter()
    risk_gate = DynamicRiskGate()
    trade_memory = TradeMemory(redis)

    # Init Fetcher & Multi-TF
    exchange = ccxt.bybit({"enableRateLimit": True})
    ohlcv_fetcher = OHLCVFetcher(exchange)
    multi_tf = MultiTFFilter(ohlcv_fetcher)

    engine = DecisionEngine(
        classifier,
        router,
        risk_gate,
        trade_memory=trade_memory,
        redis_client=redis,
        multi_tf=multi_tf,
    )

    # Set wallet balance for shadow mode — use config-based balance
    # so position sizing uses proper risk calculations instead of fallback
    SHADOW_WALLET_BALANCE = Decimal("100.0")  # $100 simulated equity
    engine.set_wallet_balance(SHADOW_WALLET_BALANCE)
    logger.info("shadow wallet balance set to %s", SHADOW_WALLET_BALANCE)

    # Build shadow-specific stores and executor
    try:
        (
            shadow_pos_store,
            shadow_trade_store,
            shadow_executor,
            shadow_apm,
        ) = await _build_shadow_components(redis, settings)
    except Exception:
        logger.exception("failed to create shadow components")
        await shutdown()
        return

    # Initialize AI Analyst
    crypto_analyst = None
    try:
        from app.alpha.analyst import CryptoAnalyst
        from app.core.ai_client import AIClient
        from app.data.ohlcv_fetcher import OHLCVFetcher

        ai_client = AIClient(
            router_url=settings.nine_router_base_url,
            auth_token=settings.nine_router_auth_token,
            model=settings.nine_router_model,
        )
        # exchange and ohlcv_fetcher already initialized above for MultiTFFilter
        crypto_analyst = CryptoAnalyst(ai_client, ohlcv_fetcher, redis)
    except Exception as e:
        logger.warning(f"Could not initialize CryptoAnalyst in shadow loop: {e}")

    # Initialize PortfolioRiskManager
    risk_manager = None
    try:
        from app.execution.bybit_client import BybitClient
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        class _SectorMapping:
            async def get_sector(self, s: str) -> str:
                from app.data.sector_mapping import get_sector as _get

                return _get(s)

        sector_mapping = _SectorMapping()
        bybit_client = BybitClient()
        await bybit_client.connect()
        from app.core import metrics

        metrics.vpn_status.set(1)
        metrics.bybit_status.set(1)

        risk_manager = PortfolioRiskManager(
            redis_client=redis,
            position_store=shadow_pos_store,
            trade_store=shadow_trade_store,
            sector_mapping=sector_mapping,
            bybit_client=bybit_client,
        )
    except Exception as e:
        logger.warning(f"Could not initialize PortfolioRiskManager in shadow loop: {e}")

    # Wire signal handler
    async def on_signal(symbol: str, sig: TradeSignal) -> None:
        emitter.record_signal()
        await _on_signal_shadow(
            symbol,
            sig,
            shadow_executor,
            shadow_pos_store,
            shadow_trade_store,
            crypto_analyst,
            risk_manager,
            engine,
        )

    consumer = MarketConsumer(redis, engine, on_signal, _on_candle)

    # Read dynamic universe from Redis, fall back to static config
    universe_symbols = await _read_universe(redis)
    initial_symbols = (
        universe_symbols
        if universe_symbols
        else (settings.watchlist.split(",") if settings.watchlist else settings.symbols)
    )
    logger.info(
        f"shadow universe: {len(initial_symbols)} symbols from {'redis' if universe_symbols else 'config'}"
    )

    # Pre-fill CandleBuffer with historical candles so DecisionEngine can evaluate immediately
    for sym in initial_symbols:
        try:
            candles = await ohlcv_fetcher.fetch(sym, "1h", 60)
            if candles:
                for c in candles:
                    consumer._buffer.append(sym, c)
            logger.info(
                f"shadow pre-filled buffer for {sym} with {len(candles or [])} candles"
            )
        except Exception as e:
            logger.warning(f"failed to pre-fill {sym}: {e}")

    ingestor, ingestor_task = _start_ingestor(
        settings, redis, consumer, initial_symbols
    )
    universe_task = asyncio.create_task(
        _universe_refresh_loop(redis, ingestor), name="shadow-universe"
    )
    consumer_task = asyncio.create_task(consumer.start(), name="shadow-consumer")
    apm_task = asyncio.create_task(shadow_apm.run(), name="shadow-apm")
    orphan_task = asyncio.create_task(
        _orphan_cleanup_loop(shadow_pos_store, shadow_trade_store), name="shadow-orphan"
    )

    logger.info("karsa-shadow started")

    try:
        await shutdown_event.wait()
    finally:
        consumer.stop()
        ingestor_task.cancel()
        universe_task.cancel()
        consumer_task.cancel()
        apm_task.cancel()
        orphan_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task
        with contextlib.suppress(asyncio.CancelledError):
            await ingestor_task
        with contextlib.suppress(asyncio.CancelledError):
            await universe_task
        with contextlib.suppress(asyncio.CancelledError):
            await apm_task
        with contextlib.suppress(asyncio.CancelledError):
            await orphan_task
        await ingestor.stop()
        await emitter.stop()
        await exchange.close()
        await db_engine.dispose()
        await shutdown()
        logger.info("karsa-shadow stopped")


if __name__ == "__main__":
    asyncio.run(main())

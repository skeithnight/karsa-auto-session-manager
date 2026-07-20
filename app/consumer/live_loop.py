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

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
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
from app.bot.alert_service import AlertService

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


async def _wallet_metrics_loop(
    bybit: Any,
    position_store: PositionStore,
    redis: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 30,
) -> None:
    """Periodically publish wallet balance, position metrics, and max positions to Prometheus."""
    from app.core import metrics

    while not shutdown_event.is_set():
        try:
            try:
                max_pos = int(await redis.get("karsa:settings:max_positions") or 5)
            except Exception:
                max_pos = 5
            metrics.max_positions.set(max_pos)

            if bybit:
                wallet = await bybit.get_wallet_balance()
                available = float(wallet.get("available", 0))
                balance = float(wallet.get("balance", 0))
                metrics.wallet_balance.set(available)

            open_positions = await position_store.list_all()

            # Wallet equity = balance + unrealized PnL
            if bybit:
                total_unrealized = sum(float(p.get("pnl", 0)) for p in open_positions)
                metrics.wallet_total_equity.set(balance + total_unrealized)

            # Per-position Prometheus metrics (replaces position_lifecycle.py)
            tracked_symbols = set()
            for pos in open_positions:
                sym = pos.get("symbol", "")
                if not sym:
                    continue
                tracked_symbols.add(sym)
                pos.get("side", "LONG")
                entry = float(pos.get("entry_price", 0))
                amount = float(pos.get("amount", 0))
                sl_price = float(
                    pos.get("sl_price", 0) or pos.get("virtual_sl", 0) or 0
                )

                metrics.position_size.labels(symbol=sym).set(amount)
                metrics.position_entry_price.labels(symbol=sym).set(entry)
                if sl_price > 0:
                    metrics.position_sl_price.labels(symbol=sym).set(sl_price)

                # Duration from entered_at
                entered_at = pos.get("entered_at", "")
                if entered_at:
                    from datetime import UTC, datetime

                    try:
                        entered = datetime.fromisoformat(entered_at)
                        elapsed = (datetime.now(UTC) - entered).total_seconds()
                        metrics.position_duration.labels(symbol=sym).set(elapsed)
                    except Exception:
                        pass

                # Unrealized PnL — compute from current price if not stored
                pnl = float(pos.get("pnl", 0))
                metrics.position_unrealized_pnl.labels(symbol=sym).set(pnl)

            # Clear metrics for closed positions
            # (prometheus_client doesn't support removal, but 0 amount = no display in table)

        except Exception:
            logger.warning("wallet_metrics_loop error", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def _on_signal_live(  # noqa: PLR0913  # noqa: PLR0913
    symbol: str,
    signal: TradeSignal,
    position_store: PositionStore,
    executor: Any,
    risk_manager: Any,
    trade_store: TradeStore,
    engine: Any | None = None,
) -> None:
    """Handle a TradeSignal by executing a real order on Bybit.

    Checks:
    1. No duplicate position already open.
    2. Consecutive loss block (3+ losses in same regime).
    3. PortfolioRiskManager approves.
    4. Execute via SmartOrderRouter.
    5. Record trade in DB.
    """
    # ASM gate — fail-closed: must be explicitly "1" to allow trade.
    # If key is missing, Redis is down, or any exception → block (never open).
    try:
        _asm_raw = (
            await engine._redis.get("karsa:auto:state:active")
            if engine and engine._redis
            else None
        )
        if str(_asm_raw or "").strip() != "1":
            logger.debug("skip %s — ASM not active (state=%r)", symbol, _asm_raw)
            return
    except Exception:
        logger.warning(
            "skip %s — ASM state check failed (fail-closed), blocking trade", symbol
        )
        return

    # Check Auto-Adjustment Regime Overrides
    try:
        raw_cfg = await position_store.redis.get("karsa:auto:config")
        if raw_cfg:
            import json

            cfg = json.loads(raw_cfg)
            overrides = cfg.get("regime_overrides", {})
            if overrides.get(signal.regime.value) == "DISABLE":
                logger.info(
                    "skip %s — %s regime is temporarily DISABLED by Shadow Auto-Adjustment",
                    symbol,
                    signal.regime.value,
                )
                return
    except Exception as e:
        logger.debug("regime_overrides check failed: %s", e)

    # Skip if position already open
    has_pos = await position_store.has_position(symbol)
    if has_pos:
        logger.info("skip %s — position already open", symbol)
        return

    # Max positions check (ponytail: read from Redis if set, default 3)
    open_positions = await position_store.list_all()
    max_pos = 3  # ponytail: hardcoded default, Redis override available
    if len(open_positions) >= max_pos:
        logger.info(
            "skip %s — max positions %d reached (%d open)",
            symbol,
            max_pos,
            len(open_positions),
        )
        return

    # Consecutive loss block
    if engine and await engine.check_consecutive_losses(symbol, signal.regime):
        logger.info("skip %s — consecutive loss block", symbol)
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

    # Bybit V5 returns avgPrice, SOR returns average or price
    fill_price = Decimal(
        str(result.get("average", result.get("avgPrice", result.get("price", 0))))
    )

    # Compute initial_risk_per_unit from actual fill price and signal SL.
    # This is the CRITICAL field APM uses for breakeven/trailing/SL placement.
    # Without it, APM bails out on every cycle and leaves position unprotected.
    initial_risk_per_unit = abs(fill_price - signal.sl_price)
    if initial_risk_per_unit <= Decimal("0"):
        # Fallback: derive from ATR and RiskProfile sl_atr_buffer
        initial_risk_per_unit = signal.atr * signal.risk_profile.sl_atr_buffer

    # Save position — all APM-critical fields must be present here
    await position_store.save(
        symbol=symbol,
        side=signal.direction,
        entry_price=fill_price,
        amount=signal.amount,
        atr=signal.atr,
        entry_confidence=signal.score,
        regime=signal.regime.value,
        entry_regime=signal.regime.value,
        initial_risk_per_unit=str(initial_risk_per_unit),
        risk_profile_json=signal.risk_profile.to_json(),
    )

    # Record trade
    if trade_store:
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
        symbol,
        signal.direction,
        fill_price,
        signal.score,
        signal.regime.value,
    )

    # Prometheus: count opened position
    from app.core import metrics

    metrics.positions_opened.labels(symbol=symbol, side=signal.direction).inc()


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


async def _position_exit_loop(
    bybit: Any,
    position_store: PositionStore,
    trade_store: TradeStore | None,
    alert_service: AlertService | None,
    interval_s: int = 15,
) -> None:
    """Poll Bybit positions, detect closures, send TP/SL alerts."""
    if not bybit or not alert_service:
        return

    from app.bot.utils.formatters import (
        format_sl_alert,
        format_tp_alert,
        format_breakeven_alert,
    )

    while True:
        await asyncio.sleep(interval_s)
        try:
            # Get current positions from Bybit
            exchange_positions = await bybit.fetch_positions() or []
            exchange_map: dict[str, dict] = {}
            for p in exchange_positions:
                sym = (p.get("symbol") or "").replace("/", "")
                side_raw = p.get("side", "")  # "buy"/"sell"
                exchange_map[f"{sym}:{side_raw}"] = p

            # Get positions from Redis
            internal = await position_store.list_all()
            for pos in internal:
                symbol = pos.get("symbol", "")
                side = pos.get("side", "LONG")
                api_side = "buy" if side == "LONG" else "sell"
                ccxt_sym = symbol.replace("/", "")
                key = f"{ccxt_sym}:{api_side}"

                if key in exchange_map:
                    continue  # still open

                # Position closed on exchange — determine TP/SL/breakeven
                entry_price = float(pos.get("entry_price", 0))
                amount = float(pos.get("amount", 0))

                # Fetch recent closed order to get exit price
                exit_price = 0.0
                exit_reason = "unknown"
                try:
                    orders = (
                        await bybit.fetch_closed_orders(symbol)
                        if hasattr(bybit, "fetch_closed_orders")
                        else []
                    )
                    if orders:
                        last = orders[0]
                        exit_price = float(last.get("average", last.get("price", 0)))
                except Exception:
                    pass

                if exit_price <= 0:
                    # Fallback: use entry_price as exit_price to close the loop
                    exit_price = entry_price
                    exit_reason = "closed_unknown"
                    logger.warning(
                        "exit_price unknown for %s — using entry_price as fallback",
                        symbol,
                    )

                # Calculate PnL
                if side == "LONG":
                    pnl = (exit_price - entry_price) * amount
                else:
                    pnl = (entry_price - exit_price) * amount

                if side == "LONG":
                    pnl_pct = (
                        ((exit_price - entry_price) / entry_price * 100)
                        if entry_price > 0
                        else 0
                    )
                else:
                    pnl_pct = (
                        ((entry_price - exit_price) / entry_price * 100)
                        if entry_price > 0
                        else 0
                    )

                # Determine exit reason by price comparison
                if pnl > 0:
                    exit_reason = "tp"
                    msg = format_tp_alert(
                        symbol, side, entry_price, exit_price, pnl, pnl_pct
                    )
                elif pnl < -0.5:
                    exit_reason = "sl"
                    msg = format_sl_alert(
                        symbol, side, entry_price, exit_price, pnl, pnl_pct
                    )
                else:
                    exit_reason = "breakeven"
                    msg = format_breakeven_alert(
                        symbol, side, entry_price, exit_price, pnl, pnl_pct
                    )

                try:
                    await alert_service.send(msg)
                except Exception:
                    logger.warning("failed to send exit alert for %s", symbol)

                # Record in trade store
                if trade_store:
                    try:
                        await trade_store.close_trade(
                            symbol,
                            Decimal(str(exit_price)),
                            Decimal(str(pnl)),
                            exit_reason,
                        )
                    except Exception:
                        logger.warning("failed to record exit trade for %s", symbol)

                # Remove from Redis
                try:
                    await position_store.remove(symbol, side)
                except Exception:
                    pass

                logger.info(
                    "exit detected: %s %s pnl=%.2f reason=%s",
                    symbol,
                    side,
                    pnl,
                    exit_reason,
                )

                # Prometheus: count closed position
                from app.core import metrics

                metrics.positions_closed.labels(
                    symbol=symbol, side=side, exit_reason=exit_reason
                ).inc()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("position_exit_loop error")


async def _status_update_loop(
    bybit: Any,
    position_store: PositionStore,
    alert_service: AlertService | None,
    interval_s: int = 900,
) -> None:
    """Send 15-minute position status update to Telegram."""
    if not bybit or not alert_service:
        return

    while True:
        await asyncio.sleep(interval_s)
        try:
            internal = await position_store.list_all()
            if not internal:
                continue

            # Fetch live prices
            tickers = (
                await bybit.fetch_tickers() if hasattr(bybit, "fetch_tickers") else []
            )
            price_map: dict[str, float] = {}
            for t in tickers:
                sym = (t.get("symbol") or "").replace("/", "")
                last = t.get("last") or t.get("close")
                if sym and last:
                    price_map[sym] = float(last)

            try:
                bybit_positions = (
                    await bybit.fetch_positions()
                    if hasattr(bybit, "fetch_positions")
                    else []
                )
            except Exception as e:
                logger.error(f"status_update: Bybit fetch failed: {e}")
                bybit_positions = []

            # Fallback to internal if Bybit is completely down, but prefer Bybit data
            active_count = len(bybit_positions) if bybit_positions else len(internal)
            lines = [f"⏰ <b>15min Status — {active_count} open</b>", ""]
            total_pnl = 0.0

            if bybit_positions:
                for pos in bybit_positions:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "LONG").upper()
                    pnl = float(pos.get("unrealized_pnl", 0))
                    total_pnl += pnl

                    # Compute ROE% if we have leverage, otherwise raw asset %
                    leverage = float(pos.get("leverage", 1))
                    entry = float(pos.get("entry_price", 0))
                    mark = float(pos.get("markPrice", pos.get("current_price", entry)))

                    if entry > 0 and mark > 0 and leverage > 0:
                        raw_pct = (
                            ((mark - entry) / entry)
                            if side in ("BUY", "LONG")
                            else ((entry - mark) / entry)
                        )
                        pnl_pct = raw_pct * leverage * 100
                    else:
                        pnl_pct = 0.0

                    arrow = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                    lines.append(
                        f"{arrow} <b>{symbol}</b> {side} | ${pnl:+.2f} ({pnl_pct:+.2f}%)"
                    )
            else:
                for pos in internal:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "LONG")
                    entry_price = float(pos.get("entry_price", 0))
                    amount = float(pos.get("amount", 0))
                    ccxt_sym = symbol.replace("/", "")
                    live_price = price_map.get(ccxt_sym, entry_price)

                    if side == "LONG":
                        pnl = (live_price - entry_price) * amount
                    else:
                        pnl = (entry_price - live_price) * amount

                    if side == "LONG":
                        pnl_pct = (
                            ((live_price - entry_price) / entry_price * 100)
                            if entry_price > 0
                            else 0
                        )
                    else:
                        pnl_pct = (
                            ((entry_price - live_price) / entry_price * 100)
                            if entry_price > 0
                            else 0
                        )
                    total_pnl += pnl

                    arrow = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                    lines.append(
                        f"{arrow} <b>{symbol}</b> {side} | ${pnl:+.2f} ({pnl_pct:+.2f}%)"
                    )

            lines.append(f"\n⚖️ <b>Unrealized: ${total_pnl:+.2f}</b>")

            await alert_service.send("\n".join(lines))

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("status_update_loop error")


async def _redis_health_check_loop(
    redis: Any,
    alert_service: AlertService | None,
    interval_s: int = 30,
) -> None:
    """Scheduled Redis health check — runs every `interval_s` seconds.

    Checks:
    1. Redis ping — alert and log on failure.
    2. All karsa:position:* keys contain valid JSON with canonical side (LONG/SHORT).
    3. Critical fields (symbol, side, entry_price, amount) are non-empty.
    4. Purges malformed keys that would cause APM to crash or loop.
    """
    import json as _json

    _CRITICAL_FIELDS = ["symbol", "side", "entry_price", "amount"]
    _consecutive_redis_failures = 0

    while True:
        await asyncio.sleep(interval_s)
        try:
            # 1. Ping check
            try:
                await redis.ping()
                if _consecutive_redis_failures > 0:
                    logger.info(
                        "redis_health: Redis recovered after %d failures",
                        _consecutive_redis_failures,
                    )
                    if alert_service:
                        try:
                            await alert_service.send(
                                "✅ Redis recovered — connection restored."
                            )
                        except Exception:
                            pass
                _consecutive_redis_failures = 0
            except Exception as ping_err:
                _consecutive_redis_failures += 1
                logger.error(
                    "redis_health: PING FAILED (failure #%d): %s",
                    _consecutive_redis_failures,
                    ping_err,
                )
                if _consecutive_redis_failures in (1, 5, 10):
                    if alert_service:
                        try:
                            await alert_service.send(
                                f"🔴 Redis ping FAILED (#{_consecutive_redis_failures}). "
                                "APM cannot read positions — check Redis connectivity!"
                            )
                        except Exception:
                            pass
                continue  # skip key audit if Redis is down

            # 2. Audit position keys
            try:
                keys = await redis.keys("karsa:position:*")
            except Exception:
                continue

            malformed = 0
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode()
                try:
                    raw = await redis.get(key_str)
                    if not raw:
                        await redis.delete(key_str)
                        malformed += 1
                        logger.warning("redis_health: purged empty key %s", key_str)
                        continue

                    pos = _json.loads(raw)

                    # Check canonical side
                    side = pos.get("side", "")
                    if side not in ("LONG", "SHORT"):
                        logger.warning(
                            "redis_health: key %s has non-canonical side=%r — normalising",
                            key_str,
                            side,
                        )
                        pos["side"] = "LONG" if side in ("buy", "Buy") else "SHORT"
                        await redis.set(key_str, _json.dumps(pos))

                    # Check critical fields
                    missing = [f for f in _CRITICAL_FIELDS if not pos.get(f)]
                    if missing:
                        logger.warning(
                            "redis_health: key %s missing fields %s (will be repaired by APM health check)",
                            key_str,
                            missing,
                        )

                except _json.JSONDecodeError:
                    logger.error(
                        "redis_health: purging corrupt (non-JSON) key %s", key_str
                    )
                    await redis.delete(key_str)
                    malformed += 1
                except Exception as key_err:
                    logger.warning(
                        "redis_health: error inspecting key %s: %s", key_str, key_err
                    )

            if malformed:
                logger.warning(
                    "redis_health: purged %d malformed position keys", malformed
                )
                if alert_service:
                    try:
                        await alert_service.send(
                            f"⚠️ Redis health: purged {malformed} malformed position key(s). "
                            "Check logs for details."
                        )
                    except Exception:
                        pass

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("redis_health: unexpected error in health check loop")


async def _balance_refresh_loop(
    bybit: Any,
    engine: DecisionEngine,
    interval_s: int = 60,
) -> None:
    """Fetch wallet balance every 60s and update engine for position sizing."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            bal = await bybit.fetch_balance()
            free = Decimal(str(bal.get("free", 0)))
            if free > 0:
                engine.set_wallet_balance(free)
                logger.debug("balance refreshed: %s USDT", free)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("balance refresh failed")


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
    from app.alpha.trade_memory import TradeMemory
    from app.alpha.multi_tf import MultiTFFilter
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

    # Telegram alerts for live trades
    alert_service: AlertService | None = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            from telegram import Bot as _TGBot

            _tg_bot = _TGBot(token=settings.telegram_bot_token)
            alert_service = AlertService(settings.telegram_chat_id)
            alert_service.register_bot(_tg_bot)
            logger.info("telegram alerts enabled")
        except Exception:
            logger.warning("telegram bot init failed — alerts disabled")

    executor = (
        SmartOrderRouter(bybit, alert_service=alert_service)
        if bybit is not None
        else None
    )

    # APM — manages open positions: breakeven, trailing, time exit, regime kill switch
    from app.execution.position_manager import ActivePositionManager

    apm = (
        ActivePositionManager(
            bybit_client=bybit,
            position_store=position_store,
            regime_classifier=classifier,
            alert_service=alert_service,
            logger_=logger,
        )
        if bybit is not None
        else None
    )

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
            symbol, sig, position_store, executor, risk_manager, trade_store, engine
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
        f"live universe: {len(initial_symbols)} symbols from {'redis' if universe_symbols else 'config'}"
    )

    # Pre-fill CandleBuffer with historical candles so DecisionEngine can evaluate immediately
    try:
        # exchange and ohlcv_fetcher already initialized above for MultiTFFilter
        for sym in initial_symbols:
            try:
                candles = await ohlcv_fetcher.fetch(sym, "1h", 60)
                if candles:
                    for c in candles:
                        consumer._buffer.append(sym, c)
                logger.info(
                    f"live pre-filled buffer for {sym} with {len(candles or [])} candles"
                )
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
                    side = p.get(
                        "side", ""
                    )  # already "buy"/"sell" from fetch_positions
                    exchange_map[f"{sym}:{side}"] = p

                # Check which exchange positions are NOT in Redis
                existing_keys = await position_store.redis.keys("karsa:position:*")
                existing: set[str] = set()
                for key in existing_keys:
                    try:
                        raw = await position_store.redis.get(
                            key if isinstance(key, str) else key.decode()
                        )
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
                    logger.warning(
                        "stale_cleanup: removed %d stale Redis keys", len(stale_keys)
                    )

                # Save exchange positions missing from Redis
                synced = 0
                for ex_key, p in exchange_map.items():
                    if ex_key not in existing:
                        # Convert buy/sell back to LONG/SHORT for position_store
                        ccxt_sym = (p.get("symbol") or "").replace("/", "")
                        ccxt_sym_fmt = (
                            ccxt_sym[:-4] + "/" + ccxt_sym[-4:]
                            if len(ccxt_sym) > 4
                            else ccxt_sym
                        )
                        side_long = "LONG" if p.get("side") == "buy" else "SHORT"
                        entry_price = Decimal(str(p.get("entry_price", 0)))
                        amount = Decimal(str(p.get("contracts", 0)))

                        # Compute regime/atr for APM time-exit and trailing
                        entry_regime = ""
                        atr_val = Decimal("0")
                        try:
                            import numpy as np

                            _candles = []
                            for _c in consumer._buffer.get(ccxt_sym_fmt) or []:
                                _candles.append(_c)
                            if len(_candles) >= 50:
                                _arr = np.array(_candles[-60:], dtype=np.float64)
                                entry_regime = classifier.classify(_arr).value
                                atr_val = engine._calculate_atr(_arr)
                        except Exception:
                            pass

                        initial_risk = Decimal("0")
                        try:
                            rp = (
                                risk_gate.get_profile(MarketRegime(entry_regime))
                                if entry_regime
                                else None
                            )
                            if rp and atr_val > 0:
                                initial_risk = atr_val * rp.sl_atr_buffer
                        except Exception:
                            pass

                        await position_store.save(
                            symbol=ccxt_sym_fmt,
                            side=side_long,
                            entry_price=entry_price,
                            amount=amount,
                            atr=atr_val,
                            entry_confidence="0",
                            regime=entry_regime,
                        )
                        # Patch fields that save() doesn't write
                        key = f"karsa:position:{ccxt_sym_fmt}:{side_long}"
                        try:
                            raw = await position_store.redis.get(key)
                            if raw:
                                pos_data = json.loads(raw)
                                pos_data["entry_regime"] = entry_regime
                                pos_data["initial_risk_per_unit"] = str(initial_risk)
                                await position_store.redis.set(
                                    key, json.dumps(pos_data)
                                )
                        except Exception:
                            pass
                        synced += 1
                        logger.info(
                            "reconciled_position: saved %s %s regime=%s atr=%s",
                            ccxt_sym_fmt,
                            side_long,
                            entry_regime,
                            atr_val,
                        )

                if synced:
                    logger.warning(
                        "reconciled_position: synced %d exchange positions to Redis",
                        synced,
                    )
            except Exception:
                logger.exception("position_sync failed")
    except Exception:
        logger.exception("reconciliation_failed — continuing startup")

    # Market data ingestor — feeds orderbook/funding/OI to CHOP scorer
    ingestor, ingestor_task = _start_ingestor(
        settings, redis, consumer, initial_symbols
    )
    universe_task = asyncio.create_task(
        _universe_refresh_loop(redis, ingestor), name="live-universe"
    )

    # Fetch balance once before starting consumer — prevents over-allocation on first signal
    if bybit:
        try:
            bal = await bybit.fetch_balance()
            free = Decimal(str(bal.get("free", 0)))
            if free > 0:
                engine.set_wallet_balance(free)
                logger.info("initial balance: %s USDT", free)
        except Exception:
            logger.warning("initial balance fetch failed — using fallback sizing")

    consumer_task = asyncio.create_task(consumer.start(), name="live-consumer")

    # Position exit monitor + 15-min status update + APM
    exit_task = asyncio.create_task(
        _position_exit_loop(bybit, position_store, trade_store, alert_service),
        name="live-exit-monitor",
    )
    status_task = asyncio.create_task(
        _status_update_loop(bybit, position_store, alert_service),
        name="live-status-update",
    )
    apm_task = (
        asyncio.create_task(apm.start_monitoring(), name="live-apm") if apm else None
    )
    # APM health check: every 60s, detect + auto-repair positions with missing fields
    apm_health_task = (
        asyncio.create_task(
            apm.start_health_check_loop(interval_s=60), name="live-apm-health"
        )
        if apm
        else None
    )
    # Redis health check: every 30s, verify connectivity and alert on degradation
    redis_health_task = asyncio.create_task(
        _redis_health_check_loop(redis, alert_service), name="live-redis-health"
    )
    balance_task = (
        asyncio.create_task(_balance_refresh_loop(bybit, engine), name="live-balance")
        if bybit
        else None
    )
    wallet_metrics_task = asyncio.create_task(
        _wallet_metrics_loop(bybit, position_store, redis, shutdown_event),
        name="live-wallet-metrics",
    )

    try:
        await shutdown_event.wait()
    finally:
        consumer.stop()
        ingestor_task.cancel()
        universe_task.cancel()
        consumer_task.cancel()
        exit_task.cancel()
        status_task.cancel()
        redis_health_task.cancel()
        if apm_task:
            apm_task.cancel()
        if apm_health_task:
            apm_health_task.cancel()
        if balance_task:
            balance_task.cancel()
        wallet_metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task
        with contextlib.suppress(asyncio.CancelledError):
            await ingestor_task
        with contextlib.suppress(asyncio.CancelledError):
            await universe_task
        with contextlib.suppress(asyncio.CancelledError):
            await exit_task
        with contextlib.suppress(asyncio.CancelledError):
            await status_task
        if apm_task:
            with contextlib.suppress(asyncio.CancelledError):
                await apm_task
        if balance_task:
            with contextlib.suppress(asyncio.CancelledError):
                await balance_task
        with contextlib.suppress(asyncio.CancelledError):
            await wallet_metrics_task
        await ingestor.stop()
        await emitter.stop()
        await exchange.close()
        await db_engine.dispose()
        await shutdown()
        logger.info("karsa-live stopped")


if __name__ == "__main__":
    asyncio.run(main())

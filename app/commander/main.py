"""karsa-commander — standalone Telegram bot control plane.

Wires Redis, DB, BybitClient (wallet display only), and AlertService
into the existing app.bot runner. No trading loops, no WebSocket
connections, no ExchangeConnector. Publishes risk/hot-reload commands
to Redis for karsa-live/karsa-shadow to consume.

Entrypoint dispatched by entrypoint.sh when KARSA_ROLE=commander.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time

from app.bot.alert_service import AlertService
from app.bot.runner import run_bot
from app.core.config import get_settings
from app.core.database import DatabaseEngine
from app.core.redis_client import RedisClient
from app.core.telemetry import TelemetryEmitter
from app.core.trade_reconciler import TradeReconciler
from app.core.trade_store import TradeStore

logger = logging.getLogger("karsa.commander")


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


async def _connect_services() -> tuple[RedisClient, DatabaseEngine]:
    """Connect Redis and Postgres. BybitClient is created on-demand by bot_data wiring."""
    t0 = time.monotonic()
    settings = get_settings()

    # Redis
    redis_client = RedisClient()
    await redis_client.connect()
    logger.info("redis connected (%.0fms)", (time.monotonic() - t0) * 1000)

    # Postgres
    db = DatabaseEngine()
    await db.connect(settings.postgres_url)
    logger.info("postgres connected (%.0fms)", (time.monotonic() - t0) * 1000)

    return redis_client, db


async def scheduled_bulk_backtest_task(
    redis_client: RedisClient,
    db_engine: DatabaseEngine,
    alert_service: AlertService,
    kill_switch: asyncio.Event,
    interval_hours: int = 24,
) -> None:
    """Periodically fetches dynamic universe and runs bulk backtest."""
    logger.debug("scheduled_bulk_backtest_task: entering")
    import json

    from app.backtest.formatter import format_bulk_backtest_summary
    from app.backtest.orchestrator import BacktestOrchestrator

    orch = BacktestOrchestrator(redis_client, db_engine)

    # Wait initially before starting the first run
    await asyncio.sleep(5)

    while not kill_switch.is_set():
        try:
            # 1. Fetch Universe
            raw = await redis_client.redis.get("system:universe:symbols")
            symbols = []
            if raw:
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                if isinstance(data, dict):
                    symbols = data.get("symbols", [])

            if not symbols:
                logger.warning("scheduled_bulk_backtest_task: No symbols in universe")
            else:
                logger.info(
                    "scheduled_bulk_backtest_task: Starting bulk backtest for %d symbols",
                    len(symbols),
                )

                # 2. Submit Bulk Job
                bulk_id = await orch.submit_bulk_job(symbols, candle_limit=500)

                # 3. Poll for completion
                while not kill_switch.is_set():
                    await asyncio.sleep(30)
                    status = await orch.get_bulk_job_status(bulk_id)
                    if status["status"] == "completed":
                        break

                if not kill_switch.is_set():
                    # 4. Generate Report
                    results = await orch.get_bulk_job_results(bulk_id)
                    status = await orch.get_bulk_job_status(bulk_id)
                    report_text = format_bulk_backtest_summary(results, bulk_id, status)

                    # 5. Send Alert
                    await alert_service.send(report_text, parse_mode="HTML")
                    logger.info("scheduled_bulk_backtest_task: Report sent")

        except Exception as e:
            logger.error("scheduled_bulk_backtest_task error: %s", e)

        # Sleep until next interval (chunked for kill switch)
        total_wait = interval_hours * 3600
        waited = 0
        while waited < total_wait and not kill_switch.is_set():
            await asyncio.sleep(60)
            waited += 60

    logger.debug("scheduled_bulk_backtest_task: returning None")


async def telemetry_listener_task(
    redis_client: RedisClient,
    db_engine: DatabaseEngine,
    kill_switch: asyncio.Event,
) -> None:
    """Continuously listens for backtest_complete events to update telemetry."""
    logger.debug("telemetry_listener_task: entering")
    from app.backtest.orchestrator import BacktestOrchestrator

    orch = BacktestOrchestrator(redis_client, db_engine)

    while not kill_switch.is_set():
        try:
            await orch.listen_for_completion(timeout_s=5)
        except Exception as e:
            logger.error("telemetry_listener_task error: %s", e)
            await asyncio.sleep(5)

    logger.debug("telemetry_listener_task: returning None")


async def shadow_feedback_task(
    redis_client: RedisClient,
    db_engine: DatabaseEngine,
    alert_service: AlertService,
    kill_switch: asyncio.Event,
    interval_hours: int = 1,
) -> None:
    """Periodically queries shadow performance and disables unprofitable regimes for Live."""
    logger.debug("shadow_feedback_task: entering")
    from sqlalchemy import text
    import json
    from datetime import datetime, timedelta, timezone

    # Initial wait
    await asyncio.sleep(10)

    while not kill_switch.is_set():
        try:
            logger.info("shadow_feedback_task: running Auto-Adjustment check")
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)

            regime_stats = {}
            async with db_engine.engine.connect() as conn:
                rows = await conn.execute(
                    text("""
                        SELECT regime, 
                               COUNT(*) as total, 
                               COUNT(*) FILTER (WHERE pnl > 0) as wins,
                               SUM(pnl) as net_pnl
                        FROM shadow_trades
                        WHERE exit_time >= :cutoff 
                          AND pnl IS NOT NULL
                          AND exit_reason != 'orphan_cleanup'
                        GROUP BY regime
                    """),
                    {"cutoff": cutoff_date},
                )
                for row in rows:
                    regime = row[0]
                    if not regime:
                        continue
                    total = row[1]
                    wins = row[2]
                    net_pnl = float(row[3] or 0)
                    win_rate = (wins / total * 100) if total > 0 else 0
                    regime_stats[regime] = {
                        "total": total,
                        "wins": wins,
                        "net_pnl": net_pnl,
                        "win_rate": win_rate,
                    }

            # Fetch current config
            raw_cfg = await redis_client.redis.get("karsa:auto:config")
            cfg = json.loads(raw_cfg) if raw_cfg else {}
            overrides = cfg.get("regime_overrides", {})
            changed = False
            alerts = []

            for regime, stats in regime_stats.items():
                if stats["total"] >= 5:
                    current_status = overrides.get(regime, "ENABLE")
                    # Disable logic
                    if (
                        stats["win_rate"] < 40.0 or stats["net_pnl"] < -5.0
                    ) and current_status != "DISABLE":
                        overrides[regime] = "DISABLE"
                        changed = True
                        alerts.append(
                            f"🛡️ <b>Auto-Adjustment Alert</b>\n"
                            f"Shadow Mode detected poor performance in <b>{regime}</b> regime.\n"
                            f"Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}/{stats['total']})\n"
                            f"Net PnL: ${stats['net_pnl']:.2f}\n\n"
                            f"🔴 Live trading for {regime} is now <b>DISABLED</b> to protect capital."
                        )
                    # Re-enable logic
                    elif (
                        stats["win_rate"] >= 50.0
                        and stats["net_pnl"] > 0
                        and current_status == "DISABLE"
                    ):
                        overrides[regime] = "ENABLE"
                        changed = True
                        alerts.append(
                            f"🟢 <b>Auto-Adjustment Alert</b>\n"
                            f"Shadow Mode detected recovery in <b>{regime}</b> regime.\n"
                            f"Win Rate: {stats['win_rate']:.1f}% ({stats['wins']}/{stats['total']})\n"
                            f"Net PnL: ${stats['net_pnl']:.2f}\n\n"
                            f"Live trading for {regime} is now <b>RESTORED</b>."
                        )

            if changed:
                cfg["regime_overrides"] = overrides
                await redis_client.redis.set("karsa:auto:config", json.dumps(cfg))
                logger.info(
                    "shadow_feedback_task: updated regime_overrides: %s", overrides
                )
                for alert in alerts:
                    await alert_service.send(alert, parse_mode="HTML")

        except Exception as e:
            logger.error("shadow_feedback_task error: %s", e)

        # Sleep until next interval
        total_wait = interval_hours * 3600
        waited = 0
        while waited < total_wait and not kill_switch.is_set():
            await asyncio.sleep(60)
            waited += 60

    logger.debug("shadow_feedback_task: returning None")


async def main() -> None:
    """Commander entrypoint — only bot, no trading loops."""
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

    logger.info("karsa-commander starting (role=%s)", settings.karsa_role)

    # Check Telegram token
    if not settings.telegram_bot_token:
        logger.error("telegram_bot_token not set — commander cannot start")
        return

    # Connect infrastructure
    redis_client, db_engine = await _connect_services()

    # Create BybitClient for wallet balance display in /dashboard
    # Lazy connect — only used for wallet queries, not for trading
    try:
        from app.execution.bybit_client import BybitClient

        bybit_client = BybitClient()
        await bybit_client.connect()
        logger.info("bybit connected (wallet display only)")
    except Exception:
        logger.warning("bybit connection failed — wallet display disabled")
        bybit_client = None  # type: ignore[assignment]

    # AlertService
    alert_service = AlertService(settings.telegram_chat_id)

    # Telemetry — commander heartbeat (no trading metrics, just liveness)
    emitter = TelemetryEmitter(redis_client.redis, "commander")
    await emitter.start()

    # Session manager (reads/writes Redis config)
    from app.core.session import AutonomousSessionManager

    session_manager = AutonomousSessionManager(
        redis_client=redis_client,
        kill_switch=shutdown_event,
    )

    # Trade Reconciler
    trade_store = TradeStore(db_engine)
    trade_reconciler = (
        TradeReconciler(bybit_client, trade_store, alert_service)
        if bybit_client
        else None
    )

    # Register alert service with PTB bot after run_bot wires it
    # (AlertService lazily grabs bot from application.bot_data["bot_instance"])

    # Start the bot — blocks until kill_switch fires
    bot_task = asyncio.create_task(
        run_bot(
            redis_client=redis_client,
            bybit_client=bybit_client,
            kill_switch=shutdown_event,
            session_manager=session_manager,
            db_engine=db_engine,
            alert_service=alert_service,
            emitter=emitter,
            trade_reconciler=trade_reconciler,
        ),
        name="commander-bot",
    )

    # Start the scheduled bulk backtest
    bulk_task = asyncio.create_task(
        scheduled_bulk_backtest_task(
            redis_client=redis_client,
            db_engine=db_engine,
            alert_service=alert_service,
            kill_switch=shutdown_event,
            interval_hours=24,  # Run daily
        ),
        name="commander-bulk-backtest",
    )

    # Start the telemetry listener to update job statuses
    listener_task = asyncio.create_task(
        telemetry_listener_task(
            redis_client=redis_client,
            db_engine=db_engine,
            kill_switch=shutdown_event,
        ),
        name="commander-telemetry-listener",
    )

    # Start the shadow feedback auto-adjustment task
    feedback_task = asyncio.create_task(
        shadow_feedback_task(
            redis_client=redis_client,
            db_engine=db_engine,
            alert_service=alert_service,
            kill_switch=shutdown_event,
            interval_hours=1,  # Run every hour
        ),
        name="commander-shadow-feedback",
    )

    logger.info("karsa-commander bot and bulk backtest started")

    try:
        await shutdown_event.wait()
    finally:
        bot_task.cancel()
        bulk_task.cancel()
        listener_task.cancel()
        feedback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(
                bot_task,
                bulk_task,
                listener_task,
                feedback_task,
                return_exceptions=True,
            )

        # Cleanup
        await emitter.stop()
        if bybit_client is not None:
            with contextlib.suppress(Exception):
                await bybit_client.disconnect()

        await redis_client.disconnect()
        await db_engine.dispose()
        logger.info("karsa-commander stopped")


if __name__ == "__main__":
    asyncio.run(main())

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
                    await alert_service.send(report_text)
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


async def scheduled_reports_task(
    db_engine: DatabaseEngine,
    alert_service: AlertService,
    kill_switch: asyncio.Event,
) -> None:
    """Dispatches daily analytics reports."""
    logger.debug("scheduled_reports_task: entering")
    from app.analytics.evidence import EvidenceAnalyzer
    from app.analytics.ai_effectiveness import AIEffectivenessAnalyzer
    from app.analytics.lifecycle import LifecycleAnalyzer
    from app.analytics.calibration import CalibrationAnalyzer
    from app.analytics.reports import ReportGenerator

    generator = ReportGenerator(
        evidence=EvidenceAnalyzer(db_engine),
        ai=AIEffectivenessAnalyzer(db_engine),
        lifecycle=LifecycleAnalyzer(db_engine),
        calibration=CalibrationAnalyzer(db_engine),
    )

    # Initial wait before starting
    await asyncio.sleep(10)

    while not kill_switch.is_set():
        try:
            report = await generator.generate_daily_report()
            await alert_service.send(report)
            logger.info("scheduled_reports_task: Daily report sent")
        except Exception as e:
            logger.error("scheduled_reports_task error: %s", e)

        # Sleep 24 hours (chunked)
        total_wait = 24 * 3600
        waited = 0
        while waited < total_wait and not kill_switch.is_set():
            await asyncio.sleep(60)
            waited += 60

    logger.debug("scheduled_reports_task: returning None")


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
    import json
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    # Initial wait
    await asyncio.sleep(10)

    while not kill_switch.is_set():
        try:
            logger.info("shadow_feedback_task: running Auto-Adjustment check")
            current_date_utc = datetime.now(UTC)
            cutoff_date = current_date_utc - timedelta(days=7)

            regime_stats = {}
            async with db_engine.engine.connect() as conn:
                rows = await conn.execute(
                    text("""
                        SELECT regime,
                               COUNT(*) as total,
                               COUNT(*) FILTER (WHERE pnl > 0) as wins,
                               SUM(pnl) as net_pnl,
                               COALESCE(AVG(pnl) FILTER (WHERE pnl > 0), 0) as avg_win,
                               COALESCE(ABS(AVG(pnl) FILTER (WHERE pnl <= 0)), 0) as avg_loss
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
                    avg_win = float(row[4] or 0)
                    avg_loss = float(row[5] or 0)
                    
                    win_rate = (wins / total) if total > 0 else 0.0
                    loss_rate = 1.0 - win_rate
                    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
                    
                    regime_stats[regime] = {
                        "total": total,
                        "wins": wins,
                        "net_pnl": net_pnl,
                        "win_rate": win_rate * 100.0,
                        "avg_win": avg_win,
                        "avg_loss": avg_loss,
                        "expectancy": expectancy,
                    }

            # Fetch current config
            raw_cfg = await redis_client.redis.get("karsa:auto:config")
            cfg = json.loads(raw_cfg) if raw_cfg else {}
            overrides = cfg.get("regime_overrides", {})
            sizing = cfg.get("regime_sizing", {})
            cooldown = cfg.get("regime_cooldown", {})
            changed = False
            alerts = []

            for regime, stats in regime_stats.items():
                if stats["total"] < 30:
                    logger.debug("shadow_feedback_task: skipping %s due to low sample size (%d < 30)", regime, stats["total"])
                    continue

                current_status = overrides.get(regime, "ENABLE")
                current_size = sizing.get(regime, 1.0)
                cooldown_meta = cooldown.get(regime, {})
                expectancy = stats["expectancy"]
                
                # Check if we are currently disabled
                is_disabled = (current_status == "DISABLE" or current_size == 0.0)
                
                if not is_disabled:
                    # Degradation Logic
                    if expectancy < 0:
                        overrides[regime] = "DISABLE"
                        sizing[regime] = 0.0
                        cooldown[regime] = {
                            "disabled_at": current_date_utc.isoformat(),
                            "consecutive_positive_days": 0,
                            "last_positive_day": None
                        }
                        changed = True
                        alerts.append(
                            f"🛡️ <b>Auto-Adjustment Alert</b>\n"
                            f"Shadow Mode detected negative expectancy in <b>{regime}</b> regime.\n"
                            f"Expectancy: ${expectancy:.2f} (Win Rate: {stats['win_rate']:.1f}% | {stats['wins']}/{stats['total']})\n"
                            f"Net PnL: ${stats['net_pnl']:.2f}\n\n"
                            f"🔴 Live trading for {regime} is now <b>DISABLED (Size 0.0x)</b> to protect capital."
                        )
                    elif expectancy < 1.0:
                        if current_size != 0.5:
                            sizing[regime] = 0.5
                            changed = True
                            alerts.append(
                                f"⚠️ <b>Auto-Adjustment Alert</b>\n"
                                f"Shadow Mode detected low expectancy in <b>{regime}</b> regime.\n"
                                f"Expectancy: ${expectancy:.2f} (Win Rate: {stats['win_rate']:.1f}%)\n\n"
                                f"🟨 Live trading size for {regime} reduced to <b>0.5x</b>."
                            )
                    else:
                        if current_size != 1.0:
                            sizing[regime] = 1.0
                            changed = True
                            alerts.append(
                                f"🟢 <b>Auto-Adjustment Alert</b>\n"
                                f"Shadow Mode detected strong expectancy in <b>{regime}</b> regime.\n"
                                f"Expectancy: ${expectancy:.2f} (Win Rate: {stats['win_rate']:.1f}%)\n\n"
                                f"🟩 Live trading size for {regime} increased to <b>1.0x</b>."
                            )
                else:
                    # Cooldown and Restore Logic
                    disabled_at_str = cooldown_meta.get("disabled_at")
                    consecutive_positive_days = cooldown_meta.get("consecutive_positive_days", 0)
                    last_positive_day = cooldown_meta.get("last_positive_day")
                    
                    if disabled_at_str:
                        try:
                            disabled_at = datetime.fromisoformat(disabled_at_str)
                            if disabled_at.tzinfo is None:
                                disabled_at = disabled_at.replace(tzinfo=UTC)
                                
                            days_disabled = (current_date_utc - disabled_at).total_seconds() / 86400.0
                            if days_disabled >= 14:
                                # Passed the 14-day cooldown
                                current_day_str = current_date_utc.strftime("%Y-%m-%d")
                                if expectancy > 0:
                                    if last_positive_day != current_day_str:
                                        consecutive_positive_days += 1
                                        cooldown_meta["consecutive_positive_days"] = consecutive_positive_days
                                        cooldown_meta["last_positive_day"] = current_day_str
                                        cooldown[regime] = cooldown_meta
                                        changed = True
                                        
                                        if consecutive_positive_days >= 3:
                                            overrides[regime] = "ENABLE"
                                            sizing[regime] = 0.5  # Start gently at 0.5x
                                            cooldown.pop(regime, None)
                                            changed = True
                                            alerts.append(
                                                f"🟢 <b>Auto-Adjustment Alert</b>\n"
                                                f"Shadow Mode verified consistent recovery in <b>{regime}</b> regime "
                                                f"after 14-day cooldown.\n"
                                                f"Expectancy: ${expectancy:.2f} (Win Rate: {stats['win_rate']:.1f}%)\n\n"
                                                f"Live trading for {regime} is now <b>RESTORED (Size 0.5x)</b>."
                                            )
                                else:
                                    if consecutive_positive_days > 0:
                                        cooldown_meta["consecutive_positive_days"] = 0
                                        cooldown[regime] = cooldown_meta
                                        changed = True
                        except ValueError:
                            logger.error("Failed to parse disabled_at timestamp for %s: %s", regime, disabled_at_str)

            if changed:
                cfg["regime_overrides"] = overrides
                cfg["regime_sizing"] = sizing
                cfg["regime_cooldown"] = cooldown
                await redis_client.redis.set("karsa:auto:config", json.dumps(cfg))
                logger.info(
                    "shadow_feedback_task: updated config: overrides=%s, sizing=%s, cooldown=%s",
                    overrides, sizing, cooldown
                )
                for alert in alerts:
                    await alert_service.send(alert)

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
            interval_hours=settings.commander_bulk_backtest_interval_hours,
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

    # Start scheduled reports
    reports_task = asyncio.create_task(
        scheduled_reports_task(
            db_engine=db_engine,
            alert_service=alert_service,
            kill_switch=shutdown_event,
        ),
        name="commander-scheduled-reports",
    )

    # Start the shadow feedback auto-adjustment task
    feedback_task = asyncio.create_task(
        shadow_feedback_task(
            redis_client=redis_client,
            db_engine=db_engine,
            alert_service=alert_service,
            kill_switch=shutdown_event,
            interval_hours=settings.commander_feedback_interval_hours,
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
        reports_task.cancel()
        feedback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(
                bot_task,
                bulk_task,
                listener_task,
                reports_task,
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

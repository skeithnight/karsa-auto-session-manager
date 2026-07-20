"""PTB ApplicationBuilder, handler registration, bot_data wiring.

Wires BybitClient and RedisClient into bot_data, registers all command
and callback handlers, starts asyncio polling, and shuts down cleanly
when kill_switch fires.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

if TYPE_CHECKING:
    from app.core.redis_client import RedisClient
    from app.core.telemetry import TelemetryEmitter
    from app.execution.bybit_client import BybitClient


async def run_bot(  # noqa: PLR0913
    redis_client: RedisClient,
    bybit_client: BybitClient,
    kill_switch: asyncio.Event,
    session_manager: object | None = None,
    db_engine: object | None = None,
    alert_service: object | None = None,
    emitter: TelemetryEmitter | None = None,
    trade_reconciler: object | None = None,
) -> None:
    """Callers: main.py. alert_service gets bot registered after PTB init. No schema change."""
    """Build, start, and run PTB until kill_switch fires."""
    logger.debug("run_bot: entering")
    from app.bot.handlers import (
        activity_cmd,
        backtest_cmd,
        button_callback,
        control_cmd,
        dashboard_cmd,
        performance_cmd,
        portfolio_cmd,
        report_menu_cmd,
        report_shadow_cmd,
        settings_cmd,
        start_cmd,
        trade_history_cmd,
        view_positions_detail_cmd,
    )
    from app.core.config import get_settings

    settings = get_settings()

    if not settings.telegram_bot_token:
        logger.error("telegram_bot_token_missing — bot cannot start")
        logger.debug("run_bot: returning (no token)")
        return

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # ── Wire shared dependencies ────────────────────────────────────────
    application.bot_data["redis_client"] = redis_client
    application.bot_data["bybit_client"] = bybit_client
    application.bot_data["kill_switch"] = kill_switch
    application.bot_data["session_manager"] = session_manager
    application.bot_data["db_engine"] = db_engine
    application.bot_data["emitter"] = emitter
    application.bot_data["trade_reconciler"] = trade_reconciler
    logger.info(
        f"bot_data wired: redis={'ok' if redis_client else 'None'} bybit={'ok' if bybit_client else 'None'} session_manager={'ok' if session_manager else 'None'} db={'ok' if db_engine else 'None'} emitter={'ok' if emitter else 'None'} reconciler={'ok' if trade_reconciler else 'None'}"
    )

    # ── Register command handlers ───────────────────────────────────────
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("dashboard", dashboard_cmd))
    application.add_handler(CommandHandler("activity", activity_cmd))
    application.add_handler(CommandHandler("portfolio", portfolio_cmd))
    application.add_handler(CommandHandler("performance", performance_cmd))
    application.add_handler(CommandHandler("report_menu", report_menu_cmd))
    application.add_handler(CommandHandler("report_shadow", report_shadow_cmd))
    application.add_handler(CommandHandler("control", control_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("positions", view_positions_detail_cmd))
    application.add_handler(CommandHandler("history", trade_history_cmd))
    application.add_handler(CommandHandler("backtest", backtest_cmd))

    # ── Register central callback dispatcher ────────────────────────────
    application.add_handler(CallbackQueryHandler(button_callback))

    # ── Start polling ───────────────────────────────────────────────────
    await application.initialize()
    await application.start()

    # Register bot instance with AlertService for proactive push alerts
    if alert_service is not None:
        alert_service.register_bot(application.bot)

    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("bot_polling_started")

    # ── Wait for kill switch ────────────────────────────────────────────
    await kill_switch.wait()
    logger.info("kill_switch_received_shutting_down_bot")

    # ── Graceful shutdown (must complete within 5s per spec) ────────────
    try:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("bot_shutdown_complete")
    except Exception as exc:
        logger.error("bot_shutdown_error", extra={"error": str(exc)})
    logger.debug("run_bot: returning None")

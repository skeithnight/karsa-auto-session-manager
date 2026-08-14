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
        ai_status_cmd,
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
        summary_cmd,
        trade_history_cmd,
        view_positions_detail_cmd,
        health_cmd,
        analytics_cmd,
        defi_cmd,
    )
    from app.core.config import get_settings

    settings = get_settings()

    if not settings.telegram_bot_token:
        logger.error("telegram_bot_token_missing — bot cannot start")
        logger.debug("run_bot: returning (no token)")
        return

    def _build_app():
        app = (
            ApplicationBuilder()
            .token(settings.telegram_bot_token)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .pool_timeout(30.0)
            .build()
        )
        app.bot_data["redis_client"] = redis_client
        app.bot_data["bybit_client"] = bybit_client
        app.bot_data["kill_switch"] = kill_switch
        app.bot_data["session_manager"] = session_manager
        app.bot_data["db_engine"] = db_engine
        app.bot_data["emitter"] = emitter
        app.bot_data["trade_reconciler"] = trade_reconciler

        from telegram import Update as _Update
        from telegram.ext import TypeHandler

        async def _log_update(u: _Update, c):
            user_id = u.effective_user.id if u.effective_user else "unknown"
            txt = u.effective_message.text if u.effective_message else (u.callback_query.data if u.callback_query else "non-text")
            logger.info(f"📩 TELEGRAM INCOMING UPDATE: user={user_id} payload={txt!r}")

        app.add_handler(TypeHandler(_Update, _log_update), group=-1)

        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("dashboard", dashboard_cmd))
        app.add_handler(CommandHandler("activity", activity_cmd))
        app.add_handler(CommandHandler("portfolio", portfolio_cmd))
        app.add_handler(CommandHandler("performance", performance_cmd))
        app.add_handler(CommandHandler("report_menu", report_menu_cmd))
        app.add_handler(CommandHandler("report_shadow", report_shadow_cmd))
        app.add_handler(CommandHandler("control", control_cmd))
        app.add_handler(CommandHandler("settings", settings_cmd))
        app.add_handler(CommandHandler("positions", view_positions_detail_cmd))
        app.add_handler(CommandHandler("history", trade_history_cmd))
        app.add_handler(CommandHandler("backtest", backtest_cmd))
        app.add_handler(CommandHandler("health", health_cmd))
        app.add_handler(CommandHandler("analytics", analytics_cmd))
        app.add_handler(CommandHandler("ai_status", ai_status_cmd))
        app.add_handler(CommandHandler("summary", summary_cmd))
        app.add_handler(CommandHandler("defi", defi_cmd))
        app.add_handler(CommandHandler("treasury", defi_cmd))

        app.add_handler(CallbackQueryHandler(button_callback))

        from telegram.ext import MessageHandler, filters

        async def _plain_text_handler(update, context):
            text = (update.message.text or "").strip().lower() if update.message else ""
            logger.info(f"Telegram update received: '{text}' from user {update.effective_user.id if update.effective_user else 'unknown'}")
            if text in {"start", "dashboard", "/start", "/dashboard"}:
                await start_cmd(update, context)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _plain_text_handler))

        async def _on_error(update, context):
            logger.error(f"PTB update error: {context.error}", exc_info=context.error)

        app.add_error_handler(_on_error)
        return app

    # ── Start polling (with retry loop and clean rebuild) ──
    started = False
    max_retries = 10
    application = _build_app()

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("run_bot: calling application.initialize() attempt=%d", attempt)
            await application.initialize()
            logger.info("run_bot: calling application.start()")
            await application.start()

            # Register bot instance with AlertService for proactive push alerts
            if alert_service is not None:
                logger.info("run_bot: registering bot with AlertService")
                alert_service.register_bot(application.bot)

            logger.info("run_bot: calling updater.start_polling()")
            await application.updater.start_polling(drop_pending_updates=True)
            logger.info("bot_polling_started")
            started = True
            break
        except Exception as exc:
            logger.warning(f"run_bot startup attempt {attempt}/{max_retries} failed: {exc}")
            with contextlib.suppress(Exception):
                await application.shutdown()
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 15))
                application = _build_app()

    if not started:
        logger.critical("run_bot failed to start after %d attempts", max_retries)
        return

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

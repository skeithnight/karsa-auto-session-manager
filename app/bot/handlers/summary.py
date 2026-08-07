"""Summary command handler — manual /summary and callback routing."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_redis,
    _is_authorized,
    _reply,
)

logger = logging.getLogger(__name__)


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual daily summary command — /summary."""
    logger.debug("summary_cmd: entering")
    if not _is_authorized(update):
        return

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        await _reply(update, "Database not available.", parse_mode="HTML")
        return

    from app.bot.daily_summary import DailySummaryService

    r = _get_redis(context)
    service = DailySummaryService(redis_client=r, db_engine=db_engine)

    try:
        message = await service.generate_summary()
        keyboard = [
            [
                InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_summary"),
                InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
            ],
        ]
        await _reply(update, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as exc:
        logger.error("summary_cmd_failed: {}", exc)
        await _reply(update, "Failed to generate summary.", parse_mode="HTML")

    logger.debug("summary_cmd: returning None")

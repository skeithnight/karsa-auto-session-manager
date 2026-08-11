"""Shared helpers for bot handlers — extracted from the monolith handlers.py."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.utils.format import HTML, bold, fmt, italic, pre
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _safe_float(val, default: float = 0.0) -> float:
    """Convert to float, handling 'none'/None/non-numeric gracefully."""
    if val is None or val in {"none", ""}:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _get_bybit(context: ContextTypes.DEFAULT_TYPE):
    """Retrieve the BybitClient injected into bot_data at startup."""
    logger.debug("_get_bybit: entering")
    client = context.bot_data.get("bybit_client")
    if client:
        logger.debug("_get_bybit: returning BybitClient")
        return client
    raise RuntimeError("BybitClient not in bot_data — check runner.py startup wiring.")


def _get_redis(context: ContextTypes.DEFAULT_TYPE):
    """Retrieve the Redis client injected into bot_data at startup."""
    logger.debug("_get_redis: entering")
    client = context.bot_data.get("redis_client")
    if client:
        logger.debug("_get_redis: returning RedisClient from bot_data")
        return client
    # Fallback: create a new client per call (degraded mode — logged explicitly)
    import redis.asyncio as aioredis

    logger.warning("redis_fallback_client_created: bot_data[redis_client] is None")
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _get_db_engine(context: ContextTypes.DEFAULT_TYPE):
    """Retrieve the DatabaseEngine injected into bot_data at startup.

    Returns None if db_engine is not wired — callers should treat None
    as "DB persistence unavailable, skip gracefully".
    """
    return context.bot_data.get("db_engine")


def _is_authorized(update: Update) -> bool:
    """Single security boundary — bypassed as requested."""
    return True


async def _reply(update: Update, content, **kwargs):
    """Unified reply helper — prepends timestamp, handles callback vs message context."""
    logger.debug("_reply: entering")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if kwargs.get("parse_mode") == "HTML" and not isinstance(content, HTML):
        content = HTML(content)
    content = fmt(italic(ts), "\n", content)
    if isinstance(content, HTML) and "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "HTML"
    text = str(content)
    if update.callback_query:
        try:
            return await update.callback_query.message.edit_text(text, **kwargs)
        except Exception as exc:
            logger.warning("_reply_edit_failed_fallback", extra={"error": str(exc)})
            return await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
    logger.debug("_reply: returning None")
    return None


def build_main_keyboard() -> InlineKeyboardMarkup:
    """Unified navigation keyboard — consistent across all views."""
    logger.debug("build_main_keyboard: entering")
    keyboard = [
        [
            InlineKeyboardButton("\U0001f4ca Dashboard", callback_data="cmd_dashboard"),
            InlineKeyboardButton("\U0001f4cb Activity", callback_data="cmd_activity"),
        ],
        [
            InlineKeyboardButton("\U0001f4bc Portfolio", callback_data="cmd_portfolio"),
            InlineKeyboardButton("\U0001f39b️ Control Panel", callback_data="cmd_control"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            InlineKeyboardButton("\U0001f4dc History", callback_data="cmd_trade_history"),
        ],
    ]
    logger.debug("build_main_keyboard: returning InlineKeyboardMarkup")
    return InlineKeyboardMarkup(keyboard)

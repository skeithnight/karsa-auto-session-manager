"""Backtest orchestration handler."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _is_authorized,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.telegram_helpers import send_or_edit_message

logger = logging.getLogger(__name__)


def _get_backtest_orchestrator(context: ContextTypes.DEFAULT_TYPE):
    """Retrieve or build a BacktestOrchestrator from bot_data deps."""
    orch = context.bot_data.get("backtest_orchestrator")
    if orch is not None:
        return orch
    # Lazy build from existing redis + db_engine
    redis = context.bot_data.get("redis_client")
    db_engine = context.bot_data.get("db_engine")
    if redis is None or db_engine is None:
        return None
    from app.backtest.orchestrator import BacktestOrchestrator

    orch = BacktestOrchestrator(redis, db_engine)
    context.bot_data["backtest_orchestrator"] = orch
    return orch


async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backtest command — shows recent jobs and bulk progress."""
    logger.debug("backtest_cmd: entering")
    if not _is_authorized(update):
        return

    orch = _get_backtest_orchestrator(context)
    if orch is None:
        await _reply(
            update,
            "⚠️ Backtest orchestrator unavailable (DB or Redis missing).",
            reply_markup=build_main_keyboard(),
        )
        return

    from app.backtest.formatter import format_backtest_list

    # Just list recent jobs (which may include individual jobs if any, and bulk stats if added)
    jobs = await orch.list_recent_jobs(limit=10)
    bulk_jobs = await orch.list_active_bulk_jobs()

    active_bulk = None
    for b in bulk_jobs:
        if b.get("status") in ("running", "completed"):
            active_bulk = b
            break

    text = format_backtest_list(jobs, active_bulk=active_bulk)

    keyboard = [
        [
            InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_backtest"),
        ],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            ),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
    ]
    await send_or_edit_message(
        update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

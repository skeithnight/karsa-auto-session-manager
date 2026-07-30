"""Trade history handler (paginated)."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _is_authorized,
    _reply,
)
from app.bot.utils.telegram_helpers import send_or_edit_message

logger = logging.getLogger(__name__)


async def _fetch_trade_history_page(
    page: int = 1, context: ContextTypes.DEFAULT_TYPE | None = None
):
    """Fetch a page of trades + summary stats. Returns (trades, total, wins, losses, net_pnl)."""
    logger.debug(f"_fetch_trade_history_page: entering page={page}")
    if context is None:
        logger.warning("trade_history_no_context")
        return [], 0, 0, 0, 0.0
    from app.core.trade_store import TradeStore

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        logger.warning("trade_history_no_db_engine")
        return [], 0, 0, 0, 0.0
    trade_store = TradeStore(db_engine)
    return await trade_store.get_history(page)


async def trade_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated trade history."""
    logger.debug("trade_history_cmd: entering")
    if not _is_authorized(update):
        return

    try:
        from app.bot.utils.formatters.trade_history_formatter import (
            TradeHistoryFormatter,
        )

        trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(
            1, context
        )
        text, keyboard = TradeHistoryFormatter.build_message(
            trades, 1, total, wins, losses, net_pnl
        )
        await send_or_edit_message(
            update, text, reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as exc:
        logger.error("trade_history_failed", extra={"error": str(exc)})
        await _reply(
            update,
            "❌ Trade history load failed.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\U0001f3e0 Back to Dashboard", callback_data="cmd_dashboard"
                        )
                    ]
                ]
            ),
        )
    logger.debug("trade_history_cmd: returning None")

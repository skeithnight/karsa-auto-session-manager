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
    page: int = 1,
    date_str: str | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
):
    """Fetch a page of trades + summary stats + recent dates. Returns (trades, total, wins, losses, net_pnl, recent_dates)."""
    logger.debug(f"_fetch_trade_history_page: entering page={page} date_str={date_str}")
    if context is None:
        logger.warning("trade_history_no_context")
        return [], 0, 0, 0, 0.0, []
    from app.core.trade_store import TradeStore

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        logger.warning("trade_history_no_db_engine")
        return [], 0, 0, 0, 0.0, []
    trade_store = TradeStore(db_engine)
    trades, total, wins, losses, net_pnl = await trade_store.get_history(page, per_page=10, date_str=date_str)
    recent_dates = await trade_store.get_recent_trade_dates(limit=7)
    return trades, total, wins, losses, net_pnl, recent_dates


async def trade_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str: str | None = None):
    """Show paginated trade history."""
    logger.debug("trade_history_cmd: entering")
    if not _is_authorized(update):
        return

    try:
        from app.bot.utils.formatters.trade_history_formatter import (
            TradeHistoryFormatter,
        )

        trades, total, wins, losses, net_pnl, recent_dates = await _fetch_trade_history_page(
            page=1, date_str=date_str, context=context
        )
        text, keyboard = TradeHistoryFormatter.build_message(
            trades, 1, total, wins, losses, net_pnl, selected_date=date_str, available_dates=recent_dates
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

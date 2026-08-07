"""Trade reconciliation handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def reconcile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger manual trade reconciliation and auto-repair."""
    query = update.callback_query
    if query:
        await query.answer()

    trade_reconciler = context.bot_data.get("trade_reconciler")
    if not trade_reconciler:
        msg = "⚠️ Reconciler not available."
        if query:
            await query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    msg = await (query.message if query else update.message).reply_text(
        "\U0001f504 Running manual reconciliation..."
    )

    try:
        # For manual reconciliation, extend lookback to 7 days (168 hours) to catch older discrepancies
        original_lookback = trade_reconciler.lookback_hours
        original_max_repairs = trade_reconciler.MAX_REPAIRS_PER_CYCLE
        original_max_pages = trade_reconciler.MAX_PAGES

        trade_reconciler.lookback_hours = 168
        trade_reconciler.MAX_REPAIRS_PER_CYCLE = 200
        trade_reconciler.MAX_PAGES = 10

        # Reset backfill flag to ensure we fetch historical missing trades if any
        trade_reconciler._backfill_done = False
        backfilled = await trade_reconciler.backfill_from_bybit()

        report = await trade_reconciler.reconcile()

        # Restore original limits
        trade_reconciler.lookback_hours = original_lookback
        trade_reconciler.MAX_REPAIRS_PER_CYCLE = original_max_repairs
        trade_reconciler.MAX_PAGES = original_max_pages

        lines = [
            "✅ <b>Reconciliation Complete</b>",
            "",
            f"Historical trades backfilled: {backfilled}",
            f"Fills checked (7d): {report.bybit_fills_checked}",
            f"Trades checked (7d): {report.local_trades_checked}",
            f"Discrepancies found: {len(report.discrepancies)}",
            f"Repairs made: {report.repairs_made}",
        ]

        if report.errors:
            lines.append("")
            lines.append("⚠️ <b>Errors:</b>")
            for err in report.errors:
                lines.append(f" - {err}")

        await msg.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Manual reconcile failed: {e}")
        await msg.edit_text(f"❌ Reconciliation failed: {e}")

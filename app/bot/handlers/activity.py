"""Activity feed handler."""

from __future__ import annotations

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_redis,
    _is_authorized,
    _reply,
)
from app.bot.utils.format import bold, fmt, italic

logger = logging.getLogger(__name__)


async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live activity feed — recent events from Redis or graceful stub."""
    logger.debug("activity_cmd: entering")
    if not _is_authorized(update):
        return

    r = _get_redis(context)

    # Session state
    is_active = False
    try:
        is_active = (await r.get("karsa:auto:state:active")) == "1"
    except Exception as exc:
        logger.warning("activity_session_state_failed", extra={"error": str(exc)})

    # Recent events from Redis sorted set (score = timestamp)
    events: list[dict] = []
    try:
        raw_events = await r.zrevrange("karsa:events:log", 0, 4)
        if raw_events:
            for e in raw_events:
                try:
                    events.append(
                        json.loads(e)
                        if isinstance(e, (str, bytes))
                        else {"msg": str(e)}
                    )
                except Exception:
                    events.append({"msg": str(e), "ts": "—"})
    except Exception as exc:
        logger.warning("activity_events_fetch_failed", extra={"error": str(exc)})

    lines = [
        bold("\U0001f4cb LIVE ACTIVITY FEED"),
    ]
    lines.append("━" * 32)
    session_status = "\U0001f7e2 ACTIVE" if is_active else "⚫ IDLE"
    lines.append(f"Session  {session_status}")
    lines.append("━" * 32)

    if events:
        lines.append(bold("\U0001f4cc Last Events"))
        for e in events:
            ts = e.get("ts", "—") if isinstance(e, dict) else "—"
            msg = e.get("msg", str(e)) if isinstance(e, dict) else str(e)
            lines.append(f"  • {ts}  {msg[:60]}")
    else:
        lines.append(bold("\U0001f4cc Last Events"))
        lines.append(italic("  • Live event stream not yet connected"))
        lines.append(italic("  • Trades visible in History screen"))
        lines.append(italic("  • Positions visible in Positions screen"))

    lines.append("━" * 32)
    lines.append(
        italic("⚠️ Full event log active once trade tables are wired")
    )

    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_activity")],
        [
            InlineKeyboardButton("\U0001f4bc Positions", callback_data="cmd_portfolio"),
            InlineKeyboardButton(
                "\U0001f4dc History", callback_data="cmd_trade_history"
            ),
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(
        update,
        fmt(*lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    logger.debug("activity_cmd: returning None")

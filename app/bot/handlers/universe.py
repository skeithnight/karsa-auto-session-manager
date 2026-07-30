"""Symbol universe handler."""

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
from app.bot.utils.format import bold, fmt, italic, pre
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def universe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading universe as a 5-per-row grid."""
    logger.debug("universe_cmd: entering")
    if not _is_authorized(update):
        return

    redis_client = _get_redis(context)
    universe = []
    source_msg = "Source: config (UniverseEngine not yet ported)"

    sector_msg = "Sector scoring: pending"
    try:
        raw_universe = await redis_client.get("system:universe:symbols")
        if raw_universe:
            universe_data = json.loads(raw_universe)
            universe = universe_data.get("symbols", [])
            scores = universe_data.get("scores", {})
            if universe:
                source_msg = "Source: dynamic (UniverseEngine active)"
            if scores:
                sector_msg = "Sector scoring: active (Dynamic)"
    except Exception as exc:
        logger.warning(
            f"universe_cmd: Failed to read dynamic universe from Redis: {exc}"
        )

    if not universe:
        universe = settings.symbols
        source_msg = "Source: config (Dynamic universe unavailable)"

    n = len(universe)

    grid_lines = []
    if scores:
        # Sort by score descending
        universe_sorted = sorted(
            universe, key=lambda x: float(scores.get(x, 0)), reverse=True
        )
        grid_lines.append(f"{'Symbol':<12} {'Score':<5} | {'Symbol':<12} {'Score':<5}")
        grid_lines.append("-" * 39)
        for i in range(0, n, 2):
            row_str = ""
            for j in range(2):
                if i + j < n:
                    sym = universe_sorted[i + j]
                    sc = f"{float(scores.get(sym, 0.0)):.1f}"
                    row_str += f"{sym:<12} {sc:<5}"
                    if j == 0 and i + j + 1 < n:
                        row_str += " | "
            grid_lines.append(row_str)
    else:
        # 3-per-row fits better on mobile without wrapping
        grid_lines.append(f"{'Symbol':<11} {'Symbol':<11} {'Symbol':<11}")
        grid_lines.append("-" * 35)
        for i in range(0, n, 3):
            row = universe[i : i + 3]
            row_str = "".join(f"{sym:<11} " for sym in row)
            grid_lines.append(row_str)

    grid_text = pre("\n".join(grid_lines))

    text = fmt(
        bold(f"\U0001f4e1 CRYPTO UNIVERSE  ·  {n} pairs"),
        "\n",
        "━" * 32,
        "\n\n",
        grid_text,
        "\n\n",
        italic(source_msg),
        "\n",
        italic(sector_msg),
    )

    keyboard = [
        [
            InlineKeyboardButton("\U0001f504 Refresh", callback_data="universe_detail"),
            InlineKeyboardButton("\U0001f4bc Positions", callback_data="cmd_portfolio"),
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("universe_cmd: returning None")


async def _show_universe_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
):
    """Universe detail — stub view showing configured symbols."""
    logger.debug(f"_show_universe_detail: entering page={page}")
    await universe_cmd(update, context)
    logger.debug("_show_universe_detail: returning None")

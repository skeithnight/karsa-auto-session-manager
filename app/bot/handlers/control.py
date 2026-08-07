"""Emergency control panel handler."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_bybit,
    _get_redis,
    _is_authorized,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt, italic, pre

logger = logging.getLogger(__name__)


async def control_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency control panel: halt, sell-all, resume; shows live risk gate state."""
    logger.debug("control_cmd: entering")
    if not _is_authorized(update):
        return

    halt_active = False
    cooldown = None
    alerts_on = True
    max_pos = 5
    regime_on = True

    try:
        r = _get_redis(context)
        halt_active = bool(await r.get("karsa:global_halt"))
        cooldown = await r.get("karsa:crypto_cooldown")
        alerts_raw = await r.get("karsa:alerts_enabled")
        alerts_on = alerts_raw in ("1", b"1") if alerts_raw is not None else True
        max_pos = int(await r.get("karsa:settings:max_positions") or 5)
        regime_raw = await r.get("karsa:settings:regime_filter")
        regime_on = regime_raw in ("1", b"1") if regime_raw is not None else True
    except Exception as exc:
        logger.error("control_cmd_redis_failed", extra={"error": str(exc)})

    _halt_str = "\U0001f6a8 ACTIVE" if halt_active else "\U0001f7e2 INACTIVE"
    _cool_str = "⏳ ACTIVE" if cooldown else "\U0001f7e2 INACTIVE"
    _alert_str = "\U0001f514 ON" if alerts_on else "\U0001f515 MUTED"
    state_block = (
        f"Global Halt   {_halt_str}\n"
        f"Cooldown      {_cool_str}\n"
        f"Trade Alerts  {_alert_str}"
    )

    _regime_str = "ON  ✅" if regime_on else "OFF ❌"
    gates_block = (
        f"Max Positions  {max_pos}\n"
        f"Regime Filter  {_regime_str}\n"
        f"AI Analyst     MANDATORY \U0001f512"
    )

    text = fmt(
        bold("\U0001f39b️ DESK CONTROL PANEL"),
        "\n",
        "━" * 32,
        "\n\n",
        bold("System State"),
        "\n",
        pre(state_block),
        "\n\n",
        bold("Risk Gates"),
        "\n",
        pre(gates_block),
        "\n\n",
        "━" * 32,
        "\n",
        italic("⚠️  Emergency actions below are IRREVERSIBLE"),
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "\U0001f515 Mute Alerts" if alerts_on else "\U0001f514 Unmute Alerts",
                callback_data="toggle_alerts",
            )
        ],
        [
            InlineKeyboardButton(
                "🚨 Close All Positions", callback_data="close_all_positions"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f6a8 EXECUTE KILL (Close All + Halt)", callback_data="crypto_kill"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f9f9 Sell All (15m break)", callback_data="crypto_sellall"
            )
        ],
        [
            InlineKeyboardButton(
                "▶️ Resume Operations", callback_data="crypto_resume"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f4c8 Performance", callback_data="cmd_performance"
            ),
            InlineKeyboardButton("\U0001f52c Backtest", callback_data="cmd_backtest"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("control_cmd: returning None")


async def _execute_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency kill — flatten all, set global halt."""
    logger.debug("_execute_kill: entering")
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        from app.execution.sor import SmartOrderRouter

        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        await sor.flatten_all_positions()
        r = _get_redis(context)
        await r.set("karsa:global_halt", "1")
        logger.critical("emergency_kill_executed", extra={"operator": operator})
        await _reply(
            update,
            "\U0001f6a8 EMERGENCY KILL EXECUTED. Global halt active.",
            reply_markup=build_main_keyboard(),
        )
    except Exception as exc:
        logger.error("execute_kill_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Kill failed.", reply_markup=build_main_keyboard())
    logger.debug("_execute_kill: returning None")


async def _execute_sellall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sell all positions, set 15-minute cooldown."""
    logger.debug("_execute_sellall: entering")
    try:
        from app.execution.sor import SmartOrderRouter

        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)
        await sor.flatten_all_positions()
        r = _get_redis(context)
        await r.set("karsa:crypto_cooldown", "1", ex=900)
        logger.warning("sell_all_executed")
        await _reply(
            update,
            "\U0001f9f9 SELL ALL EXECUTED. 15 minute cooldown active.",
            reply_markup=build_main_keyboard(),
        )
    except Exception as exc:
        logger.error("execute_sellall_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Sell all failed.", reply_markup=build_main_keyboard())
    logger.debug("_execute_sellall: returning None")


async def _execute_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear global halt and cooldown — resume trading."""
    logger.debug("_execute_resume: entering")
    operator = update.effective_user.username or str(update.effective_user.id)
    try:
        r = _get_redis(context)
        await r.delete("karsa:global_halt")
        await r.delete("karsa:crypto_cooldown")
        logger.warning("trading_resumed", extra={"operator": operator})
        await _reply(
            update,
            "▶️ TRADING RESUMED. Halts and cooldowns cleared.",
            reply_markup=build_main_keyboard(),
        )
    except Exception as exc:
        logger.error("execute_resume_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Resume failed.", reply_markup=build_main_keyboard())
    logger.debug("_execute_resume: returning None")

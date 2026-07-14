"""Karsa Auto Session Manager — Crypto Telegram Bot Handlers.

Adapted from karsa-claude-trading src/bot/crypto_handlers.py.

Key adaptations applied:
  - All imports remapped:  src.*  →  app.*
  - Secrets come from app.core.config.get_settings() (never hardcoded)
  - Redis accessed via context.bot_data["redis_client"] (RedisClient instance)
  - BybitClient accessed via context.bot_data["bybit_client"] (direct reference)
  - ASM / ProfileManager / UniverseEngine / Regime / Performance subsystems
    that are not yet ported are represented as graceful stubs that log a warning
    and return a user-facing "not yet available" message rather than crashing.

Silent "except: pass" blocks replaced with logger.warning() / logger.error()
per DEFINITION_OF_DONE.md §4 Anti-Pattern #3.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.core.config import get_settings
from app.bot.utils.format import HTML, bold, italic, code, pre, fmt, join
from app.bot.utils.telegram_helpers import send_or_edit_message, send_toast

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _is_authorized(update: Update) -> bool:
    """Single security boundary — rejects any chat not in TELEGRAM_CHAT_ID."""
    logger.debug("_is_authorized: entering")
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if not settings.telegram_chat_id:
        logger.debug("_is_authorized: returning False (no chat_id configured)")
        return False
    result = chat_id == str(settings.telegram_chat_id)
    logger.debug(f"_is_authorized: returning {result}")
    return result


async def _reply(update: Update, content, **kwargs):
    """Unified reply helper — prepends timestamp, handles callback vs message context."""
    logger.debug("_reply: entering")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
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
        [InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
         InlineKeyboardButton("📋 Activity", callback_data="cmd_activity")],
        [InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
         InlineKeyboardButton("🎛️ Control Panel", callback_data="cmd_control")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
         InlineKeyboardButton("📜 History", callback_data="cmd_trade_history")],
    ]
    logger.debug("build_main_keyboard: returning InlineKeyboardMarkup")
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# 1. Dashboard (Main Hub)
# ---------------------------------------------------------------------------

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified Dashboard — shows system health + wallet; adapts to ASM state."""
    logger.debug("dashboard_cmd: entering")
    if not _is_authorized(update):
        return

    import asyncio
    import time

    t0 = time.monotonic()
    r = _get_redis(context)

    # Guard against pre-startup access
    try:
        bybit = _get_bybit(context)
    except RuntimeError:
        logger.warning("dashboard_cmd_bybit_not_ready")
        await update.effective_message.reply_text(
            "⏳ System is starting up. Please try again in a few seconds."
        )
        return

    # ── Parallel data fetch ──────────────────────────────────────────────
    async def _fetch_redis():
        t = time.monotonic()
        try:
            redis_ok = await r.ping()
            halt_active = bool(await r.get("karsa:global_halt"))
            is_active = (await r.get("karsa:auto:state:active")) == "1"
            logger.info("fetch_redis_done ms=%d", int((time.monotonic() - t) * 1000))
            return {"redis_ok": redis_ok, "halt_active": halt_active, "is_active": is_active}
        except Exception as exc:
            logger.error("fetch_redis_failed", extra={"error": str(exc)})
            return {"redis_ok": False, "halt_active": False, "is_active": False}

    async def _fetch_db():
        t = time.monotonic()
        try:
            db_engine = context.bot_data.get("db_engine")
            if db_engine:
                ok = await db_engine.check()
            else:
                logger.warning("fetch_db: no db_engine in bot_data")
                ok = False
            logger.info("fetch_db_done ms=%d ok=%s", int((time.monotonic() - t) * 1000), ok)
            return ok
        except Exception as exc:
            logger.error("fetch_db_failed", extra={"error": str(exc)})
            return False

    async def _fetch_wallet():
        t = time.monotonic()
        try:
            wallet = await bybit.get_wallet_balance()
            logger.info("fetch_wallet_done ms=%d", int((time.monotonic() - t) * 1000))
            return {"wallet": wallet, "ok": not wallet.get("error")}
        except Exception as exc:
            logger.error("fetch_wallet_failed", extra={"error": str(exc)})
            return {"wallet": {}, "ok": False}

    async def _with_timeout(coro, timeout_sec):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("fetch_timeout_or_error", extra={"error": str(exc)})
            return None

    results = await asyncio.gather(
        _with_timeout(_fetch_redis(), 2),
        _with_timeout(_fetch_db(), 2),
        _with_timeout(_fetch_wallet(), 3),
    )

    logger.info("dashboard_parallel_fetch_total ms=%d", int((time.monotonic() - t0) * 1000))

    redis_data = results[0] if isinstance(results[0], dict) else {"redis_ok": False, "halt_active": False, "is_active": False}
    db_ok = results[1] if isinstance(results[1], bool) else False
    wallet_data = results[2] if isinstance(results[2], dict) else {"wallet": {}, "ok": False}

    redis_ok = redis_data.get("redis_ok", False)
    halt_active = redis_data.get("halt_active", False)
    is_active = redis_data.get("is_active", False)
    bybit_ok = wallet_data.get("ok", False)
    wallet = wallet_data.get("wallet", {})

    system_online = all([redis_ok, bybit_ok, db_ok])
    sys_icon = "🟢" if system_online else "🔴"
    asm_icon = "🟢" if is_active else "🔴"
    halt_line = f"\n🚨 HALT ACTIVE" if halt_active else ""

    balance = Decimal(str(wallet.get("balance", 0) or 0))
    available = Decimal(str(wallet.get("available", 0) or 0))

    text = fmt(
        bold("🤖 Karsa Auto Session Manager"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        f"{sys_icon} System: {'Healthy' if system_online else 'Degraded'}", halt_line, "\n",
        f"🤖 ASM: {asm_icon} {'ACTIVE' if is_active else 'IDLE'}\n",
        f"💰 Wallet Balance: ${balance:,.2f}\n",
        f"📂 Available: ${available:,.2f}\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        "Ready to deploy capital. Select an action below.",
    )

    if is_active:
        keyboard = [
            [InlineKeyboardButton("💼 Positions", callback_data="view_positions_detail"),
             InlineKeyboardButton("📜 Trade History", callback_data="cmd_trade_history")],
            [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="cmd_dashboard")],
            [InlineKeyboardButton("⏸ Pause Session", callback_data="asm_pause"),
             InlineKeyboardButton("🛑 Stop & Close All", callback_data="asm_stop")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🚀 LAUNCH NEW SESSION", callback_data="auto_launch")],
            [InlineKeyboardButton("📜 Trade History", callback_data="cmd_trade_history"),
             InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings")],
            [InlineKeyboardButton("🎛️ Control Panel", callback_data="cmd_control"),
             InlineKeyboardButton("💼 Positions", callback_data="view_positions_detail")],
        ]

    await send_or_edit_message(update, str(text), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("dashboard_cmd: returning None")


# ---------------------------------------------------------------------------
# 2. Activity Feed
# ---------------------------------------------------------------------------

async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live activity feed — recent signals and closed trades."""
    logger.debug("activity_cmd: entering")
    if not _is_authorized(update):
        return

    # Stub: ASM / signal tables not yet ported — show degraded state
    lines = [
        bold("📋 LIVE ACTIVITY FEED"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        "⚠️ Activity feed requires signal/trade tables (pending DATA_MODEL.md §7 sign-off).\n",
        italic("Once the trade tables are ported, this view will show recent signals and closed trades."),
    ]
    back_keyboard = [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]
    await _reply(update, fmt(*lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_keyboard))
    logger.debug("activity_cmd: returning None")


# ---------------------------------------------------------------------------
# 3. Portfolio (Open Positions)
# ---------------------------------------------------------------------------

async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open positions — fetches from Bybit, formats as pre-formatted table."""
    logger.debug("portfolio_cmd: entering")
    if not _is_authorized(update):
        return
    try:
        from app.bot.utils.telegram_helpers import format_pre_table
        bybit = _get_bybit(context)
        positions = await bybit.get_positions()

        if not positions:
            text = fmt(
                bold("💼 ACTIVE PORTFOLIO"),
                "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
                italic("📭 No active positions. Desk is in cash."),
            )
            await _reply(update, text, reply_markup=build_main_keyboard())
            return

        headers = ["Sym", "Side", "Size", "Mark", "uPnL"]
        rows = []
        total_pnl = Decimal("0")

        for p in positions:
            pnl = Decimal(str(p.get("unrealized_pnl", 0) or 0))
            total_pnl += pnl
            emoji = "🟢" if pnl >= 0 else "🔴"
            side = "L" if p.get("side") == "Buy" else "S"
            rows.append([
                p.get("symbol", "?"),
                side,
                str(Decimal(str(p.get("size", 0) or 0))[:8]),
                f"${Decimal(str(p.get('current_price', 0))):,.2f}",
                f"{emoji}${pnl:+,.1f}",
            ])

        table = format_pre_table(headers, rows, align_right=[2, 3, 4])
        t_emoji = "🟢" if total_pnl >= 0 else "🔴"

        text = fmt(
            bold("💼 ACTIVE PORTFOLIO"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
            pre(table), "\n",
            bold(f"Total Unrealized: {t_emoji} ${total_pnl:+,.2f}"),
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
    except Exception as exc:
        logger.error("portfolio_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Portfolio load failed.", reply_markup=build_main_keyboard())
    logger.debug("portfolio_cmd: returning None")


# ---------------------------------------------------------------------------
# 4. Performance
# ---------------------------------------------------------------------------

async def performance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Performance metrics — stub until PerformanceTracker is ported."""
    logger.debug("performance_cmd: entering")
    if not _is_authorized(update):
        return
    text = fmt(
        bold("📈 PERFORMANCE & AUDIT"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        "⚠️ PerformanceTracker and CryptoAuditMetrics not yet ported.\n",
        italic("Planned: 30-day PnL, win rate, profit factor, max drawdown, AI audit grade."),
    )
    await _reply(update, text, reply_markup=build_main_keyboard())
    logger.debug("performance_cmd: returning None")


# ---------------------------------------------------------------------------
# 4b. Settings
# ---------------------------------------------------------------------------

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot settings and preferences with inline toggles."""
    logger.debug("settings_cmd: entering")
    if not _is_authorized(update):
        return

    r = _get_redis(context)

    try:
        alerts_raw = await r.get("karsa:alerts_enabled")
        alerts_on = alerts_raw in ("1", b"1") if alerts_raw is not None else True
    except Exception as exc:
        logger.warning("settings_read_alerts_failed", extra={"error": str(exc)})
        alerts_on = True

    try:
        max_pos = int(await r.get("karsa:settings:max_positions") or 5)
    except Exception as exc:
        logger.warning("settings_read_max_pos_failed", extra={"error": str(exc)})
        max_pos = 5

    try:
        regime_raw = await r.get("karsa:settings:regime_filter")
        regime_on = regime_raw in ("1", b"1") if regime_raw is not None else True
    except Exception as exc:
        logger.warning("settings_read_regime_failed", extra={"error": str(exc)})
        regime_on = True

    text = fmt(
        bold("⚙️ Bot Settings & Preferences"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n\n",
        bold("Current Configuration:"), "\n\n",
        f"📂 Max Open Positions: {max_pos}\n",
        f"📊 Regime Filter: {'ENABLED' if regime_on else 'DISABLED'}\n",
        f"🔔 Trade Alerts: {'ENABLED' if alerts_on else 'MUTED'}\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        "Select a parameter below to modify it.",
    )

    keyboard = [
        [InlineKeyboardButton(f"📂 Max Pos: {max_pos}", callback_data="settings:max_positions"),
         InlineKeyboardButton(f"📊 Regime: {'ON' if regime_on else 'OFF'}", callback_data="settings:regime_filter")],
        [InlineKeyboardButton(f"🔔 Alerts: {'ON' if alerts_on else 'OFF'}", callback_data="settings:alerts")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="cmd_dashboard")],
    ]

    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("settings_cmd: returning None")


async def _toggle_max_pos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cycle max positions: 3 → 5 → 8 → 3."""
    logger.debug("_toggle_max_pos: entering")
    r = _get_redis(context)
    try:
        current = int(await r.get("karsa:settings:max_positions") or 5)
    except Exception as exc:
        logger.warning("toggle_max_pos_read_failed", extra={"error": str(exc)})
        current = 5

    cycle = {3: 5, 5: 8, 8: 3}
    new_val = cycle.get(current, 5)
    await r.set("karsa:settings:max_positions", str(new_val))
    await settings_cmd(update, context)
    logger.debug("_toggle_max_pos: returning None")


async def _toggle_regime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle regime filter on/off."""
    logger.debug("_toggle_regime: entering")
    r = _get_redis(context)
    try:
        current = await r.get("karsa:settings:regime_filter")
        is_on = current in ("1", b"1") if current is not None else True
    except Exception as exc:
        logger.warning("toggle_regime_read_failed", extra={"error": str(exc)})
        is_on = True

    await r.set("karsa:settings:regime_filter", "0" if is_on else "1")
    await settings_cmd(update, context)
    logger.debug("_toggle_regime: returning None")


async def _toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle trade alert notifications on/off."""
    logger.debug("_toggle_alerts: entering")
    r = _get_redis(context)
    try:
        current = await r.get("karsa:alerts_enabled")
        is_on = current in ("1", b"1") if current is not None else True
        await r.set("karsa:alerts_enabled", "0" if is_on else "1")
        status = "🔕 Alerts Muted" if is_on else "🔔 Alerts Enabled"
        await update.callback_query.edit_message_text(
            f"<b>{status}</b>",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
    except Exception as exc:
        logger.error("toggle_alerts_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Failed to toggle alerts.", reply_markup=build_main_keyboard())
    logger.debug("_toggle_alerts: returning None")


# ---------------------------------------------------------------------------
# 5. Control Panel (Emergency & Overrides)
# ---------------------------------------------------------------------------

async def control_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency control panel: halt, sell-all, resume, walkforward."""
    logger.debug("control_cmd: entering")
    if not _is_authorized(update):
        return

    try:
        r = _get_redis(context)
        halt_active = bool(await r.get("karsa:global_halt"))
        cooldown = await r.get("karsa:crypto_cooldown")
        alerts_raw = await r.get("karsa:alerts_enabled")
        alerts_on = alerts_raw in ("1", b"1") if alerts_raw is not None else True
    except Exception as exc:
        logger.error("control_cmd_redis_failed", extra={"error": str(exc)})
        halt_active, cooldown, alerts_on = False, None, True

    state_block = (
        f"Global Halt: {'🚨 ACTIVE' if halt_active else '🟢 INACTIVE'}\n"
        f"Cooldown: {'⏳ ACTIVE' if cooldown else '🟢 INACTIVE'}\n"
        f"Trade Alerts: {'🔔 ON' if alerts_on else '🔕 MUTED'}"
    )

    text = fmt(
        bold("🎛️ DESK CONTROL PANEL"), "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        bold("System State:"), "\n", pre(state_block), "\n\n",
        italic("Select an operation below."),
    )

    keyboard = [
        [InlineKeyboardButton(
            "🔕 Mute Alerts" if alerts_on else "🔔 Unmute Alerts",
            callback_data="toggle_alerts",
        )],
        [InlineKeyboardButton("🚨 EXECUTE KILL (Close All)", callback_data="crypto_kill")],
        [InlineKeyboardButton("🧹 Sell All (15m break)", callback_data="crypto_sellall")],
        [InlineKeyboardButton("▶️ Resume Operations", callback_data="crypto_resume")],
        [InlineKeyboardButton("🏠 Dashboard", callback_data="cmd_dashboard")],
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
        await sor.cancel_all_positions()
        r = _get_redis(context)
        await r.set("karsa:global_halt", "1")
        logger.critical("emergency_kill_executed", extra={"operator": operator})
        await _reply(update, "🚨 EMERGENCY KILL EXECUTED. Global halt active.", reply_markup=build_main_keyboard())
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
        await sor.cancel_all_positions()
        r = _get_redis(context)
        await r.set("karsa:crypto_cooldown", "1", ex=900)
        logger.warning("sell_all_executed")
        await _reply(update, "🧹 SELL ALL EXECUTED. 15 minute cooldown active.", reply_markup=build_main_keyboard())
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
        await _reply(update, "▶️ TRADING RESUMED. Halts and cooldowns cleared.", reply_markup=build_main_keyboard())
    except Exception as exc:
        logger.error("execute_resume_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Resume failed.", reply_markup=build_main_keyboard())
    logger.debug("_execute_resume: returning None")


# ---------------------------------------------------------------------------
# 6. Open Positions (Detail View with SL→BE)
# ---------------------------------------------------------------------------

async def view_positions_detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed position view with Move SL to BE buttons and allocation percentages."""
    logger.debug("view_positions_detail_cmd: entering")
    if not _is_authorized(update):
        return

    bybit = _get_bybit(context)
    positions = []
    try:
        raw = await bybit.get_positions()
        positions = [p for p in raw if float(p.get("size", 0)) > 0]
    except Exception as exc:
        logger.error("view_positions_detail_fetch_failed", extra={"error": str(exc)})

    total_equity = Decimal("0")
    try:
        wallet = await bybit.get_wallet_balance()
        total_equity = Decimal(str(wallet.get("balance", 0) or 0))
    except Exception as exc:
        logger.warning("view_positions_detail_wallet_failed", extra={"error": str(exc)})

    from app.bot.utils.formatters import format_position_card

    lines = [bold("📊 OPEN POSITIONS DETAIL"), "\n", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n"]

    total_position_value = Decimal("0")
    position_values = []
    for p in positions:
        entry = Decimal(str(p.get("entry_price", 0) or 0))
        size = Decimal(str(p.get("size", 0) or 0))
        pos_value = entry * size
        total_position_value += pos_value
        position_values.append(pos_value)

    cash = total_equity - total_position_value if total_equity > 0 else Decimal("0")
    cash_pct = float(cash / total_equity * 100) if total_equity > 0 else 0

    if total_equity > 0 and positions:
        lines.append(bold("💰 ALLOCATION"))
        lines.append(f"Equity: ${total_equity:,.2f} | Cash: ${cash:,.2f} ({cash_pct:.1f}%)")
        lines.append(f"Positions: {len(positions)} | Deployed: ${total_position_value:,.2f} ({100-cash_pct:.1f}%)")
        lines.append("")

    keyboard = []

    if not positions:
        lines.append("No open positions.")
    else:
        for i, (p, pos_val) in enumerate(zip(positions, position_values), 1):
            pos_pct = float(pos_val / total_equity * 100) if total_equity > 0 else 0
            card = format_position_card(p, index=i, pos_pct=pos_pct)
            lines.append(card)
            lines.append("")
            symbol = p.get("symbol", "?")
            keyboard.append([
                InlineKeyboardButton(f"🏃 Close {symbol}", callback_data=f"close_pos_{symbol}"),
                InlineKeyboardButton(f"🛡 SL→BE {symbol}", callback_data=f"move_sl_be_{symbol}"),
            ])

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(italic("💡 Move SL to BE shifts Stop Loss to Entry Price — risk-free trade."))
    keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="cmd_dashboard")])

    await send_or_edit_message(update, str(fmt(*lines, sep="\n")), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("view_positions_detail_cmd: returning None")


async def _move_sl_to_be(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """Move stop loss to breakeven (entry price) for a specific position."""
    logger.debug(f"_move_sl_to_be: entering symbol={symbol}")
    bybit = _get_bybit(context)

    try:
        positions = await bybit.get_positions()
        pos = None
        for p in (positions or []):
            if p.get("symbol") == symbol and float(p.get("size", 0)) > 0:
                pos = p
                break

        if not pos:
            await _reply(update, f"❌ No open position found for {symbol}")
            return

        entry_price = Decimal(str(pos.get("entry_price", 0) or 0))
        if entry_price <= 0:
            await _reply(update, f"❌ Cannot determine entry price for {symbol}")
            return

        side = pos.get("side", "Buy")
        new_sl = entry_price

        try:
            orders = await bybit.get_open_orders(symbol)
            sl_order = None
            for o in (orders or []):
                if o.get("stopLoss") or o.get("order_type") == "Stop":
                    sl_order = o
                    break

            if sl_order:
                order_id = sl_order.get("order_id", "")
                await bybit.amend_order(symbol=symbol, order_id=order_id, stop_loss=str(new_sl))
            else:
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
        except Exception as amend_err:
            logger.warning("move_sl_be_amend_failed", extra={"symbol": symbol, "error": str(amend_err)})
            try:
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
            except Exception as fallback_err:
                logger.error("move_sl_be_fallback_failed", extra={"symbol": symbol, "error": str(fallback_err)})
                await _reply(update, f"❌ Failed to amend SL for {symbol}: {amend_err}")
                return

        await view_positions_detail_cmd(update, context)

        chat_id = update.effective_chat.id
        toast_text = fmt(
            bold("✅ SL Moved to Breakeven"), "\n",
            f"Symbol: {symbol}", "\n",
            f"New SL: ${new_sl:,.2f}",
        )
        toast_msg = await send_toast(context.bot, chat_id, str(toast_text))
        if toast_msg:
            dismiss_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Dismiss", callback_data=f"dismiss_toast_{toast_msg.message_id}")]
            ])
            try:
                await toast_msg.edit_reply_markup(reply_markup=dismiss_kb)
            except Exception as exc:
                logger.warning("toast_dismiss_button_failed", extra={"error": str(exc)})

    except Exception as exc:
        logger.error("move_sl_be_failed", extra={"symbol": symbol, "error": str(exc)})
        await _reply(update, f"❌ Move SL to BE failed: {exc}")
    logger.debug(f"_move_sl_to_be: returning None")


async def _close_position(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """Market close a specific position."""
    logger.debug(f"_close_position: entering symbol={symbol}")
    try:
        from app.execution.sor import SmartOrderRouter
        bybit = _get_bybit(context)
        sor = SmartOrderRouter(bybit)

        positions = await bybit.get_positions()
        pos = None
        for p in (positions or []):
            if p.get("symbol") == symbol and float(p.get("size", 0)) > 0:
                pos = p
                break

        if not pos:
            await _reply(update, f"❌ No open position for {symbol}")
            return

        side = pos.get("side", "Buy")
        size = Decimal(str(pos.get("size", 0)))
        close_side = "Sell" if side == "Buy" else "Buy"

        await bybit.place_order(symbol=symbol, side=close_side, order_type="Market", qty=str(size))
        await _reply(update, f"🏃 <b>{symbol}</b> position closed (Market {close_side})", reply_markup=build_main_keyboard())

    except Exception as exc:
        logger.error("close_position_failed", extra={"symbol": symbol, "error": str(exc)})
        await _reply(update, f"❌ Failed to close {symbol}: {exc}")
    logger.debug(f"_close_position: returning None")


# ---------------------------------------------------------------------------
# 7. Universe (Stub — pending UniverseEngine port)
# ---------------------------------------------------------------------------

async def universe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading universe — stub until UniverseEngine is ported."""
    logger.debug("universe_cmd: entering")
    if not _is_authorized(update):
        return

    # Use the configured symbols as fallback until UniverseEngine is available
    universe = settings.symbols
    lines = [
        bold("📡 Crypto Universe"),
        f"Scanning {len(universe)} coins (from config):",
        "",
    ]
    for i, sym in enumerate(universe, 1):
        lines.append(f"  {i}. {sym}")
    lines.append("")
    keyboard = [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]
    await _reply(update, fmt(*lines, sep="\n"), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("universe_cmd: returning None")


async def _show_universe_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Universe detail — stub view showing configured symbols."""
    logger.debug(f"_show_universe_detail: entering page={page}")
    await universe_cmd(update, context)
    logger.debug("_show_universe_detail: returning None")


# ---------------------------------------------------------------------------
# 8. Trade History (paginated)
# ---------------------------------------------------------------------------

async def _fetch_trade_history_page(page: int = 1):
    """Fetch a page of trades + summary stats. Returns (trades, total, wins, losses, net_pnl)."""
    logger.debug(f"_fetch_trade_history_page: entering page={page}")
    # Stub — returns empty until ClosedPaperTrade table is confirmed in DATA_MODEL.md
    logger.warning("trade_history_db_not_yet_wired")
    logger.debug("_fetch_trade_history_page: returning empty stub")
    return [], 0, 0, 0, 0.0


async def trade_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated trade history."""
    logger.debug("trade_history_cmd: entering")
    if not _is_authorized(update):
        return

    try:
        from app.bot.utils.formatters.trade_history_formatter import TradeHistoryFormatter
        trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(1)
        text, keyboard = TradeHistoryFormatter.build_message(trades, 1, total, wins, losses, net_pnl)
        await send_or_edit_message(update, text, reply_markup=keyboard, parse_mode=None)
    except Exception as exc:
        logger.error("trade_history_failed", extra={"error": str(exc)})
        await _reply(update, "❌ Trade history load failed.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Back to Dashboard", callback_data="cmd_dashboard")]]))
    logger.debug("trade_history_cmd: returning None")


# ---------------------------------------------------------------------------
# 9. Clear Halt
# ---------------------------------------------------------------------------

async def clear_halt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear emergency halt state. Admin-only."""
    logger.debug("clear_halt_cmd: entering")
    if not _is_authorized(update):
        return
    try:
        r = _get_redis(context)
        await r.delete("karsa:global_halt")
        await r.delete("karsa:crypto_cooldown")
        logger.warning("halt_cleared", extra={"operator": f"tg_{update.effective_user.id}"})
        await _reply(update, "✅ <b>Halt cleared.</b> Trading can resume.", reply_markup=build_main_keyboard())
    except Exception as exc:
        logger.error("clear_halt_failed", extra={"error": str(exc)})
        await _reply(update, f"❌ Failed to clear halt: {exc}")
    logger.debug("clear_halt_cmd: returning None")


# ---------------------------------------------------------------------------
# 10. /start — entry point
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — shows the main dashboard."""
    logger.debug("start_cmd: entering")
    try:
        await dashboard_cmd(update, context)
    except Exception as exc:
        logger.error("start_cmd_failed", extra={"error": str(exc)[:200]})
        try:
            await update.effective_message.reply_text(
                "⚠️ Failed to load dashboard. Please try again."
            )
        except Exception as inner_exc:
            logger.error("start_cmd_fallback_also_failed", extra={"error": str(inner_exc)})
    logger.debug("start_cmd: returning None")


# ---------------------------------------------------------------------------
# Global Callback Router
# ---------------------------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central dispatcher for all InlineKeyboard callbacks."""
    logger.debug("button_callback: entering")
    query = update.callback_query
    await query.answer()
    data = query.data
    r = _get_redis(context)

    # 5 Core Views
    if data == "cmd_dashboard":
        await dashboard_cmd(update, context)
    elif data == "cmd_activity":
        await activity_cmd(update, context)
    elif data == "cmd_portfolio":
        await portfolio_cmd(update, context)
    elif data == "cmd_performance":
        await performance_cmd(update, context)
    elif data == "cmd_control":
        await control_cmd(update, context)
    elif data == "cmd_settings":
        await settings_cmd(update, context)

    # Settings toggles
    elif data == "settings:max_positions":
        await _toggle_max_pos(update, context)
    elif data == "settings:regime_filter":
        await _toggle_regime(update, context)
    elif data == "settings:alerts":
        await _toggle_alerts(update, context)

    # Positions
    elif data == "view_positions_detail":
        await view_positions_detail_cmd(update, context)
    elif data == "cmd_positions":
        await view_positions_detail_cmd(update, context)

    # Move SL to BE
    elif data.startswith("move_sl_be_"):
        symbol = data.replace("move_sl_be_", "")
        await _move_sl_to_be(update, context, symbol)

    # Close position
    elif data.startswith("close_pos_"):
        symbol = data.replace("close_pos_", "")
        await _close_position(update, context, symbol)

    # Trade History
    elif data == "cmd_trade_history":
        await trade_history_cmd(update, context)
    elif data.startswith("karsa:history:page:"):
        try:
            page = int(data.split(":")[-1])
            from app.bot.utils.formatters.trade_history_formatter import TradeHistoryFormatter
            trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(page)
            text, keyboard = TradeHistoryFormatter.build_message(trades, page, total, wins, losses, net_pnl)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=None)
        except Exception as exc:
            logger.error("history_pagination_failed", extra={"error": str(exc)})
            await query.answer("Failed to load page", show_alert=True)

    # Universe
    elif data == "universe_detail":
        await _show_universe_detail(update, context, page=0)
    elif data.startswith("univ_page_"):
        try:
            page_num = int(data.replace("univ_page_", ""))
            await _show_universe_detail(update, context, page=page_num)
        except (ValueError, IndexError) as exc:
            logger.warning("universe_pagination_invalid", extra={"data": data, "error": str(exc)})

    # Emergency operations
    elif data == "crypto_kill":
        await _execute_kill(update, context)
    elif data == "crypto_sellall":
        await _execute_sellall(update, context)
    elif data == "crypto_resume":
        await _execute_resume(update, context)

    # ASM launch — show risk level selection
    elif data == "auto_launch":
        logger.debug("auto_launch: showing risk selection")
        keyboard = [
            [InlineKeyboardButton("10%", callback_data="asm_risk_10"),
             InlineKeyboardButton("30%", callback_data="asm_risk_30")],
            [InlineKeyboardButton("50%", callback_data="asm_risk_50"),
             InlineKeyboardButton("70%", callback_data="asm_risk_70")],
            [InlineKeyboardButton("100%", callback_data="asm_risk_100")],
            [InlineKeyboardButton("← Back", callback_data="main_menu")]
        ]
        await _reply(update, "📊 Select risk %:",
                     reply_markup=InlineKeyboardMarkup(keyboard))

    # ASM risk selected — start session
    elif data.startswith("asm_risk_"):
        risk_pct = int(data.split("_")[2])
        logger.debug(f"asm_risk: selected risk={risk_pct}%")
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr and r:
            try:
                await session_mgr.start_session(
                    duration_min=60,
                    risk_pct=risk_pct,
                    max_pos=3,
                )
                await _reply(update, f"🚀 Session launched at {risk_pct}% risk.",
                             reply_markup=InlineKeyboardMarkup(
                                 [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                             ))
            except Exception as exc:
                logger.error("asm_launch_failed", extra={"risk_pct": risk_pct, "error": str(exc)})
                await _reply(update, "❌ Launch failed.", reply_markup=build_main_keyboard())
        else:
            logger.warning(f"asm_risk: unavailable — session_mgr={session_mgr is not None} redis={r is not None}")
            await _reply(update, "⚠️ Session manager unavailable.",
                         reply_markup=build_main_keyboard())

    # Main menu — return to dashboard
    elif data == "main_menu":
        await dashboard_cmd(update, context)

    # ASM pause — stop session
    elif data == "asm_pause":
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr:
            await session_mgr.stop_session()
            await _reply(update, "⏸ Session paused.",
                         reply_markup=InlineKeyboardMarkup(
                             [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                         ))
        else:
            await _reply(update, "⚠️ Session manager unavailable.",
                         reply_markup=build_main_keyboard())

    # ASM stop — stop session + return to dashboard
    elif data == "asm_stop":
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr:
            await session_mgr.stop_session()
            await _reply(update, "🛑 Session stopped. All positions remain open.",
                         reply_markup=InlineKeyboardMarkup(
                             [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                         ))
        else:
            await _reply(update, "⚠️ Session manager unavailable.",
                         reply_markup=build_main_keyboard())

    # Toast dismiss
    elif data.startswith("dismiss_toast_"):
        try:
            msg_id = int(data.replace("dismiss_toast_", ""))
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception as exc:
            logger.warning("dismiss_toast_failed", extra={"data": data, "error": str(exc)})

    # Noop (page indicator buttons)
    elif data == "noop":
        pass

    else:
        logger.warning("unhandled_callback_data", extra={"data": data})
    logger.debug("button_callback: returning None")

"""Central callback dispatcher for all InlineKeyboard callbacks."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_redis,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt

logger = logging.getLogger(__name__)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central dispatcher for all InlineKeyboard callbacks."""
    # Lazy imports to avoid circular dependencies
    from app.bot.handlers.dashboard import dashboard_cmd
    from app.bot.handlers.activity import activity_cmd
    from app.bot.handlers.positions import (
        portfolio_cmd,
        repair_positions_cmd,
        view_positions_detail_cmd,
        _move_sl_to_be,
        _show_close_confirmation,
        _execute_close_position,
        _show_close_all_confirmation,
        _execute_close_all_positions,
    )
    from app.bot.handlers.reports import (
        performance_cmd,
        report_shadow_cmd,
        report_live_cmd,
        report_menu_cmd,
        ai_accuracy_cmd,
        feature_correlation_cmd,
        backtest_hybrid_report_cmd,
    )
    from app.bot.handlers.settings import (
        settings_cmd,
        _toggle_max_pos,
        _toggle_risk,
        _toggle_regime,
        _toggle_alerts,
        _set_risk_pct,
        _set_max_pos,
        _toggle_trade_alerts,
        _toggle_daily_summary,
        _toggle_guardrail_alerts,
        _set_mute_duration,
    )
    from app.bot.handlers.control import (
        control_cmd,
        _execute_kill,
        _execute_sellall,
        _execute_resume,
    )
    from app.bot.handlers.universe import _show_universe_detail
    from app.bot.handlers.history import (
        trade_history_cmd,
        _fetch_trade_history_page,
    )
    from app.bot.handlers.backtest import backtest_cmd, _get_backtest_orchestrator
    from app.bot.handlers.reconciliation import reconcile_cmd

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
    elif data == "cmd_report_menu":
        await report_menu_cmd(update, context)
    elif data == "cmd_report_shadow":
        await report_shadow_cmd(update, context)
    elif data == "cmd_report_live":
        await report_live_cmd(update, context)

    # AI Accuracy & Feature Correlation
    elif data == "cmd_ai_accuracy":
        await ai_accuracy_cmd(update, context)
    elif data == "cmd_feature_correlation":
        await feature_correlation_cmd(update, context)

    # Daily Summary
    elif data == "cmd_summary":
        from app.bot.handlers.summary import summary_cmd
        await summary_cmd(update, context)

    # Settings toggles (legacy)
    elif data == "settings:max_positions":
        await _toggle_max_pos(update, context)
    elif data == "settings:risk_pct":
        await _toggle_risk(update, context)
    elif data == "settings:regime_filter":
        await _toggle_regime(update, context)
    elif data == "settings:alerts":
        await _toggle_alerts(update, context)

    # Settings: direct risk % buttons
    elif data.startswith("settings:risk:"):
        try:
            value = int(data.split(":")[-1])
            await _set_risk_pct(update, context, value)
        except (ValueError, IndexError):
            await query.answer("Invalid risk value", show_alert=True)

    # Settings: direct max positions buttons
    elif data.startswith("settings:max_pos:"):
        try:
            value = int(data.split(":")[-1])
            await _set_max_pos(update, context, value)
        except (ValueError, IndexError):
            await query.answer("Invalid position value", show_alert=True)

    # Settings: individual alert toggles
    elif data == "settings:alert:trade":
        await _toggle_trade_alerts(update, context)
    elif data == "settings:alert:daily_summary":
        await _toggle_daily_summary(update, context)
    elif data == "settings:alert:guardrail":
        await _toggle_guardrail_alerts(update, context)

    # Settings: mute duration
    elif data.startswith("settings:mute:"):
        try:
            hours = int(data.split(":")[-1])
            await _set_mute_duration(update, context, hours)
        except (ValueError, IndexError):
            await query.answer("Invalid mute value", show_alert=True)

    # AI Status command
    elif data == "cmd_ai_status":
        from app.bot.handlers.dashboard import ai_status_cmd

        await ai_status_cmd(update, context)

    # Positions
    elif data in {"view_positions_detail", "cmd_positions"}:
        await view_positions_detail_cmd(update, context)
    elif data == "cmd_repair_positions":
        await repair_positions_cmd(update, context)

    # Move SL to BE
    elif data.startswith("move_sl_be_"):
        symbol = data.replace("move_sl_be_", "")
        await _move_sl_to_be(update, context, symbol)

    # Close position — confirmation flow
    elif data.startswith("close_position_"):
        parts = data.split("_")
        # close_position_{symbol}_{side} — symbol may contain "/" so split on last 2 parts
        side = parts[-1]
        symbol = "_".join(parts[1:-1])
        await _show_close_confirmation(update, context, symbol, side)

    # Confirm close position — execute
    elif data.startswith("confirm_close_position_"):
        parts = data.split("_")
        # confirm_close_position_{symbol}_{side}
        side = parts[-1]
        symbol = "_".join(parts[2:-1])
        await _execute_close_position(update, context, symbol, side)

    # Close all positions — confirmation
    elif data == "close_all_positions":
        await _show_close_all_confirmation(update, context)

    # Confirm close all — execute
    elif data == "confirm_close_all_positions":
        await _execute_close_all_positions(update, context)

    # Cancel close — back to positions
    elif data == "cancel_close":
        await view_positions_detail_cmd(update, context)

    # Backtest
    elif data == "cmd_backtest":
        await backtest_cmd(update, context)

    # Hybrid Backtest
    elif data == "cmd_backtest_hybrid":
        await backtest_hybrid_report_cmd(update, context)

    # Trade History
    elif data == "cmd_trade_history":
        await trade_history_cmd(update, context)

    # Cooldown Reset
    elif data == "cmd_reset_cooldowns":
        await _clear_all_cooldowns(update, context)

    elif data.startswith("karsa:history:page:"):
        try:
            page = int(data.split(":")[-1])
            from app.bot.utils.formatters.trade_history_formatter import (
                TradeHistoryFormatter,
            )

            trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(page, context)
            text, keyboard = TradeHistoryFormatter.build_message(trades, page, total, wins, losses, net_pnl)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as exc:
            logger.error("history_pagination_failed", extra={"error": str(exc)})
            await query.answer("Failed to load page", show_alert=True)
    elif data == "cmd_reconcile":
        await reconcile_cmd(update, context)

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

    # ASM launch — start session dynamically
    elif data == "auto_launch":
        logger.debug("auto_launch: starting session directly")
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr and r:
            try:
                max_pos = int(await r.get("karsa:settings:max_positions") or 5)
            except Exception as exc:
                logger.warning("auto_launch_read_max_pos_failed", extra={"error": str(exc)})
                max_pos = 5

            try:
                risk_pct = int(await r.get("karsa:settings:risk_pct") or 10)
            except Exception as exc:
                logger.warning("auto_launch_read_risk_pct_failed", extra={"error": str(exc)})
                risk_pct = 10

            try:
                await session_mgr.start_session(
                    duration_min=0,
                    risk_pct=risk_pct,
                    max_pos=max_pos,
                )
                await _reply(
                    update,
                    f"\U0001f680 Session launched indefinitely!\n\nRisk: {risk_pct}%\nMax Pos: {max_pos}",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                    ),
                )
            except Exception as exc:
                logger.error("asm_launch_failed", extra={"risk_pct": risk_pct, "error": str(exc)})
                await _reply(update, "❌ Launch failed.", reply_markup=build_main_keyboard())
        else:
            logger.warning(f"auto_launch: unavailable — session_mgr={session_mgr is not None} redis={r is not None}")
            await _reply(
                update,
                "⚠️ Session manager unavailable.",
                reply_markup=build_main_keyboard(),
            )

    # Main menu — return to dashboard
    elif data == "main_menu":
        await dashboard_cmd(update, context)

    # ASM pause — stop session
    elif data == "asm_pause":
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr:
            await session_mgr.stop_session()
            await _reply(
                update,
                "⏸ Session paused.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]),
            )
        else:
            await _reply(
                update,
                "⚠️ Session manager unavailable.",
                reply_markup=build_main_keyboard(),
            )

    # ASM stop — stop session + return to dashboard
    elif data == "asm_stop":
        session_mgr = context.bot_data.get("session_manager")
        if session_mgr:
            await session_mgr.stop_session()
            await _reply(
                update,
                "\U0001f6d1 Session stopped. All positions remain open.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]),
            )
        else:
            await _reply(
                update,
                "⚠️ Session manager unavailable.",
                reply_markup=build_main_keyboard(),
            )

    # Toast dismiss
    elif data.startswith("dismiss_toast_"):
        try:
            msg_id = int(data.replace("dismiss_toast_", ""))
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except Exception as exc:
            logger.warning("dismiss_toast_failed", extra={"data": data, "error": str(exc)})

    # Toggle alerts (from control panel)
    elif data == "toggle_alerts":
        await _toggle_alerts(update, context)

    # Re-run backtest (from results view)
    elif data.startswith("bt_rerun_"):
        from app.backtest.orchestrator import BacktestJobSpec

        job_id_short = data.replace("bt_rerun_", "")
        orch = _get_backtest_orchestrator(context)
        if orch is not None:
            # Resubmit with same params by scanning recent jobs
            jobs = await orch.list_recent_jobs(limit=5)
            for j in jobs:
                if j.job_id.startswith(job_id_short):
                    spec = BacktestJobSpec(symbol=j.symbol)
                    new_id = await orch.submit_job(spec)
                    await _reply(update, f"✅ Re-submitted backtest `{new_id[:8]}`")
                    return
        await _reply(
            update,
            "⚠️ Could not re-run — job not found.",
            reply_markup=build_main_keyboard(),
        )

    # Noop (page indicator buttons)
    elif data == "noop":
        pass

    else:
        logger.warning("unhandled_callback_data", extra={"data": data})
    logger.debug("button_callback: returning None")


async def _clear_all_cooldowns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all active symbol cooldowns in TradeMemory."""
    r = _get_redis(context)
    from app.alpha.trade_memory import TradeMemory

    trade_memory = TradeMemory(r)

    query = update.callback_query
    active_cooldowns = await trade_memory.get_active_cooldowns()

    if not active_cooldowns:
        await query.answer("No active cooldowns to reset.", show_alert=True)
        return

    for symbol in active_cooldowns:
        await trade_memory.clear_cooldown(symbol)

    await query.answer(f"Reset cooldowns for {len(active_cooldowns)} symbols.", show_alert=True)
    # Refresh the portfolio view to hide the button
    from app.bot.handlers.positions import portfolio_cmd

    await portfolio_cmd(update, context)

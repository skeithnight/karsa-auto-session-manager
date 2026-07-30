"""Bot handlers package — re-exports for backward compatibility with runner.py."""

from app.bot.handlers.dashboard import (
    ai_status_cmd,
    analytics_cmd,
    dashboard_cmd,
    health_cmd,
    start_cmd,
)
from app.bot.handlers.activity import activity_cmd
from app.bot.handlers.positions import (
    portfolio_cmd,
    repair_positions_cmd,
    view_positions_detail_cmd,
)
from app.bot.handlers.reports import (
    performance_cmd,
    report_live_cmd,
    report_menu_cmd,
    report_shadow_cmd,
)
from app.bot.handlers.settings import settings_cmd
from app.bot.handlers.control import control_cmd
from app.bot.handlers.universe import universe_cmd
from app.bot.handlers.history import trade_history_cmd
from app.bot.handlers.backtest import backtest_cmd
from app.bot.handlers.reconciliation import reconcile_cmd
from app.bot.handlers.summary import summary_cmd
from app.bot.handlers.callback_router import button_callback
from app.bot.handlers._helpers import _is_authorized, build_main_keyboard

__all__ = [
    "start_cmd",
    "dashboard_cmd",
    "ai_status_cmd",
    "activity_cmd",
    "portfolio_cmd",
    "performance_cmd",
    "report_menu_cmd",
    "report_shadow_cmd",
    "report_live_cmd",
    "control_cmd",
    "settings_cmd",
    "summary_cmd",
    "view_positions_detail_cmd",
    "trade_history_cmd",
    "backtest_cmd",
    "health_cmd",
    "analytics_cmd",
    "button_callback",
    "build_main_keyboard",
    "repair_positions_cmd",
    "universe_cmd",
    "reconcile_cmd",
    "_is_authorized",
]

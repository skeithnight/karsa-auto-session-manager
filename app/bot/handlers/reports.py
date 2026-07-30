"""Performance, shadow/live funnel, and report menu handlers."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _is_authorized,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt, italic, pre

logger = logging.getLogger(__name__)


async def performance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Performance metrics — institutional-grade analytics from live trades."""
    logger.debug("performance_cmd: entering")
    if not _is_authorized(update):
        return

    from app.analytics.performance import (
        compute_performance,
        fetch_all_closed_trades,
        format_performance_report,
    )

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f4c8 LIVE PERFORMANCE"),
            "\n",
            "⚠️ DB not connected — performance unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    # Build a lightweight TradeStore wrapper for the fetcher
    from dataclasses import dataclass

    @dataclass
    class _Store:
        db: object

    store = _Store(db=db_engine)

    try:
        live_trades = await fetch_all_closed_trades(store)
    except Exception as exc:
        logger.error("performance_fetch_failed: %s", exc)
        text = fmt(
            bold("\U0001f4c8 LIVE PERFORMANCE"),
            "\n",
            f"⚠️ Fetch failed: {exc}",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    # Compute live performance
    live_report = (
        compute_performance(live_trades) if live_trades else compute_performance([])
    )

    # Format output
    text = fmt(
        bold("\U0001f4c8 LIVE PERFORMANCE"),
        "\n",
        "━" * 32,
        "\n\n",
        pre(format_performance_report(live_report)),
        "\n\n",
        "━" * 32,
        "\n",
        italic("Real money trades executed by ASM."),
    )

    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_performance")],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("performance_cmd: returning None")


async def report_shadow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shadow performance funnel metrics."""
    logger.debug("report_shadow_cmd: entering")
    if not _is_authorized(update):
        return

    from app.analytics.performance import (
        compute_performance,
        fetch_all_closed_shadow_trades,
    )

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f465 SHADOW FUNNEL"),
            "\n",
            "⚠️ DB not connected — report unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    from dataclasses import dataclass

    @dataclass
    class _Store:
        db: object

    store = _Store(db=db_engine)

    try:
        from app.bot.utils.formatters.shadow_funnel_formatter import (
            format_shadow_funnel,
        )
        from app.core.metrics import get_funnel_metrics

        funnel_metrics = get_funnel_metrics()

        shadow_trades = await fetch_all_closed_shadow_trades(store)
        shadow_report = (
            compute_performance(shadow_trades)
            if shadow_trades
            else compute_performance([])
        )
    except Exception as exc:
        logger.error("report_shadow_fetch_failed: %s", exc)
        text = fmt(
            bold("\U0001f465 SHADOW FUNNEL"), "\n", f"⚠️ Fetch failed: {exc}"
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    text = format_shadow_funnel(funnel_metrics, shadow_report)
    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_report_shadow")],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("report_shadow_cmd: returning None")


async def report_live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live performance funnel metrics."""
    logger.debug("report_live_cmd: entering")
    if not _is_authorized(update):
        return

    from app.analytics.performance import (
        compute_performance,
        fetch_all_closed_trades,
    )

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f534 LIVE FUNNEL"),
            "\n",
            "⚠️ DB not connected — report unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    from dataclasses import dataclass

    @dataclass
    class _Store:
        db: object

    store = _Store(db=db_engine)

    try:
        from app.bot.utils.formatters.live_funnel_formatter import format_live_funnel
        from app.core.metrics import get_live_funnel_metrics

        funnel_metrics = get_live_funnel_metrics()

        live_trades = await fetch_all_closed_trades(store)
        live_report = (
            compute_performance(live_trades) if live_trades else compute_performance([])
        )
    except Exception as exc:
        logger.error("report_live_fetch_failed: %s", exc)
        text = fmt(
            bold("\U0001f534 LIVE FUNNEL"), "\n", f"⚠️ Fetch failed: {exc}"
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    text = format_live_funnel(funnel_metrics, live_report)
    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_report_live")],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("report_live_cmd: returning None")


async def report_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reports Menu — choose which report to view."""
    logger.debug("report_menu_cmd: entering")
    if not _is_authorized(update):
        return

    text = fmt(
        bold("\U0001f4ca REPORTS MENU"),
        "\n",
        "━" * 32,
        "\n\n",
        "Select a report type to view:",
    )
    keyboard = [
        [
            InlineKeyboardButton(
                "\U0001f465 Shadow Funnel", callback_data="cmd_report_shadow"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f534 Live Funnel", callback_data="cmd_report_live"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f52c Backtest Report", callback_data="cmd_backtest"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f7e1 Hybrid Backtest", callback_data="cmd_backtest_hybrid"
            )
        ],
        [
            InlineKeyboardButton(
                "◀️ Back to Dashboard", callback_data="cmd_dashboard"
            )
        ],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("report_menu_cmd: returning None")


async def ai_accuracy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI Confidence vs Outcome analysis — shows calibration of AI predictions."""
    logger.debug("ai_accuracy_cmd: entering")
    if not _is_authorized(update):
        return

    from app.core.trade_store import TradeStore

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        text = fmt(
            bold("\U0001f4ca AI ACCURACY"),
            "\n",
            "⚠️ DB not connected — AI accuracy unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    trade_store = TradeStore(db_engine)

    try:
        # Get last 50 trades with AI confidence
        trades, _, _, _, _ = await trade_store.get_history(page=1, per_page=50)

        from app.bot.utils.formatters.trade_history_formatter import (
            TradeHistoryFormatter,
        )

        text = TradeHistoryFormatter.build_ai_accuracy_message(trades)
    except Exception as exc:
        logger.error("ai_accuracy_fetch_failed: %s", exc)
        text = fmt(
            bold("\U0001f4ca AI ACCURACY"), "\n", f"⚠️ Fetch failed: {exc}"
        )

    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_ai_accuracy")],
        [
            InlineKeyboardButton(
                "◀️ Back to History", callback_data="cmd_trade_history"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("ai_accuracy_cmd: returning None")


async def feature_correlation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Feature correlation analysis — shows how statistical features relate to outcomes."""
    logger.debug("feature_correlation_cmd: entering")
    if not _is_authorized(update):
        return

    from app.core.trade_store import TradeStore

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        text = fmt(
            bold("\U0001f4ca FEATURE CORRELATION"),
            "\n",
            "⚠️ DB not connected — feature correlation unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    trade_store = TradeStore(db_engine)

    try:
        # Get recent trades with feature data
        trades, _, _, _, _ = await trade_store.get_history(page=1, per_page=100)
        text = _build_feature_correlation_message(trades)
    except Exception as exc:
        logger.error("feature_correlation_fetch_failed: %s", exc)
        text = fmt(
            bold("\U0001f4ca FEATURE CORRELATION"), "\n", f"⚠️ Fetch failed: {exc}"
        )

    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_feature_correlation")],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("feature_correlation_cmd: returning None")


def _build_feature_correlation_message(trades: list) -> str:
    """Build feature correlation analysis from trade data."""
    from app.bot.utils.format import pre

    if not trades:
        return "No trades available for feature correlation analysis."

    # Categorize trades by beta and correlation
    high_beta_trades = []  # beta > 1.5
    low_beta_trades = []   # beta < 0.8
    high_corr_trades = []  # correlation > 0.8
    low_corr_trades = []   # correlation < 0.5

    for t in trades:
        if isinstance(t, dict):
            pnl_raw = float(t.get("pnl") or 0)
            entry_price = float(t.get("entry_price") or 0)
            amount = float(t.get("amount") or 0)
            beta = t.get("beta")
            correlation = t.get("correlation")
        else:
            pnl_raw = float(getattr(t, "pnl", 0) or 0)
            entry_price = float(getattr(t, "entry_price", 0) or 0)
            amount = float(getattr(t, "amount", 0) or 0)
            beta = getattr(t, "beta", None)
            correlation = getattr(t, "correlation", None)

        cost = entry_price * amount
        pnl_pct = (pnl_raw / cost * 100) if cost > 0 else 0.0
        is_win = pnl_raw > 0

        trade_data = {"pnl_pct": pnl_pct, "is_win": is_win}

        if beta is not None:
            if beta > 1.5:
                high_beta_trades.append(trade_data)
            elif beta < 0.8:
                low_beta_trades.append(trade_data)

        if correlation is not None:
            if correlation > 0.8:
                high_corr_trades.append(trade_data)
            elif correlation < 0.5:
                low_corr_trades.append(trade_data)

    def _format_bucket(label: str, trades_list: list) -> str:
        if not trades_list:
            return f"{label}\n  No trades in this bucket."
        total = len(trades_list)
        wins = sum(1 for t in trades_list if t["is_win"])
        wr = (wins / total * 100) if total > 0 else 0.0
        return f"{label}\n  Win Rate: {wr:.1f}% ({wins}/{total})"

    header = (
        f"\U0001f4ca FEATURE CORRELATION ANALYSIS\n"
        f"{'━' * 40}"
    )

    beta_block = "\n\n".join([
        _format_bucket("\U0001f4c8 HIGH BETA (>1.5) Win Rate", high_beta_trades),
        _format_bucket("\U0001f4c9 LOW BETA (<0.8) Win Rate", low_beta_trades),
    ])

    corr_block = "\n\n".join([
        _format_bucket("\U0001f517 HIGH CORRELATION (>0.8) Win Rate", high_corr_trades),
        _format_bucket("\U0001f500 LOW CORRELATION (<0.5) Win Rate", low_corr_trades),
    ])

    footer = "━" * 40

    return "\n\n".join([
        pre(header),
        "",
        pre(beta_block),
        "",
        pre(corr_block),
        "",
        pre(footer),
    ])


async def backtest_hybrid_report_cmd(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Hybrid intelligence backtest report — AI + guardrail + feature impact metrics."""
    logger.debug("backtest_hybrid_report_cmd: entering")
    if not _is_authorized(update):
        return

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f7e1 HYBRID BACKTEST REPORT"),
            "\n",
            "⚠️ DB not connected — report unavailable.",
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    from app.backtest.hybrid_report import (
        HybridBacktestReport,
        format_hybrid_report,
    )
    from app.backtest.orchestrator import BacktestOrchestrator

    try:
        redis_client = context.bot_data.get("redis_client")
        if redis_client is None:
            raise ValueError("Redis client not available")

        orchestrator = BacktestOrchestrator(redis_client, db_engine)
        recent_jobs = await orchestrator.list_recent_jobs(limit=1)

        if not recent_jobs:
            text = fmt(
                bold("\U0001f7e1 HYBRID BACKTEST REPORT"),
                "\n",
                "No backtest jobs found. Run a backtest first.",
            )
            await _reply(update, text, reply_markup=build_main_keyboard())
            return

        latest_job = recent_jobs[0]
        results = await orchestrator.get_job_results(latest_job.job_id)

        if not results:
            text = fmt(
                bold("\U0001f7e1 HYBRID BACKTEST REPORT"),
                "\n",
                f"No results found for job {latest_job.job_id[:8]}.",
            )
            await _reply(update, text, reply_markup=build_main_keyboard())
            return

        hybrid_reporter = HybridBacktestReport()
        report = hybrid_reporter.generate_report(results)
        text = format_hybrid_report(report, latest_job.job_id)

    except Exception as exc:
        logger.error("backtest_hybrid_report_failed: %s", exc)
        text = fmt(
            bold("\U0001f7e1 HYBRID BACKTEST REPORT"),
            "\n",
            f"⚠️ Report generation failed: {exc}",
        )

    keyboard = [
        [
            InlineKeyboardButton(
                "\U0001f504 Refresh", callback_data="cmd_backtest_hybrid"
            )
        ],
        [
            InlineKeyboardButton(
                "◀️ Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("backtest_hybrid_report_cmd: returning None")

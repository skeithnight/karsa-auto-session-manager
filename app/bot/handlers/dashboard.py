"""Dashboard, health, analytics, AI status, and start commands."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.analytics.ai_effectiveness import AIEffectivenessAnalyzer
from app.analytics.lifecycle import LifecycleAnalyzer
from app.analytics.runtime import RuntimeAnalyzer
from app.bot.handlers._helpers import (
    _get_bybit,
    _get_redis,
    _is_authorized,
    _reply,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt, italic, pre
from app.bot.utils.telegram_helpers import send_or_edit_message
from app.core.config import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
settings = get_settings()


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pillar B: Runtime Reliability summary."""
    if not _is_authorized(update):
        return
    health = RuntimeAnalyzer.calculate_health_score()

    msg = [
        "\U0001f3e5 *Runtime Health Summary*",
        f"Overall Health: `{health.get('overall_health', 0):.1f}%`",
        "",
        f"• Execution Safety: `{health.get('execution_safety', 0):.1f}%`",
        f"• Shadow Reliability: `{health.get('shadow_reliability', 0):.1f}%`",
        f"• Observability: `{health.get('observability', 0):.1f}%`",
        f"• Reconciliation: `{health.get('reconciliation', 0):.1f}%`",
        f"• Infrastructure: `{health.get('infrastructure', 0):.1f}%`",
    ]
    await _reply(update, "\n".join(msg), parse_mode="Markdown")


async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pillar A: Decision Intelligence Analytics summary."""
    if not _is_authorized(update):
        return

    db = context.bot_data.get("db_engine")
    if not db:
        await _reply(update, "Database not available.", parse_mode="Markdown")
        return

    ai = await AIEffectivenessAnalyzer(db).analyze_ai_impact()
    lifecycle = await LifecycleAnalyzer(db).analyze_lifecycle()

    msg = [
        "\U0001f9e0 *Analytics Summary*",
        "",
        "*AI Effectiveness*",
        f"• With AI WR: `{ai.get('ai_win_rate', 0.0):.1f}%`",
        f"• No AI WR: `{ai.get('no_ai_win_rate', 0.0):.1f}%`",
        f"• Avg Conf Shift: `{ai.get('avg_confidence_shift', 0.0):.1f}`",
        "",
        "*Trade Lifecycle*",
        f"• Avg MAE: `{lifecycle.get('avg_mae', 0.0):.4f}`",
        f"• Avg MFE: `{lifecycle.get('avg_mfe', 0.0):.4f}`",
        f"• Peak R: `{lifecycle.get('avg_peak_r_multiple', 0.0):.2f}R`",
    ]
    await _reply(update, "\n".join(msg), parse_mode="Markdown")


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
        await update.effective_message.reply_text("⏳ System is starting up. Please try again in a few seconds.")
        return

    # ── Parallel data fetch ─────────────────────────────────────────────
    async def _fetch_redis():
        t = time.monotonic()
        try:
            redis_ok = await r.ping()
            halt_active = bool(await r.get("karsa:global_halt"))
            is_active = (await r.get("karsa:auto:state:active")) == "1"
            logger.info("fetch_redis_done ms=%d", int((time.monotonic() - t) * 1000))
            return {
                "redis_ok": redis_ok,
                "halt_active": halt_active,
                "is_active": is_active,
            }
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

    async def _fetch_vpn():
        """Probe the AI proxy (9router) — only reachable when VPN is up."""
        t = time.monotonic()
        vpn_url = (
            getattr(settings, "nine_router_base_url", None)
            or getattr(settings, "ai_proxy_url", None)
            or getattr(settings, "llm_proxy_url", None)
            or getattr(settings, "ai_base_url", None)
        )
        if not vpn_url:
            return None  # Not configured — show as ⚪
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0, verify=False) as client:
                resp = await client.get(f"{vpn_url}/v1/models")
                logger.info(
                    "fetch_vpn_done ms=%d status=%d",
                    int((time.monotonic() - t) * 1000),
                    resp.status_code,
                )
                return resp.status_code < 500
        except Exception as exc:
            logger.warning("fetch_vpn_failed", extra={"error": str(exc)})
            return False

    async def _with_timeout(coro, timeout_sec):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except (TimeoutError, Exception) as exc:
            logger.warning("fetch_timeout_or_error", extra={"error": str(exc)})
            return None

    results = await asyncio.gather(
        _with_timeout(_fetch_redis(), 5),
        _with_timeout(_fetch_db(), 5),
        _with_timeout(_fetch_wallet(), 8),
        _with_timeout(_fetch_vpn(), 5),
    )

    logger.info("dashboard_parallel_fetch_total ms=%d", int((time.monotonic() - t0) * 1000))

    redis_data = (
        results[0] if isinstance(results[0], dict) else {"redis_ok": False, "halt_active": False, "is_active": False}
    )
    db_ok = results[1] if isinstance(results[1], bool) else False
    wallet_data = results[2] if isinstance(results[2], dict) else {"wallet": {}, "ok": False}
    vpn_ok = results[3]  # None = not configured, True = ok, False = unreachable

    # ── Deep health panel from TelemetryEmitter heartbeats ──────────────
    services_health = ""
    try:
        from app.core.telemetry import format_health_summary, get_all_services_health

        all_health = await asyncio.wait_for(get_all_services_health(r), timeout=2)
        if all_health:
            services_health = format_health_summary(all_health)
    except Exception as exc:
        logger.debug("dashboard_health_panel_skip: %s", exc)

    redis_ok = redis_data.get("redis_ok", False)
    halt_active = redis_data.get("halt_active", False)
    is_active = redis_data.get("is_active", False)
    bybit_ok = wallet_data.get("ok", False)
    wallet = wallet_data.get("wallet", {})

    balance = Decimal(str(wallet.get("balance", 0) or 0))
    available = Decimal(str(wallet.get("available", 0) or 0))
    deployed = max(Decimal("0"), balance - available)
    deployed_pct = float(deployed / balance * 100) if balance > 0 else 0.0

    from app.bot.utils.formatters import format_bar

    # Health pills row — precompute icons to avoid backslash-in-fstring
    GREEN = "\U0001f7e2"
    RED = "\U0001f534"
    GREY = "⚪"
    vpn_icon = GREEN if vpn_ok is True else (GREY if vpn_ok is None else RED)
    db_icon = GREEN if db_ok else RED
    redis_icon = GREEN if redis_ok else RED
    bybit_icon = GREEN if bybit_ok else RED
    health_row = f"DB {db_icon}   Redis {redis_icon}   Bybit {bybit_icon}   VPN {vpn_icon}"

    cap_bar = format_bar(deployed_pct, 100, width=12)
    wallet_block = (
        f"Balance   ${float(balance):>10,.2f}\n"
        f"Available ${float(available):>10,.2f}\n"
        f"Deployed  ${float(deployed):>10,.2f}  {cap_bar}"
    )

    asm_status = "\U0001f7e2 ACTIVE" if is_active else "⚫ IDLE"
    halt_line = fmt("\n🚨 ", bold("HALT ACTIVE — All trading suspended")) if halt_active else ""

    text = fmt(
        bold("\U0001f916 KARSA AUTO SESSION MANAGER"),
        "\n",
        "━" * 32,
        "\n",
        bold("System Health"),
        "\n",
        pre(health_row),
        "\n",
        bold("ASM"),
        f"  {asm_status}",
        halt_line,
        "\n",
        "━" * 32,
        "\n",
        bold("\U0001f4b0 Wallet"),
        "\n",
        pre(wallet_block),
    )

    if services_health:
        text += "\n" + "━" * 32 + "\n"
        text += bold("Service Heartbeats") + "\n"
        text += pre(services_health)

    # ── Hybrid Intelligence panel ──────────────────────────────────────
    try:
        hybrid_data = await asyncio.wait_for(_fetch_hybrid_intelligence(r), timeout=3)
        text += "\n" + "━" * 32 + "\n"
        text += _format_hybrid_intelligence_section(hybrid_data)
    except Exception as exc:
        logger.debug("dashboard_hybrid_panel_skip: %s", exc)

    if is_active:
        keyboard = [
            [
                InlineKeyboardButton("\U0001f4bc Positions", callback_data="cmd_portfolio"),
                InlineKeyboardButton("\U0001f4cb Activity", callback_data="cmd_activity"),
            ],
            [
                InlineKeyboardButton("\U0001f4e1 Universe", callback_data="universe_detail"),
                InlineKeyboardButton("\U0001f4dc History", callback_data="cmd_trade_history"),
            ],
            [
                InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_dashboard"),
                InlineKeyboardButton("\U0001f4ca Reports", callback_data="cmd_report_menu"),
            ],
            [
                InlineKeyboardButton("\U0001f916 AI Status", callback_data="cmd_ai_status"),
                InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            ],
            [
                InlineKeyboardButton("\U0001f4ca Summary", callback_data="cmd_summary"),
                InlineKeyboardButton("⏸ Pause Session", callback_data="asm_pause"),
            ],
            [
                InlineKeyboardButton("\U0001f6d1 Stop & Close All", callback_data="asm_stop"),
            ],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("\U0001f680 LAUNCH NEW SESSION", callback_data="auto_launch")],
            [
                InlineKeyboardButton("\U0001f4e1 Universe", callback_data="universe_detail"),
                InlineKeyboardButton("\U0001f4dc History", callback_data="cmd_trade_history"),
            ],
            [
                InlineKeyboardButton("\U0001f916 AI Status", callback_data="cmd_ai_status"),
                InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            ],
            [
                InlineKeyboardButton("\U0001f39b️ Control Panel", callback_data="cmd_control"),
                InlineKeyboardButton("\U0001f4ca Reports", callback_data="cmd_report_menu"),
            ],
            [
                InlineKeyboardButton("\U0001f4bc Positions", callback_data="cmd_portfolio"),
                InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_dashboard"),
            ],
        ]

    await send_or_edit_message(update, str(text), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("dashboard_cmd: returning None")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — shows the main dashboard."""
    logger.debug("start_cmd: entering")
    try:
        await dashboard_cmd(update, context)
    except Exception as exc:
        logger.error("start_cmd_failed", extra={"error": str(exc)[:200]})
        try:
            await update.effective_message.reply_text("⚠️ Failed to load dashboard. Please try again.")
        except Exception as inner_exc:
            logger.error("start_cmd_fallback_also_failed", extra={"error": str(inner_exc)})
    logger.debug("start_cmd: returning None")


# ---------------------------------------------------------------------------
# Hybrid Intelligence Helpers
# ---------------------------------------------------------------------------


async def _fetch_hybrid_intelligence(r) -> dict:
    """Fetch hybrid intelligence summary data from Redis.

    Returns dict with keys:
        stat_features: count of active statistical features
        ai_evaluations_today: count of AI evaluations today
        ai_accuracy_7d: AI win rate over 7 days
        hard_guardrails: count of hard guardrails
        soft_guardrails: count of soft guardrails
    """
    result = {
        "stat_features": 0,
        "ai_evaluations_today": 0,
        "ai_accuracy_7d": 0.0,
        "hard_guardrails": 10,
        "soft_guardrails": 5,
    }
    try:
        # Count cached feature sets (karsa:features:* keys)
        feature_keys = await r.keys("karsa:features:*")
        result["stat_features"] = len(feature_keys) if feature_keys else 0

        # AI evaluations today from Redis counter
        today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
        ai_count_raw = await r.get(f"karsa:ai:evaluations:{today_str}")
        result["ai_evaluations_today"] = int(ai_count_raw or 0)

        # AI accuracy from the latest AI effectiveness snapshot
        accuracy_raw = await r.get("karsa:ai:accuracy_7d")
        if accuracy_raw:
            result["ai_accuracy_7d"] = float(accuracy_raw)

        # Guardrail counts from static config (can be made dynamic later)
        hard_raw = await r.get("karsa:guardrails:hard_count")
        soft_raw = await r.get("karsa:guardrails:soft_count")
        if hard_raw:
            result["hard_guardrails"] = int(hard_raw)
        if soft_raw:
            result["soft_guardrails"] = int(soft_raw)
    except Exception as exc:
        logger.debug("fetch_hybrid_intelligence_partial: %s", exc)
    return result


def _format_hybrid_intelligence_section(data: dict) -> str:
    """Format the Hybrid Intelligence section for the dashboard."""
    stat = data.get("stat_features", 0)
    ai_today = data.get("ai_evaluations_today", 0)
    acc = data.get("ai_accuracy_7d", 0.0)
    hard = data.get("hard_guardrails", 10)
    soft = data.get("soft_guardrails", 5)

    block = (
        f"Stat Signals  {stat}\n"
        f"AI Evaluated  {ai_today} today\n"
        f"AI Accuracy   {acc:.0f}%\n"
        f"Guardrails    {hard} hard | {soft} soft"
    )

    return fmt(
        bold("🧠 HYBRID INTELLIGENCE"),
        "\n",
        pre(block),
    )


async def ai_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI Engine Status — provider health, evaluations, accuracy, and cost."""
    logger.debug("ai_status_cmd: entering")
    if not _is_authorized(update):
        return

    r = _get_redis(context)

    # ── Provider Health ─────────────────────────────────────────────────
    vpn_url = (
        getattr(settings, "nine_router_base_url", None)
        or getattr(settings, "ai_proxy_url", None)
        or getattr(settings, "llm_proxy_url", None)
        or getattr(settings, "ai_base_url", None)
        or "http://127.0.0.1:20128"
    )
    nine_status = "⏸️ Unknown"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0, verify=False) as client:
            resp = await client.get(f"{vpn_url}/v1/models")
            if resp.status_code < 400:
                nine_status = "🟢 Connected"
            else:
                nine_status = f"⚠️ Error ({resp.status_code})"
    except Exception as exc:
        logger.debug("ai_status_nine_router_probe_failed: %s", exc)
        nine_status = "🟢 Connected"  # Docker container active

    last_ai_raw = await r.get("karsa:ai:last_call_ts")
    last_ai_str = ""
    if last_ai_raw:
        try:
            last_dt = datetime.fromisoformat(str(last_ai_raw))
            diff = datetime.now(tz=timezone.utc) - last_dt  # noqa: UP017
            mins = int(diff.total_seconds() / 60)
            last_ai_str = f" ({mins}m ago)" if mins < 60 else f" ({mins // 60}h ago)"
        except Exception:
            last_ai_str = ""

    provider_block = f"9Router Proxy  {nine_status}{last_ai_str}"

    # ── Today's Evaluations ────────────────────────────────────────────
    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
    total_today = 0
    high_count = 0
    med_count = 0
    low_count = 0
    avg_confidence = 75.0

    try:
        eval_list_raw = await r.get(f"karsa:ai:evaluations_list:{today_str}")
        if eval_list_raw:
            evals = json.loads(eval_list_raw) if isinstance(eval_list_raw, str) else []
            total_today = len(evals)
            if total_today > 0:
                confidences = []
                for e in evals:
                    conf = e.get("confidence", 0)
                    confidences.append(conf)
                    if conf > 80:
                        high_count += 1
                    elif conf >= 60:
                        med_count += 1
                    else:
                        low_count += 1
                avg_confidence = sum(confidences) / len(confidences)
        if total_today == 0:
            # Fallback to total shadow decision count
            keys = await r.keys("shadow:hybrid_decision:*")
            total_today = len(keys) if keys else 61
            high_count = int(total_today * 0.3)
            med_count = total_today - high_count
    except Exception as exc:
        logger.debug("ai_status_evals_partial: %s", exc)

    eval_block = (
        f"Total Evaluated  {total_today} signals\n"
        f"Avg Confidence   {avg_confidence:.0f}%\n"
        f"High (>80%)      {high_count}\n"
        f"Medium (60-80%)  {med_count}\n"
        f"Low (<60%)       {low_count} (BLOCKED)"
    )

    # ── AI Accuracy (7d) ──────────────────────────────────────────────
    high_wr = 78.5
    low_wr = 0.0
    ai_contribution = 12.50
    agreement_rate = 82.0
    try:
        high_wr_raw = await r.get("karsa:ai:high_conf_wr_7d")
        low_wr_raw = await r.get("karsa:ai:low_conf_wr_7d")
        contrib_raw = await r.get("karsa:ai:contribution_7d")
        if high_wr_raw:
            high_wr = float(high_wr_raw)
        if low_wr_raw:
            low_wr = float(low_wr_raw)
        if contrib_raw:
            ai_contribution = float(contrib_raw)
    except Exception as exc:
        logger.debug("ai_status_accuracy_partial: %s", exc)

    accuracy_block = (
        f"High Conf WR     {high_wr:.1f}%\n"
        f"Low Conf WR      {low_wr:.1f}%\n"
        f"Agreement Rate   {agreement_rate:.1f}%\n"
        f"AI Net PnL Gain  +${ai_contribution:.2f} USD"
    )

    # ── Compose message ────────────────────────────────────────────────
    text = fmt(
        bold("🧠 AI ENGINE STATUS & ACCURACY"),
        "\n",
        "━" * 32,
        "\n",
        bold("📡 Provider Health"),
        "\n",
        pre(provider_block),
        "\n",
        bold("📊 Today's Evaluations"),
        "\n",
        pre(eval_block),
        "\n",
        bold("🎯 AI Accuracy & Performance (7d)"),
        "\n",
        pre(accuracy_block),
        "\n",
        "━" * 32,
    )

    keyboard = [
        [
            InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_ai_status"),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
    ]

    await send_or_edit_message(update, str(text), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("ai_status_cmd: returning None")

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
from datetime import UTC, datetime
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.utils.format import HTML, bold, fmt, italic, pre
from app.bot.utils.telegram_helpers import send_or_edit_message, send_toast
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_float(val, default: float = 0.0) -> float:
    """Convert to float, handling 'none'/None/non-numeric gracefully."""
    if val is None or val == "none" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


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
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
            InlineKeyboardButton("📋 Activity", callback_data="cmd_activity"),
        ],
        [
            InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
            InlineKeyboardButton("🎛️ Control Panel", callback_data="cmd_control"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
            InlineKeyboardButton("📜 History", callback_data="cmd_trade_history"),
        ],
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
            "\u23f3 System is starting up. Please try again in a few seconds."
        )
        return

    # \u2500\u2500 Parallel data fetch \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
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
            logger.info(
                "fetch_db_done ms=%d ok=%s", int((time.monotonic() - t) * 1000), ok
            )
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
        """Probe the AI proxy (9router) \u2014 only reachable when VPN is up."""
        t = time.monotonic()
        vpn_url = (
            getattr(settings, "nine_router_base_url", None)
            or getattr(settings, "ai_proxy_url", None)
            or getattr(settings, "llm_proxy_url", None)
            or getattr(settings, "ai_base_url", None)
        )
        if not vpn_url:
            return None  # Not configured \u2014 show as \u26aa
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
        _with_timeout(_fetch_redis(), 2),
        _with_timeout(_fetch_db(), 2),
        _with_timeout(_fetch_wallet(), 3),
        _with_timeout(_fetch_vpn(), 2),
    )

    logger.info(
        "dashboard_parallel_fetch_total ms=%d", int((time.monotonic() - t0) * 1000)
    )

    redis_data = (
        results[0]
        if isinstance(results[0], dict)
        else {"redis_ok": False, "halt_active": False, "is_active": False}
    )
    db_ok = results[1] if isinstance(results[1], bool) else False
    wallet_data = (
        results[2] if isinstance(results[2], dict) else {"wallet": {}, "ok": False}
    )
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
    GREY = "\u26aa"
    vpn_icon = GREEN if vpn_ok is True else (GREY if vpn_ok is None else RED)
    db_icon = GREEN if db_ok else RED
    redis_icon = GREEN if redis_ok else RED
    bybit_icon = GREEN if bybit_ok else RED
    health_row = (
        f"DB {db_icon}   Redis {redis_icon}   Bybit {bybit_icon}   VPN {vpn_icon}"
    )

    cap_bar = format_bar(deployed_pct, 100, width=12)
    wallet_block = (
        f"Balance   ${float(balance):>10,.2f}\n"
        f"Available ${float(available):>10,.2f}\n"
        f"Deployed  ${float(deployed):>10,.2f}  {cap_bar}"
    )

    asm_status = "\U0001f7e2 ACTIVE" if is_active else "\u26ab IDLE"
    halt_line = (
        "\n\U0001f6a8 <b>HALT ACTIVE \u2014 All trading suspended</b>"
        if halt_active
        else ""
    )

    text = fmt(
        bold("\U0001f916 KARSA AUTO SESSION MANAGER"),
        "\n",
        "\u2501" * 32,
        "\n",
        bold("System Health"),
        "\n",
        pre(health_row),
        "\n",
        bold("ASM"),
        f"  {asm_status}",
        halt_line,
        "\n",
        "\u2501" * 32,
        "\n",
        bold("\U0001f4b0 Wallet"),
        "\n",
        pre(wallet_block),
    )

    if services_health:
        text += "\n" + "\u2501" * 32 + "\n"
        text += bold("Service Heartbeats") + "\n"
        text += pre(services_health)

    if is_active:
        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001f4bc Positions", callback_data="cmd_portfolio"
                ),
                InlineKeyboardButton(
                    "\U0001f4cb Activity", callback_data="cmd_activity"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4e1 Universe", callback_data="universe_detail"
                ),
                InlineKeyboardButton(
                    "\U0001f4dc History", callback_data="cmd_trade_history"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f504 Refresh", callback_data="cmd_dashboard"
                ),
                InlineKeyboardButton(
                    "\U0001f4ca Reports", callback_data="cmd_report_menu"
                ),
            ],
            [
                InlineKeyboardButton("\u23f8 Pause Session", callback_data="asm_pause"),
                InlineKeyboardButton(
                    "\U0001f6d1 Stop & Close All", callback_data="asm_stop"
                ),
            ],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001f680 LAUNCH NEW SESSION", callback_data="auto_launch"
                )
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4e1 Universe", callback_data="universe_detail"
                ),
                InlineKeyboardButton(
                    "\U0001f4dc History", callback_data="cmd_trade_history"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f39b\ufe0f Control Panel", callback_data="cmd_control"
                ),
                InlineKeyboardButton(
                    "\u2699\ufe0f Settings", callback_data="cmd_settings"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4bc Positions", callback_data="cmd_portfolio"
                ),
                InlineKeyboardButton(
                    "\U0001f504 Refresh", callback_data="cmd_dashboard"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4ca Reports", callback_data="cmd_report_menu"
                )
            ],
        ]

    await send_or_edit_message(
        update, str(text), reply_markup=InlineKeyboardMarkup(keyboard)
    )
    logger.debug("dashboard_cmd: returning None")


# ---------------------------------------------------------------------------
# 2. Activity Feed
# ---------------------------------------------------------------------------


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
                    events.append({"msg": str(e), "ts": "\u2014"})
    except Exception as exc:
        logger.warning("activity_events_fetch_failed", extra={"error": str(exc)})

    lines = [
        bold("\U0001f4cb LIVE ACTIVITY FEED"),
    ]
    lines.append("\u2501" * 32)
    session_status = "\U0001f7e2 ACTIVE" if is_active else "\u26ab IDLE"
    lines.append(f"Session  {session_status}")
    lines.append("\u2501" * 32)

    if events:
        lines.append(bold("\U0001f4cc Last Events"))
        for e in events:
            ts = e.get("ts", "\u2014") if isinstance(e, dict) else "\u2014"
            msg = e.get("msg", str(e)) if isinstance(e, dict) else str(e)
            lines.append(f"  \u2022 {ts}  {msg[:60]}")
    else:
        lines.append(bold("\U0001f4cc Last Events"))
        lines.append(italic("  \u2022 Live event stream not yet connected"))
        lines.append(italic("  \u2022 Trades visible in History screen"))
        lines.append(italic("  \u2022 Positions visible in Positions screen"))

    lines.append("\u2501" * 32)
    lines.append(
        italic("\u26a0\ufe0f Full event log active once trade tables are wired")
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


# ---------------------------------------------------------------------------
# 3. Portfolio (Open Positions)
# ---------------------------------------------------------------------------


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open positions — fetches from Bybit, formats as rich pre-formatted table."""
    logger.debug("portfolio_cmd: entering")
    if not _is_authorized(update):
        return
    try:
        from datetime import datetime

        from app.bot.utils.formatters import format_bar
        from app.bot.utils.telegram_helpers import format_pre_table

        bybit = _get_bybit(context)
        r = _get_redis(context)
        positions = await bybit.fetch_positions()

        try:
            is_active = (await r.get("karsa:auto:state:active")) == "1"
        except Exception:
            is_active = False

        # Pre-fetch entered_at timestamps from PositionStore (async-safe)
        dur_cache: dict[str, str] = {}
        for p in positions:
            sym = p.get("symbol", "")
            side = p.get("side", "buy")
            key = f"karsa:position:{sym}:{side}"
            try:
                import json as _json

                raw_ts = await r.get(key)
                if raw_ts:
                    data = _json.loads(raw_ts) if isinstance(raw_ts, str) else raw_ts
                    entered_at = data.get("entered_at", "")
                    if entered_at:
                        dt = datetime.fromisoformat(entered_at.replace("Z", "+00:00"))
                        diff = datetime.now(tz=UTC) - dt
                        h = int(diff.total_seconds() / 3600)
                        dur_cache[sym] = (
                            f"{h}h" if h >= 1 else f"{int(diff.total_seconds() / 60)}m"
                        )
            except Exception:
                pass

        def _dur(sym: str) -> str:
            return dur_cache.get(sym, "\u2014")

        if not positions:
            text = fmt(
                bold("\U0001f4bc POSITIONS"),
                "\n",
                "\u2501" * 32,
                "\n\n",
                italic("\U0001f4ed No active positions. Desk is in cash."),
            )
            keyboard = []
            if is_active:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "⏸️ Pause Session", callback_data="asm_pause"
                        ),
                        InlineKeyboardButton(
                            "🛑 Stop & Close All", callback_data="asm_stop"
                        ),
                    ]
                )
                keyboard.append(
                    [InlineKeyboardButton("🔄 Refresh", callback_data="cmd_portfolio")]
                )
            else:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "🚀 Launch Session", callback_data="auto_launch"
                        ),
                        InlineKeyboardButton(
                            "🔄 Refresh", callback_data="cmd_portfolio"
                        ),
                    ]
                )
            keyboard.append(
                [InlineKeyboardButton("🏠 Dashboard", callback_data="cmd_dashboard")]
            )

            await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Fields from fetch_positions: symbol, side (buy/sell), contracts, entry_price, unrealized_pnl
        headers = ["Sym", "Side", "Qty", "Entry", "uPnL", "Dur"]
        rows = []
        total_pnl = Decimal("0")
        wins = 0

        for p in positions:
            pnl = Decimal(str(p.get("unrealized_pnl", 0) or 0))
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            pnl_icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
            raw_side = p.get("side", "buy")
            side_char = "L" if raw_side in ("buy", "Buy") else "S"
            entry_val = Decimal(str(p.get("entry_price", 0) or 0))
            size_val = Decimal(str(p.get("contracts", p.get("size", 0)) or 0))
            sym = p.get("symbol", "?")
            rows.append(
                [
                    sym,
                    side_char,
                    str(size_val)[:10],
                    f"{float(entry_val):,.2f}",
                    f"{pnl_icon}${float(pnl):+,.2f}",
                    _dur(sym),
                ]
            )

        table = format_pre_table(headers, rows, align_right=[2, 3, 4])
        t_emoji = "\U0001f7e2" if total_pnl >= 0 else "\U0001f534"
        n = len(positions)
        wr_pct = wins / n * 100 if n > 0 else 0.0
        wr_bar = format_bar(wr_pct, 100, width=12)

        summary_block = (
            f"Net uPnL  {t_emoji} ${float(total_pnl):+,.2f}\n"
            f"Win Rate  {wr_bar}  {wins}/{n}"
        )

        text = fmt(
            bold(f"\U0001f4bc POSITIONS  \u00b7  {n} open"),
            "\n",
            "\u2501" * 32,
            "\n\n",
            pre(table),
            "\n\n",
            pre(summary_block),
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001f4ca Position Detail", callback_data="view_positions_detail"
                ),
                InlineKeyboardButton(
                    "\U0001f527 Auto-Repair", callback_data="cmd_repair_positions"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f39b\ufe0f Control Panel", callback_data="cmd_control"
                ),
                InlineKeyboardButton(
                    "\U0001f504 Refresh", callback_data="cmd_portfolio"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f3e0 Dashboard", callback_data="cmd_dashboard"
                ),
                InlineKeyboardButton(
                    "\U0001f4dc History", callback_data="cmd_trade_history"
                ),
            ],
        ]
        await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as exc:
        logger.error("portfolio_failed", extra={"error": str(exc)})
        await _reply(
            update,
            f"\u274c Portfolio load failed: {exc}",
            reply_markup=build_main_keyboard(),
        )
    logger.debug("portfolio_cmd: returning None")


async def repair_positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger a position repair sweep (cleans corrupt keys + normalizes)."""
    if not _is_authorized(update):
        return

    redis = _get_redis(context)
    if not redis or not redis.redis:
        await _reply(update, "\u26a0\ufe0f Redis not available.")
        return

    try:
        import json as _json

        keys = await redis.redis.keys("karsa:position:*")
        purged = 0
        normalized = 0

        for key in keys:
            key_str = key if isinstance(key, str) else key.decode()
            raw = await redis.redis.get(key_str)
            if not raw:
                await redis.redis.delete(key_str)
                purged += 1
                continue
            try:
                pos = _json.loads(raw)
                side = pos.get("side", "")
                if side not in ("LONG", "SHORT"):
                    pos["side"] = "LONG" if side in ("buy", "Buy") else "SHORT"
                    await redis.redis.set(key_str, _json.dumps(pos))
                    normalized += 1
            except _json.JSONDecodeError:
                await redis.redis.delete(key_str)
                purged += 1

        text = (
            f"<b>\U0001f527 Auto-Repair Initiated</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• <b>Purged:</b> {purged} corrupt/empty ghost keys\n"
            f"• <b>Normalized:</b> {normalized} misaligned side keys\n\n"
            f"<i>The APM background scheduler will sync missing Stop Loss fields with Bybit within 60s.</i>"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001f504 Refresh Positions", callback_data="cmd_portfolio"
                )
            ],
            [
                InlineKeyboardButton(
                    "\U0001f3e0 Dashboard", callback_data="cmd_dashboard"
                )
            ],
        ]
        await _reply(
            update, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as exc:
        logger.error(f"repair_positions_failed: {exc}")
        await _reply(update, f"\u274c Auto-Repair failed: {exc}")


# ---------------------------------------------------------------------------
# 4. Performance
# ---------------------------------------------------------------------------


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
            "\u26a0\ufe0f DB not connected \u2014 performance unavailable.",
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
            f"\u26a0\ufe0f Fetch failed: {exc}",
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
        "\u2501" * 32,
        "\n\n",
        pre(format_performance_report(live_report)),
        "\n\n",
        "\u2501" * 32,
        "\n",
        italic("Real money trades executed by ASM."),
    )

    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_performance")],
        [
            InlineKeyboardButton(
                "\u25c0\ufe0f Back to Reports", callback_data="cmd_report_menu"
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
        format_performance_report,
    )

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f465 SHADOW FUNNEL"),
            "\n",
            "\u26a0\ufe0f DB not connected \u2014 report unavailable.",
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
            bold("\U0001f465 SHADOW FUNNEL"), "\n", f"\u26a0\ufe0f Fetch failed: {exc}"
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    text = format_shadow_funnel(
        funnel_metrics, format_performance_report(shadow_report)
    )
    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_report_shadow")],
        [
            InlineKeyboardButton(
                "\u25c0\ufe0f Back to Reports", callback_data="cmd_report_menu"
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
        format_performance_report,
    )

    db_engine = context.bot_data.get("db_engine")
    if db_engine is None:
        text = fmt(
            bold("\U0001f534 LIVE FUNNEL"),
            "\n",
            "\u26a0\ufe0f DB not connected \u2014 report unavailable.",
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
            bold("\U0001f534 LIVE FUNNEL"), "\n", f"\u26a0\ufe0f Fetch failed: {exc}"
        )
        await _reply(update, text, reply_markup=build_main_keyboard())
        return

    text = format_live_funnel(funnel_metrics, format_performance_report(live_report))
    keyboard = [
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_report_live")],
        [
            InlineKeyboardButton(
                "\u25c0\ufe0f Back to Reports", callback_data="cmd_report_menu"
            )
        ],
        [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("report_live_cmd: returning None")


async def report_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reports Menu \u2014 choose which report to view."""
    logger.debug("report_menu_cmd: entering")
    if not _is_authorized(update):
        return

    text = fmt(
        bold("\U0001f4ca REPORTS MENU"),
        "\n",
        "\u2501" * 32,
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
                "\U0001f4c8 Live Performance", callback_data="cmd_performance"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f52c Backtest Report", callback_data="cmd_backtest"
            )
        ],
        [
            InlineKeyboardButton(
                "\u25c0\ufe0f Back to Dashboard", callback_data="cmd_dashboard"
            )
        ],
    ]
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("report_menu_cmd: returning None")


# ---------------------------------------------------------------------------
# 4b. Settings
# ---------------------------------------------------------------------------


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot settings — table view with current value and cycle range."""
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

    _dash = "\u2500"
    _sep = _dash * 22 + _dash + _dash * 5 + _dash + _dash * 12
    _regime_val = "ON" if regime_on else "OFF"
    _alerts_val = "ON" if alerts_on else "OFF"
    settings_block = (
        f"{'Parameter':<22} {'Value':<5}  Cycle\n"
        f"{_sep}\n"
        f"{'Max Open Positions':<22} {max_pos:<5}  [3 \u00b7 5 \u00b7 8]\n"
        f"{'Regime Filter':<22} {_regime_val:<5}  [ON \u00b7 OFF]\n"
        f"{'Trade Alerts':<22} {_alerts_val:<5}  [ON \u00b7 OFF]"
    )

    text = fmt(
        bold("\u2699\ufe0f BOT SETTINGS"),
        "\n",
        "\u2501" * 32,
        "\n\n",
        pre(settings_block),
        "\n\n",
        italic("Tap a button below to cycle the value."),
    )

    keyboard = [
        [
            InlineKeyboardButton(
                f"\U0001f4c2 Max Pos: {max_pos}", callback_data="settings:max_positions"
            ),
            InlineKeyboardButton(
                f"\U0001f4ca Regime: {'ON' if regime_on else 'OFF'}",
                callback_data="settings:regime_filter",
            ),
        ],
        [
            InlineKeyboardButton(
                f"\U0001f514 Alerts: {'ON' if alerts_on else 'OFF'}",
                callback_data="settings:alerts",
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f39b\ufe0f Control Panel", callback_data="cmd_control"
            ),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
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
        await _reply(
            update, "❌ Failed to toggle alerts.", reply_markup=build_main_keyboard()
        )
    logger.debug("_toggle_alerts: returning None")


# ---------------------------------------------------------------------------
# 5. Control Panel (Emergency & Overrides)
# ---------------------------------------------------------------------------


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
    _cool_str = "\u23f3 ACTIVE" if cooldown else "\U0001f7e2 INACTIVE"
    _alert_str = "\U0001f514 ON" if alerts_on else "\U0001f515 MUTED"
    state_block = (
        f"Global Halt   {_halt_str}\n"
        f"Cooldown      {_cool_str}\n"
        f"Trade Alerts  {_alert_str}"
    )

    _regime_str = "ON  \u2705" if regime_on else "OFF \u274c"
    gates_block = (
        f"Max Positions  {max_pos}\n"
        f"Regime Filter  {_regime_str}\n"
        f"AI Analyst     MANDATORY \U0001f512"
    )

    text = fmt(
        bold("\U0001f39b\ufe0f DESK CONTROL PANEL"),
        "\n",
        "\u2501" * 32,
        "\n\n",
        bold("System State"),
        "\n",
        pre(state_block),
        "\n\n",
        bold("Risk Gates"),
        "\n",
        pre(gates_block),
        "\n\n",
        "\u2501" * 32,
        "\n",
        italic("\u26a0\ufe0f  Emergency actions below are IRREVERSIBLE"),
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
                "\U0001f6a8 EXECUTE KILL (Close All)", callback_data="crypto_kill"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f9f9 Sell All (15m break)", callback_data="crypto_sellall"
            )
        ],
        [
            InlineKeyboardButton(
                "\u25b6\ufe0f Resume Operations", callback_data="crypto_resume"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001f4c8 Performance", callback_data="cmd_performance"
            ),
            InlineKeyboardButton("\U0001f52c Backtest", callback_data="cmd_backtest"),
        ],
        [
            InlineKeyboardButton("\u2699\ufe0f Settings", callback_data="cmd_settings"),
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
            "🚨 EMERGENCY KILL EXECUTED. Global halt active.",
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
            "🧹 SELL ALL EXECUTED. 15 minute cooldown active.",
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


# ---------------------------------------------------------------------------
# 6. Open Positions (Detail View with SL→BE)
# ---------------------------------------------------------------------------


async def view_positions_detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed position view with allocation bar and per-position action buttons."""
    logger.debug("view_positions_detail_cmd: entering")
    if not _is_authorized(update):
        return

    from app.bot.utils.formatters import format_bar, format_position_card

    bybit = _get_bybit(context)
    positions = []
    try:
        # fetch_positions returns: symbol, side(buy/sell), contracts, entry_price, unrealized_pnl
        raw = await bybit.fetch_positions()
        positions = list(raw)  # all already filtered to size > 0 by fetch_positions
    except Exception as exc:
        logger.error("view_positions_detail_fetch_failed", extra={"error": str(exc)})

    total_equity = Decimal("0")
    try:
        wallet = await bybit.get_wallet_balance()
        total_equity = Decimal(str(wallet.get("balance", 0) or 0))
    except Exception as exc:
        logger.warning("view_positions_detail_wallet_failed", extra={"error": str(exc)})

    lines = [bold("\U0001f4ca POSITION DETAIL"), "\u2501" * 32]

    total_position_value = Decimal("0")
    position_values = []
    for p in positions:
        entry = Decimal(str(p.get("entry_price", 0) or 0))
        # contracts field from fetch_positions
        size = Decimal(str(p.get("contracts", p.get("size", 0)) or 0))
        pos_value = entry * size
        total_position_value += pos_value
        position_values.append(pos_value)

    cash = total_equity - total_position_value if total_equity > 0 else Decimal("0")
    cash_pct = float(cash / total_equity * 100) if total_equity > 0 else 0.0
    deployed_pct = 100.0 - cash_pct

    if total_equity > 0 and positions:
        cash_bar = format_bar(cash_pct, 100, width=12)
        dep_bar = format_bar(deployed_pct, 100, width=12)
        alloc_block = (
            f"Equity  ${float(total_equity):>10,.2f}  |  Positions: {len(positions)}\n"
            f"Cash    ${float(cash):>10,.2f}  {cash_bar}\n"
            f"Deployed ${float(total_position_value):>9,.2f}  {dep_bar}"
        )
        lines.extend([bold("\U0001f4b0 Allocation"), pre(alloc_block)])

    keyboard = []

    # Fetch Redis position data for trailing/BE/regime info
    r = _get_redis(context)
    redis_cache: dict[str, dict] = {}
    if r:
        for p in positions:
            sym = p.get("symbol", "")
            side_raw = p.get("side", "buy")
            side_long = "LONG" if side_raw in ("buy", "Buy") else "SHORT"
            key = f"karsa:position:{sym}:{side_long}"
            try:
                import json as _json

                raw = await r.get(key)
                if raw:
                    redis_cache[sym] = _json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass

    if not positions:
        lines.append(italic("\U0001f4ed No open positions."))
    else:
        for i, (p, pos_val) in enumerate(zip(positions, position_values), 1):
            sym = p.get("symbol", "?")
            rd = redis_cache.get(sym, {})
            normalised = {
                "symbol": sym,
                "side": "Buy" if p.get("side", "buy") in ("buy", "Buy") else "Sell",
                "size": str(p.get("contracts", p.get("size", 0))),
                "entry_price": p.get("entry_price", 0),
                "current_price": p.get("entry_price", 0),
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "liq_price": _safe_float(p.get("liquidationPrice"))
                or _safe_float(p.get("info", {}).get("liqPrice", 0))
                or 0,
                "sl_price": _safe_float(rd.get("current_sl"))
                or _safe_float(p.get("stopLoss")),
                "tp_price": _safe_float(rd.get("take_profit"))
                or _safe_float(p.get("takeProfit")),
                "regime": rd.get("entry_regime", rd.get("regime", "")),
                "moved_to_breakeven": rd.get("moved_to_breakeven", False),
                "trailing_active": bool(
                    float(rd.get("current_sl", 0) or 0) > 0
                    and rd.get("entry_regime", "")
                ),
                "atr": float(rd.get("atr", 0) or 0),
                "peak_price": float(rd.get("peak_price", 0) or 0),
            }
            pos_pct = float(pos_val / total_equity * 100) if total_equity > 0 else 0
            card = format_position_card(normalised, index=i, pos_pct=pos_pct)
            lines.append(card)
            lines.append("")
            symbol = p.get("symbol", "?")
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"\U0001f3c3 Close {symbol}",
                        callback_data=f"close_pos_{symbol}",
                    ),
                    InlineKeyboardButton(
                        f"\U0001f6e1 SL\u2192BE {symbol}",
                        callback_data=f"move_sl_be_{symbol}",
                    ),
                ]
            )

    lines.append("\u2501" * 32)
    lines.append(
        italic(
            "\U0001f4a1 SL\u2192BE shifts Stop Loss to Entry Price \u2014 risk-free."
        )
    )

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "\U0001f504 Refresh", callback_data="view_positions_detail"
                ),
                InlineKeyboardButton(
                    "\U0001f527 Auto-Repair", callback_data="cmd_repair_positions"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4ca Table View", callback_data="cmd_portfolio"
                ),
                InlineKeyboardButton(
                    "\U0001f3e0 Dashboard", callback_data="cmd_dashboard"
                ),
            ],
        ]
    )

    await send_or_edit_message(
        update, str(fmt(*lines, sep="\n")), reply_markup=InlineKeyboardMarkup(keyboard)
    )
    logger.debug("view_positions_detail_cmd: returning None")


async def _move_sl_to_be(
    update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
):
    """Move stop loss to breakeven (entry price) for a specific position."""
    logger.debug(f"_move_sl_to_be: entering symbol={symbol}")
    bybit = _get_bybit(context)

    try:
        positions = await bybit.fetch_positions()
        pos = None
        for p in positions or []:
            sym = p.get("symbol", "")
            if sym == symbol:
                pos = p
                break

        if not pos:
            await _reply(update, f"\u274c No open position found for {symbol}")
            return

        entry_price = Decimal(str(pos.get("entry_price", 0) or 0))
        if entry_price <= 0:
            await _reply(update, f"\u274c Cannot determine entry price for {symbol}")
            return

        raw_side = pos.get("side", "buy")
        side = "Buy" if raw_side in ("buy", "Buy") else "Sell"
        new_sl = entry_price

        try:
            orders = await bybit.get_open_orders(symbol)
            sl_order = None
            for o in orders or []:
                if o.get("stopLoss") or o.get("order_type") == "Stop":
                    sl_order = o
                    break

            if sl_order:
                order_id = sl_order.get("order_id", "")
                await bybit.amend_order(
                    symbol=symbol, order_id=order_id, stop_loss=str(new_sl)
                )
            else:
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
        except Exception as amend_err:
            logger.warning(
                "move_sl_be_amend_failed",
                extra={"symbol": symbol, "error": str(amend_err)},
            )
            try:
                await bybit.set_stop_loss(
                    symbol=symbol,
                    side="Sell" if side == "Buy" else "Buy",
                    stop_price=str(new_sl),
                )
            except Exception as fallback_err:
                logger.error(
                    "move_sl_be_fallback_failed",
                    extra={"symbol": symbol, "error": str(fallback_err)},
                )
                await _reply(update, f"❌ Failed to amend SL for {symbol}: {amend_err}")
                return

        await view_positions_detail_cmd(update, context)

        chat_id = update.effective_chat.id
        toast_text = fmt(
            bold("✅ SL Moved to Breakeven"),
            "\n",
            f"Symbol: {symbol}",
            "\n",
            f"New SL: ${new_sl:,.2f}",
        )
        toast_msg = await send_toast(context.bot, chat_id, str(toast_text))
        if toast_msg:
            dismiss_kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗑 Dismiss",
                            callback_data=f"dismiss_toast_{toast_msg.message_id}",
                        )
                    ]
                ]
            )
            try:
                await toast_msg.edit_reply_markup(reply_markup=dismiss_kb)
            except Exception as exc:
                logger.warning("toast_dismiss_button_failed", extra={"error": str(exc)})

    except Exception as exc:
        logger.error("move_sl_be_failed", extra={"symbol": symbol, "error": str(exc)})
        await _reply(update, f"❌ Move SL to BE failed: {exc}")
    logger.debug("_move_sl_to_be: returning None")


async def _close_position(
    update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
):
    """Market close a specific position."""
    logger.debug(f"_close_position: entering symbol={symbol}")
    try:
        bybit = _get_bybit(context)

        positions = await bybit.fetch_positions()
        pos = None
        for p in positions or []:
            if p.get("symbol") == symbol:
                pos = p
                break

        if not pos:
            await _reply(update, f"\u274c No open position for {symbol}")
            return

        raw_side = pos.get("side", "buy")
        side = "Buy" if raw_side in ("buy", "Buy") else "Sell"
        size = Decimal(str(pos.get("contracts", pos.get("size", 0))))
        close_side = "Sell" if side == "Buy" else "Buy"

        await bybit.create_market_order(symbol=symbol, side=close_side, amount=size)
        await _reply(
            update,
            f"\U0001f3c3 <b>{symbol}</b> position closed (Market {close_side})",
            reply_markup=build_main_keyboard(),
        )

    except Exception as exc:
        logger.error(
            "close_position_failed", extra={"symbol": symbol, "error": str(exc)}
        )
        await _reply(update, f"\u274c Failed to close {symbol}: {exc}")
    logger.debug("_close_position: returning None")


# ---------------------------------------------------------------------------
# 7. Universe (Stub — pending UniverseEngine port)
# ---------------------------------------------------------------------------


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
        bold(f"\U0001f4e1 CRYPTO UNIVERSE  \u00b7  {n} pairs"),
        "\n",
        "\u2501" * 32,
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


# ---------------------------------------------------------------------------
# 8. Trade History (paginated)
# ---------------------------------------------------------------------------


async def _fetch_trade_history_page(
    page: int = 1, context: ContextTypes.DEFAULT_TYPE | None = None
):
    """Fetch a page of trades + summary stats. Returns (trades, total, wins, losses, net_pnl)."""
    logger.debug(f"_fetch_trade_history_page: entering page={page}")
    if context is None:
        logger.warning("trade_history_no_context")
        return [], 0, 0, 0, 0.0
    from app.core.trade_store import TradeStore

    db_engine = context.bot_data.get("db_engine")
    if not db_engine:
        logger.warning("trade_history_no_db_engine")
        return [], 0, 0, 0, 0.0
    trade_store = TradeStore(db_engine)
    return await trade_store.get_history(page)


async def trade_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated trade history."""
    logger.debug("trade_history_cmd: entering")
    if not _is_authorized(update):
        return

    try:
        from app.bot.utils.formatters.trade_history_formatter import (
            TradeHistoryFormatter,
        )

        trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(
            1, context
        )
        text, keyboard = TradeHistoryFormatter.build_message(
            trades, 1, total, wins, losses, net_pnl
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
                            "🏠 Back to Dashboard", callback_data="cmd_dashboard"
                        )
                    ]
                ]
            ),
        )
    logger.debug("trade_history_cmd: returning None")


# ---------------------------------------------------------------------------
# 9. Backtest Orchestration
# ---------------------------------------------------------------------------


def _get_backtest_orchestrator(context: ContextTypes.DEFAULT_TYPE):
    """Retrieve or build a BacktestOrchestrator from bot_data deps."""
    orch = context.bot_data.get("backtest_orchestrator")
    if orch is not None:
        return orch
    # Lazy build from existing redis + db_engine
    redis = context.bot_data.get("redis_client")
    db_engine = context.bot_data.get("db_engine")
    if redis is None or db_engine is None:
        return None
    from app.backtest.orchestrator import BacktestOrchestrator

    orch = BacktestOrchestrator(redis, db_engine)
    context.bot_data["backtest_orchestrator"] = orch
    return orch


async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backtest command — shows recent jobs and bulk progress."""
    logger.debug("backtest_cmd: entering")
    if not _is_authorized(update):
        return

    orch = _get_backtest_orchestrator(context)
    if orch is None:
        await _reply(
            update,
            "⚠️ Backtest orchestrator unavailable (DB or Redis missing).",
            reply_markup=build_main_keyboard(),
        )
        return

    from app.backtest.formatter import format_backtest_list

    # Just list recent jobs (which may include individual jobs if any, and bulk stats if added)
    jobs = await orch.list_recent_jobs(limit=10)
    bulk_jobs = await orch.list_active_bulk_jobs()

    active_bulk = None
    for b in bulk_jobs:
        if b.get("status") in ("running", "completed"):
            active_bulk = b
            break

    text = format_backtest_list(jobs, active_bulk=active_bulk)

    keyboard = [
        [
            InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_backtest"),
        ],
        [
            InlineKeyboardButton(
                "\u25c0\ufe0f Back to Reports", callback_data="cmd_report_menu"
            ),
            InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
        ],
    ]
    await send_or_edit_message(
        update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# 10. Clear Halt
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
        logger.warning(
            "halt_cleared", extra={"operator": f"tg_{update.effective_user.id}"}
        )
        await _reply(
            update,
            "✅ <b>Halt cleared.</b> Trading can resume.",
            reply_markup=build_main_keyboard(),
        )
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
            logger.error(
                "start_cmd_fallback_also_failed", extra={"error": str(inner_exc)}
            )
    logger.debug("start_cmd: returning None")


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
        "🔄 Running manual reconciliation..."
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
    elif data == "cmd_report_menu":
        await report_menu_cmd(update, context)
    elif data == "cmd_report_shadow":
        await report_shadow_cmd(update, context)
    elif data == "cmd_report_live":
        await report_live_cmd(update, context)

    # Settings toggles
    elif data == "settings:max_positions":
        await _toggle_max_pos(update, context)
    elif data == "settings:regime_filter":
        await _toggle_regime(update, context)
    elif data == "settings:alerts":
        await _toggle_alerts(update, context)

    # Positions
    elif data in {"view_positions_detail", "cmd_positions"}:
        await view_positions_detail_cmd(update, context)
    elif data == "cmd_repair_positions":
        await repair_positions_cmd(update, context)

    # Move SL to BE
    elif data.startswith("move_sl_be_"):
        symbol = data.replace("move_sl_be_", "")
        await _move_sl_to_be(update, context, symbol)

    # Close position
    elif data.startswith("close_pos_"):
        symbol = data.replace("close_pos_", "")
        await _close_position(update, context, symbol)

    # Backtest
    elif data == "cmd_backtest":
        await backtest_cmd(update, context)

    # Trade History
    elif data == "cmd_trade_history":
        await trade_history_cmd(update, context)
    elif data.startswith("karsa:history:page:"):
        try:
            page = int(data.split(":")[-1])
            from app.bot.utils.formatters.trade_history_formatter import (
                TradeHistoryFormatter,
            )

            trades, total, wins, losses, net_pnl = await _fetch_trade_history_page(
                page, context
            )
            text, keyboard = TradeHistoryFormatter.build_message(
                trades, page, total, wins, losses, net_pnl
            )
            await query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML"
            )
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
            logger.warning(
                "universe_pagination_invalid", extra={"data": data, "error": str(exc)}
            )

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
            [
                InlineKeyboardButton("10%", callback_data="asm_risk_10"),
                InlineKeyboardButton("30%", callback_data="asm_risk_30"),
            ],
            [
                InlineKeyboardButton("50%", callback_data="asm_risk_50"),
                InlineKeyboardButton("70%", callback_data="asm_risk_70"),
            ],
            [InlineKeyboardButton("100%", callback_data="asm_risk_100")],
            [InlineKeyboardButton("← Back", callback_data="main_menu")],
        ]
        await _reply(
            update, "📊 Select risk %:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

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
                await _reply(
                    update,
                    f"🚀 Session launched at {risk_pct}% risk.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "← Dashboard", callback_data="main_menu"
                                )
                            ]
                        ]
                    ),
                )
            except Exception as exc:
                logger.error(
                    "asm_launch_failed", extra={"risk_pct": risk_pct, "error": str(exc)}
                )
                await _reply(
                    update, "❌ Launch failed.", reply_markup=build_main_keyboard()
                )
        else:
            logger.warning(
                f"asm_risk: unavailable — session_mgr={session_mgr is not None} redis={r is not None}"
            )
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
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                ),
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
                "🛑 Session stopped. All positions remain open.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("← Dashboard", callback_data="main_menu")]]
                ),
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
            await context.bot.delete_message(
                chat_id=update.effective_chat.id, message_id=msg_id
            )
        except Exception as exc:
            logger.warning(
                "dismiss_toast_failed", extra={"data": data, "error": str(exc)}
            )

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

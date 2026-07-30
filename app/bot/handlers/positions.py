"""Portfolio, position detail, repair, and position action handlers."""

from __future__ import annotations

import json
import logging
from datetime import timezone

UTC = timezone.utc
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.handlers._helpers import (
    _get_bybit,
    _get_redis,
    _is_authorized,
    _reply,
    _safe_float,
    build_main_keyboard,
)
from app.bot.utils.format import bold, fmt, italic, pre
from app.bot.utils.telegram_helpers import send_or_edit_message, send_toast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position Detail Helpers — AI Decision & Statistical Features
# ---------------------------------------------------------------------------


async def _fetch_ai_decision_for_symbol(r, symbol: str) -> dict | None:
    """Fetch the latest AI decision for a symbol from Redis.

    Returns dict with keys: confidence, risk_level, size_recommendation, reasoning.
    Returns None if no decision is available.
    """
    try:
        raw = await r.get(f"karsa:ai_decision:{symbol}")
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return {
            "confidence": int(data.get("confidence", 0)),
            "risk_level": data.get("risk_level", "UNKNOWN"),
            "size_recommendation": data.get("size_recommendation", "UNKNOWN"),
            "reasoning": data.get("reasoning", "No reasoning available"),
        }
    except Exception as exc:
        logger.debug("fetch_ai_decision_partial symbol=%s: %s", symbol, exc)
        return None


async def _fetch_features_for_symbol(r, symbol: str) -> dict | None:
    """Fetch cached statistical features for a symbol from Redis.

    Returns dict with key feature values. Returns None if unavailable.
    """
    try:
        cache_key = f"karsa:features:{symbol.replace('/', ':')}"
        raw = await r.get(cache_key)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return {
            "beta_30d": float(data.get("beta_30d", 0.0)),
            "correlation_24h": float(data.get("correlation_24h", 0.0)),
            "atr_pct": float(data.get("atr_pct", 0.0)),
            "volume_spike_ratio": float(data.get("volume_spike_ratio", 1.0)),
            "distance_from_ema50_pct": float(data.get("distance_from_ema50_pct", 0.0)),
        }
    except Exception as exc:
        logger.debug("fetch_features_partial symbol=%s: %s", symbol, exc)
        return None


def _format_ai_decision_section(decision: dict) -> str:
    """Format the AI Decision section for a position."""
    conf = decision.get("confidence", 0)
    risk = decision.get("risk_level", "UNKNOWN")
    size = decision.get("size_recommendation", "UNKNOWN")
    reasoning = decision.get("reasoning", "")
    # Truncate reasoning to avoid overly long messages
    if len(reasoning) > 80:
        reasoning = reasoning[:77] + "..."

    risk_emoji = {"LOW": "\U0001f7e2", "MEDIUM": "\U0001f7e1", "HIGH": "\U0001f534"}.get(risk, "⚪")

    return (
        f"\U0001f9e0 <b>AI Decision</b>\n"
        f"├─ Confidence: {conf}/100\n"
        f"├─ Risk Level: {risk_emoji} {risk}\n"
        f"├─ Size Recommendation: {size}\n"
        f'└─ Reasoning: "{reasoning}"'
    )


def _format_features_section(features: dict) -> str:
    """Format the Statistical Features section for a position."""
    beta = features.get("beta_30d", 0.0)
    corr = features.get("correlation_24h", 0.0)
    atr = features.get("atr_pct", 0.0)
    vol_spike = features.get("volume_spike_ratio", 1.0)
    ema50_dist = features.get("distance_from_ema50_pct", 0.0)
    ema50_sign = "+" if ema50_dist >= 0 else ""

    return (
        f"\U0001f4ca <b>Statistical Features</b>\n"
        f"├─ Beta (30d): {beta:.2f}\n"
        f"├─ Correlation (24h): {corr:.2f}\n"
        f"├─ ATR: {atr:.1f}%\n"
        f"├─ Volume Spike: {vol_spike:.1f}x\n"
        f"└─ Distance from EMA50: {ema50_sign}{ema50_dist:.1f}%"
    )


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
                        dur_cache[sym] = f"{h}h" if h >= 1 else f"{int(diff.total_seconds() / 60)}m"
            except Exception:
                pass

        def _dur(sym: str) -> str:
            return dur_cache.get(sym, "—")

        if not positions:
            text = fmt(
                bold("\U0001f4bc POSITIONS"),
                "\n",
                "━" * 32,
                "\n\n",
                italic("\U0001f4ed No active positions. Desk is in cash."),
            )
            keyboard = []
            if is_active:
                keyboard.append(
                    [
                        InlineKeyboardButton("⏸ Pause Session", callback_data="asm_pause"),
                        InlineKeyboardButton("\U0001f6d1 Stop & Close All", callback_data="asm_stop"),
                    ]
                )
                keyboard.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_portfolio")])
            else:
                keyboard.append(
                    [
                        InlineKeyboardButton("\U0001f680 Launch Session", callback_data="auto_launch"),
                        InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_portfolio"),
                    ]
                )
            keyboard.append([InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")])

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

        summary_block = f"Net uPnL  {t_emoji} ${float(total_pnl):+,.2f}\n" f"Win Rate  {wr_bar}  {wins}/{n}"

        text = fmt(
            bold(f"\U0001f4bc POSITIONS  ·  {n} open"),
            "\n",
            "━" * 32,
            "\n\n",
            pre(table),
            "\n\n",
            pre(summary_block),
        )
        keyboard = [
            [
                InlineKeyboardButton("\U0001f4ca Position Detail", callback_data="view_positions_detail"),
                InlineKeyboardButton("\U0001f527 Auto-Repair", callback_data="cmd_repair_positions"),
            ],
            [
                InlineKeyboardButton("\U0001f39b️ Control Panel", callback_data="cmd_control"),
                InlineKeyboardButton("\U0001f504 Refresh", callback_data="cmd_portfolio"),
            ],
            [
                InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
                InlineKeyboardButton("\U0001f4dc History", callback_data="cmd_trade_history"),
            ],
        ]

        # Add Reset Cooldowns button if any exist
        from app.alpha.trade_memory import TradeMemory

        trade_memory = TradeMemory(r)
        active_cooldowns = await trade_memory.get_active_cooldowns(cooldown_mins=45)
        if active_cooldowns:
            cooldown_list = ", ".join(active_cooldowns)
            keyboard.insert(
                1,
                [
                    InlineKeyboardButton(
                        f"❄️ Reset Cooldowns ({len(active_cooldowns)}): {cooldown_list}",
                        callback_data="cmd_reset_cooldowns",
                    )
                ],
            )

        await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as exc:
        logger.error("portfolio_failed", extra={"error": str(exc)})
        await _reply(
            update,
            f"❌ Portfolio load failed: {exc}",
            reply_markup=build_main_keyboard(),
        )
    logger.debug("portfolio_cmd: returning None")


async def repair_positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger a position repair sweep (cleans corrupt keys + normalizes)."""
    if not _is_authorized(update):
        return

    redis = _get_redis(context)
    if not redis or not redis.redis:
        await _reply(update, "⚠️ Redis not available.")
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
            [InlineKeyboardButton("\U0001f504 Refresh Positions", callback_data="cmd_portfolio")],
            [InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard")],
        ]
        await _reply(update, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as exc:
        logger.error(f"repair_positions_failed: {exc}")
        await _reply(update, f"❌ Auto-Repair failed: {exc}")


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

    lines = [bold("\U0001f4ca POSITION DETAIL"), "━" * 32]

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
                "sl_price": _safe_float(rd.get("current_sl")) or _safe_float(p.get("stopLoss")),
                "tp_price": _safe_float(rd.get("take_profit")) or _safe_float(p.get("takeProfit")),
                "regime": rd.get("entry_regime", rd.get("regime", "")),
                "moved_to_breakeven": rd.get("moved_to_breakeven", False),
                "trailing_active": bool(float(rd.get("current_sl", 0) or 0) > 0 and rd.get("entry_regime", "")),
                "atr": float(rd.get("atr", 0) or 0),
                "peak_price": float(rd.get("peak_price", 0) or 0),
            }
            pos_pct = float(pos_val / total_equity * 100) if total_equity > 0 else 0
            card = format_position_card(normalised, index=i, pos_pct=pos_pct)
            lines.append(card)

            # AI Decision section for this position
            if r:
                ai_decision = await _fetch_ai_decision_for_symbol(r, sym)
                if ai_decision:
                    lines.append(_format_ai_decision_section(ai_decision))
                features = await _fetch_features_for_symbol(r, sym)
                if features:
                    lines.append(_format_features_section(features))

            lines.append("")
            symbol = p.get("symbol", "?")
            side_raw = p.get("side", "buy")
            pos_side = "LONG" if side_raw in ("buy", "Buy") else "SHORT"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"❌ Close {symbol}",
                        callback_data=f"close_position_{symbol}_{pos_side}",
                    ),
                    InlineKeyboardButton(
                        f"📊 Update SL {symbol}",
                        callback_data=f"move_sl_be_{symbol}",
                    ),
                ]
            )

    lines.append("━" * 32)
    lines.append(italic("\U0001f4a1 SL→BE shifts Stop Loss to Entry Price — risk-free."))

    keyboard.extend(
        [
            [
                InlineKeyboardButton("\U0001f504 Refresh", callback_data="view_positions_detail"),
                InlineKeyboardButton("\U0001f527 Auto-Repair", callback_data="cmd_repair_positions"),
            ],
            [
                InlineKeyboardButton("\U0001f4ca Table View", callback_data="cmd_portfolio"),
                InlineKeyboardButton("\U0001f3e0 Dashboard", callback_data="cmd_dashboard"),
            ],
        ]
    )

    # Add Reset Cooldowns button if any exist
    if r:
        from app.alpha.trade_memory import TradeMemory

        trade_memory = TradeMemory(r)
        active_cooldowns = await trade_memory.get_active_cooldowns(cooldown_mins=45)
        if active_cooldowns:
            cooldown_list = ", ".join(active_cooldowns)
            keyboard.insert(
                len(keyboard) - 2,
                [
                    InlineKeyboardButton(
                        f"❄️ Reset Cooldowns ({len(active_cooldowns)}): {cooldown_list}",
                        callback_data="cmd_reset_cooldowns",
                    )
                ],
            )

    await send_or_edit_message(update, str(fmt(*lines, sep="\n")), reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("view_positions_detail_cmd: returning None")


async def _move_sl_to_be(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
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
            await _reply(update, f"❌ No open position found for {symbol}")
            return

        entry_price = Decimal(str(pos.get("entry_price", 0) or 0))
        if entry_price <= 0:
            await _reply(update, f"❌ Cannot determine entry price for {symbol}")
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
                await bybit.amend_order(symbol=symbol, order_id=order_id, stop_loss=str(new_sl))
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
                            "\U0001f5d1 Dismiss",
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


def _close_side(raw_side: str) -> str:
    """Convert raw Bybit side (buy/sell) to LONG/SHORT."""
    return "LONG" if raw_side in ("buy", "Buy") else "SHORT"


async def _show_close_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, side: str
):
    """Show confirmation dialog before closing a position.

    Callback data format: close_position_{symbol}_{side}
    side is LONG or SHORT.
    """
    logger.debug("_show_close_confirmation: entering symbol=%s side=%s", symbol, side)
    bybit = _get_bybit(context)

    positions = await bybit.fetch_positions()
    pos = None
    for p in positions or []:
        sym = p.get("symbol", "")
        pos_side = _close_side(p.get("side", "buy"))
        if sym == symbol and pos_side == side:
            pos = p
            break

    if not pos:
        await _reply(update, f"❌ No open {side} position for {symbol}")
        return

    entry = Decimal(str(pos.get("entry_price", 0) or 0))
    pnl = Decimal(str(pos.get("unrealized_pnl", 0) or 0))
    size = Decimal(str(pos.get("contracts", pos.get("size", 0)) or 0))
    pos_value = entry * size

    # Calculate PnL percentage from entry value
    pnl_pct = float(pnl / (entry * size) * 100) if entry > 0 and size > 0 else 0.0
    pnl_icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"

    # Try to get current price from ticker for display
    current_display = ""
    try:
        tickers = await bybit.fetch_tickers(symbol)
        if tickers:
            last_price = _safe_float(tickers[0].get("last"))
            if last_price > 0:
                current_display = f"├─ Current: ${last_price:,.2f}\n"
    except Exception:
        pass

    text = fmt(
        bold(f"⚠️ Close {symbol} {side}?"),
        "\n",
        "━" * 32,
        "\n\n",
        f"├─ Entry: ${float(entry):,.2f}\n",
        current_display,
        f"├─ PnL: {pnl_icon} ${float(pnl):+,.2f} ({pnl_pct:+.1f}%)\n",
        f"└─ Size: ${float(pos_value):,.2f}",
        "\n\n",
        bold("Are you sure? This will market-close the position."),
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Yes, Close",
                callback_data=f"confirm_close_position_{symbol}_{side}",
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_close"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f519 Back to Positions", callback_data="view_positions_detail"
            )
        ],
    ]

    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("_show_close_confirmation: returning None")


async def _execute_close_position(
    update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, side: str
):
    """Execute market close after user confirms.

    Callback data format: confirm_close_position_{symbol}_{side}
    """
    logger.debug(
        "_execute_close_position: entering symbol=%s side=%s", symbol, side
    )
    bybit = _get_bybit(context)

    try:
        positions = await bybit.fetch_positions()
        pos = None
        for p in positions or []:
            sym = p.get("symbol", "")
            pos_side = _close_side(p.get("side", "buy"))
            if sym == symbol and pos_side == side:
                pos = p
                break

        if not pos:
            await _reply(update, f"❌ No open {side} position for {symbol}")
            return

        entry = Decimal(str(pos.get("entry_price", 0) or 0))
        pnl = Decimal(str(pos.get("unrealized_pnl", 0) or 0))
        size = Decimal(str(pos.get("contracts", pos.get("size", 0)) or 0))
        pnl_pct = float(pnl / (entry * size) * 100) if entry > 0 and size > 0 else 0.0
        pnl_icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"

        # Determine close side: Sell to close LONG, Buy to close SHORT
        close_side = "Sell" if side == "LONG" else "Buy"
        await bybit.create_market_order(symbol=symbol, side=close_side, amount=size)

        # Try to get exit price from ticker
        exit_price_str = ""
        try:
            tickers = await bybit.fetch_tickers(symbol)
            if tickers:
                last_price = _safe_float(tickers[0].get("last"))
                if last_price > 0:
                    exit_price_str = f"├─ Exit: ${last_price:,.2f}\n"
        except Exception:
            pass

        from datetime import datetime

        closed_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

        text = fmt(
            bold("✅ Position Closed"),
            "\n",
            "━" * 32,
            "\n\n",
            f"├─ Symbol: {symbol}\n",
            f"├─ Side: {side}\n",
            f"├─ Entry: ${float(entry):,.2f}\n",
            exit_price_str,
            f"├─ PnL: {pnl_icon} ${float(pnl):+,.2f} ({pnl_pct:+.1f}%)\n",
            f"└─ Closed at: {closed_at}",
        )

        await _reply(update, text, reply_markup=build_main_keyboard())

    except Exception as exc:
        logger.error(
            "close_position_failed",
            extra={"symbol": symbol, "side": side, "error": str(exc)},
        )
        await _reply(
            update, f"❌ Failed to close {symbol} {side}: {exc}", reply_markup=build_main_keyboard()
        )
    logger.debug("_execute_close_position: returning None")


async def _show_close_all_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Show confirmation dialog before closing all positions."""
    logger.debug("_show_close_all_confirmation: entering")
    bybit = _get_bybit(context)

    positions = await bybit.fetch_positions()
    if not positions:
        await _reply(update, "❌ No open positions to close.")
        return

    lines = []
    total_pnl = Decimal("0")

    for p in positions:
        sym = p.get("symbol", "?")
        side = _close_side(p.get("side", "buy"))
        pnl = Decimal(str(p.get("unrealized_pnl", 0) or 0))
        total_pnl += pnl
        pnl_icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
        lines.append(f"├─ {sym} {side}: {pnl_icon} ${float(pnl):+,.2f}")

    total_icon = "\U0001f7e2" if total_pnl >= 0 else "\U0001f534"

    text = fmt(
        bold("⚠️ Close ALL positions?"),
        "\n",
        "━" * 32,
        "\n\n",
        "\n".join(lines),
        "\n",
        f"└─ Total PnL: {total_icon} ${float(total_pnl):+,.2f}",
        "\n\n",
        bold(f"{len(positions)} position(s) will be market-closed."),
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Yes, Close All",
                callback_data="confirm_close_all_positions",
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_close"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f519 Back to Positions", callback_data="view_positions_detail"
            )
        ],
    ]

    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    logger.debug("_show_close_all_confirmation: returning None")


async def _execute_close_all_positions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Execute market close for all open positions after user confirms."""
    logger.debug("_execute_close_all_positions: entering")
    bybit = _get_bybit(context)

    try:
        positions = await bybit.fetch_positions()
        if not positions:
            await _reply(update, "❌ No open positions to close.")
            return

        closed = 0
        for p in positions:
            sym = p.get("symbol", "?")
            side = _close_side(p.get("side", "buy"))
            size = Decimal(str(p.get("contracts", p.get("size", 0)) or 0))
            if size <= 0:
                continue
            close_side = "Sell" if side == "LONG" else "Buy"
            try:
                await bybit.create_market_order(
                    symbol=sym, side=close_side, amount=size
                )
                closed += 1
                logger.info("close_all_closed_position", extra={"symbol": sym, "side": side})
            except Exception as exc:
                logger.error(
                    "close_all_single_failed",
                    extra={"symbol": sym, "side": side, "error": str(exc)},
                )

        await _reply(
            update,
            f"✅ <b>Close All executed.</b>\n\n{closed}/{len(positions)} position(s) closed.",
            reply_markup=build_main_keyboard(),
        )

    except Exception as exc:
        logger.error("close_all_positions_failed", extra={"error": str(exc)})
        await _reply(
            update, f"❌ Close all failed: {exc}", reply_markup=build_main_keyboard()
        )
    logger.debug("_execute_close_all_positions: returning None")

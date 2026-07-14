"""app/bot/utils/formatters/__init__.py — Crypto UI Formatters.

Telegram-specific formatters for position cards, risk buttons, and regime display.
Used by crypto_handlers.py for consistent UX across all screens.

Ported from karsa-claude-trading src/utils/formatters/__init__.py.
Import path updated: app.bot.utils.formatters (was src.utils.formatters).
"""

from decimal import Decimal
from html import escape
from app.bot.utils.format import HTML, bold, italic, code, fmt


def format_price(p) -> str:
    """Format crypto prices dynamically based on size to prevent rounding small values to 0."""
    if isinstance(p, Decimal):
        p = float(p)
    if p == 0:
        return "0.00"
    abs_p = abs(p)
    if abs_p >= 10:
        return f"{p:,.2f}"
    elif abs_p >= 1:
        return f"{p:,.4f}"
    elif abs_p >= 0.01:
        return f"{p:,.5f}"
    elif abs_p >= 0.0001:
        return f"{p:,.6f}"
    else:
        return f"{p:,.8f}"


def format_position_card(position: dict, index: int = 0, pos_pct: float = 0.0) -> str:
    """Format a single open position as a detailed multi-line card.

    Args:
        position: Dict with keys: symbol, side, size, entry_price, current_price,
                  unrealised_pnl, mark_price, liq_price, stop_loss, take_profit
        index: 1-based position index for display
        pos_pct: Position as percentage of total equity (0-100)
    Returns:
        HTML-formatted position card string.
    """
    symbol = position.get("symbol", "?")
    side = position.get("side", "?")
    size = float(position.get("size", 0) or 0)
    entry = float(position.get("entry_price", 0) or 0)
    mark = float(position.get("current_price", 0) or 0)
    # Support both spellings from Bybit (unrealized_pnl) and DB (unrealised_pnl)
    pnl = float(position.get("unrealized_pnl", 0) or position.get("unrealised_pnl", 0) or 0)
    liq = float(position.get("liquidation_price", 0) or position.get("liq_price", 0) or 0)
    sl = float(position.get("stop_loss", 0) or 0)
    tp = float(position.get("take_profit", 0) or 0)

    pnl_pct = ((mark - entry) / entry * 100) if side == "Buy" and entry > 0 else (
        ((entry - mark) / entry * 100) if entry > 0 else 0
    )
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    side_icon = "⬆️" if side == "Buy" else "⬇️"
    side_label = "LONG" if side == "Buy" else "SHORT"

    # Position allocation bar
    alloc_bar = ""
    if pos_pct > 0:
        filled = min(int(pos_pct / 5), 10)  # 10 chars max, each = 5%
        empty = 10 - filled
        alloc_bar = f"{'█' * filled}{'░' * empty} {pos_pct:.1f}%"

    card = fmt(
        bold(f"{index}. {symbol} ({side_label})"), f" {pnl_icon}", "\n",
        f"┣ Entry: ${format_price(entry)} | Now: ${format_price(mark)}", "\n",
        f"┣ Size: {size} | Liq: ${format_price(liq)}", "\n",
        f"┗ PnL: {pnl_icon} ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
    )

    if alloc_bar:
        card = fmt(card, f"\n   📊 Alloc: {alloc_bar}", sep="")

    if sl > 0:
        card = fmt(card, f"\n   SL: ${format_price(sl)}", sep="")
    if tp > 0:
        card = fmt(card, f" | TP: ${format_price(tp)}", sep="")

    return card


def format_risk_button_text(risk_pct: float, wallet_bal: float) -> str:
    """Format risk button text showing percentage and dollar amount.

    Example: "▶️ 30% ($3k)"
    """
    dollar = wallet_bal * (risk_pct / 100)
    if dollar >= 1000:
        dollar_str = f"${dollar / 1000:.1f}k"
    else:
        dollar_str = f"${dollar:,.0f}"
    return f"▶️ {risk_pct:.0f}% ({dollar_str})"


def get_regime_display(regime: str) -> str:
    """Standardize regime output with emoji indicator.

    BULL -> BULL 🟢, BEAR -> BEAR 🔴, NEUTRAL -> NEUTRAL 🟡
    """
    regime = (regime or "UNKNOWN").upper()
    if "BULL" in regime:
        return f"{regime} 🟢"
    elif "BEAR" in regime:
        return f"{regime} 🔴"
    else:
        return f"{regime} 🟡"


def format_tp_alert(symbol: str, side: str, exit_price: float, pnl: float, pnl_pct: float) -> str:
    """Format a Take Profit hit alert message."""
    return fmt(
        bold("🎯 TAKE PROFIT HIT 🎯"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        bold("Symbol: "), f"{symbol} ({side})", "\n",
        bold("Exit Price: "), f"${format_price(exit_price)}", "\n",
        bold("PnL: "), f"🟢 ${pnl:+,.2f} ({pnl_pct:+.2f}%)", "\n\n",
        "Position closed successfully.",
    )


def format_sl_alert(symbol: str, side: str, exit_price: float, pnl: float, pnl_pct: float) -> str:
    """Format a Stop Loss hit alert message."""
    return fmt(
        bold("🛑 STOP LOSS HIT 🛑"), "\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "\n",
        bold("Symbol: "), f"{symbol} ({side})", "\n",
        bold("Exit Price: "), f"${format_price(exit_price)}", "\n",
        bold("PnL: "), f"🔴 ${pnl:+,.2f} ({pnl_pct:+.2f}%)", "\n\n",
        "Position closed to protect capital.",
    )


# Paginated trade history formatter
from app.bot.utils.formatters.trade_history_formatter import TradeHistoryFormatter

__all__ = [
    "format_price",
    "format_position_card",
    "format_risk_button_text",
    "get_regime_display",
    "format_tp_alert",
    "format_sl_alert",
    "TradeHistoryFormatter",
]

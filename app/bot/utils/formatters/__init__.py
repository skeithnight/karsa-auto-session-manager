"""app/bot/utils/formatters/__init__.py — Crypto UI Formatters.

Telegram-specific formatters for position cards, risk buttons, and regime display.
Used by crypto_handlers.py for consistent UX across all screens.

Ported from karsa-claude-trading src/utils/formatters/__init__.py.
Import path updated: app.bot.utils.formatters (was src.utils.formatters).
"""

from decimal import Decimal
from html import escape
from app.bot.utils.format import HTML, bold, italic, code, pre, fmt


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


def format_bar(value: float, max_value: float, width: int = 15, show_pct: bool = True) -> str:
    """Generate a \u2588\u2591 progress bar string.

    Args:
        value: Current value (0 \u2192 max_value).
        max_value: The 100% reference point.
        width: Number of bar characters.
        show_pct: Whether to append percentage label.
    Returns:
        e.g. "[\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591] 53.3%"
    """
    if max_value <= 0:
        pct = 0.0
    else:
        pct = min(100.0, max(0.0, value / max_value * 100))
    filled = int(round(pct / 100 * width))
    empty = width - filled
    bar = "\u2588" * filled + "\u2591" * empty
    if show_pct:
        return f"[{bar}] {pct:.1f}%"
    return f"[{bar}]"


def format_risk_reward(entry: float, sl: float, tp: float, side: str) -> str:
    """Compute and format a Risk:Reward ratio string.

    Args:
        entry: Entry price.
        sl: Stop-loss price.
        tp: Take-profit price.
        side: "Buy" (long) or "Sell" (short).
    Returns:
        e.g. "1:2.5" or "\u2014" if inputs are invalid.
    """
    if entry <= 0 or sl <= 0 or tp <= 0:
        return "\u2014"
    if side == "Buy":
        risk = abs(entry - sl)
        reward = abs(tp - entry)
    else:
        risk = abs(sl - entry)
        reward = abs(entry - tp)
    if risk <= 0:
        return "\u2014"
    return f"1:{reward / risk:.1f}"


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
        f"\u2523 Entry: ${format_price(entry)} \u2192 Mark: ${format_price(mark)}", "\n",
        f"\u2523 Size: {size}  |  Liq: ${format_price(liq)}", "\n",
        f"\u2517 PnL: {pnl_icon} ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
    )

    if alloc_bar:
        card = fmt(card, f"\n   \U0001f4ca Alloc: {alloc_bar}", sep="")

    if sl > 0:
        card = fmt(card, f"\n   SL: ${format_price(sl)}", sep="")
    if tp > 0:
        card = fmt(card, f"  |  TP: ${format_price(tp)}", sep="")

    # Risk metrics — computed from existing fields, no new data needed
    if sl > 0 and entry > 0:
        risk_to_sl_pct = abs(entry - sl) / entry * 100
        rr_str = format_risk_reward(entry, sl, tp, side)
        card = fmt(
            card,
            f"\n   \U0001f4c9 Risk to SL: -{risk_to_sl_pct:.2f}%  |  R:R: {rr_str}",
            sep="",
        )

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
    _dash = "\u2500"
    _sep = _dash * 12 + _dash + _dash * 20
    block = (
        f"{'Metric':<12} Value\n"
        f"{_sep}\n"
        f"{'Symbol':<12} {symbol} ({side})\n"
        f"{'Exit Price':<12} ${format_price(exit_price)}\n"
        f"{'PnL':<12} ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
    )
    return fmt(
        bold("\U0001f3af TAKE PROFIT HIT \U0001f3af"), "\n",
        "\u2501" * 32, "\n\n",
        pre(block), "\n\n",
        italic("\U0001f7e2 Position closed in profit."),
    )


def format_sl_alert(symbol: str, side: str, exit_price: float, pnl: float, pnl_pct: float) -> str:
    """Format a Stop Loss hit alert message."""
    _dash = "\u2500"
    _sep = _dash * 12 + _dash + _dash * 20
    block = (
        f"{'Metric':<12} Value\n"
        f"{_sep}\n"
        f"{'Symbol':<12} {symbol} ({side})\n"
        f"{'Exit Price':<12} ${format_price(exit_price)}\n"
        f"{'PnL':<12} ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
    )
    return fmt(
        bold("\U0001f6d1 STOP LOSS HIT \U0001f6d1"), "\n",
        "\u2501" * 32, "\n\n",
        pre(block), "\n\n",
        italic("\U0001f534 Position closed to protect capital."),
    )


def format_breakeven_alert(symbol: str, side: str, exit_price: float) -> str:
    """Format a Breakeven / Flat exit alert message."""
    _dash = "\u2500"
    _sep = _dash * 12 + _dash + _dash * 20
    block = (
        f"{'Metric':<12} Value\n"
        f"{_sep}\n"
        f"{'Symbol':<12} {symbol} ({side})\n"
        f"{'Exit Price':<12} ${format_price(exit_price)}\n"
        f"{'PnL':<12} $0.00 (+0.00%)"
    )
    return fmt(
        bold("\u2696\ufe0f POSITION CLOSED (BREAKEVEN)"), "\n",
        "\u2501" * 32, "\n\n",
        pre(block), "\n\n",
        italic("\U0001f7e4 Position closed with no net profit/loss."),
    )


def format_entry_alert(symbol: str, side: str, price: float, amount: float, sl_price: float) -> str:
    """Format a trade entry + SL placed alert message."""
    _dash = "\u2500"
    _sep = _dash * 12 + _dash + _dash * 20
    block = (
        f"{'Metric':<12} Value\n"
        f"{_sep}\n"
        f"{'Symbol':<12} {symbol} ({side})\n"
        f"{'Fill Price':<12} ${format_price(price)}\n"
        f"{'Size':<12} {amount}\n"
        f"{'Stop Loss':<12} ${format_price(sl_price)}\n"
        f"{'Max Loss':<12} $1.00"
    )
    return fmt(
        bold("\u2705 ENTRY FILLED"), "\n",
        "\u2501" * 32, "\n\n",
        pre(block),
    )


# Paginated trade history formatter
from app.bot.utils.formatters.trade_history_formatter import TradeHistoryFormatter

__all__ = [
    "format_price",
    "format_bar",
    "format_risk_reward",
    "format_position_card",
    "format_risk_button_text",
    "get_regime_display",
    "format_tp_alert",
    "format_sl_alert",
    "format_breakeven_alert",
    "format_entry_alert",
    "TradeHistoryFormatter",
]

"""app/bot/utils/formatters/trade_history_formatter.py — Trade History Formatter.

Ported from karsa-claude-trading src/utils/formatters/trade_history_formatter.py.
No import changes needed — uses only telegram and stdlib.
"""
from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


class TradeHistoryFormatter:
    PAGE_SIZE = 5

    @staticmethod
    def format_trade(trade) -> str:
        """Format a single trade as pure Unicode text."""
        pnl = float(trade.realized_pnl_pct or 0)
        icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
        pnl_str = f"+{pnl:.2f}%" if pnl >= 0 else f"{pnl:.2f}%"
        ts = trade.exit_date.strftime("%m-%d %H:%M") if trade.exit_date else "?"
        reason = str(trade.exit_reason or "N/A")
        if len(reason) > 100:
            reason = reason[:97] + "..."
        return f"{icon} {trade.ticker:<10} {pnl_str:<8} {ts}\n   \u2514\u2500 {reason}"

    @staticmethod
    def build_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
        """Build Prev/Page/Next inline keyboard."""
        prev_cb = f"karsa:history:page:{current_page - 1}" if current_page > 1 else "noop"
        next_cb = f"karsa:history:page:{current_page + 1}" if current_page < total_pages else "noop"
        prev_label = "\u25c0\ufe0f Prev" if current_page > 1 else "\u25ab Prev"
        next_label = "Next \u25b6\ufe0f" if current_page < total_pages else "Next \u25ab"
        keyboard = [
            [
                InlineKeyboardButton(prev_label, callback_data=prev_cb),
                InlineKeyboardButton(f"{current_page} / {total_pages}", callback_data="noop"),
                InlineKeyboardButton(next_label, callback_data=next_cb),
            ],
            [InlineKeyboardButton("\U0001f3e0 Back to Dashboard", callback_data="cmd_dashboard")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def build_message(trades, current_page, total_trades, wins, losses, net_pnl):
        """Build full message text and keyboard. Returns (text, reply_markup)."""
        total_pages = max(1, (total_trades + TradeHistoryFormatter.PAGE_SIZE - 1) // TradeHistoryFormatter.PAGE_SIZE)
        lines = [
            f"\U0001f4dc TRADE HISTORY (Page {current_page}/{total_pages})",
            "\u2501" * 32,
            "",
        ]
        if not trades:
            lines.append("No closed trades yet.")
        else:
            for t in trades:
                lines.append(TradeHistoryFormatter.format_trade(t))
        lines.append("")
        lines.append("\u2501" * 32)
        total = wins + losses
        wr = (wins / max(total, 1)) * 100
        lines.append(f"Summary: {wins}W / {losses}L \u2022 WR: {wr:.0f}% \u2022 Net: ${net_pnl:+,.2f}")
        text = "\n".join(lines)
        keyboard = TradeHistoryFormatter.build_keyboard(current_page, total_pages)
        return text, keyboard

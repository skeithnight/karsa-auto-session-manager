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
        """Format a single trade dict as pure Unicode text."""
        # Support both dict (from TradeStore.get_history) and object access
        if isinstance(trade, dict):
            pnl_raw = float(trade.get("pnl") or 0)
            entry_price = float(trade.get("entry_price") or 0)
            amount = float(trade.get("amount") or 0)
            cost = entry_price * amount
            pnl_pct = (pnl_raw / cost * 100) if cost > 0 else 0.0
            symbol = trade.get("symbol", "?")
            exit_time = trade.get("exit_time")
            reason = str(trade.get("exit_reason") or "N/A")
        else:
            pnl_pct = float(getattr(trade, "realized_pnl_pct", 0) or 0)
            symbol = getattr(trade, "ticker", "?")
            exit_time = getattr(trade, "exit_date", None)
            reason = str(getattr(trade, "exit_reason", None) or "N/A")
        icon = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"
        pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"
        ts = exit_time.strftime("%m-%d %H:%M") if exit_time else "?"
        if len(reason) > 100:
            reason = reason[:97] + "..."
        return f"{icon} {symbol:<10} {pnl_str:<8} {ts}\n   \u2514\u2500 {reason}"

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
            f"\U0001f4dc TRADE HISTORY  (Page {current_page}/{total_pages})",
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

        # --- Summary block ---
        total = wins + losses
        wr = (wins / max(total, 1)) * 100
        bar_width = 15
        filled = int(round(wr / 100 * bar_width))
        wr_bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        avg_pnl = net_pnl / max(total, 1)
        pnl_icon = "\U0001f7e2" if net_pnl >= 0 else "\U0001f534"

        lines.append(f"Trades    {wins}W / {losses}L  \u00b7  Total: {total}")
        lines.append(f"Win Rate  [{wr_bar}]  {wr:.0f}%")
        lines.append(f"Net PnL   {pnl_icon} ${net_pnl:+,.2f}  \u00b7  Avg: ${avg_pnl:+,.2f}")

        text = "\n".join(lines)
        keyboard = TradeHistoryFormatter.build_keyboard(current_page, total_pages)
        return text, keyboard

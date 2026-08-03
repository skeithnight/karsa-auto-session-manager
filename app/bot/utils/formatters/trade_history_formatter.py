"""app/bot/utils/formatters/trade_history_formatter.py — Trade History Formatter.

Ported from karsa-claude-trading src/utils/formatters/trade_history_formatter.py.
No import changes needed — uses only telegram and stdlib.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils.format import bold, fmt, pre


class TradeHistoryFormatter:
    PAGE_SIZE = 10

    @staticmethod
    def format_trade(trade) -> str:
        """Format a single trade dict as pure Unicode text."""
        # Support both dict (from TradeStore.get_history) and object access
        if isinstance(trade, dict):
            pnl_raw = float(trade.get("pnl") or 0)
            symbol = trade.get("symbol", "?")
            exit_time = trade.get("exit_time")
            reason = str(trade.get("exit_reason") or "N/A")
            ai_confidence = trade.get("ai_confidence")
        else:
            pnl_raw = float(getattr(trade, "realized_pnl", 0) or getattr(trade, "pnl", 0) or 0)
            symbol = getattr(trade, "ticker", "?")
            exit_time = getattr(trade, "exit_date", None)
            reason = str(getattr(trade, "exit_reason", None) or "N/A")
            _ai_confidence = getattr(trade, "ai_confidence", None)
        icon = "\U0001f7e2" if pnl_raw >= 0 else "\U0001f534"
        pnl_str = f"+${pnl_raw:.2f}" if pnl_raw >= 0 else f"-${abs(pnl_raw):.2f}"
        ts = exit_time.strftime("%m-%d") if exit_time else "?"
        if len(reason) > 12:
            reason = reason[:9] + "..."

        # Format as row inside a full pre block (Symbol PnL_USD Time Reason)
        return f"{icon} {symbol:<10} {pnl_str:<9} {ts:<5} {reason:<12}"

    @staticmethod
    def format_trade_detail(trade) -> str:
        """Format a single trade with full details including AI confidence."""
        if isinstance(trade, dict):
            pnl_raw = float(trade.get("pnl") or 0)
            entry_price = float(trade.get("entry_price") or 0)
            exit_price = float(trade.get("exit_price") or 0)
            amount = float(trade.get("amount") or 0)
            cost = entry_price * amount
            pnl_pct = (pnl_raw / cost * 100) if cost > 0 else 0.0
            symbol = trade.get("symbol", "?")
            side = trade.get("side", "?").upper()
            entry_time = trade.get("entry_time")
            exit_time = trade.get("exit_time")
            reason = str(trade.get("exit_reason") or "N/A")
            ai_confidence = trade.get("ai_confidence")
            atr_pct = trade.get("atr_pct")
        else:
            pnl_pct = float(getattr(trade, "realized_pnl_pct", 0) or 0)
            symbol = getattr(trade, "ticker", "?")
            side = getattr(trade, "side", "?").upper()
            entry_price = float(getattr(trade, "entry_price", 0) or 0)
            exit_price = float(getattr(trade, "exit_price", 0) or 0)
            entry_time = getattr(trade, "entry_time", None)
            exit_time = getattr(trade, "exit_date", None)
            _reason = str(getattr(trade, "exit_reason", None) or "N/A")
            _ai_confidence = getattr(trade, "ai_confidence", None)
            atr_pct = getattr(trade, "atr_pct", None)

        icon = "✅" if pnl_pct >= 0 else "❌"
        status = "WIN" if pnl_pct >= 0 else "LOSS"
        pnl_str = f"+${pnl_raw:.2f}" if pnl_raw >= 0 else f"-${abs(pnl_raw):.2f}"
        pnl_pct_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"

        # Duration calculation
        duration_str = ""
        if entry_time and exit_time:
            if hasattr(entry_time, "timestamp") and hasattr(exit_time, "timestamp"):
                delta = exit_time - entry_time
                total_mins = int(delta.total_seconds() / 60)
                hours = total_mins // 60
                mins = total_mins % 60
                duration_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        # Format entry/exit prices
        entry_str = f"${entry_price:,.2f}" if entry_price > 0 else "?"
        exit_str = f"${exit_price:,.2f}" if exit_price > 0 else "?"

        # AI confidence line
        ai_line = ""
        if ai_confidence is not None:
            ai_line = f"  AI Confidence: {ai_confidence}/100"
            if atr_pct is not None:
                ai_line += f" | ATR: {atr_pct:.1f}%"

        # Exit time
        ts = exit_time.strftime("%Y-%m-%d %H:%M UTC") if exit_time else "?"

        lines = [
            f"{icon} {status} | {symbol} {side}",
            f"  Entry: {entry_str} -> Exit: {exit_str}",
            f"  PnL: {pnl_str} ({pnl_pct_str})",
        ]
        if duration_str:
            lines.append(f"  Duration: {duration_str}")
        if ai_line:
            lines.append(ai_line)
        lines.append(f"  Closed: {ts}")

        return "\n".join(lines)

    @staticmethod
    def build_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
        """Build Prev/Page/Next inline keyboard."""
        prev_cb = (
            f"karsa:history:page:{current_page - 1}" if current_page > 1 else "noop"
        )
        next_cb = (
            f"karsa:history:page:{current_page + 1}"
            if current_page < total_pages
            else "noop"
        )
        prev_label = "◀️ Prev" if current_page > 1 else "▫ Prev"
        next_label = (
            "Next ▶️" if current_page < total_pages else "Next ▫"
        )
        keyboard = [
            [
                InlineKeyboardButton(prev_label, callback_data=prev_cb),
                InlineKeyboardButton(
                    f"{current_page} / {total_pages}", callback_data="noop"
                ),
                InlineKeyboardButton(next_label, callback_data=next_cb),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4ca AI Accuracy", callback_data="cmd_ai_accuracy"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f504 Reconcile Trades", callback_data="cmd_reconcile"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f3e0 Back to Dashboard", callback_data="cmd_dashboard"
                )
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def build_message(trades, current_page, total_trades, wins, losses, net_pnl):
        """Build full message text and keyboard. Returns (text, reply_markup)."""
        total_pages = max(
            1,
            (total_trades + TradeHistoryFormatter.PAGE_SIZE - 1)
            // TradeHistoryFormatter.PAGE_SIZE,
        )
        lines = [
            bold(f"📜 TRADE HISTORY (Page {current_page}/{total_pages})"),
            "\n",
            "━" * 32,
            "\n",
        ]
        if not trades:
            lines.append("<i>No closed trades recorded yet.</i>\n")
        else:
            table_lines = [f"{'Symbol':<11} {'PnL':<8} {'Time':<5} {'Reason':<10}", "-" * 36]
            for t in trades:
                table_lines.append(TradeHistoryFormatter.format_trade(t))
            lines.append(pre("\n".join(table_lines)))
            lines.append("\n")

        lines.append("━" * 32)
        lines.append("\n")
        lines.append(bold("📊 Performance Summary"))
        lines.append("\n")

        total = wins + losses
        wr = (wins / max(total, 1)) * 100
        avg_pnl = net_pnl / max(total, 1)

        summary_block = (
            f"Trades    {wins}W / {losses}L (Total: {total})\n"
            f"Win Rate  {wr:.1f}%\n"
            f"Net PnL   {'+$' if net_pnl >= 0 else '-$'}{abs(net_pnl):,.2f} USD\n"
            f"Avg PnL   {'+$' if avg_pnl >= 0 else '-$'}{abs(avg_pnl):,.2f} USD"
        )
        lines.append(pre(summary_block))

        text = fmt(*lines)
        return text, TradeHistoryFormatter.build_keyboard(current_page, total_pages)

    @staticmethod
    def build_ai_accuracy_message(trades: list) -> str:
        """Build AI Confidence vs Outcome analysis from recent trades."""
        if not trades:
            return fmt(
                bold("🧠 AI ACCURACY ANALYSIS"),
                "\n",
                "━" * 32,
                "\n",
                pre("No trades recorded yet for AI accuracy analysis."),
            )

        # Categorize trades by confidence level
        high_conf = []  # >80
        med_conf = []   # 60-80
        low_conf = []   # <60

        for t in trades:
            conf = t.get("ai_confidence") if isinstance(t, dict) else getattr(t, "ai_confidence", None)
            if conf is None:
                conf = 75.0  # default fallback if confidence wasn't saved

            pnl_raw = float(t.get("pnl", 0)) if isinstance(t, dict) else float(getattr(t, "pnl", 0) or 0)
            entry_price = float(t.get("entry_price", 0)) if isinstance(t, dict) else float(getattr(t, "entry_price", 0) or 0)
            amount = float(t.get("amount", 0)) if isinstance(t, dict) else float(getattr(t, "amount", 0) or 0)
            cost = entry_price * amount
            pnl_pct = (pnl_raw / cost * 100) if cost > 0 else 0.0
            is_win = pnl_raw > 0

            trade_data = {
                "confidence": conf,
                "pnl": pnl_raw,
                "pnl_pct": pnl_pct,
                "is_win": is_win,
            }

            if conf > 80:
                high_conf.append(trade_data)
            elif conf >= 60:
                med_conf.append(trade_data)
            else:
                low_conf.append(trade_data)

        def _format_bucket_summary(label: str, trades_list: list) -> str:
            if not trades_list:
                return f"{label}\n  No trades in this bucket."
            total = len(trades_list)
            wins = sum(1 for t in trades_list if t["is_win"])
            wr = (wins / total * 100) if total > 0 else 0.0
            avg_pnl = sum(t["pnl"] for t in trades_list) / total
            total_pnl = sum(t["pnl"] for t in trades_list)
            return (
                f"{label}\n"
                f"  Trades  : {total}\n"
                f"  Win Rate: {wr:.1f}% ({wins}/{total})\n"
                f"  Avg PnL : ${avg_pnl:+,.2f} USD\n"
                f"  Net PnL : ${total_pnl:+,.2f} USD"
            )

        calibration_buckets = [
            ("90-100", lambda c: c >= 90),
            ("80-90 ", lambda c: 80 <= c < 90),
            ("70-80 ", lambda c: 70 <= c < 80),
            ("60-70 ", lambda c: 60 <= c < 70),
            ("<60   ", lambda c: c < 60),
        ]

        calibration_lines = []
        all_with_conf = []
        for t in trades:
            conf = t.get("ai_confidence") if isinstance(t, dict) else getattr(t, "ai_confidence", None)
            if conf is None:
                conf = 75.0
            pnl_raw = float(t.get("pnl", 0)) if isinstance(t, dict) else float(getattr(t, "pnl", 0) or 0)
            is_win = pnl_raw > 0
            all_with_conf.append({"confidence": conf, "is_win": is_win, "pnl": pnl_raw})

        for label, predicate in calibration_buckets:
            bucket_trades = [t for t in all_with_conf if predicate(t["confidence"])]
            if bucket_trades:
                total = len(bucket_trades)
                wins = sum(1 for t in bucket_trades if t["is_win"])
                wr = (wins / total * 100) if total > 0 else 0.0
                calibration_lines.append(f"  {label}: {total} trades, {wr:.0f}% WR")
            else:
                calibration_lines.append(f"  {label}: 0 trades, --% WR")

        calibration_str = "\n".join(calibration_lines)

        block_parts = [
            f"AI CONFIDENCE VS OUTCOME ({len(trades)} Trades)",
            "━" * 36,
            _format_bucket_summary("HIGH CONFIDENCE (>80)", high_conf),
            "",
            _format_bucket_summary("MEDIUM CONFIDENCE (60-80)", med_conf),
            "",
            _format_bucket_summary("LOW CONFIDENCE (<60)", low_conf),
            "",
            "CONFIDENCE CALIBRATION",
            calibration_str,
        ]

        unified_block = "\n".join(block_parts)

        return fmt(
            bold("🧠 AI CONFIDENCE & ACCURACY ANALYSIS"),
            "\n",
            "━" * 36,
            "\n\n",
            pre(unified_block),
        )

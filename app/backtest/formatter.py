"""Backtest Results Formatter — Telegram-ready reports from backtest data.

Renders backtest results as text with:
  - Summary metrics (trades, win rate, PnL, profit factor)
  - Regime breakdown
  - Equity curve (ASCII spark)
  - Recent trade list
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.backtest.orchestrator import BacktestJobStatus, BacktestTradeResult


@dataclass
class BacktestSummary:
    """Aggregated backtest metrics from trade results."""

    total_reports: int = 0
    trades_taken: int = 0
    trades_skipped: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_pnl: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    profit_factor: float = 0.0
    avg_bars_held: float = 0.0
    regime_counts: dict[str, int] = field(default_factory=dict)
    direction_counts: dict[str, int] = field(default_factory=dict)
    exit_reason_counts: dict[str, int] = field(default_factory=dict)


def compute_backtest_summary(results: list[BacktestTradeResult]) -> BacktestSummary:
    """Compute aggregate metrics from raw backtest results."""
    s = BacktestSummary()
    s.total_reports = len(results)

    taken = [r for r in results if r.trade_taken]
    s.trades_taken = len(taken)
    s.trades_skipped = s.total_reports - s.trades_taken

    if not taken:
        return s

    winners = [r for r in taken if r.pnl_net > Decimal("0")]
    losers = [r for r in taken if r.pnl_net < Decimal("0")]
    s.winning_trades = len(winners)
    s.losing_trades = len(losers)
    s.win_rate = (s.winning_trades / s.trades_taken) * 100

    s.gross_profit = sum((r.pnl_net for r in winners), Decimal("0"))
    s.gross_loss = abs(sum((r.pnl_net for r in losers), Decimal("0")))
    s.net_pnl = s.gross_profit - s.gross_loss
    s.profit_factor = (
        float(s.gross_profit / s.gross_loss)
        if s.gross_loss > 0
        else float("inf") if s.gross_profit > 0 else 0.0
    )

    total_bars = sum(r.bars_held for r in taken)
    s.avg_bars_held = total_bars / s.trades_taken

    s.regime_counts = dict(Counter(r.regime for r in taken if r.regime))
    s.direction_counts = dict(Counter(r.direction for r in taken))
    s.exit_reason_counts = dict(
        Counter(r.exit_reason for r in taken if r.exit_reason)
    )

    return s


def format_backtest_status(status: BacktestJobStatus) -> str:
    """Format a BacktestJobStatus as a brief status line."""
    emoji_map = {
        "pending": "⏳",
        "running": "\U0001f504",
        "completed": "✅",
        "failed": "❌",
    }
    emoji = emoji_map.get(status.status, "❓")
    parts = [f"{emoji} {status.status.upper()}"]
    if status.symbol:
        parts.append(status.symbol)
    if status.trades_taken:
        parts.append(f"{status.trades_taken} trades")
    if status.total_pnl and status.total_pnl != "0":
        parts.append(f"PnL: ${status.total_pnl}")
    if status.error:
        parts.append(f"Error: {status.error}")
    return " | ".join(parts)


def format_backtest_summary(
    results: list[BacktestTradeResult],
    job_id: str,
    status: BacktestJobStatus | None = None,
) -> str:
    """Render backtest results as Telegram-ready text.

    Shows summary metrics, regime breakdown, and recent trades.
    """
    s = compute_backtest_summary(results)

    lines: list[str] = []
    lines.append(f"Backtest Results — {job_id[:8]}")
    if status and status.symbol:
        lines.append(f"Symbol: {status.symbol}")
    lines.append("")

    # Summary
    lines.append("Summary")
    lines.append(
        f"  Reports: {s.total_reports}  |  "
        f"Trades: {s.trades_taken}  |  "
        f"Skipped: {s.trades_skipped}"
    )
    lines.append(
        f"  Wins: {s.winning_trades}  |  "
        f"Losses: {s.losing_trades}  |  "
        f"Win Rate: {s.win_rate:.1f}%"
    )
    lines.append(f"  Net PnL: ${float(s.net_pnl):>8.2f}")

    pf_str = (
        f"{s.profit_factor:.2f}"
        if s.profit_factor != float("inf")
        else "∞"
    )
    lines.append(f"  Profit Factor: {pf_str}  |  Avg Hold: {s.avg_bars_held:.0f} bars")
    lines.append("")

    # Regime breakdown
    if s.regime_counts:
        lines.append("Regime Breakdown")
        for regime, count in sorted(s.regime_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {regime}: {count}")
        lines.append("")

    # Direction breakdown
    if s.direction_counts:
        direction_str = "  ".join(
            f"{d}: {c}" for d, c in s.direction_counts.items()
        )
        lines.append(f"Directions: {direction_str}")
        lines.append("")

    # Exit reasons
    if s.exit_reason_counts:
        lines.append("Exit Reasons")
        for reason, count in sorted(
            s.exit_reason_counts.items(), key=lambda x: -x[1]
        ):
            lines.append(f"  {reason}: {count}")
        lines.append("")

    taken = [r for r in results if r.trade_taken]
    if taken:
        lines.extend(_format_recent_trades(taken[-5:]))
        lines.extend(_format_equity_spark(taken))

    return "\n".join(lines)


def _format_recent_trades(trades: list[BacktestTradeResult]) -> list[str]:
    """Format last N trades as a list of strings."""
    lines: list[str] = ["Recent Trades"]
    for r in trades:
        pnl_str = f"${float(r.pnl_net):>6.2f}"
        sign = "+" if r.pnl_net > 0 else "-" if r.pnl_net < 0 else "="
        entry_str = f"${float(r.entry_price):,.2f}" if r.entry_price else "?"
        lines.append(
            f"  {sign} {r.direction:>5} {r.symbol:>12} "
            f"entry={entry_str} pnl={pnl_str} "
            f"({r.exit_reason or 'n/a'}, {r.bars_held} bars)"
        )
    lines.append("")
    return lines


def _format_equity_spark(trades: list[BacktestTradeResult]) -> list[str]:
    """Mini ASCII equity spark chart from cumulative PnL."""
    cumul = Decimal("0")
    peaks: list[float] = []
    for r in trades:
        cumul += r.pnl_net
        peaks.append(float(cumul))
    if not peaks:
        return []
    min_val = min(peaks)
    max_val = max(peaks)
    rng = max_val - min_val if max_val != min_val else 1.0
    chars = "▁▂▃▄▅▆▇█"
    spark = ""
    for v in peaks:
        idx = int((v - min_val) / rng * (len(chars) - 1))
        spark += chars[max(0, min(idx, len(chars) - 1))]
    return [
        f"Equity: {spark}",
        f"  Low: ${min_val:>8.2f}  High: ${max_val:>8.2f}",
    ]


def format_bulk_backtest_summary(results: list[BacktestTradeResult], bulk_id: str, status: dict[str, Any]) -> str:
    """Render a telegram alert for a completed bulk backtest."""
    lines = [
        "🚀 <b>BULK BACKTEST COMPLETED</b>",
        f"<pre>Job ID : {bulk_id[:8]}",
        f"Symbols: {status.get('total', 0)}",
        f"Failed : {status.get('failed', 0)}</pre>",
        ""
    ]

    if not results:
        lines.append("No trades taken across all symbols.")
        return "\n".join(lines)

    s = compute_backtest_summary(results)

    lines.append("📊 <b>Aggregated Performance</b>")
    lines.append("<pre>")
    lines.append(f"Trades   : {s.trades_taken}")
    lines.append(f"Win Rate : {s.win_rate:.1f}% ({s.winning_trades}W / {s.losing_trades}L)")
    lines.append(f"Net PnL  : ${float(s.net_pnl):.2f}")

    pf_str = f"{s.profit_factor:.2f}" if s.profit_factor != float("inf") else "∞"
    lines.append(f"Prf Fctr : {pf_str}")
    lines.append(f"Avg Hold : {s.avg_bars_held:.0f} bars")
    lines.append("</pre>")

    return "\n".join(lines)


def format_backtest_list(jobs: list[BacktestJobStatus], active_bulk: dict[str, Any] | None = None) -> str:
    """Format a list of recent backtest jobs for display."""
    lines: list[str] = []

    if active_bulk:
        completed = active_bulk.get("completed", 0)
        total = active_bulk.get("total", 0)
        failed = active_bulk.get("failed", 0)
        pending = active_bulk.get("pending", 0)

        pct = (completed / total * 100) if total > 0 else 0
        bars = int(pct / 10)
        bar_str = "█" * bars + "░" * (10 - bars)

        lines.append("🚀 <b>ACTIVE BULK BACKTEST</b>")
        lines.append(f"<pre>Job: {active_bulk.get('bulk_id', '')[:8]}")
        lines.append(f"Progress: [{bar_str}] {pct:.1f}%")
        lines.append(f"Tested: {completed} | Pending: {pending} | Failed: {failed}</pre>")
        lines.append("")

    if not jobs:
        lines.append("No backtest jobs found.")
        return "\n".join(lines)

    lines.append("📜 <b>Recent Backtest Jobs</b>\n")
    lines.append(f"<pre>{'Job ID':<9} {'Status':<10} {'Symbol':<10} {'PnL':<9}")
    lines.append("-" * 42)

    for j in jobs:
        jid = j.job_id[:8]
        status_val = j.status.upper()[:9]
        symbol = (j.symbol or "N/A")[:9]

        pnl = "N/A"
        if j.total_pnl and j.total_pnl != "0":
            try:
                pnl = f"${float(j.total_pnl):.2f}"
            except ValueError:
                pnl = f"${j.total_pnl}"

        lines.append(f"{jid:<9} {status_val:<10} {symbol:<10} {pnl:<9}")

    lines.append("</pre>")
    body = "\n".join(lines)
    return f"<b>🔬 BACKTEST ORCHESTRATOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{body}"

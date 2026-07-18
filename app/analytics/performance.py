"""Performance Tracker — institutional-grade metrics from trade history.

Computes from closed trades (pnl_usdt NOT NULL):

  Sharpe Ratio (annualized, assuming 252 trading-day compounding)
  Sortino Ratio (downside deviation only)
  Max Drawdown (peak-to-trough equity decline)
  Calmar Ratio (annualized return / |max drawdown|)
  Win Rate (winning trades / total closed)
  Profit Factor (gross wins / gross losses)
  Average Win / Average Loss
  Expected Value (mean PnL per trade)
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from loguru import logger


@dataclass
class TradeRecord:
    """Normalized trade record used for performance computation.

    Fields aligned with both trades and shadow_trades schemas.
    """

    symbol: str
    side: str
    pnl: Decimal
    entry_time: datetime
    exit_time: datetime | None = None
    regime: str = ""
    exit_reason: str = ""
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")


@dataclass
class PerformanceReport:
    """Computed performance metrics snapshot."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    profit_factor: float = 0.0
    net_pnl: Decimal = Decimal("0")
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    avg_pnl: Decimal = Decimal("0")
    std_pnl: Decimal = Decimal("0")
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown: Decimal = Decimal("0")
    calmar_ratio: float = 0.0
    total_fees: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    equity_peak: Decimal = Decimal("0")
    pnl_series: list[Decimal] = field(default_factory=list)


def _compute_drawdown(trades: Sequence[TradeRecord]) -> tuple[list[Decimal], Decimal, Decimal, float]:
    """Compute equity curve and max drawdown from trade PnL series.

    Returns:
        (equity_series, equity_peak, max_drawdown_abs, max_drawdown_pct)
    """
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    max_dd_pct = 0.0
    pnl_values: list[Decimal] = []
    for t in trades:
        equity += t.pnl
        pnl_values.append(equity)
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = float(dd / peak) * 100 if peak else 0.0
    return pnl_values, peak, max_dd, max_dd_pct


def _std(values: list[Decimal]) -> float:
    """Population standard deviation of Decimal list as float."""
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(float(v) for v in values) / n
    variance = sum((float(v) - mean) ** 2 for v in values) / n
    return math.sqrt(variance) if variance > 0 else 0.0


def _risk_ratios(
    pnl_decimals: list[Decimal],
    mean_pnl: float,
    std_pnl: float,
    excess: float,
) -> tuple[float, float]:
    """Compute Sharpe and Sortino ratios from PnL series."""
    sharpe = (excess / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
    downside = [float(p) for p in pnl_decimals if float(p) < mean_pnl]
    down_var = sum((r - mean_pnl) ** 2 for r in downside)
    down_std = math.sqrt(down_var / len(downside)) if len(downside) and down_var > 0 else 0.0
    sortino = (excess / down_std) * math.sqrt(252) if down_std > 0 else 0.0
    return sharpe, sortino


def compute_performance(trades: Sequence[TradeRecord], risk_free_rate: float = 0.02) -> PerformanceReport:
    """Compute full performance report from a list of TradeRecord.

    Args:
        trades: Closed trade records (pnl is the realized PnL).
        risk_free_rate: Annual risk-free rate (default 2 %).

    Returns:
        PerformanceReport with all institutional metrics.
    """
    report = PerformanceReport()
    if not trades:
        return report

    # Separate winners / losers
    winners = [t for t in trades if t.pnl > Decimal("0")]
    losers = [t for t in trades if t.pnl < Decimal("0")]
    report.total_trades = len(trades)
    report.winning_trades = len(winners)
    report.losing_trades = len(losers)
    report.win_rate = (report.winning_trades / report.total_trades) * 100 if report.total_trades else 0.0

    # Gross profit / loss
    report.gross_profit = sum((t.pnl for t in winners), Decimal("0"))
    report.gross_loss = abs(sum((t.pnl for t in losers), Decimal("0")))
    report.profit_factor = (
        float(report.gross_profit / report.gross_loss) if report.gross_loss else float("inf")
        if report.gross_profit > 0 else 0.0
    )
    report.net_pnl = report.gross_profit - report.gross_loss

    # Averages
    report.avg_win = report.gross_profit / report.winning_trades if report.winning_trades else Decimal("0")
    report.avg_loss = report.gross_loss / report.losing_trades if report.losing_trades else Decimal("0")
    report.avg_pnl = report.net_pnl / report.total_trades if report.total_trades else Decimal("0")

    report.total_fees = sum((t.fees for t in trades), Decimal("0"))
    report.total_slippage = sum((t.slippage for t in trades), Decimal("0"))

    # Equity curve & drawdown via helper
    report.pnl_series, report.equity_peak, report.max_drawdown, report.max_drawdown_pct = _compute_drawdown(trades)

    # Risk-adjusted metrics via helper
    pnl_decimals = [t.pnl for t in trades]
    std_pnl_float = _std(pnl_decimals)
    report.std_pnl = Decimal(str(std_pnl_float))
    excess = float(report.avg_pnl) - (risk_free_rate / 252)
    report.sharpe_ratio, report.sortino_ratio = _risk_ratios(pnl_decimals, float(report.avg_pnl), std_pnl_float, excess)
    if report.max_drawdown_pct > 0:
        report.calmar_ratio = float(report.net_pnl) / (report.max_drawdown_pct / 100)

    return report


def format_performance_report(report: PerformanceReport) -> str:
    """Render a PerformanceReport as an HTML pre block for Telegram."""
    if report.total_trades == 0:
        return "No closed trades yet."

    sharpe_str = f"{report.sharpe_ratio:.2f}" if report.sharpe_ratio else "N/A"
    sortino_str = f"{report.sortino_ratio:.2f}" if report.sortino_ratio else "N/A"
    calmar_str = f"{report.calmar_ratio:.2f}" if report.calmar_ratio else "N/A"
    pf_str = f"{report.profit_factor:.2f}" if report.profit_factor and report.profit_factor != float("inf") else "∞"

    lines = [
        "Performance Summary",
        f"  Trades: {report.total_trades}  |  Wins: {report.winning_trades}  |  Losses: {report.losing_trades}",
        f"  Win Rate: {report.win_rate:.1f}%",
        f"  Net PnL: ${float(report.net_pnl):>8.2f}",
        "",
        "Risk-Adjusted Metrics",
        f"  Sharpe: {sharpe_str}  |  Sortino: {sortino_str}  |  Calmar: {calmar_str}",
        f"  Max Drawdown: ${float(report.max_drawdown):>8.2f}  ({report.max_drawdown_pct:.2f}%)",
        f"  Profit Factor: {pf_str}",
        "",
        "Averages",
        f"  Avg Win: ${float(report.avg_win):>8.2f}  |  Avg Loss: ${float(report.avg_loss):>8.2f}",
        f"  Avg PnL: ${float(report.avg_pnl):>8.2f}",
        "",
        "Costs",
        f"  Total Fees: ${float(report.total_fees):>8.2f}  |  Total Slippage: ${float(report.total_slippage):>8.2f}",
    ]
    return "\n".join(lines)


# Trade Store integration helpers


async def fetch_all_closed_trades(trade_store: object) -> list[TradeRecord]:
    """Pull all closed trades from TradeStore into normalized TradeRecords.

    Args:
        trade_store: TradeStore instance (duck-typed for testability).

    Returns:
        List of TradeRecord sorted by exit_time ascending.
    """
    from sqlalchemy import text

    trades: list[TradeRecord] = []
    try:
        async with trade_store.db.engine.connect() as conn:
            rows = await conn.execute(
                text("""
                    SELECT symbol, side, amount, entry_price, exit_price, pnl,
                           regime, entry_time, exit_time, exit_reason
                    FROM trades
                    WHERE exit_time IS NOT NULL AND pnl IS NOT NULL
                    ORDER BY exit_time ASC
                """)
            )
            for row in rows:
                trades.append(
                    TradeRecord(
                        symbol=row[0],
                        side=row[1],
                        pnl=Decimal(str(row[5])) if row[5] is not None else Decimal("0"),
                        entry_time=row[7],
                        exit_time=row[8],
                        regime=row[6] or "",
                        exit_reason=row[9] or "",
                        amount=Decimal(str(row[2])) if row[2] is not None else Decimal("0"),
                    )
                )
    except Exception as exc:
        logger.error("fetch_all_closed_trades failed: %s", exc)
    return trades


async def fetch_all_closed_shadow_trades(shadow_trade_store: object) -> list[TradeRecord]:
    """Pull all closed shadow trades into normalized TradeRecords.

    Args:
        shadow_trade_store: ShadowTradeStore instance.

    Returns:
        List of TradeRecord sorted by exit_time ascending.
    """
    from sqlalchemy import text

    trades: list[TradeRecord] = []
    try:
        async with shadow_trade_store.db.engine.connect() as conn:
            rows = await conn.execute(
                text("""
                    SELECT symbol, side, amount, entry_price, exit_price, pnl,
                           fees_applied, slippage_applied, entry_time, exit_time,
                           regime, exit_reason
                    FROM shadow_trades
                    WHERE exit_time IS NOT NULL AND pnl IS NOT NULL
                    ORDER BY exit_time ASC
                """)
            )
            for row in rows:
                trades.append(
                    TradeRecord(
                        symbol=row[0],
                        side=row[1],
                        pnl=Decimal(str(row[5])) if row[5] is not None else Decimal("0"),
                        entry_time=row[8],
                        exit_time=row[9],
                        regime=row[10] or "",
                        exit_reason=row[11] or "",
                        fees=Decimal(str(row[6])) if row[6] is not None else Decimal("0"),
                        slippage=Decimal(str(row[7])) if row[7] is not None else Decimal("0"),
                        amount=Decimal(str(row[2])) if row[2] is not None else Decimal("0"),
                    )
                )
    except Exception as exc:
        logger.error("fetch_all_closed_shadow_trades failed: %s", exc)
    return trades

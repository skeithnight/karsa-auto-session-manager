"""Shadow-vs-Live Reconciliation — execution quality and parity report.

Compares shadow trades (virtual execution) against live trades (real execution)
to measure:
  - Execution slippage (shadow vs live entry/exit prices)
  - PnL parity (shadow PnL vs live PnL per symbol)
  - Latency penalty (extra slippage in live vs shadow)
  - Fee asymmetry (live fees vs shadow fees)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class ReconciliationEntry:
    """Matched pair of live + shadow trade for comparison.

    Aligns on (symbol, side, entry_time within 5-minute window).
    """

    symbol: str
    side: str
    live_entry_price: Decimal
    shadow_entry_price: Decimal
    live_exit_price: Decimal
    shadow_exit_price: Decimal
    live_pnl: Decimal
    shadow_pnl: Decimal
    live_fees: Decimal
    shadow_fees: Decimal
    live_slippage: Decimal
    shadow_slippage: Decimal
    live_entry_time: datetime
    shadow_entry_time: datetime
    entry_price_delta: Decimal = Decimal("0")
    exit_price_delta: Decimal = Decimal("0")
    pnl_delta: Decimal = Decimal("0")
    execution_penalty: Decimal = Decimal("0")


@dataclass
class ReconciliationReport:
    """Aggregate reconciliation metrics."""

    total_pairs: int = 0
    matched_pairs: list[ReconciliationEntry] = None
    avg_entry_slippage: Decimal = Decimal("0")
    avg_exit_slippage: Decimal = Decimal("0")
    avg_pnl_delta: Decimal = Decimal("0")
    total_live_pnl: Decimal = Decimal("0")
    total_shadow_pnl: Decimal = Decimal("0")
    total_pnl_penalty: Decimal = Decimal("0")
    avg_execution_penalty: Decimal = Decimal("0")
    fee_asymmetry: Decimal = Decimal("0")
    slippage_asymmetry: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.matched_pairs is None:
            self.matched_pairs = []


def match_trades(
    live_trades: Sequence[dict],
    shadow_trades: Sequence[dict],
    time_window_seconds: int = 300,
) -> list[ReconciliationEntry]:
    """Match live trades to shadow trades by (symbol, side, entry_time window).

    Args:
        live_trades: Live trade dicts with keys:
            symbol, side, entry_price, exit_price, pnl, fees, slippage, entry_time
        shadow_trades: Shadow trade dicts with same keys.
        time_window_seconds: Max time gap for matching (default 5 min).

    Returns:
        List of matched ReconciliationEntry.
    """
    matched: list[ReconciliationEntry] = []
    shadow_pool = list(shadow_trades)

    for live in live_trades:
        best_match: dict | None = None
        best_delta: float = float("inf")

        for shadow in shadow_pool:
            if live.get("symbol") != shadow.get("symbol"):
                continue
            if live.get("side") != shadow.get("side"):
                continue

            live_time = live.get("entry_time")
            shadow_time = shadow.get("entry_time")
            if live_time is None or shadow_time is None:
                continue

            live_ts = live_time.timestamp() if isinstance(live_time, datetime) else float(live_time)

            shadow_ts = shadow_time.timestamp() if isinstance(shadow_time, datetime) else float(shadow_time)

            delta = abs(live_ts - shadow_ts)
            if delta <= time_window_seconds and delta < best_delta:
                best_delta = delta
                best_match = shadow

        if best_match is None:
            continue

        shadow_pool.remove(best_match)

        entry_delta = Decimal(str(live.get("entry_price", 0))) - Decimal(str(best_match.get("entry_price", 0)))
        exit_delta = Decimal(str(live.get("exit_price", 0))) - Decimal(str(best_match.get("exit_price", 0)))
        pnl_delta = Decimal(str(live.get("pnl", 0))) - Decimal(str(best_match.get("pnl", 0)))
        exec_penalty = abs(entry_delta) + abs(exit_delta)

        matched.append(
            ReconciliationEntry(
                symbol=live["symbol"],
                side=live["side"],
                live_entry_price=Decimal(str(live.get("entry_price", 0))),
                shadow_entry_price=Decimal(str(best_match.get("entry_price", 0))),
                live_exit_price=Decimal(str(live.get("exit_price", 0))),
                shadow_exit_price=Decimal(str(best_match.get("exit_price", 0))),
                live_pnl=Decimal(str(live.get("pnl", 0))),
                shadow_pnl=Decimal(str(best_match.get("pnl", 0))),
                live_fees=Decimal(str(live.get("fees", 0))),
                shadow_fees=Decimal(str(best_match.get("fees", 0))),
                live_slippage=Decimal(str(live.get("slippage", 0))),
                shadow_slippage=Decimal(str(best_match.get("slippage", 0))),
                live_entry_time=live.get("entry_time", datetime.now(UTC)),
                shadow_entry_time=best_match.get("entry_time", datetime.now(UTC)),
                entry_price_delta=entry_delta,
                exit_price_delta=exit_delta,
                pnl_delta=pnl_delta,
                execution_penalty=exec_penalty,
            )
        )

    return matched


def compute_reconciliation(matched: list[ReconciliationEntry]) -> ReconciliationReport:
    """Compute aggregate reconciliation metrics from matched trade pairs.

    Args:
        matched: List of matched live+shadow trade pairs.

    Returns:
        ReconciliationReport with aggregate quality metrics.
    """
    report = ReconciliationReport()
    report.matched_pairs = matched
    report.total_pairs = len(matched)

    if not matched:
        return report

    report.avg_entry_slippage = sum((e.entry_price_delta for e in matched), Decimal("0")) / len(matched)
    report.avg_exit_slippage = sum((e.exit_price_delta for e in matched), Decimal("0")) / len(matched)
    report.avg_pnl_delta = sum((e.pnl_delta for e in matched), Decimal("0")) / len(matched)

    report.total_live_pnl = sum((e.live_pnl for e in matched), Decimal("0"))
    report.total_shadow_pnl = sum((e.shadow_pnl for e in matched), Decimal("0"))
    report.total_pnl_penalty = report.total_live_pnl - report.total_shadow_pnl

    report.avg_execution_penalty = sum((e.execution_penalty for e in matched), Decimal("0")) / len(matched)

    report.fee_asymmetry = sum((e.live_fees - e.shadow_fees for e in matched), Decimal("0"))
    report.slippage_asymmetry = sum((e.live_slippage - e.shadow_slippage for e in matched), Decimal("0"))

    return report


def format_reconciliation_report(report: ReconciliationReport) -> str:
    """Render a ReconciliationReport as text for Telegram."""
    if report.total_pairs == 0:
        return "No matched trade pairs found."

    lines = [
        "Reconciliation Report",
        f"  Matched Pairs: {report.total_pairs}",
        "",
        f"Avg Entry Slippage: ${float(report.avg_entry_slippage):>8.4f}",
        f"Avg Exit Slippage:  ${float(report.avg_exit_slippage):>8.4f}",
        f"Avg Execution Penalty: ${float(report.avg_execution_penalty):>8.4f}",
        "",
        "PnL Parity",
        f"  Live PnL:   ${float(report.total_live_pnl):>8.2f}",
        f"  Shadow PnL: ${float(report.total_shadow_pnl):>8.2f}",
        f"  Penalty:    ${float(report.total_pnl_penalty):>8.2f}",
        "",
        f"Fee Asymmetry:      ${float(report.fee_asymmetry):>8.4f}",
        f"Slippage Asymmetry: ${float(report.slippage_asymmetry):>8.4f}",
    ]
    return "\n".join(lines)

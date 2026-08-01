"""Tests for app.analytics.performance and app.analytics.reconciliation."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.analytics.performance import (
    PerformanceReport,
    TradeRecord,
    compute_performance,
    format_performance_report,
)
from app.analytics.reconciliation import (
    ReconciliationEntry,
    compute_reconciliation,
    format_reconciliation_report,
    match_trades,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(y: int = 2024, m: int = 1, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def _winning_trade(pnl: float = 10.0) -> TradeRecord:
    return TradeRecord(
        symbol="BTC/USDT", side="LONG", pnl=Decimal(str(pnl)), entry_time=_ts(), exit_time=_ts()
    )


def _losing_trade(pnl: float = -5.0) -> TradeRecord:
    return TradeRecord(
        symbol="BTC/USDT", side="SHORT", pnl=Decimal(str(pnl)), entry_time=_ts(), exit_time=_ts()
    )


# ---------------------------------------------------------------------------
# PerformanceReport defaults
# ---------------------------------------------------------------------------


class TestPerformanceReport:
    def test_defaults(self) -> None:
        r = PerformanceReport()
        assert r.total_trades == 0
        assert r.win_rate == 0.0
        assert r.profit_factor == 0.0


# ---------------------------------------------------------------------------
# compute_performance
# ---------------------------------------------------------------------------


class TestComputePerformance:
    def test_empty(self) -> None:
        r = compute_performance([])
        assert r.total_trades == 0
        assert r.win_rate == 0.0

    def test_single_winning_trade(self) -> None:
        trades = [_winning_trade(10.0)]
        r = compute_performance(trades)
        assert r.total_trades == 1
        assert r.winning_trades == 1
        assert r.losing_trades == 0
        assert r.win_rate == 100.0
        assert r.gross_profit == Decimal("10")
        assert r.gross_loss == Decimal("0")
        assert r.net_pnl == Decimal("10")
        assert r.profit_factor == float("inf")

    def test_single_losing_trade(self) -> None:
        trades = [_losing_trade(-5.0)]
        r = compute_performance(trades)
        assert r.total_trades == 1
        assert r.winning_trades == 0
        assert r.losing_trades == 1
        assert r.win_rate == 0.0
        assert r.gross_profit == Decimal("0")
        assert r.gross_loss == Decimal("5")
        assert r.net_pnl == Decimal("-5")

    def test_mixed_trades_win_rate(self) -> None:
        trades = [_winning_trade(10), _winning_trade(20), _losing_trade(-5)]
        r = compute_performance(trades)
        assert r.win_rate == pytest.approx(200 / 3)
        assert r.gross_profit == Decimal("30")
        assert r.gross_loss == Decimal("5")
        assert r.profit_factor == pytest.approx(6.0)

    def test_max_drawdown_from_equity_curve(self) -> None:
        # equity: 0 -> 10 -> 5 -> -5 -> 0. peak=10, trough=-5, max_dd=15
        trades = [
            _winning_trade(10),
            _losing_trade(-5),
            _losing_trade(-10),
            _winning_trade(5),
        ]
        r = compute_performance(trades)
        assert r.max_drawdown == Decimal("15")
        assert r.max_drawdown_pct == pytest.approx(150.0)

    def test_std_pnl_positive(self) -> None:
        trades = [_winning_trade(10), _losing_trade(-10)]
        r = compute_performance(trades)
        assert float(r.std_pnl) > 0

    def test_all_same_sharpe_zero(self) -> None:
        # All identical PnL → std_pnl=0 → Sharpe undefined → 0
        trades = [_winning_trade(10) for _ in range(5)]
        r = compute_performance(trades)
        assert r.sharpe_ratio == 0.0

    def test_all_losers_sortino_zero(self) -> None:
        trades = [_losing_trade(-10) for _ in range(5)]
        r = compute_performance(trades)
        assert r.sortino_ratio == 0.0

    def test_calmar_ratio(self) -> None:
        trades = [
            _winning_trade(20),
            _losing_trade(-5),
            _winning_trade(10),
        ]
        r = compute_performance(trades)
        # equity: 0->20->15->25. peak=20, trough=15, max_dd=5, dd_pct=25%
        assert r.max_drawdown == Decimal("5")
        # calmar = net_pnl(25) / (max_dd_pct(25)/100) = 25/0.25 = 100
        assert r.calmar_ratio == pytest.approx(100.0)

    def test_profit_factor_zero_when_no_trades(self) -> None:
        r = compute_performance([])
        assert r.profit_factor == 0.0

    def test_pnl_series_length(self) -> None:
        trades = [_winning_trade(5), _losing_trade(-2), _winning_trade(3)]
        r = compute_performance(trades)
        assert len(r.pnl_series) == 3
        assert r.pnl_series[0] == Decimal("5")
        assert r.pnl_series[1] == Decimal("3")
        assert r.pnl_series[2] == Decimal("6")

    def test_fees_and_slippage_totals(self) -> None:
        t1 = TradeRecord(symbol="BTC", side="LONG", pnl=Decimal("10"), entry_time=_ts(), fees=Decimal("2"), slippage=Decimal("1"))
        t2 = TradeRecord(symbol="BTC", side="LONG", pnl=Decimal("-5"), entry_time=_ts(), fees=Decimal("1"), slippage=Decimal("0.5"))
        r = compute_performance([t1, t2])
        assert r.total_fees == Decimal("3")
        assert r.total_slippage == Decimal("1.5")


# ---------------------------------------------------------------------------
# format_performance_report
# ---------------------------------------------------------------------------


class TestFormatPerformanceReport:
    def test_empty(self) -> None:
        r = compute_performance([])
        text = format_performance_report(r)
        assert "No closed trades" in text

    def test_nonempty_contains_key_sections(self) -> None:
        trades = [_winning_trade(10), _losing_trade(-5)]
        r = compute_performance(trades)
        text = format_performance_report(r)
        assert "Performance Summary" in text
        assert "Win Rate" in text
        assert "Sharpe" in text
        assert "Profit Factor" in text
        assert "Total Fees" in text


# ---------------------------------------------------------------------------
# match_trades
# ---------------------------------------------------------------------------


class TestMatchTrades:
    def test_basic_match(self) -> None:
        live = [
            {
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 50000, "exit_price": 51000, "pnl": 100,
                "fees": 5, "slippage": 2, "entry_time": _ts(),
            },
        ]
        shadow = [
            {
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 50001, "exit_price": 51002, "pnl": 98,
                "fees": 4, "slippage": 1, "entry_time": _ts(),
            },
        ]
        matched = match_trades(live, shadow)
        assert len(matched) == 1
        assert matched[0].entry_price_delta == Decimal("-1")
        assert matched[0].exit_price_delta == Decimal("-2")
        assert matched[0].pnl_delta == Decimal("2")

    def test_no_match_different_symbol(self) -> None:
        live = [{"symbol": "BTC/USDT", "side": "LONG", "entry_time": _ts()}]
        shadow = [{"symbol": "ETH/USDT", "side": "LONG", "entry_time": _ts()}]
        assert len(match_trades(live, shadow)) == 0

    def test_no_match_outside_window(self) -> None:
        from datetime import timedelta

        live = [{"symbol": "BTC/USDT", "side": "LONG", "entry_time": _ts()}]
        shadow = [
            {"symbol": "BTC/USDT", "side": "LONG", "entry_time": _ts() + timedelta(seconds=600)},
        ]
        assert len(match_trades(live, shadow, time_window_seconds=300)) == 0

    def test_match_within_window(self) -> None:
        from datetime import timedelta

        live = [
            {
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 100, "exit_price": 110, "pnl": 10,
                "fees": 1, "slippage": 0.5, "entry_time": _ts(),
            },
        ]
        shadow = [
            {
                "symbol": "BTC/USDT", "side": "LONG",
                "entry_price": 101, "exit_price": 111, "pnl": 9,
                "fees": 0.8, "slippage": 0.3,
                "entry_time": _ts() + timedelta(seconds=200),
            },
        ]
        matched = match_trades(live, shadow, time_window_seconds=300)
        assert len(matched) == 1

    def test_multiple_trades_greedy_match(self) -> None:
        from datetime import timedelta

        live = [
            {
                "symbol": "BTC", "side": "LONG",
                "entry_price": 100, "exit_price": 110, "pnl": 10,
                "fees": 1, "slippage": 0, "entry_time": _ts(),
            },
            {
                "symbol": "BTC", "side": "LONG",
                "entry_price": 200, "exit_price": 210, "pnl": 10,
                "fees": 1, "slippage": 0,
                "entry_time": _ts() + timedelta(hours=1),
            },
        ]
        shadow = [
            {
                "symbol": "BTC", "side": "LONG",
                "entry_price": 101, "exit_price": 111, "pnl": 9,
                "fees": 1, "slippage": 0,
                "entry_time": _ts() + timedelta(seconds=10),
            },
            {
                "symbol": "BTC", "side": "LONG",
                "entry_price": 201, "exit_price": 211, "pnl": 9,
                "fees": 1, "slippage": 0,
                "entry_time": _ts() + timedelta(hours=1, seconds=10),
            },
        ]
        matched = match_trades(live, shadow)
        assert len(matched) == 2


# ---------------------------------------------------------------------------
# compute_reconciliation
# ---------------------------------------------------------------------------


class TestComputeReconciliation:
    def test_empty(self) -> None:
        r = compute_reconciliation([])
        assert r.total_pairs == 0

    def test_single_pair(self) -> None:
        entry = ReconciliationEntry(
            symbol="BTC", side="LONG",
            live_entry_price=Decimal("50000"), shadow_entry_price=Decimal("50001"),
            live_exit_price=Decimal("51000"), shadow_exit_price=Decimal("51002"),
            live_pnl=Decimal("100"), shadow_pnl=Decimal("98"),
            live_fees=Decimal("5"), shadow_fees=Decimal("4"),
            live_slippage=Decimal("2"), shadow_slippage=Decimal("1"),
            live_entry_time=_ts(), shadow_entry_time=_ts(),
            entry_price_delta=Decimal("-1"), exit_price_delta=Decimal("-2"),
            pnl_delta=Decimal("2"), execution_penalty=Decimal("3"),
        )
        r = compute_reconciliation([entry])
        assert r.total_pairs == 1
        assert r.avg_entry_slippage == Decimal("-1")
        assert r.total_pnl_penalty == Decimal("2")
        assert r.fee_asymmetry == Decimal("1")
        assert r.slippage_asymmetry == Decimal("1")

    def test_multiple_pairs(self) -> None:
        e1 = ReconciliationEntry(
            symbol="BTC", side="LONG",
            live_entry_price=Decimal("100"), shadow_entry_price=Decimal("101"),
            live_exit_price=Decimal("110"), shadow_exit_price=Decimal("111"),
            live_pnl=Decimal("10"), shadow_pnl=Decimal("9"),
            live_fees=Decimal("1"), shadow_fees=Decimal("0.5"),
            live_slippage=Decimal("0.5"), shadow_slippage=Decimal("0.2"),
            live_entry_time=_ts(), shadow_entry_time=_ts(),
            entry_price_delta=Decimal("-1"), exit_price_delta=Decimal("-1"),
            pnl_delta=Decimal("1"), execution_penalty=Decimal("2"),
        )
        e2 = ReconciliationEntry(
            symbol="ETH", side="SHORT",
            live_entry_price=Decimal("2000"), shadow_entry_price=Decimal("2002"),
            live_exit_price=Decimal("1980"), shadow_exit_price=Decimal("1982"),
            live_pnl=Decimal("20"), shadow_pnl=Decimal("18"),
            live_fees=Decimal("2"), shadow_fees=Decimal("1.5"),
            live_slippage=Decimal("1"), shadow_slippage=Decimal("0.8"),
            live_entry_time=_ts(), shadow_entry_time=_ts(),
            entry_price_delta=Decimal("-2"), exit_price_delta=Decimal("-2"),
            pnl_delta=Decimal("2"), execution_penalty=Decimal("4"),
        )
        r = compute_reconciliation([e1, e2])
        assert r.total_pairs == 2
        assert r.avg_entry_slippage == Decimal("-1.5")
        assert r.total_live_pnl == Decimal("30")
        assert r.total_shadow_pnl == Decimal("27")


# ---------------------------------------------------------------------------
# format_reconciliation_report
# ---------------------------------------------------------------------------


class TestFormatReconciliationReport:
    def test_empty(self) -> None:
        r = compute_reconciliation([])
        text = format_reconciliation_report(r)
        assert "No matched" in text

    def test_nonempty(self) -> None:
        entry = ReconciliationEntry(
            symbol="BTC", side="LONG",
            live_entry_price=Decimal("100"), shadow_entry_price=Decimal("101"),
            live_exit_price=Decimal("110"), shadow_exit_price=Decimal("111"),
            live_pnl=Decimal("10"), shadow_pnl=Decimal("9"),
            live_fees=Decimal("1"), shadow_fees=Decimal("0.5"),
            live_slippage=Decimal("0.5"), shadow_slippage=Decimal("0.2"),
            live_entry_time=_ts(), shadow_entry_time=_ts(),
            entry_price_delta=Decimal("-1"), exit_price_delta=Decimal("-1"),
            pnl_delta=Decimal("1"), execution_penalty=Decimal("2"),
        )
        r = compute_reconciliation([entry])
        text = format_reconciliation_report(r)
        assert "Matched Pairs" in text
        assert "PnL Parity" in text
        assert "Fee Asymmetry" in text

"""Tests for app.backtest.hybrid_report — hybrid intelligence metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.backtest.hybrid_report import (
    FeatureImpactAnalysis,
    GuardrailEffectiveness,
    HybridBacktestReport,
    HybridReport,
    MonthlyReturns,
    AIPerformanceMetrics,
    format_hybrid_report,
)
from app.backtest.orchestrator import BacktestTradeResult


NOW = datetime.now(timezone.utc)


def _make_result(
    pnl_net: float = 10.0,
    ai_confidence: float | None = None,
    beta: float | None = None,
    correlation: float | None = None,
    volume_spike: float | None = None,
    guardrail_type: str | None = None,
    guardrail_action: str | None = None,
    monthly_bucket: str | None = None,
    trade_taken: bool = True,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        job_id="job-123",
        symbol="BTC/USDT",
        direction="LONG",
        regime="TREND_BULL",
        score=72.5,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        exit_reason="tp_hit",
        sl_price=Decimal("49000"),
        tp_price=Decimal("52000"),
        amount=Decimal("0.01"),
        size_multiplier=Decimal("1"),
        pnl_gross=Decimal(str(pnl_net)),
        pnl_net=Decimal(str(pnl_net)),
        total_fees=Decimal("2.5"),
        total_funding=Decimal("0.5"),
        bars_held=24,
        entry_time=NOW,
        exit_time=NOW,
        trade_taken=trade_taken,
        ai_confidence=ai_confidence,
        beta=beta,
        correlation=correlation,
        volume_spike=volume_spike,
        guardrail_type=guardrail_type,
        guardrail_action=guardrail_action,
        monthly_bucket=monthly_bucket,
    )


# ---------------------------------------------------------------------------
# HybridBacktestReport
# ---------------------------------------------------------------------------


class TestHybridBacktestReport:
    def test_empty_results(self) -> None:
        reporter = HybridBacktestReport()
        report = reporter.generate_report([])
        assert report.total_trades == 0
        assert report.winning_trades == 0
        assert report.win_rate == 0.0

    def test_all_skipped(self) -> None:
        results = [_make_result(trade_taken=False) for _ in range(5)]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.total_trades == 0

    def test_basic_metrics(self) -> None:
        results = [
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=-5.0),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.total_trades == 3
        assert report.winning_trades == 2
        assert report.win_rate == pytest.approx(66.67, rel=1e-2)

    def test_sharpe_ratio(self) -> None:
        results = [_make_result(pnl_net=float(i)) for i in range(10)]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.sharpe_ratio > 0

    def test_max_drawdown(self) -> None:
        results = [
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=-5.0),
            _make_result(pnl_net=-3.0),
            _make_result(pnl_net=8.0),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.max_drawdown_pct >= 0


# ---------------------------------------------------------------------------
# AI Performance
# ---------------------------------------------------------------------------


class TestAIPerformance:
    def test_no_ai_data(self) -> None:
        results = [_make_result(pnl_net=10.0)]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.ai_performance.total_evaluations == 0

    def test_ai_confidence_average(self) -> None:
        results = [
            _make_result(pnl_net=10.0, ai_confidence=80.0),
            _make_result(pnl_net=-5.0, ai_confidence=60.0),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.ai_performance.total_evaluations == 2
        assert report.ai_performance.avg_confidence == 70.0

    def test_high_confidence_win_rate(self) -> None:
        results = [
            _make_result(pnl_net=10.0, ai_confidence=80.0),
            _make_result(pnl_net=10.0, ai_confidence=85.0),
            _make_result(pnl_net=-5.0, ai_confidence=75.0),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.ai_performance.high_confidence_win_rate == pytest.approx(
            66.67, rel=1e-2
        )

    def test_low_confidence_win_rate(self) -> None:
        results = [
            _make_result(pnl_net=10.0, ai_confidence=30.0),
            _make_result(pnl_net=-5.0, ai_confidence=35.0),
            _make_result(pnl_net=-3.0, ai_confidence=25.0),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.ai_performance.low_confidence_win_rate == pytest.approx(
            33.33, rel=1e-2
        )

    def test_ai_cost_estimate(self) -> None:
        results = [_make_result(pnl_net=10.0, ai_confidence=75.0) for _ in range(10)]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.ai_performance.ai_cost_estimate == Decimal("0.20")


# ---------------------------------------------------------------------------
# Guardrail Effectiveness
# ---------------------------------------------------------------------------


class TestGuardrailEffectiveness:
    def test_no_guardrails(self) -> None:
        results = [_make_result(pnl_net=10.0)]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.guardrail_effectiveness.hard_guardrails_triggered == 0
        assert report.guardrail_effectiveness.soft_guardrails_triggered == 0

    def test_hard_guardrails_blocked(self) -> None:
        results = [
            _make_result(
                pnl_net=-5.0, guardrail_type="hard", guardrail_action="blocked"
            ),
            _make_result(
                pnl_net=-3.0, guardrail_type="hard", guardrail_action="blocked"
            ),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.guardrail_effectiveness.hard_guardrails_triggered == 2
        assert report.guardrail_effectiveness.trades_blocked == 2
        assert report.guardrail_effectiveness.losses_prevented == Decimal("8")

    def test_soft_guardrails_downgrade(self) -> None:
        results = [
            _make_result(
                pnl_net=5.0, guardrail_type="soft", guardrail_action="downgrade"
            ),
            _make_result(
                pnl_net=3.0, guardrail_type="soft", guardrail_action="downgrade"
            ),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.guardrail_effectiveness.soft_guardrails_triggered == 2
        assert report.guardrail_effectiveness.position_downgrades == 2


# ---------------------------------------------------------------------------
# Feature Impact Analysis
# ---------------------------------------------------------------------------


class TestFeatureImpactAnalysis:
    def test_high_beta(self) -> None:
        results = [
            _make_result(pnl_net=10.0, beta=1.8),
            _make_result(pnl_net=-5.0, beta=2.0),
            _make_result(pnl_net=8.0, beta=1.6),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.feature_impact.high_beta_count == 3
        assert report.feature_impact.high_beta_win_rate == pytest.approx(
            66.67, rel=1e-2
        )

    def test_low_beta(self) -> None:
        results = [
            _make_result(pnl_net=10.0, beta=0.5),
            _make_result(pnl_net=10.0, beta=0.7),
            _make_result(pnl_net=-5.0, beta=0.6),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.feature_impact.low_beta_count == 3
        assert report.feature_impact.low_beta_win_rate == pytest.approx(
            66.67, rel=1e-2
        )

    def test_high_correlation(self) -> None:
        results = [
            _make_result(pnl_net=10.0, correlation=0.9),
            _make_result(pnl_net=-5.0, correlation=0.85),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.feature_impact.high_correlation_count == 2
        assert report.feature_impact.high_correlation_win_rate == 50.0

    def test_volume_confirmed(self) -> None:
        results = [
            _make_result(pnl_net=10.0, volume_spike=2.0),
            _make_result(pnl_net=10.0, volume_spike=1.8),
            _make_result(pnl_net=-5.0, volume_spike=2.5),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert report.feature_impact.volume_confirmed_count == 3
        assert report.feature_impact.volume_confirmed_win_rate == pytest.approx(
            66.67, rel=1e-2
        )


# ---------------------------------------------------------------------------
# Monthly Returns
# ---------------------------------------------------------------------------


class TestMonthlyReturns:
    def test_monthly_bucket(self) -> None:
        results = [
            _make_result(pnl_net=10.0, monthly_bucket="2024-01"),
            _make_result(pnl_net=-5.0, monthly_bucket="2024-01"),
            _make_result(pnl_net=8.0, monthly_bucket="2024-02"),
        ]
        reporter = HybridBacktestReport()
        report = reporter.generate_report(results)
        assert "2024-01" in report.monthly_returns.monthly_data
        assert "2024-02" in report.monthly_returns.monthly_data
        assert report.monthly_returns.monthly_data["2024-01"] == Decimal("5")
        assert report.monthly_returns.monthly_data["2024-02"] == Decimal("8")

    def test_auto_bucket_from_entry_time(self) -> None:
        entry_time = datetime(2024, 3, 15, tzinfo=timezone.utc)
        result = BacktestTradeResult(
            job_id="job-123",
            symbol="BTC/USDT",
            direction="LONG",
            regime="TREND_BULL",
            score=72.5,
            entry_price=Decimal("50000"),
            exit_price=Decimal("51000"),
            exit_reason="tp_hit",
            sl_price=None,
            tp_price=None,
            amount=Decimal("0.01"),
            size_multiplier=Decimal("1"),
            pnl_gross=Decimal("10"),
            pnl_net=Decimal("10"),
            total_fees=Decimal("2.5"),
            total_funding=Decimal("0.5"),
            bars_held=24,
            entry_time=entry_time,
            exit_time=entry_time,
            trade_taken=True,
        )
        reporter = HybridBacktestReport()
        report = reporter.generate_report([result])
        assert "2024-03" in report.monthly_returns.monthly_data


# ---------------------------------------------------------------------------
# format_hybrid_report
# ---------------------------------------------------------------------------


class TestFormatHybridReport:
    def test_empty_report(self) -> None:
        report = HybridReport()
        text = format_hybrid_report(report, "job-abc")
        assert "Backtest Results" in text
        assert "Total Trades: 0" in text

    def test_with_ai_metrics(self) -> None:
        report = HybridReport(
            total_trades=100,
            winning_trades=60,
            win_rate=60.0,
            total_return_pct=15.0,
            sharpe_ratio=1.5,
            max_drawdown_pct=8.0,
            ai_performance=AIPerformanceMetrics(
                total_evaluations=100,
                avg_confidence=72.0,
                high_confidence_win_rate=70.0,
                low_confidence_win_rate=45.0,
                ai_contribution_pnl=Decimal("500"),
                ai_cost_estimate=Decimal("2.00"),
            ),
        )
        text = format_hybrid_report(report, "job-abc")
        assert "AI Performance" in text
        assert "Total Evaluations: 100" in text
        assert "Avg Confidence: 72/100" in text

    def test_with_guardrails(self) -> None:
        report = HybridReport(
            total_trades=100,
            winning_trades=60,
            win_rate=60.0,
            guardrail_effectiveness=GuardrailEffectiveness(
                hard_guardrails_triggered=50,
                trades_blocked=30,
                losses_prevented=Decimal("1500"),
                soft_guardrails_triggered=100,
                position_downgrades=80,
            ),
        )
        text = format_hybrid_report(report, "job-abc")
        assert "Guardrail Effectiveness" in text
        assert "Hard Guardrails Triggered: 50" in text
        assert "Trades Blocked: 30" in text

    def test_with_feature_impact(self) -> None:
        report = HybridReport(
            total_trades=100,
            winning_trades=60,
            win_rate=60.0,
            feature_impact=FeatureImpactAnalysis(
                high_beta_count=20,
                high_beta_win_rate=55.0,
                low_beta_count=40,
                low_beta_win_rate=65.0,
                high_correlation_count=15,
                high_correlation_win_rate=50.0,
                volume_confirmed_count=60,
                volume_confirmed_win_rate=62.0,
            ),
        )
        text = format_hybrid_report(report, "job-abc")
        assert "Feature Impact Analysis" in text
        assert "High Beta (>1.5) Trades: 20" in text
        assert "Volume Confirmed Trades: 60" in text

    def test_with_monthly_returns(self) -> None:
        report = HybridReport(
            total_trades=100,
            winning_trades=60,
            win_rate=60.0,
            monthly_returns=MonthlyReturns(
                monthly_data={
                    "2024-01": Decimal("5.2"),
                    "2024-02": Decimal("3.8"),
                    "2024-03": Decimal("-2.1"),
                }
            ),
        )
        text = format_hybrid_report(report, "job-abc")
        assert "Monthly Returns" in text

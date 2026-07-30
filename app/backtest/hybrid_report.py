"""Hybrid Intelligence Backtest Report — AI + Guardrail + Feature Impact metrics.

Generates comprehensive hybrid intelligence reports showing:
  - AI performance metrics (confidence, win rates by confidence level)
  - Guardrail effectiveness (hard/soft guardrails, trades blocked)
  - Feature impact analysis (beta, correlation, volume spike)
  - Monthly returns breakdown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.backtest.orchestrator import BacktestTradeResult


@dataclass
class AIPerformanceMetrics:
    """AI-specific performance metrics from backtest results."""

    total_evaluations: int = 0
    avg_confidence: float = 0.0
    high_confidence_win_rate: float = 0.0
    low_confidence_win_rate: float = 0.0
    ai_contribution_pnl: Decimal = Decimal("0")
    ai_cost_estimate: Decimal = Decimal("0")


@dataclass
class GuardrailEffectiveness:
    """Guardrail effectiveness metrics."""

    hard_guardrails_triggered: int = 0
    trades_blocked: int = 0
    losses_prevented: Decimal = Decimal("0")
    soft_guardrails_triggered: int = 0
    position_downgrades: int = 0


@dataclass
class FeatureImpactAnalysis:
    """Feature impact analysis metrics."""

    high_beta_count: int = 0
    high_beta_win_rate: float = 0.0
    low_beta_count: int = 0
    low_beta_win_rate: float = 0.0
    high_correlation_count: int = 0
    high_correlation_win_rate: float = 0.0
    volume_confirmed_count: int = 0
    volume_confirmed_win_rate: float = 0.0


@dataclass
class MonthlyReturns:
    """Monthly returns data."""

    monthly_data: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class HybridReport:
    """Complete hybrid intelligence backtest report."""

    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    ai_performance: AIPerformanceMetrics = field(default_factory=AIPerformanceMetrics)
    guardrail_effectiveness: GuardrailEffectiveness = field(
        default_factory=GuardrailEffectiveness
    )
    feature_impact: FeatureImpactAnalysis = field(
        default_factory=FeatureImpactAnalysis
    )
    monthly_returns: MonthlyReturns = field(default_factory=MonthlyReturns)


class HybridBacktestReport:
    """Generate hybrid intelligence metrics for backtest results."""

    HIGH_CONFIDENCE_THRESHOLD = 70.0
    LOW_CONFIDENCE_THRESHOLD = 40.0
    HIGH_BETA_THRESHOLD = 1.5
    LOW_BETA_THRESHOLD = 0.8
    HIGH_CORRELATION_THRESHOLD = 0.8
    VOLUME_SPIKE_THRESHOLD = 1.5
    AI_COST_PER_EVALUATION = Decimal("0.02")
    HIGH_CONFIDENCE_BONUS_MULTIPLIER = Decimal("1.5")

    def generate_report(self, results: list[BacktestTradeResult]) -> HybridReport:
        """Generate hybrid intelligence report for backtest results.

        Args:
            results: List of BacktestTradeResult from backtest.

        Returns:
            HybridReport with all metrics computed.
        """
        report = HybridReport()

        if not results:
            return report

        taken = [r for r in results if r.trade_taken]
        if not taken:
            return report

        report.total_trades = len(taken)
        report.winning_trades = sum(1 for r in taken if r.pnl_net > Decimal("0"))
        report.win_rate = (report.winning_trades / report.total_trades) * 100

        total_pnl = sum(r.pnl_net for r in taken)
        initial_capital = Decimal("10000")
        report.total_return_pct = float((total_pnl / initial_capital) * 100)

        report.sharpe_ratio = self._calculate_sharpe(taken)
        report.max_drawdown_pct = self._calculate_max_drawdown(taken)

        report.ai_performance = self._compute_ai_performance(taken)
        report.guardrail_effectiveness = self._compute_guardrail_effectiveness(taken)
        report.feature_impact = self._compute_feature_impact(taken)
        report.monthly_returns = self._compute_monthly_returns(taken)

        return report

    def _compute_ai_performance(
        self, results: list[BacktestTradeResult]
    ) -> AIPerformanceMetrics:
        """Compute AI-specific performance metrics."""
        metrics = AIPerformanceMetrics()

        trades_with_ai = [r for r in results if r.ai_confidence is not None]
        if not trades_with_ai:
            return metrics

        metrics.total_evaluations = len(trades_with_ai)
        metrics.avg_confidence = sum(r.ai_confidence for r in trades_with_ai) / len(
            trades_with_ai
        )

        high_conf = [
            r for r in trades_with_ai if r.ai_confidence >= self.HIGH_CONFIDENCE_THRESHOLD
        ]
        low_conf = [
            r for r in trades_with_ai if r.ai_confidence <= self.LOW_CONFIDENCE_THRESHOLD
        ]

        if high_conf:
            high_wins = sum(1 for r in high_conf if r.pnl_net > Decimal("0"))
            metrics.high_confidence_win_rate = (high_wins / len(high_conf)) * 100

        if low_conf:
            low_wins = sum(1 for r in low_conf if r.pnl_net > Decimal("0"))
            metrics.low_confidence_win_rate = (low_wins / len(low_conf)) * 100

        high_conf_pnl = sum(
            r.pnl_net * self.HIGH_CONFIDENCE_BONUS_MULTIPLIER
            for r in high_conf
            if r.pnl_net > Decimal("0")
        )
        low_conf_pnl = sum(
            r.pnl_net
            for r in low_conf
            if r.pnl_net > Decimal("0")
        )
        metrics.ai_contribution_pnl = high_conf_pnl - low_conf_pnl

        metrics.ai_cost_estimate = (
            Decimal(str(metrics.total_evaluations)) * self.AI_COST_PER_EVALUATION
        )

        return metrics

    def _compute_guardrail_effectiveness(
        self, results: list[BacktestTradeResult]
    ) -> GuardrailEffectiveness:
        """Compute guardrail effectiveness metrics."""
        effectiveness = GuardrailEffectiveness()

        for r in results:
            if r.guardrail_type == "hard":
                effectiveness.hard_guardrails_triggered += 1
                if r.guardrail_action == "blocked":
                    effectiveness.trades_blocked += 1
                    if r.pnl_net < Decimal("0"):
                        effectiveness.losses_prevented += abs(r.pnl_net)
            elif r.guardrail_type == "soft":
                effectiveness.soft_guardrails_triggered += 1
                if r.guardrail_action == "downgrade":
                    effectiveness.position_downgrades += 1

        return effectiveness

    def _compute_feature_impact(
        self, results: list[BacktestTradeResult]
    ) -> FeatureImpactAnalysis:
        """Compute feature impact analysis metrics."""
        impact = FeatureImpactAnalysis()

        high_beta = [r for r in results if r.beta is not None and r.beta > self.HIGH_BETA_THRESHOLD]
        low_beta = [r for r in results if r.beta is not None and r.beta < self.LOW_BETA_THRESHOLD]
        high_corr = [
            r for r in results
            if r.correlation is not None and r.correlation > self.HIGH_CORRELATION_THRESHOLD
        ]
        vol_confirmed = [
            r for r in results
            if r.volume_spike is not None and r.volume_spike > self.VOLUME_SPIKE_THRESHOLD
        ]

        if high_beta:
            impact.high_beta_count = len(high_beta)
            high_beta_wins = sum(1 for r in high_beta if r.pnl_net > Decimal("0"))
            impact.high_beta_win_rate = (high_beta_wins / impact.high_beta_count) * 100

        if low_beta:
            impact.low_beta_count = len(low_beta)
            low_beta_wins = sum(1 for r in low_beta if r.pnl_net > Decimal("0"))
            impact.low_beta_win_rate = (low_beta_wins / impact.low_beta_count) * 100

        if high_corr:
            impact.high_correlation_count = len(high_corr)
            high_corr_wins = sum(1 for r in high_corr if r.pnl_net > Decimal("0"))
            impact.high_correlation_win_rate = (high_corr_wins / impact.high_correlation_count) * 100

        if vol_confirmed:
            impact.volume_confirmed_count = len(vol_confirmed)
            vol_wins = sum(1 for r in vol_confirmed if r.pnl_net > Decimal("0"))
            impact.volume_confirmed_win_rate = (vol_wins / impact.volume_confirmed_count) * 100

        return impact

    def _compute_monthly_returns(
        self, results: list[BacktestTradeResult]
    ) -> MonthlyReturns:
        """Compute monthly returns breakdown."""
        monthly = MonthlyReturns()

        monthly_pnl: dict[str, Decimal] = {}
        for r in results:
            bucket = r.monthly_bucket
            if not bucket and r.entry_time:
                bucket = r.entry_time.strftime("%Y-%m")
            if bucket:
                monthly_pnl[bucket] = monthly_pnl.get(bucket, Decimal("0")) + r.pnl_net

        monthly.monthly_data = dict(sorted(monthly_pnl.items()))
        return monthly

    def _calculate_sharpe(self, results: list[BacktestTradeResult]) -> float:
        """Calculate Sharpe ratio from trade PnLs."""
        import math

        pnls = [float(r.pnl_net) for r in results]
        if len(pnls) < 2:
            return 0.0

        mean_r = sum(pnls) / len(pnls)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in pnls) / (len(pnls) - 1))

        if std_r == 0:
            return 0.0

        return round(mean_r / std_r * math.sqrt(365), 3)

    def _calculate_max_drawdown(self, results: list[BacktestTradeResult]) -> float:
        """Calculate maximum drawdown percentage."""
        cumulative = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")

        for r in results:
            cumulative += r.pnl_net
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        if peak == 0:
            return 0.0

        return float((max_dd / peak) * 100) if peak > 0 else 0.0


def format_hybrid_report(report: HybridReport, job_id: str) -> str:
    """Format hybrid intelligence report as Telegram-ready text.

    Args:
        report: HybridReport with all metrics computed.
        job_id: Backtest job ID for display.

    Returns:
        Formatted string ready for Telegram.
    """
    lines: list[str] = []

    lines.append(f"\U0001f7e1 Backtest Results — {job_id[:8]}")
    lines.append("━" * 32)
    lines.append("")

    lines.append("\U0001f4ca Overall Performance")
    lines.append(f"├─ Total Trades: {report.total_trades:,}")
    lines.append(
        f"├─ Winning Trades: {report.winning_trades:,} ({report.win_rate:.1f}%)"
    )
    lines.append(f"├─ Total Return: {report.total_return_pct:+.1f}%")
    lines.append(f"├─ Sharpe Ratio: {report.sharpe_ratio:.2f}")
    lines.append(f"└─ Max Drawdown: -{report.max_drawdown_pct:.1f}%")
    lines.append("")

    ai = report.ai_performance
    if ai.total_evaluations > 0:
        lines.append("\U0001f9e0 AI Performance (Backtest)")
        lines.append(f"├─ Total Evaluations: {ai.total_evaluations:,}")
        lines.append(f"├─ Avg Confidence: {ai.avg_confidence:.0f}/100")
        lines.append(
            f"├─ High Confidence Win Rate: {ai.high_confidence_win_rate:.1f}%"
        )
        lines.append(
            f"├─ Low Confidence Win Rate: {ai.low_confidence_win_rate:.1f}%"
        )
        lines.append(
            f"├─ AI Contribution: {ai.ai_contribution_pnl:+,.2f} (estimated)"
        )
        lines.append(f"└─ AI Cost (estimated): ${ai.ai_cost_estimate:.2f}")
        lines.append("")

    g = report.guardrail_effectiveness
    if g.hard_guardrails_triggered > 0 or g.soft_guardrails_triggered > 0:
        lines.append("\U0001f6e1️ Guardrail Effectiveness")
        lines.append(
            f"├─ Hard Guardrails Triggered: {g.hard_guardrails_triggered}"
        )
        lines.append(f"├─ Trades Blocked: {g.trades_blocked}")
        lines.append(
            f"├─ Losses Prevented: {g.losses_prevented:+,.2f}"
        )
        lines.append(
            f"├─ Soft Guardrails Triggered: {g.soft_guardrails_triggered}"
        )
        lines.append(
            f"└─ Position Downgrades: {g.position_downgrades}"
        )
        lines.append("")

    f = report.feature_impact
    if (
        f.high_beta_count > 0
        or f.low_beta_count > 0
        or f.high_correlation_count > 0
        or f.volume_confirmed_count > 0
    ):
        lines.append("\U0001f4ca Feature Impact Analysis")
        if f.high_beta_count > 0:
            lines.append(
                f"├─ High Beta (>1.5) Trades: {f.high_beta_count}, "
                f"Win Rate: {f.high_beta_win_rate:.0f}%"
            )
        if f.low_beta_count > 0:
            lines.append(
                f"├─ Low Beta (<0.8) Trades: {f.low_beta_count}, "
                f"Win Rate: {f.low_beta_win_rate:.0f}%"
            )
        if f.high_correlation_count > 0:
            lines.append(
                f"├─ High Correlation (>0.8) Trades: {f.high_correlation_count}, "
                f"Win Rate: {f.high_correlation_win_rate:.0f}%"
            )
        if f.volume_confirmed_count > 0:
            lines.append(
                f"└─ Volume Confirmed Trades: {f.volume_confirmed_count}, "
                f"Win Rate: {f.volume_confirmed_win_rate:.0f}%"
            )
        lines.append("")

    m = report.monthly_returns
    if m.monthly_data:
        lines.append("\U0001f4c5 Monthly Returns")
        months_per_line = 3
        month_items = list(m.monthly_data.items())
        for i in range(0, len(month_items), months_per_line):
            chunk = month_items[i : i + months_per_line]
            parts = []
            for month, pnl in chunk:
                month_short = month[-2:] if len(month) > 2 else month
                parts.append(f"{month_short}: {pnl:+.1f}%")
            lines.append(f"└─ {' | '.join(parts)}")
        lines.append("")

    lines.append("━" * 32)

    return "\n".join(lines)

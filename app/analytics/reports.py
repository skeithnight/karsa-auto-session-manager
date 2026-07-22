"""Reports generation module.

Formats daily, weekly, and monthly reports combining pipeline funnel, 
decision quality, AI metrics, and runtime health.
"""

from __future__ import annotations

import logging
from app.analytics.runtime import RuntimeAnalyzer
from app.analytics.evidence import EvidenceAnalyzer
from app.analytics.ai_effectiveness import AIEffectivenessAnalyzer
from app.analytics.lifecycle import LifecycleAnalyzer
from app.analytics.calibration import CalibrationAnalyzer

logger = logging.getLogger("karsa.analytics.reports")


class ReportGenerator:
    """Generates formatted reports for Telegram Dispatch."""

    def __init__(
        self,
        evidence: EvidenceAnalyzer,
        ai: AIEffectivenessAnalyzer,
        lifecycle: LifecycleAnalyzer,
        calibration: CalibrationAnalyzer
    ) -> None:
        self.evidence = evidence
        self.ai = ai
        self.lifecycle = lifecycle
        self.calibration = calibration

    async def generate_daily_report(self) -> str:
        """Generate Daily Analytics Report."""
        health = RuntimeAnalyzer.calculate_health_score()
        lifecycle = await self.lifecycle.analyze_lifecycle()
        
        report = []
        report.append("📊 *Daily Decision Intelligence Report*")
        report.append(f"Overall Health Score: `{health.get('overall_health', 0):.1f}%`")
        
        report.append("\n*Runtime Integrity*")
        report.append(f"• Execution Safety: `{health.get('execution_safety', 0):.1f}%`")
        report.append(f"• Shadow Reliability: `{health.get('shadow_reliability', 0):.1f}%`")
        report.append(f"• Recon Integrity: `{health.get('reconciliation', 0):.1f}%`")
        
        report.append("\n*Trade Lifecycle*")
        report.append(f"• Avg MAE: `{lifecycle.get('avg_mae', 0.0):.4f}`")
        report.append(f"• Avg MFE: `{lifecycle.get('avg_mfe', 0.0):.4f}`")
        report.append(f"• Peak R-Multiple: `{lifecycle.get('avg_peak_r_multiple', 0.0):.2f}R`")
        
        return "\n".join(report)

    async def generate_weekly_report(self) -> str:
        """Generate Weekly Analytics Report."""
        evidence = await self.evidence.analyze_evidence_performance()
        ai = await self.ai.analyze_ai_impact()
        
        report = []
        report.append("📅 *Weekly Decision Intelligence Report*")
        
        report.append("\n*Evidence Effectiveness*")
        for col, metrics in evidence.items():
            name = col.replace("evidence_", "").title()
            report.append(f"• {name}: `{metrics['win_rate']:.1f}% Win Rate` ({metrics['total_trades']} trades)")
            
        report.append("\n*AI Impact*")
        report.append(f"• With AI Win Rate: `{ai.get('ai_win_rate', 0.0):.1f}%`")
        report.append(f"• No AI Win Rate: `{ai.get('no_ai_win_rate', 0.0):.1f}%`")
        report.append(f"• Avg Conf Shift: `{ai.get('avg_confidence_shift', 0.0):.1f}`")
        report.append(f"• Avg Latency: `{ai.get('avg_ai_latency_ms', 0.0):.0f}ms`")
        
        return "\n".join(report)

    async def generate_monthly_report(self) -> str:
        """Generate Monthly Analytics Report."""
        calib = await self.calibration.analyze_calibration()
        health = RuntimeAnalyzer.calculate_health_score()
        
        report = []
        report.append("📈 *Monthly Portfolio & Operations Review*")
        report.append(f"Health Baseline: `{health.get('overall_health', 0):.1f}%`")
        
        report.append("\n*Confidence Calibration*")
        for bucket, metrics in sorted(calib.items()):
            report.append(f"• {bucket}%: `{metrics['win_rate']:.1f}% WR` ({metrics['total_trades']} trades, {metrics['avg_pnl']:.4f} avg PnL)")
            
        return "\n".join(report)

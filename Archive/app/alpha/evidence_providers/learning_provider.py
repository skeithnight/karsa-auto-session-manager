"""Learning Evidence Provider (Sprint 5).

Consolidates Expected Edge and Trade Fatigue into a single evidence provider.
"""
from __future__ import annotations

from app.alpha.evidence_provider import IEvidenceProvider
from app.core.decision_context import DecisionContext
from app.learning.expected_edge import ExpectedEdgeCalculator
from app.learning.statistical_learning import StatisticalLearning


class LearningProvider(IEvidenceProvider):
    """Provides historical statistical intelligence as evidence."""

    def __init__(self, edge_calculator: ExpectedEdgeCalculator, statistical_learning: StatisticalLearning) -> None:
        self.edge = edge_calculator
        self.stats = statistical_learning

    @property
    def name(self) -> str:
        return "learning"

    async def collect(self, context: DecisionContext) -> None:
        # Calculate Expected Edge
        edge_profile = await self.edge.calculate(context)
        if edge_profile.sample_size > 5:
            if edge_profile.expectancy > 1.0:
                context.add_evidence(self.name, 1.0, 15.0, f"High historical expectancy: {edge_profile.expectancy:.2f}%")
            elif edge_profile.expectancy < -0.5:
                context.add_evidence(self.name, -1.0, 20.0, f"Negative historical expectancy: {edge_profile.expectancy:.2f}%")

        # Calculate Trade Fatigue Penalty
        calibration_result = await self.stats.calibrate(context)
        if calibration_result.fatigue_penalty > 0:
            context.add_evidence(self.name, -1.0, calibration_result.fatigue_penalty, "Trade fatigue detected")

"""AI Evidence Provider (Sprint 5).

Connects to 9router AI service to request probabilistic evidence.
"""
from __future__ import annotations

from loguru import logger

from app.ai.service import IAIService
from app.alpha.evidence_provider import IEvidenceProvider
from app.core.decision_context import DecisionContext


class AIEvidenceProvider(IEvidenceProvider):
    """Provides AI-driven market analysis as evidence."""

    def __init__(self, ai_service: IAIService | None = None) -> None:
        self.ai = ai_service

    @property
    def name(self) -> str:
        return "ai"

    async def collect(self, context: DecisionContext) -> None:
        if not self.ai:
            return

        try:
            # The AI provider calls the AI Service, passing feature data.
            # We catch exceptions to gracefully degrade if AI is down (Circuit Breaker).
            ai_evidence = await self.ai.analyze_market(context)
            if ai_evidence:
                if ai_evidence.confidence > 50:
                    if ai_evidence.bullish_probability > ai_evidence.bearish_probability:
                        context.add_evidence(self.name, 1.0, ai_evidence.confidence * 0.2, f"AI Bullish ({ai_evidence.bullish_probability:.1f}%)")
                    elif ai_evidence.bearish_probability > ai_evidence.bullish_probability:
                        context.add_evidence(self.name, -1.0, ai_evidence.confidence * 0.2, f"AI Bearish ({ai_evidence.bearish_probability:.1f}%)")
        except Exception as e:
            logger.warning(f"AI Evidence Provider degraded: {e}")

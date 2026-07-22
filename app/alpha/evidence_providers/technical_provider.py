"""Technical Evidence Provider (Sprint 5).

Translates technical feature data into decision evidence.
"""
from __future__ import annotations

from app.alpha.evidence_provider import IEvidenceProvider
from app.core.decision_context import DecisionContext


class TechnicalProvider(IEvidenceProvider):

    @property
    def name(self) -> str:
        return "technical"

    async def collect(self, context: DecisionContext) -> None:
        features = context.features

        # Example technical logic migrating from old StrategyRouter/EvidenceCollector
        rsi = features.rsi_14 or 50.0
        if rsi < 30:
            context.add_evidence(
                source=self.name,
                direction=1.0,
                weight=10.0,
                reason=f"Oversold RSI ({rsi:.1f})"
            )
        elif rsi > 70:
            context.add_evidence(
                source=self.name,
                direction=-1.0,
                weight=10.0,
                reason=f"Overbought RSI ({rsi:.1f})"
            )

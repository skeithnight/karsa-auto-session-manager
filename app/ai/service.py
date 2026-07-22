"""AI Service Interface (Sprint 5)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.dto import AIEvidenceDTO
from app.core.decision_context import DecisionContext


class IAIService(ABC):
    """Interface for AI analytics services."""

    @abstractmethod
    async def analyze_market(self, context: DecisionContext) -> AIEvidenceDTO | None:
        """Analyze the market based on context and return AIEvidenceDTO."""
        pass

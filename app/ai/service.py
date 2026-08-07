"""AI Service Interface (Sprint 5)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.dto import AIDecisionDTO
from app.core.decision_context import DecisionContext


class IAIService(ABC):
    """Interface for AI analytics services."""

    @abstractmethod
    async def analyze_market(self, context: DecisionContext) -> AIDecisionDTO | None:
        """Analyze the market based on context and return AIDecisionDTO."""
        pass

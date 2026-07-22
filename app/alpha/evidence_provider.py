"""Evidence Plugin Framework (Sprint 5).

Defines the core interface for modular evidence providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.decision_context import DecisionContext


class IEvidenceProvider(ABC):
    """Interface for all evidence providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider (e.g., 'technical', 'ai')."""
        pass

    @abstractmethod
    async def collect(self, context: DecisionContext) -> None:
        """Evaluate the context and add Evidence objects to it."""
        pass

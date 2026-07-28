"""Base class for edge families.

Each edge family represents a distinct return stream with its own:
- Entry logic
- Regime requirements
- Filter set
- Expected holding period
- Stop/target behavior

This decomposition replaces the monolithic scoring stack in DecisionEngine
with composable, independently evaluable components.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot


@dataclass(frozen=True)
class EdgeFamilyResult:
    """Result from an edge family evaluation.

    Attributes:
        family_name: Name of the edge family.
        score: Strategy score (0-100) from this family.
        confidence: Confidence in this signal (0.0-1.0).
        regime_aligned: Whether the signal aligns with the regime.
        filters_passed: Dict of filter_name -> passed status.
        metadata: Additional family-specific data.
        reject_reason: If rejected, the reason why.
    """

    family_name: str
    score: float
    confidence: float
    regime_aligned: bool
    filters_passed: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    reject_reason: str | None = None


class EdgeFamily(ABC):
    """Base class for all edge families.

    Each family encapsulates:
    - Its own entry logic
    - Its own regime requirements
    - Its own filter set
    - Its own expected holding period
    - Its own stop/target behavior
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique family name."""
        ...

    @property
    @abstractmethod
    def supported_regimes(self) -> list[MarketRegime]:
        """Regimes where this family is active."""
        ...

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        features: Any,
        snapshot: MarketSnapshot,
        context: DecisionContext | None = None,
        redis_client: Any = None,
        **kwargs: Any,
    ) -> EdgeFamilyResult:
        """Evaluate this family for the given symbol/direction.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT").
            direction: "LONG" or "SHORT".
            regime: Current market regime.
            features: FeatureVector from feature extraction.
            snapshot: MarketSnapshot with raw market data.
            context: DecisionContext for evidence tracking.
            redis_client: Redis client for external data.
            **kwargs: Additional family-specific arguments.

        Returns:
            EdgeFamilyResult with score, confidence, and filter status.
        """
        ...

    def is_active(self, regime: MarketRegime) -> bool:
        """Check if this family is active for the given regime."""
        return regime in self.supported_regimes

    def _reject(self, family_name: str, reason: str) -> EdgeFamilyResult:
        """Helper to create a rejected result."""
        return EdgeFamilyResult(
            family_name=family_name,
            score=0.0,
            confidence=0.0,
            regime_aligned=False,
            filters_passed={},
            reject_reason=reason,
        )

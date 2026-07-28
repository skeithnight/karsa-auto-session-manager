"""Score Composer — Cross-family score composition.

Evaluates all active edge families independently and composes
the final score. Each family is evaluated in isolation; the composer
picks the highest-scoring family and applies cross-family penalties.

This replaces the monolithic scoring stack in DecisionEngine with
composable, independently evaluable components.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ComposedScore:
    """Final composed score from all edge families.

    Attributes:
        symbol: Trading pair.
        direction: LONG or SHORT.
        regime: Current market regime.
        total_score: Final score from winning family.
        family_scores: Results from all evaluated families.
        winning_family: Name of the family that produced the score.
        confidence: Confidence in the signal (0.0-1.0).
        regime_aligned: Whether the signal aligns with the regime.
        filters_passed: Cross-family filter status.
        metadata: Additional composition metadata.
    """

    symbol: str
    direction: str
    regime: MarketRegime
    total_score: float
    family_scores: dict[str, EdgeFamilyResult]
    winning_family: str | None
    confidence: float
    regime_aligned: bool
    filters_passed: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ScoreComposer:
    """Composes scores from multiple edge families.

    Each family is evaluated independently. The composer:
    1. Runs all active families for the regime
    2. Picks the highest-scoring family
    3. Applies cross-family penalties (correlation, etc.)
    4. Returns the composed score with attribution

    This design ensures:
    - Each family is evaluated in isolation
    - No cross-family contamination
    - Clear attribution of alpha source
    - Independent testing of each family
    """

    def __init__(self, families: list[EdgeFamily] | None = None) -> None:
        """Initialize the score composer.

        Args:
            families: List of edge families to evaluate.
        """
        self._families = families or []

    def register_family(self, family: EdgeFamily) -> None:
        """Register an edge family."""
        self._families.append(family)

    async def compose(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        features: Any,
        snapshot: MarketSnapshot,
        context: DecisionContext | None = None,
        redis_client: Any = None,
        **kwargs: Any,
    ) -> ComposedScore:
        """Evaluate all active families and compose final score.

        Args:
            symbol: Trading pair.
            direction: LONG or SHORT.
            regime: Current market regime.
            features: FeatureVector from feature extraction.
            snapshot: MarketSnapshot with raw market data.
            context: DecisionContext for evidence tracking.
            redis_client: Redis client for external data.
            **kwargs: Additional arguments passed to families.

        Returns:
            ComposedScore with winning family and final score.
        """
        # Filter to active families for this regime
        active_families = [f for f in self._families if f.is_active(regime)]
        family_results: dict[str, EdgeFamilyResult] = {}

        # Evaluate each family independently
        for family in active_families:
            try:
                result = await family.evaluate(
                    symbol=symbol,
                    direction=direction,
                    regime=regime,
                    features=features,
                    snapshot=snapshot,
                    context=context,
                    redis_client=redis_client,
                    **kwargs,
                )
                family_results[family.name] = result
            except Exception as e:
                logger.error(
                    "ScoreComposer: family %s failed for %s %s: %s",
                    family.name, symbol, direction, e,
                )
                family_results[family.name] = EdgeFamilyResult(
                    family_name=family.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=False,
                    reject_reason=f"evaluation_error: {e}",
                )

        # Filter to valid results (no rejection)
        valid_results = {
            k: v for k, v in family_results.items()
            if v.reject_reason is None
        }

        # If no valid results, return empty
        if not valid_results:
            return ComposedScore(
                symbol=symbol,
                direction=direction,
                regime=regime,
                total_score=0.0,
                family_scores=family_results,
                winning_family=None,
                confidence=0.0,
                regime_aligned=False,
                filters_passed={"all_families": False},
            )

        # Find winning family (highest score)
        winning = max(valid_results.values(), key=lambda r: r.score)

        # Check if all families passed their filters
        all_filters_passed = all(
            all(r.filters_passed.values())
            for r in valid_results.values()
            if r.filters_passed
        )

        # Log family scores for debugging
        for name, result in family_results.items():
            if result.reject_reason:
                logger.debug(
                    "ScoreComposer: %s %s %s REJECTED (%s)",
                    symbol, direction, name, result.reject_reason,
                )
            else:
                logger.debug(
                    "ScoreComposer: %s %s %s score=%.1f",
                    symbol, direction, name, result.score,
                )

        logger.info(
            "ScoreComposer: %s %s winner=%s score=%.1f (families=%d/%d)",
            symbol, direction, winning.family_name, winning.score,
            len(valid_results), len(family_results),
        )

        return ComposedScore(
            symbol=symbol,
            direction=direction,
            regime=regime,
            total_score=winning.score,
            family_scores=family_results,
            winning_family=winning.family_name,
            confidence=winning.confidence,
            regime_aligned=winning.regime_aligned,
            filters_passed={"all_families": all_filters_passed},
            metadata={
                "active_families": [f.name for f in active_families],
                "valid_families": list(valid_results.keys()),
                "rejected_families": [
                    k for k, v in family_results.items()
                    if v.reject_reason is not None
                ],
            },
        )

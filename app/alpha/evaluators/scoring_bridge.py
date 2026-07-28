"""Scoring Bridge (v3.5 Prototype).

Provides a bridge between the new v3.5 evaluator architecture and
the existing DecisionEngine scoring logic.

This module allows the v3.5 evaluators to be used as a scoring
alternative to the existing StrategyRouter, without requiring
a full rewrite of the DecisionEngine.

Usage:
    # In DecisionEngine.evaluate(), replace the scoring logic:
    from app.alpha.evaluators.scoring_bridge import ScoringBridge

    bridge = ScoringBridge()

    # Instead of:
    #   score = await self._router.evaluate_signal(...)
    # Use:
    #   score = await bridge.score(features, snapshot, regime)
"""
from __future__ import annotations

import time
from typing import Any

from app.alpha.evaluators.fusion import FusionResult
from app.alpha.evaluators.registry import EvaluatorRegistry
from app.alpha.regime_classifier import MarketRegime
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


class ScoringBridge:
    """Bridge between v3.5 evaluators and existing scoring logic.

    This module provides a drop-in replacement for the scoring
    logic in DecisionEngine.evaluate().

    Features:
    - Optional integration (can be enabled/disabled)
    - Falls back to existing scoring if evaluators fail
    - Collects metrics for monitoring
    - Regime-specific weighting
    """

    def __init__(self, enabled: bool = False) -> None:
        """Initialize the scoring bridge.

        Args:
            enabled: Whether to use v3.5 evaluators (default: False for safety)
        """
        self._enabled = enabled
        self._registry = EvaluatorRegistry()
        self._fallback_score: float | None = None

    @property
    def enabled(self) -> bool:
        """Check if v3.5 scoring is enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable v3.5 scoring."""
        self._enabled = True

    def disable(self) -> None:
        """Disable v3.5 scoring (use existing scoring)."""
        self._enabled = False

    async def score(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
        regime: MarketRegime | None = None,
        fallback_score: float | None = None,
    ) -> tuple[float, FusionResult | None]:
        """Compute score using v3.5 evaluators or fallback.

        Args:
            features: Feature vector from FeatureExtractor
            snapshot: Optional market snapshot
            regime: Optional regime for regime-specific weighting
            fallback_score: Score to use if v3.5 is disabled or fails

        Returns:
            Tuple of (score, fusion_result)
            - score: Computed score (0-100 scale)
            - fusion_result: Full fusion result if v3.5 was used, None otherwise
        """
        # Store fallback score
        self._fallback_score = fallback_score

        # If disabled, return fallback immediately
        if not self._enabled:
            return fallback_score or 0.0, None

        try:
            # Run v3.5 evaluators
            start_time = time.perf_counter()
            fusion_result = self._registry.evaluate(features, snapshot, regime)
            eval_time_ms = (time.perf_counter() - start_time) * 1000

            # Convert fusion result to score
            score = self._fusion_to_score(fusion_result)

            # Log metrics
            import logging
            logger = logging.getLogger(__name__)
            logger.info(
                "v3.5 scoring: score=%.1f confidence=%.2f recommendation=%s time=%.1fms",
                score,
                fusion_result.confidence,
                fusion_result.recommendation,
                eval_time_ms,
            )

            return score, fusion_result

        except Exception as e:
            # Fallback to existing scoring on error
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("v3.5 scoring failed, falling back: %s", e)

            return fallback_score or 0.0, None

    def _fusion_to_score(self, fusion_result: FusionResult) -> float:
        """Convert fusion result to score format.

        The score should be in 0-100 scale, similar to the existing
        StrategyRouter output.

        Args:
            fusion_result: Result from evaluator fusion

        Returns:
            Score in 0-100 scale
        """
        # Use weight directly — confidence is already factored into
        # the evaluator weights and fusion agreement, so multiplying
        # again would double-count and deflate the score.
        score = fusion_result.weight

        # Clamp to 0-100
        return max(0.0, min(100.0, score))

    def get_recommendation(self, fusion_result: FusionResult) -> str:
        """Get recommendation from fusion result.

        Args:
            fusion_result: Result from evaluator fusion

        Returns:
            Recommendation string (HOLD, REDUCE, EXIT, INCREASE, TRAIL)
        """
        return fusion_result.recommendation

    def should_trade(self, fusion_result: FusionResult, min_score: float = 75.0) -> bool:
        """Determine if we should trade based on fusion result.

        Args:
            fusion_result: Result from evaluator fusion
            min_score: Minimum score threshold

        Returns:
            True if we should trade
        """
        score = self._fusion_to_score(fusion_result)
        return (
            score >= min_score and
            fusion_result.is_actionable and
            fusion_result.confidence > 0.4
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize bridge state."""
        return {
            "enabled": self._enabled,
            "registry": self._registry.to_dict(),
            "fallback_score": self._fallback_score,
        }

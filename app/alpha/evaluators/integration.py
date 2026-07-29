"""Integration Bridge (v3.5 Prototype).

Bridges the new evaluator architecture with the existing DecisionEngine.
This module shows how to use the v3.5 evaluation pipeline within
the current codebase without requiring a full rewrite.

Usage:
    from app.alpha.evaluators.integration import EvaluatorBridge

    bridge = EvaluatorBridge()

    # In DecisionEngine.evaluate(), after feature extraction:
    fusion_result = bridge.evaluate(features, snapshot, regime)

    # Use fusion result for decision making
    if fusion_result.is_actionable:
        signal = build_signal(fusion_result)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.alpha.evaluators.fusion import FusionResult
from app.alpha.evaluators.registry import EvaluatorRegistry
from app.alpha.regime_classifier import MarketRegime
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


@dataclass
class EvaluationMetrics:
    """Metrics from the evaluation pipeline."""
    evaluation_time_ms: float = 0.0
    evaluator_count: int = 0
    confidence: float = 0.0
    recommendation: str = "HOLD"
    signal_strength: float = 0.0
    dominant_evaluator: str | None = None


class EvaluatorBridge:
    """Bridge between v3.5 evaluators and existing DecisionEngine.

    This module provides a clean interface for integrating the new
    evaluation pipeline into the existing codebase.

    Features:
    - Backward compatible with existing code
    - Optional integration (can be enabled/disabled)
    - Metrics collection for monitoring
    - Graceful degradation if evaluators fail
    """

    def __init__(self, enabled: bool = True) -> None:
        """Initialize the bridge.

        Args:
            enabled: Whether the bridge is active
        """
        self._enabled = enabled
        self._registry = EvaluatorRegistry()
        self._last_metrics: EvaluationMetrics | None = None

    @property
    def enabled(self) -> bool:
        """Check if the bridge is enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable the bridge."""
        self._enabled = True

    def disable(self) -> None:
        """Disable the bridge."""
        self._enabled = False

    def evaluate(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
        regime: MarketRegime | None = None,
    ) -> FusionResult | None:
        """Run evaluation pipeline.

        Args:
            features: Feature vector from FeatureExtractor
            snapshot: Optional market snapshot
            regime: Optional regime for regime-specific weighting

        Returns:
            FusionResult if enabled and successful, None otherwise
        """
        if not self._enabled:
            return None

        start_time = time.perf_counter()

        try:
            result = self._registry.evaluate(features, snapshot, regime)

            # Collect metrics
            evaluation_time_ms = (time.perf_counter() - start_time) * 1000
            self._last_metrics = EvaluationMetrics(
                evaluation_time_ms=evaluation_time_ms,
                evaluator_count=len(result.evaluator_results),
                confidence=result.confidence,
                recommendation=result.recommendation,
                signal_strength=result.signal_strength,
                dominant_evaluator=result.dominant_evaluator,
            )

            return result

        except Exception as e:
            # Log error but don't crash the main pipeline
            logger.warning("EvaluatorBridge error: %s", e)
            self._last_metrics = None
            return None

    def get_metrics(self) -> EvaluationMetrics | None:
        """Get the last evaluation metrics."""
        return self._last_metrics

    def get_available_evaluators(self) -> list[str]:
        """Get list of available evaluators."""
        return self._registry.get_available_evaluators()

    def to_dict(self) -> dict[str, Any]:
        """Serialize bridge state."""
        return {
            "enabled": self._enabled,
            "registry": self._registry.to_dict(),
            "last_metrics": {
                "evaluation_time_ms": self._last_metrics.evaluation_time_ms,
                "evaluator_count": self._last_metrics.evaluator_count,
                "confidence": self._last_metrics.confidence,
                "recommendation": self._last_metrics.recommendation,
                "signal_strength": self._last_metrics.signal_strength,
                "dominant_evaluator": self._last_metrics.dominant_evaluator,
            } if self._last_metrics else None,
        }


def convert_fusion_to_signal_score(fusion_result: FusionResult) -> float:
    """Convert FusionResult to the score format used by existing DecisionEngine.

    This allows using v3.5 evaluators as a drop-in replacement for
    the current scoring logic.

    Args:
        fusion_result: Result from evaluator fusion

    Returns:
        Score in the format expected by DecisionEngine (0-100 scale)
    """
    # Map signal_strength (0-100) to score (0-100)
    # Apply confidence as a multiplier
    base_score = fusion_result.weight
    confidence_multiplier = fusion_result.confidence

    # Scale to 0-100 range
    score = min(base_score * confidence_multiplier, 100.0)

    return score


def get_recommendation_confidence(fusion_result: FusionResult) -> float:
    """Get confidence for a specific recommendation.

    Args:
        fusion_result: Result from evaluator fusion

    Returns:
        Confidence value (0-1)
    """
    return fusion_result.confidence


def should_take_action(fusion_result: FusionResult, min_signal_strength: float = 15.0) -> bool:
    """Determine if we should take action based on fusion result.

    Args:
        fusion_result: Result from evaluator fusion
        min_signal_strength: Minimum signal strength threshold

    Returns:
        True if we should take action
    """
    return (
        fusion_result.is_actionable and
        fusion_result.signal_strength >= min_signal_strength
    )

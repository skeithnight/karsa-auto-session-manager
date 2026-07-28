"""Evaluator Registry (v3.5 Prototype).

Central registry for managing evaluators and running fusion.
This module provides the bridge between the new evaluator architecture
and the existing DecisionEngine.

Usage:
    from app.alpha.evaluators.registry import EvaluatorRegistry

    registry = EvaluatorRegistry()
    result = registry.evaluate(features, snapshot, regime)

    # Or use individual evaluators
    trend_result = registry.get_evaluator("trend").evaluate(features)
"""
from __future__ import annotations

from typing import Any

from app.alpha.evaluators.fusion import EvaluatorFusion, FusionResult
from app.alpha.evaluators.momentum_evaluator import MomentumEvaluator
from app.alpha.evaluators.trend_evaluator import EvaluatorResult, TrendEvaluator
from app.alpha.evaluators.volatility_evaluator import VolatilityEvaluator
from app.alpha.regime_classifier import MarketRegime
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


class EvaluatorRegistry:
    """Registry for managing evaluators and running fusion.

    This is the main entry point for the v3.5 evaluation pipeline.
    It manages evaluator instances and provides a unified interface.
    """

    def __init__(self, custom_weights: dict[str, float] | None = None) -> None:
        """Initialize the registry with default evaluators.

        Args:
            custom_weights: Custom weights for evaluator types.
                           If None, uses default weights.
        """
        # Initialize evaluators
        self._evaluators: dict[str, Any] = {
            "trend": TrendEvaluator(),
            "volatility": VolatilityEvaluator(),
            "momentum": MomentumEvaluator(),
        }

        # Initialize fusion layer
        self._fusion = EvaluatorFusion(weights=custom_weights)

        # Regime-specific evaluator weights (optional)
        self._regime_weights: dict[MarketRegime, dict[str, float]] = {
            MarketRegime.TREND_BULL: {"trend": 1.2, "momentum": 1.0, "volatility": 0.8},
            MarketRegime.TREND_BEAR: {"trend": 1.2, "momentum": 1.0, "volatility": 0.8},
            MarketRegime.RANGE: {"trend": 0.6, "momentum": 0.8, "volatility": 1.2},
            MarketRegime.HYPER_BULL: {"trend": 1.0, "momentum": 1.2, "volatility": 1.0},
            MarketRegime.HYPER_BEAR: {"trend": 1.0, "momentum": 1.2, "volatility": 1.0},
        }

    def get_evaluator(self, name: str) -> Any:
        """Get an evaluator by name.

        Args:
            name: Evaluator name (trend, volatility, momentum)

        Returns:
            The evaluator instance

        Raises:
            KeyError: If evaluator not found
        """
        if name not in self._evaluators:
            raise KeyError(f"Evaluator '{name}' not found. Available: {list(self._evaluators.keys())}")
        return self._evaluators[name]

    def register_evaluator(self, name: str, evaluator: Any) -> None:
        """Register a new evaluator.

        Args:
            name: Evaluator name
            evaluator: Evaluator instance with evaluate() method
        """
        self._evaluators[name] = evaluator

    def evaluate(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
        regime: MarketRegime | None = None,
    ) -> FusionResult:
        """Run all evaluators and fuse results.

        Args:
            features: Feature vector from FeatureExtractor
            snapshot: Optional market snapshot for additional data
            regime: Optional regime for regime-specific weighting

        Returns:
            Fused result with recommendation and attribution
        """
        # Get regime-specific weights if available
        weights = None
        if regime and regime in self._regime_weights:
            weights = self._regime_weights[regime]

        # Run all evaluators
        results: list[EvaluatorResult] = []
        names: list[str] = []

        for name, evaluator in self._evaluators.items():
            try:
                result = evaluator.evaluate(features, snapshot)
                results.append(result)
                names.append(name)
            except Exception as e:
                # Log error but continue with other evaluators
                print(f"Warning: Evaluator '{name}' failed: {e}")

        # Fuse results
        if weights:
            # Create temporary fusion with regime-specific weights
            fusion = EvaluatorFusion(weights=weights)
            return fusion.fuse(results, names)
        else:
            return self._fusion.fuse(results, names)

    def evaluate_single(
        self,
        evaluator_name: str,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> EvaluatorResult:
        """Run a single evaluator.

        Args:
            evaluator_name: Name of the evaluator to run
            features: Feature vector
            snapshot: Optional market snapshot

        Returns:
            Evaluator result

        Raises:
            KeyError: If evaluator not found
        """
        evaluator = self.get_evaluator(evaluator_name)
        return evaluator.evaluate(features, snapshot)

    def get_available_evaluators(self) -> list[str]:
        """Get list of available evaluator names."""
        return list(self._evaluators.keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialize registry state for debugging."""
        return {
            "evaluators": list(self._evaluators.keys()),
            "fusion_weights": self._fusion._weights,
            "regime_weights": {
                regime.value: weights
                for regime, weights in self._regime_weights.items()
            },
        }

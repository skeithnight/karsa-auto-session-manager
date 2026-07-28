"""Evaluator Fusion Layer (v3.5 Prototype).

Combines results from multiple independent evaluators into a final
recommendation with decision attribution.

Architecture:
    Evidence → Feature Graph → Independent Evaluators → Fusion → Recommendation

Usage:
    fusion = EvaluatorFusion()
    result = fusion.fuse([
        trend_result,
        volatility_result,
        momentum_result,
    ])

    # Result contains:
    # - direction: Combined direction
    # - weight: Combined strength
    # - confidence: Combined confidence
    # - attribution: What contributed to the decision
    # - recommendation: HOLD, REDUCE, EXIT, INCREASE, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.alpha.evaluators.trend_evaluator import EvaluatorResult


@dataclass(frozen=True)
class FusionResult:
    """Result from fusing multiple evaluator outputs.

    Attributes:
        direction: Combined signal direction (1.0=bull, -1.0=bear, 0.0=neutral)
        weight: Combined signal strength (0-100)
        confidence: Combined confidence (0-1)
        attribution: Decision attribution breakdown
        recommendation: Recommended action
        reasoning: Human-readable explanation
        evaluator_results: Original evaluator results for audit
    """
    direction: float
    weight: float
    confidence: float
    attribution: dict[str, float] = field(default_factory=dict)
    recommendation: str = "HOLD"
    reasoning: str = ""
    evaluator_results: list[EvaluatorResult] = field(default_factory=list)

    @property
    def signal_strength(self) -> float:
        """Combined strength = weight * confidence."""
        return self.weight * self.confidence

    @property
    def is_actionable(self) -> bool:
        """Is this signal strong enough to act on?"""
        return self.signal_strength > 15.0 and self.confidence > 0.4

    @property
    def dominant_evaluator(self) -> str | None:
        """Which evaluator contributed most to the decision?"""
        if not self.attribution:
            return None
        return max(self.attribution, key=lambda k: abs(self.attribution[k]))


class EvaluatorFusion:
    """Combines multiple evaluator results into a final recommendation.

    Fusion Strategy:
    1. Compute weighted average of directions
    2. Compute weighted average of weights
    3. Compute confidence from evaluator agreement
    4. Generate attribution breakdown
    5. Map to recommendation

    The fusion layer does NOT make strategy decisions.
    It only combines signals and produces attribution.
    """

    # Default weights for evaluator types
    DEFAULT_WEIGHTS: dict[str, float] = {
        "trend": 1.0,
        "volatility": 0.8,
        "momentum": 0.9,
        "volume": 0.7,
        "sentiment": 0.6,
        "portfolio": 0.5,
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        """Initialize fusion layer.

        Args:
            weights: Custom weights for evaluator types.
                     If None, uses DEFAULT_WEIGHTS.
        """
        self._weights = weights or self.DEFAULT_WEIGHTS

    def fuse(
        self,
        results: list[EvaluatorResult],
        evaluator_names: list[str] | None = None,
    ) -> FusionResult:
        """Fuse multiple evaluator results into a single recommendation.

        Args:
            results: List of EvaluatorResult from independent evaluators
            evaluator_names: Optional names for each evaluator (for attribution).
                           If None, uses index-based names.

        Returns:
            FusionResult with combined direction, weight, confidence, attribution
        """
        if not results:
            return FusionResult(
                direction=0.0,
                weight=0.0,
                confidence=0.0,
                recommendation="HOLD",
                reasoning="No evaluators provided",
            )

        # Assign names if not provided
        if evaluator_names is None:
            evaluator_names = [f"evaluator_{i}" for i in range(len(results))]

        # 1. Compute weighted direction
        total_weight = 0.0
        weighted_direction_sum = 0.0
        weighted_weight_sum = 0.0

        for result, name in zip(results, evaluator_names):
            eval_weight = self._weights.get(name, 0.5)
            # Use signal_strength as the effective weight
            effective_weight = result.signal_strength * eval_weight

            weighted_direction_sum += result.direction * effective_weight
            weighted_weight_sum += result.weight * effective_weight
            total_weight += effective_weight

        if total_weight > 0:
            combined_direction = weighted_direction_sum / total_weight
            combined_weight = weighted_weight_sum / total_weight
        else:
            combined_direction = 0.0
            combined_weight = 0.0

        # 2. Compute confidence from agreement
        combined_confidence = self._compute_confidence(results)

        # 3. Generate attribution
        attribution = self._compute_attribution(results, evaluator_names)

        # 4. Map to recommendation
        recommendation = self._map_to_recommendation(
            combined_direction,
            combined_weight,
            combined_confidence,
        )

        # 5. Generate reasoning
        reasoning = self._generate_reasoning(
            results,
            evaluator_names,
            combined_direction,
            combined_weight,
            combined_confidence,
            recommendation,
        )

        return FusionResult(
            direction=combined_direction,
            weight=combined_weight,
            confidence=combined_confidence,
            attribution=attribution,
            recommendation=recommendation,
            reasoning=reasoning,
            evaluator_results=results,
        )

    def _compute_confidence(self, results: list[EvaluatorResult]) -> float:
        """Compute confidence based on evaluator agreement.

        High confidence when:
        - Multiple evaluators agree on direction
        - Evaluators have high individual confidence
        - Signal strengths are consistent

        Returns:
            Confidence from 0 to 1
        """
        if not results:
            return 0.0

        # Direction agreement
        positive = sum(1 for r in results if r.direction > 0)
        negative = sum(1 for r in results if r.direction < 0)

        total = len(results)
        max_agreement = max(positive, negative)
        agreement_ratio = max_agreement / total if total > 0 else 0.0

        # Individual confidence average
        avg_confidence = sum(r.confidence for r in results) / total

        # Signal strength consistency
        strengths = [r.signal_strength for r in results]
        if strengths:
            avg_strength = sum(strengths) / len(strengths)
            strength_variance = sum((s - avg_strength) ** 2 for s in strengths) / len(strengths)
            strength_consistency = max(0.0, 1.0 - (strength_variance / 1000.0))
        else:
            strength_consistency = 0.0

        # Combined confidence
        confidence = (
            agreement_ratio * 0.5 +
            avg_confidence * 0.3 +
            strength_consistency * 0.2
        )

        return min(confidence, 1.0)

    def _compute_attribution(
        self,
        results: list[EvaluatorResult],
        names: list[str],
    ) -> dict[str, float]:
        """Compute decision attribution breakdown.

        Returns:
            Dict mapping evaluator name to contribution percentage
        """
        if not results:
            return {}

        # Compute total absolute contribution
        total_contribution = sum(abs(r.signal_strength) for r in results)

        if total_contribution == 0:
            return {name: 0.0 for name in names}

        # Compute percentage contribution
        attribution = {}
        for result, name in zip(results, names):
            contribution_pct = (abs(result.signal_strength) / total_contribution) * 100
            attribution[name] = round(contribution_pct, 1)

        return attribution

    def _map_to_recommendation(
        self,
        direction: float,
        weight: float,
        confidence: float,
    ) -> str:
        """Map combined signals to a recommendation.

        Returns:
            One of: HOLD, REDUCE, EXIT, INCREASE, TRAIL, FREEZE
        """
        signal_strength = weight * confidence

        # No clear signal
        if signal_strength < 10.0 or confidence < 0.3:
            return "HOLD"

        # Strong bullish
        if direction > 0.5 and signal_strength > 30.0:
            return "INCREASE"
        elif direction > 0.3 and signal_strength > 20.0:
            return "TRAIL"

        # Strong bearish
        if direction < -0.5 and signal_strength > 30.0:
            return "EXIT"
        elif direction < -0.3 and signal_strength > 20.0:
            return "REDUCE"

        # Moderate signals
        if direction < -0.2 and signal_strength > 15.0:
            return "REDUCE"

        return "HOLD"

    def _generate_reasoning(
        self,
        results: list[EvaluatorResult],
        names: list[str],
        direction: float,
        weight: float,
        confidence: float,
        recommendation: str,
    ) -> str:
        """Generate human-readable reasoning for the fusion result."""
        if not results:
            return "No evaluators to fuse"

        # Find top contributors
        sorted_results = sorted(
            zip(results, names),
            key=lambda x: abs(x[0].signal_strength),
            reverse=True,
        )

        # Build reasoning
        parts = []

        # Top evaluators
        for result, name in sorted_results[:3]:
            if result.signal_strength > 5:
                eval_dir = "bullish" if result.direction > 0 else "bearish"
                parts.append(f"{name} ({eval_dir}, strength={result.signal_strength:.1f})")

        # Agreement/Disagreement
        positive = sum(1 for r in results if r.direction > 0)
        negative = sum(1 for r in results if r.direction < 0)

        if positive > 0 and negative > 0:
            parts.append(f"Mixed signals ({positive} bullish, {negative} bearish)")
        elif positive > 0:
            parts.append(f"All {positive} evaluators bullish")
        else:
            parts.append(f"All {negative} evaluators bearish")

        # Confidence
        conf_str = "high" if confidence > 0.7 else ("medium" if confidence > 0.4 else "low")
        parts.append(f"Confidence: {conf_str}")

        return f"{recommendation}: {', '.join(parts)}"

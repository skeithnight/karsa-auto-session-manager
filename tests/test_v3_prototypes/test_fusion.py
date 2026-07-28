"""Tests for EvaluatorFusion (v3.5 prototype)."""
from __future__ import annotations

import pytest

from app.alpha.evaluators.fusion import EvaluatorFusion, FusionResult
from app.alpha.evaluators.trend_evaluator import EvaluatorResult


def _make_result(
    direction: float = 0.0,
    weight: float = 50.0,
    confidence: float = 0.7,
) -> EvaluatorResult:
    """Create an EvaluatorResult for testing."""
    return EvaluatorResult(
        direction=direction,
        weight=weight,
        confidence=confidence,
        reason="test",
    )


class TestEvaluatorFusion:
    """Tests for EvaluatorFusion."""

    def setup_method(self):
        self.fusion = EvaluatorFusion()

    def test_fuse_empty(self):
        """Fusing empty list should return HOLD."""
        result = self.fusion.fuse([])
        assert result.recommendation == "HOLD"
        assert result.direction == 0.0
        assert result.weight == 0.0

    def test_fuse_single_bullish(self):
        """Single bullish evaluator should produce bullish result."""
        bullish = _make_result(direction=1.0, weight=60.0, confidence=0.8)
        result = self.fusion.fuse([bullish], ["trend"])

        assert result.direction > 0
        assert result.weight > 0
        assert result.confidence > 0.5
        assert "trend" in result.attribution

    def test_fuse_single_bearish(self):
        """Single bearish evaluator should produce bearish result."""
        bearish = _make_result(direction=-1.0, weight=60.0, confidence=0.8)
        result = self.fusion.fuse([bearish], ["trend"])

        assert result.direction < 0
        assert result.weight > 0

    def test_fuse_agreement(self):
        """Multiple agreeing evaluators should produce high confidence."""
        bullish1 = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        bullish2 = _make_result(direction=1.0, weight=60.0, confidence=0.8)
        bullish3 = _make_result(direction=1.0, weight=40.0, confidence=0.6)

        result = self.fusion.fuse(
            [bullish1, bullish2, bullish3],
            ["trend", "momentum", "volume"],
        )

        assert result.direction > 0
        assert result.confidence > 0.6  # High agreement
        assert result.is_actionable

    def test_fuse_disagreement(self):
        """Disagreeing evaluators should produce lower confidence."""
        bullish = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        bearish = _make_result(direction=-1.0, weight=50.0, confidence=0.7)

        result = self.fusion.fuse(
            [bullish, bearish],
            ["trend", "momentum"],
        )

        # Direction should be near zero (canceling out)
        assert abs(result.direction) < 0.3
        # Confidence should be lower than full agreement case
        # (but still moderate due to high individual confidence)
        assert result.confidence < 0.7  # Lower than agreement case

    def test_attribution(self):
        """Attribution should show contribution percentages."""
        strong = _make_result(direction=1.0, weight=80.0, confidence=0.9)
        weak = _make_result(direction=1.0, weight=20.0, confidence=0.5)

        result = self.fusion.fuse(
            [strong, weak],
            ["trend", "volume"],
        )

        assert "trend" in result.attribution
        assert "volume" in result.attribution
        # Strong evaluator should have higher attribution
        assert result.attribution["trend"] > result.attribution["volume"]

    def test_attribution_sums_to_100(self):
        """Attribution percentages should sum to ~100%."""
        r1 = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        r2 = _make_result(direction=-1.0, weight=60.0, confidence=0.8)
        r3 = _make_result(direction=1.0, weight=40.0, confidence=0.6)

        result = self.fusion.fuse(
            [r1, r2, r3],
            ["trend", "momentum", "volume"],
        )

        total = sum(result.attribution.values())
        assert 99.0 < total < 101.0  # Allow for rounding

    def test_recommendation_hold(self):
        """Weak signals should produce HOLD."""
        weak = _make_result(direction=0.1, weight=10.0, confidence=0.3)
        result = self.fusion.fuse([weak], ["trend"])

        assert result.recommendation == "HOLD"

    def test_recommendation_increase(self):
        """Strong bullish signals should produce INCREASE."""
        strong_bull = _make_result(direction=1.0, weight=80.0, confidence=0.9)
        result = self.fusion.fuse([strong_bull], ["trend"])

        assert result.recommendation == "INCREASE"

    def test_recommendation_exit(self):
        """Strong bearish signals should produce EXIT."""
        strong_bear = _make_result(direction=-1.0, weight=80.0, confidence=0.9)
        result = self.fusion.fuse([strong_bear], ["trend"])

        assert result.recommendation == "EXIT"

    def test_recommendation_reduce(self):
        """Moderate bearish signals should produce REDUCE."""
        mod_bear = _make_result(direction=-0.5, weight=50.0, confidence=0.7)
        result = self.fusion.fuse([mod_bear], ["trend"])

        assert result.recommendation == "REDUCE"

    def test_signal_strength(self):
        """signal_strength should be weight * confidence."""
        r = _make_result(direction=1.0, weight=60.0, confidence=0.8)
        result = self.fusion.fuse([r], ["trend"])

        expected = result.weight * result.confidence
        assert abs(result.signal_strength - expected) < 0.01

    def test_dominant_evaluator(self):
        """dominant_evaluator should return the strongest contributor."""
        weak = _make_result(direction=1.0, weight=20.0, confidence=0.5)
        strong = _make_result(direction=-1.0, weight=80.0, confidence=0.9)

        result = self.fusion.fuse(
            [weak, strong],
            ["volume", "trend"],
        )

        assert result.dominant_evaluator == "trend"

    def test_reasoning_contains_evaluators(self):
        """Reasoning should mention top evaluators."""
        r1 = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        r2 = _make_result(direction=-1.0, weight=60.0, confidence=0.8)

        result = self.fusion.fuse(
            [r1, r2],
            ["trend", "momentum"],
        )

        assert "trend" in result.reasoning or "momentum" in result.reasoning

    def test_custom_weights(self):
        """Custom weights should affect fusion."""
        # Default fusion
        fusion_default = EvaluatorFusion()
        r1 = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        r2 = _make_result(direction=-1.0, weight=50.0, confidence=0.7)

        result_default = fusion_default.fuse([r1, r2], ["trend", "momentum"])

        # Custom weights favoring trend
        fusion_custom = EvaluatorFusion(weights={"trend": 2.0, "momentum": 0.5})
        result_custom = fusion_custom.fuse([r1, r2], ["trend", "momentum"])

        # Custom weights should shift direction toward trend (bullish)
        assert result_custom.direction > result_default.direction

    def test_evaluator_results_preserved(self):
        """Original evaluator results should be preserved in fusion result."""
        r1 = _make_result(direction=1.0, weight=50.0, confidence=0.7)
        r2 = _make_result(direction=-1.0, weight=60.0, confidence=0.8)

        result = self.fusion.fuse([r1, r2], ["trend", "momentum"])

        assert len(result.evaluator_results) == 2
        assert result.evaluator_results[0] is r1
        assert result.evaluator_results[1] is r2

    def test_is_actionable(self):
        """is_actionable should check signal_strength > 15 and confidence > 0.4."""
        # Actionable (strong signal)
        strong = _make_result(direction=1.0, weight=50.0, confidence=0.8)
        result_strong = self.fusion.fuse([strong], ["trend"])
        assert result_strong.is_actionable

        # Not actionable (low weight - signal_strength < 15)
        weak_weight = _make_result(direction=1.0, weight=10.0, confidence=0.8)
        result_weak_w = self.fusion.fuse([weak_weight], ["trend"])
        assert not result_weak_w.is_actionable

        # Not actionable (very weak signal)
        very_weak = _make_result(direction=0.1, weight=5.0, confidence=0.3)
        result_very_weak = self.fusion.fuse([very_weak], ["trend"])
        assert not result_very_weak.is_actionable

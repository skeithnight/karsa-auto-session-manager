"""Tests for EvaluatorBridge integration (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.integration import (
    EvaluatorBridge,
    EvaluationMetrics,
    convert_fusion_to_signal_score,
    get_recommendation_confidence,
    should_take_action,
)
from app.alpha.evaluators.fusion import FusionResult
from app.alpha.evaluators.trend_evaluator import EvaluatorResult
from app.alpha.regime_classifier import MarketRegime
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


def _make_features(**kwargs) -> FeatureVector:
    """Create a FeatureVector with defaults for testing."""
    defaults = {
        "close": 105000.0,
        "ema_20": 104500.0,
        "ema_200": 98000.0,
        "sma_20": 104200.0,
        "atr": 1800.0,
        "atr_pct": 1.71,
        "rsi_14": 62.0,
        "adx_14": 32.0,
        "hurst": 0.62,
        "funding_rate": 0.01,
        "oi_change": 0.02,
        "orderbook_delta": 0.15,
        "cvd_slope": 0.08,
        "spread_pct": 0.0005,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def _make_snapshot() -> MarketSnapshot:
    """Create a MarketSnapshot for testing."""
    closes = [100000, 101000, 102000, 103000, 104000,
              104500, 104800, 105000, 105200, 105000]
    candles = np.array([
        [i * 3600000, c * 0.99, c * 1.01, c * 0.98, c, 1000000]
        for i, c in enumerate(closes)
    ])
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=1000000,
        candles=candles,
    )


def _make_fusion_result(**kwargs) -> FusionResult:
    """Create a FusionResult for testing."""
    defaults = {
        "direction": 1.0,
        "weight": 50.0,
        "confidence": 0.7,
        "recommendation": "INCREASE",
        "reasoning": "Test signal",
    }
    defaults.update(kwargs)
    return FusionResult(**defaults)


class TestEvaluatorBridge:
    """Tests for EvaluatorBridge."""

    def setup_method(self):
        self.bridge = EvaluatorBridge()

    def test_initialization(self):
        """Bridge should initialize as enabled."""
        assert self.bridge.enabled

    def test_enable_disable(self):
        """Bridge should be enable/disable-able."""
        self.bridge.disable()
        assert not self.bridge.enabled

        self.bridge.enable()
        assert self.bridge.enabled

    def test_evaluate_enabled(self):
        """evaluate should return result when enabled."""
        features = _make_features()
        snapshot = _make_snapshot()

        result = self.bridge.evaluate(features, snapshot)

        assert result is not None
        assert result.direction != 0.0

    def test_evaluate_disabled(self):
        """evaluate should return None when disabled."""
        self.bridge.disable()
        features = _make_features()
        snapshot = _make_snapshot()

        result = self.bridge.evaluate(features, snapshot)

        assert result is None

    def test_evaluate_with_regime(self):
        """evaluate should accept regime parameter."""
        features = _make_features()
        snapshot = _make_snapshot()

        result = self.bridge.evaluate(features, snapshot, MarketRegime.TREND_BULL)

        assert result is not None

    def test_metrics_collected(self):
        """Metrics should be collected after evaluation."""
        features = _make_features()
        snapshot = _make_snapshot()

        self.bridge.evaluate(features, snapshot)
        metrics = self.bridge.get_metrics()

        assert metrics is not None
        assert metrics.evaluation_time_ms > 0
        assert metrics.evaluator_count == 3
        assert metrics.confidence > 0
        assert metrics.recommendation in ["HOLD", "REDUCE", "EXIT", "INCREASE", "TRAIL"]

    def test_get_available_evaluators(self):
        """get_available_evaluators should return list of evaluators."""
        evaluators = self.bridge.get_available_evaluators()
        assert "trend" in evaluators
        assert "volatility" in evaluators
        assert "momentum" in evaluators

    def test_to_dict(self):
        """to_dict should serialize bridge state."""
        d = self.bridge.to_dict()
        assert "enabled" in d
        assert "registry" in d

    def test_evaluate_exception_handling(self):
        """Bridge should handle evaluator exceptions gracefully."""
        # Bridge should not crash even with bad data
        features = _make_features(rsi_14=None, cvd_slope=None)
        snapshot = _make_snapshot()

        result = self.bridge.evaluate(features, snapshot)
        # Should still return a result (evaluators handle missing data)
        assert result is not None


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_convert_fusion_to_signal_score(self):
        """convert_fusion_to_signal_score should map to 0-100 scale."""
        result = _make_fusion_result(weight=50.0, confidence=0.8)
        score = convert_fusion_to_signal_score(result)

        assert 0 <= score <= 100
        assert score == 40.0  # 50 * 0.8

    def test_get_recommendation_confidence(self):
        """get_recommendation_confidence should return confidence."""
        result = _make_fusion_result(confidence=0.75)
        conf = get_recommendation_confidence(result)

        assert conf == 0.75

    def test_should_take_action_true(self):
        """should_take_action should return True for actionable signals."""
        result = _make_fusion_result(
            weight=50.0,
            confidence=0.8,
            recommendation="INCREASE",
        )
        # Manually set is_actionable properties
        # (FusionResult.is_actionable checks signal_strength > 15 and confidence > 0.4)

        assert should_take_action(result, min_signal_strength=15.0)

    def test_should_take_action_false_weak(self):
        """should_take_action should return False for weak signals."""
        result = _make_fusion_result(weight=10.0, confidence=0.3)

        assert not should_take_action(result, min_signal_strength=15.0)

    def test_should_take_action_custom_threshold(self):
        """should_take_action should respect custom threshold."""
        result = _make_fusion_result(weight=50.0, confidence=0.8)

        assert should_take_action(result, min_signal_strength=30.0)
        assert not should_take_action(result, min_signal_strength=50.0)

"""Tests for EvaluatorRegistry (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.momentum_evaluator import MomentumEvaluator
from app.alpha.evaluators.registry import EvaluatorRegistry
from app.alpha.evaluators.trend_evaluator import EvaluatorResult, TrendEvaluator
from app.alpha.evaluators.volatility_evaluator import VolatilityEvaluator
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


class TestEvaluatorRegistry:
    """Tests for EvaluatorRegistry."""

    def setup_method(self):
        self.registry = EvaluatorRegistry()

    def test_initialization(self):
        """Registry should initialize with default evaluators."""
        evaluators = self.registry.get_available_evaluators()
        assert "trend" in evaluators
        assert "volatility" in evaluators
        assert "momentum" in evaluators

    def test_get_evaluator(self):
        """get_evaluator should return the correct evaluator."""
        trend = self.registry.get_evaluator("trend")
        assert isinstance(trend, TrendEvaluator)

        vol = self.registry.get_evaluator("volatility")
        assert isinstance(vol, VolatilityEvaluator)

        mom = self.registry.get_evaluator("momentum")
        assert isinstance(mom, MomentumEvaluator)

    def test_get_evaluator_not_found(self):
        """get_evaluator should raise KeyError for unknown evaluator."""
        with pytest.raises(KeyError):
            self.registry.get_evaluator("unknown")

    def test_register_evaluator(self):
        """register_evaluator should add a new evaluator."""
        class CustomEvaluator:
            def evaluate(self, features, snapshot=None):
                return EvaluatorResult(direction=0.5, weight=50.0, confidence=0.7, reason="custom")

        self.registry.register_evaluator("custom", CustomEvaluator())
        assert "custom" in self.registry.get_available_evaluators()

    def test_evaluate(self):
        """evaluate should run all evaluators and fuse results."""
        features = _make_features()
        snapshot = _make_snapshot()

        result = self.registry.evaluate(features, snapshot)

        assert result.direction != 0.0
        assert result.weight > 0
        assert result.confidence > 0
        assert result.recommendation in ["HOLD", "REDUCE", "EXIT", "INCREASE", "TRAIL"]

    def test_evaluate_with_regime(self):
        """evaluate with regime should use regime-specific weights."""
        features = _make_features()
        snapshot = _make_snapshot()

        result_bull = self.registry.evaluate(features, snapshot, MarketRegime.TREND_BULL)
        result_range = self.registry.evaluate(features, snapshot, MarketRegime.RANGE)

        # Results should differ due to different weights
        # (though direction might be the same)
        assert result_bull.weight != result_range.weight or \
               result_bull.confidence != result_range.confidence

    def test_evaluate_single(self):
        """evaluate_single should run only one evaluator."""
        features = _make_features()
        snapshot = _make_snapshot()

        result = self.registry.evaluate_single("trend", features, snapshot)

        assert isinstance(result, EvaluatorResult)
        assert result.direction != 0.0

    def test_evaluate_single_not_found(self):
        """evaluate_single should raise KeyError for unknown evaluator."""
        features = _make_features()

        with pytest.raises(KeyError):
            self.registry.evaluate_single("unknown", features)

    def test_to_dict(self):
        """to_dict should serialize registry state."""
        d = self.registry.to_dict()

        assert "evaluators" in d
        assert "fusion_weights" in d
        assert "regime_weights" in d
        assert "trend" in d["evaluators"]

    def test_evaluator_failure_continues(self):
        """evaluate should continue if one evaluator fails."""
        class FailingEvaluator:
            def evaluate(self, features, snapshot=None):
                raise RuntimeError("Evaluator failed")

        self.registry.register_evaluator("failing", FailingEvaluator())

        features = _make_features()
        snapshot = _make_snapshot()

        # Should not raise, just warn
        result = self.registry.evaluate(features, snapshot)
        assert result is not None

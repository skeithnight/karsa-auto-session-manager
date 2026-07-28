"""Tests for MomentumEvaluator (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.momentum_evaluator import MomentumEvaluator
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


def _make_features(**kwargs) -> FeatureVector:
    """Create a FeatureVector with defaults for testing."""
    defaults = {
        "close": 100.0,
        "ema_20": 101.0,
        "ema_200": 95.0,
        "sma_20": 100.5,
        "atr": 500.0,
        "atr_pct": 2.5,
        "rsi_14": 55.0,
        "adx_14": 28.0,
        "hurst": 0.55,
        "funding_rate": 0.01,
        "oi_change": 0.02,
        "orderbook_delta": 0.1,
        "cvd_slope": 0.05,
        "spread_pct": 0.001,
    }
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def _make_snapshot(closes: list[float] | None = None, volumes: list[float] | None = None) -> MarketSnapshot:
    """Create a MarketSnapshot for testing."""
    if closes is None:
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]

    if volumes is None:
        volumes = [1000.0] * len(closes)

    candles = np.array([
        [i * 1000, c - 1, c + 1, c - 2, c, v]
        for i, (c, v) in enumerate(zip(closes, volumes))
    ])
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=len(closes) * 1000,
        candles=candles,
    )


class TestMomentumEvaluator:
    """Tests for MomentumEvaluator."""

    def setup_method(self):
        self.evaluator = MomentumEvaluator()

    def test_bullish_momentum(self):
        """Strong bullish indicators should produce bullish result."""
        features = _make_features(rsi_14=65.0, cvd_slope=0.15)
        result = self.evaluator.evaluate(features)

        assert result.direction == 1.0
        assert result.weight >= 30.0
        assert result.confidence > 0.5

    def test_bearish_momentum(self):
        """Strong bearish indicators should produce bearish result."""
        features = _make_features(rsi_14=35.0, cvd_slope=-0.15)
        result = self.evaluator.evaluate(features)

        assert result.direction == -1.0
        assert result.weight >= 30.0

    def test_neutral_momentum(self):
        """Mixed indicators should produce weak result."""
        features = _make_features(rsi_14=50.0, cvd_slope=0.0)
        result = self.evaluator.evaluate(features)

        assert result.weight < 30.0

    def test_contributions_present(self):
        """Result should contain all contributions."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "rsi" in result.contributions
        assert "cvd_slope" in result.contributions
        assert "price_momentum" in result.contributions
        assert "volume_momentum" in result.contributions

    def test_rsi_overbought(self):
        """RSI > 70 should produce positive score (momentum)."""
        features = _make_features(rsi_14=75.0)
        result = self.evaluator.evaluate(features)

        assert result.contributions["rsi"] > 0

    def test_rsi_oversold(self):
        """RSI < 30 should produce negative score (momentum)."""
        features = _make_features(rsi_14=25.0)
        result = self.evaluator.evaluate(features)

        assert result.contributions["rsi"] < 0

    def test_cvd_positive(self):
        """Positive CVD should produce positive score."""
        features = _make_features(cvd_slope=0.15)
        result = self.evaluator.evaluate(features)

        assert result.contributions["cvd_slope"] > 0

    def test_cvd_negative(self):
        """Negative CVD should produce negative score."""
        features = _make_features(cvd_slope=-0.15)
        result = self.evaluator.evaluate(features)

        assert result.contributions["cvd_slope"] < 0

    def test_missing_features(self):
        """Evaluator should handle missing features gracefully."""
        features = _make_features(rsi_14=None, cvd_slope=None)
        result = self.evaluator.evaluate(features)

        # Should still work with price/volume momentum
        assert result.weight >= 0

    def test_reason_generation(self):
        """Result should have a meaningful reason."""
        features = _make_features(rsi_14=65.0, cvd_slope=0.15)
        result = self.evaluator.evaluate(features)

        assert len(result.reason) > 0

    def test_metadata_present(self):
        """Result should contain metadata with indicator values."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "rsi_14" in result.metadata
        assert "cvd_slope" in result.metadata

    def test_price_momentum_with_snapshot(self):
        """Price momentum should work with snapshot data."""
        snapshot = _make_snapshot(closes=[100.0, 101.0, 102.0, 103.0, 104.0,
                                          105.0, 106.0, 107.0, 108.0, 109.0])
        features = _make_features()
        result = self.evaluator.evaluate(features, snapshot)

        assert result.contributions["price_momentum"] > 0

    def test_volume_momentum_with_snapshot(self):
        """Volume momentum should work with snapshot data."""
        snapshot = _make_snapshot(volumes=[1000.0, 1100.0, 1200.0, 1300.0, 1400.0,
                                          1500.0, 1600.0, 1700.0, 1800.0, 1900.0])
        features = _make_features()
        result = self.evaluator.evaluate(features, snapshot)

        # Increasing volume with uptrend should be positive
        assert result.contributions["volume_momentum"] > 0

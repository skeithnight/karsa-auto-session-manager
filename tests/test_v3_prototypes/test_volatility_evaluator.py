"""Tests for VolatilityEvaluator (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.volatility_evaluator import VolatilityEvaluator
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


def _make_snapshot(closes: list[float] | None = None) -> MarketSnapshot:
    """Create a MarketSnapshot for testing."""
    if closes is None:
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]

    candles = np.array([
        [i * 1000, c - 1, c + 1, c - 2, c, 1000]
        for i, c in enumerate(closes)
    ])
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=len(closes) * 1000,
        candles=candles,
    )


class TestVolatilityEvaluator:
    """Tests for VolatilityEvaluator."""

    def setup_method(self):
        self.evaluator = VolatilityEvaluator()

    def test_high_volatility(self):
        """High ATR should produce favorable volatility score."""
        features = _make_features(atr=2500.0, atr_pct=4.0, hurst=0.65)
        result = self.evaluator.evaluate(features)

        assert result.direction == 1.0
        assert result.weight > 30.0
        assert result.confidence > 0.5

    def test_low_volatility(self):
        """Low ATR should produce unfavorable volatility score."""
        features = _make_features(atr=100.0, atr_pct=0.5, hurst=0.35)
        result = self.evaluator.evaluate(features)

        assert result.direction == -1.0
        assert result.weight > 20.0

    def test_neutral_volatility(self):
        """Medium ATR should produce neutral score."""
        features = _make_features(atr=600.0, atr_pct=2.0, hurst=0.5)
        result = self.evaluator.evaluate(features)

        # Should be somewhere in the middle
        assert -50 < result.weight < 50

    def test_contributions_present(self):
        """Result should contain all contributions."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "atr_percentile" in result.contributions
        assert "atr_pct" in result.contributions
        assert "hurst" in result.contributions
        assert "price_range" in result.contributions

    def test_hurst_trending(self):
        """High Hurst should produce positive score."""
        features = _make_features(hurst=0.7)
        result = self.evaluator.evaluate(features)

        assert result.contributions["hurst"] > 0

    def test_hurst_mean_reverting(self):
        """Low Hurst should produce negative score."""
        features = _make_features(hurst=0.3)
        result = self.evaluator.evaluate(features)

        assert result.contributions["hurst"] < 0

    def test_missing_features(self):
        """Evaluator should handle missing features gracefully."""
        features = _make_features(atr=None, atr_pct=None, hurst=None)
        result = self.evaluator.evaluate(features)

        assert result.direction == 0.0
        assert result.weight < 20.0

    def test_reason_generation(self):
        """Result should have a meaningful reason."""
        features = _make_features(atr=2500.0, atr_pct=4.0)
        result = self.evaluator.evaluate(features)

        assert len(result.reason) > 0

    def test_metadata_present(self):
        """Result should contain metadata with indicator values."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "atr" in result.metadata
        assert "atr_pct" in result.metadata
        assert "hurst" in result.metadata

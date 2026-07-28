"""Tests for TrendEvaluator (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.trend_evaluator import EvaluatorResult, TrendEvaluator
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


def _make_features(**kwargs) -> FeatureVector:
    """Create a FeatureVector with defaults for testing."""
    defaults = {
        "close": 100.0,
        "ema_20": 101.0,
        "ema_200": 95.0,
        "sma_20": 100.5,
        "atr": 2.0,
        "atr_pct": 2.0,
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


def _make_snapshot() -> MarketSnapshot:
    """Create a MarketSnapshot for testing."""
    candles = np.array([
        [1000, 100, 102, 98, 101, 1000],
        [2000, 101, 103, 99, 102, 1200],
        [3000, 102, 104, 100, 103, 1100],
    ])
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=3000,
        candles=candles,
    )


class TestTrendEvaluator:
    """Tests for TrendEvaluator."""

    def setup_method(self):
        self.evaluator = TrendEvaluator()

    def test_bullish_trend(self):
        """Strong bullish features should produce bullish result."""
        features = _make_features(
            close=110.0,
            ema_20=108.0,
            ema_200=95.0,
            sma_20=107.0,
            rsi_14=65.0,
            adx_14=35.0,
        )
        result = self.evaluator.evaluate(features)

        assert isinstance(result, EvaluatorResult)
        assert result.direction == 1.0
        assert result.weight > 20.0
        assert result.confidence > 0.5
        assert result.is_actionable

    def test_bearish_trend(self):
        """Strong bearish features should produce bearish result."""
        features = _make_features(
            close=90.0,
            ema_20=92.0,
            ema_200=105.0,
            sma_20=93.0,
            rsi_14=35.0,
            adx_14=32.0,
        )
        result = self.evaluator.evaluate(features)

        assert result.direction == -1.0
        assert result.weight > 20.0
        assert result.confidence > 0.5

    def test_neutral_market(self):
        """Mixed signals should produce weak result."""
        # Use values that produce mixed/neutral signals
        features = _make_features(
            close=100.0,
            ema_20=100.2,  # Slightly above
            ema_200=99.8,   # Slightly below
            sma_20=100.1,
            rsi_14=52.0,    # Neutral
            adx_14=18.0,    # Weak trend
        )
        result = self.evaluator.evaluate(features)

        # Should not be strongly bullish or bearish
        # Weight should be moderate (not strongly directional)
        assert result.weight < 25.0  # Moderate weight, not strongly directional

    def test_contributions_present(self):
        """Result should contain all contributions."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "ema_crossover" in result.contributions
        assert "adx_strength" in result.contributions
        assert "price_ma_position" in result.contributions
        assert "volume_confirmation" in result.contributions
        assert "momentum" in result.contributions

    def test_metadata_present(self):
        """Result should contain metadata with indicator values."""
        features = _make_features()
        result = self.evaluator.evaluate(features)

        assert "ema_20" in result.metadata
        assert "ema_200" in result.metadata
        assert "adx_14" in result.metadata
        assert "rsi_14" in result.metadata

    def test_signal_strength(self):
        """signal_strength should be weight * confidence."""
        features = _make_features(
            close=110.0,
            ema_20=108.0,
            ema_200=95.0,
            rsi_14=65.0,
            adx_14=35.0,
        )
        result = self.evaluator.evaluate(features)

        expected = result.weight * result.confidence
        assert abs(result.signal_strength - expected) < 0.01

    def test_missing_features(self):
        """Evaluator should handle missing features gracefully."""
        features = _make_features(
            ema_20=None,
            ema_200=None,
            sma_20=None,
            rsi_14=None,
            adx_14=None,
        )
        result = self.evaluator.evaluate(features)

        assert result.direction == 0.0
        assert result.weight < 10.0
        assert not result.is_actionable

    def test_reason_generation(self):
        """Result should have a meaningful reason."""
        features = _make_features(
            close=110.0,
            ema_20=108.0,
            ema_200=95.0,
            rsi_14=65.0,
            adx_14=35.0,
        )
        result = self.evaluator.evaluate(features)

        assert len(result.reason) > 0
        assert "bullish" in result.reason.lower() or "bearish" in result.reason.lower()

    def test_ema_crossover_bullish(self):
        """EMA crossover should score bullish when EMA20 > EMA200."""
        features = _make_features(
            close=110.0,
            ema_20=108.0,
            ema_200=95.0,
        )
        result = self.evaluator.evaluate(features)

        # EMA crossover should contribute positively
        assert result.contributions["ema_crossover"] > 0

    def test_ema_crossover_bearish(self):
        """EMA crossover should score bearish when EMA20 < EMA200."""
        features = _make_features(
            close=90.0,
            ema_20=92.0,
            ema_200=105.0,
        )
        result = self.evaluator.evaluate(features)

        assert result.contributions["ema_crossover"] < 0

    def test_adx_strong_trend(self):
        """High ADX should produce strong trend score."""
        features = _make_features(
            adx_14=45.0,
            close=110.0,
            ema_20=108.0,
        )
        result = self.evaluator.evaluate(features)

        # ADX > 40 should give strong contribution
        assert abs(result.contributions["adx_strength"]) > 40

    def test_rsi_overbought(self):
        """RSI > 70 should produce bullish momentum score."""
        features = _make_features(rsi_14=75.0)
        result = self.evaluator.evaluate(features)

        assert result.contributions["momentum"] > 0

    def test_rsi_oversold(self):
        """RSI < 30 should produce bearish momentum score."""
        features = _make_features(rsi_14=25.0)
        result = self.evaluator.evaluate(features)

        assert result.contributions["momentum"] < 0

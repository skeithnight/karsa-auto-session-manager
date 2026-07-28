"""Tests for ScoringBridge (v3.5 prototype)."""
from __future__ import annotations

import numpy as np
import pytest

from app.alpha.evaluators.scoring_bridge import ScoringBridge
from app.alpha.evaluators.fusion import FusionResult
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


class TestScoringBridge:
    """Tests for ScoringBridge."""

    def setup_method(self):
        self.bridge = ScoringBridge()

    def test_initialization(self):
        """Bridge should initialize as disabled by default."""
        assert not self.bridge.enabled

    def test_enable_disable(self):
        """Bridge should be enable/disable-able."""
        self.bridge.enable()
        assert self.bridge.enabled

        self.bridge.disable()
        assert not self.bridge.enabled

    @pytest.mark.asyncio
    async def test_score_disabled(self):
        """score should return fallback when disabled."""
        features = _make_features()
        snapshot = _make_snapshot()

        score, result = await self.bridge.score(
            features, snapshot, fallback_score=50.0
        )

        assert score == 50.0
        assert result is None

    @pytest.mark.asyncio
    async def test_score_enabled(self):
        """score should return v3.5 score when enabled."""
        self.bridge.enable()
        features = _make_features()
        snapshot = _make_snapshot()

        score, result = await self.bridge.score(features, snapshot)

        assert score > 0
        assert result is not None
        assert result.direction != 0.0

    @pytest.mark.asyncio
    async def test_score_with_regime(self):
        """score should accept regime parameter."""
        self.bridge.enable()
        features = _make_features()
        snapshot = _make_snapshot()

        score, result = await self.bridge.score(
            features, snapshot, regime=MarketRegime.TREND_BULL
        )

        assert score > 0
        assert result is not None

    @pytest.mark.asyncio
    async def test_score_fallback_on_error(self):
        """score should fallback on evaluator error."""
        self.bridge.enable()
        # Use features that might cause issues
        features = _make_features(rsi_14=None, cvd_slope=None)
        snapshot = _make_snapshot()

        score, result = await self.bridge.score(
            features, snapshot, fallback_score=40.0
        )

        # Should still return a score (evaluators handle missing data)
        assert score >= 0

    def test_should_trade(self):
        """should_trade should check score and confidence."""
        from app.alpha.evaluators.fusion import FusionResult

        # Actionable result
        result = FusionResult(
            direction=1.0,
            weight=80.0,
            confidence=0.9,
            recommendation="INCREASE",
        )

        assert self.bridge.should_trade(result, min_score=50.0)

        # Weak result
        weak = FusionResult(
            direction=0.5,
            weight=20.0,
            confidence=0.3,
            recommendation="HOLD",
        )

        assert not self.bridge.should_trade(weak, min_score=50.0)

    def test_to_dict(self):
        """to_dict should serialize bridge state."""
        d = self.bridge.to_dict()
        assert "enabled" in d
        assert "registry" in d

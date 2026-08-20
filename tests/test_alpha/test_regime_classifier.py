"""Tests for Phase 6 RegimeClassifier — per-symbol regime detection + conviction scaling."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.alpha.regime_classifier import (
    CONVICTION_ADX_CEILING,
    CONVICTION_ADX_FLOOR,
    CONVICTION_CHOP_DEFAULT,
    CONVICTION_HURST_CEILING,
    CONVICTION_HURST_FLOOR,
    MarketRegime,
    RegimeClassifier,
)
from app.core.feature_extractor import FeatureExtractor, FeatureVector
from app.core.feature_store import FeatureStore
from app.core.market_snapshot import MarketSnapshot


def _make_snapshot(candles: list[list[float]]) -> MarketSnapshot:
    """Build a MarketSnapshot from raw candle data."""
    arr = np.array(candles, dtype=float)
    if arr.ndim == 2 and arr.shape[0] > 0:
        return MarketSnapshot(symbol="BTC/USDT", timestamp_ms=int(arr[-1][0]), candles=arr)
    # Empty or 1D: create a minimal snapshot
    return MarketSnapshot(symbol="BTC/USDT", timestamp_ms=0, candles=arr.reshape(0, 6) if arr.ndim < 2 else arr)


def _make_features(**overrides) -> FeatureVector:
    """Build a FeatureVector with sensible defaults + overrides."""
    defaults = {
        "adx_14": 30.0,
        "hurst": 0.5,
        "atr_pct": 50.0,
        "sma_20": 100.0,
        "close": 100.0,
    }
    defaults.update(overrides)
    # Build a minimal FeatureVector — mock for unit tests
    fv = MagicMock(spec=FeatureVector)
    for k, v in defaults.items():
        setattr(fv, k, v)
    return fv


class TestRegimeClassifierClassify:
    """Test classify() method — pure, no Redis needed."""

    def test_insufficient_data_returns_chop(self):
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[0, 0, 101, 99, 100, 1000]] * 30)
        features = _make_features()
        assert rc.classify(features, snapshot) == MarketRegime.CHOP

    def test_all_flat_returns_range(self):
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[0, 100, 101, 99, 100, 1000]] * 60)
        features = _make_features()
        assert rc.classify(features, snapshot) == MarketRegime.RANGE

    def test_empty_candles_returns_chop(self):
        rc = RegimeClassifier()
        snapshot = _make_snapshot([])
        features = _make_features()
        assert rc.classify(features, snapshot) == MarketRegime.CHOP

    def test_list_input_works(self):
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[0, 100, 101, 99, 100, 1000]] * 60)
        features = _make_features()
        regime = rc.classify(features, snapshot)
        assert regime in MarketRegime


class TestClassifyWithConviction:
    """Test classify_with_conviction() — conviction score 0.0-1.0."""

    def test_weak_trend_bull_low_conviction(self):
        """ADX=26 should give ~0.07 conviction (barely trending)."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(60)])
        features = _make_features(adx_14=26.0, hurst=0.55, sma_20=95.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime == MarketRegime.TREND_BULL
        assert 0.0 <= conviction <= 0.15  # Very weak

    def test_strong_trend_bull_full_conviction(self):
        """ADX=45 should give ~1.0 conviction (strong trend)."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(60)])
        features = _make_features(adx_14=45.0, hurst=0.6, sma_20=95.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime in (MarketRegime.TREND_BULL, MarketRegime.HYPER_BULL)
        assert conviction == 1.0

    def test_hyper_bull_always_full_conviction(self):
        """HYPER regimes always have conviction=1.0."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(60)])
        features = _make_features(adx_14=50.0, hurst=0.6, sma_20=95.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime == MarketRegime.HYPER_BULL
        assert conviction == 1.0

    def test_range_strong_mean_reversion_high_conviction(self):
        """Hurst=0.32 should give high conviction in RANGE."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[0, 100, 101, 99, 100, 1000]] * 60)
        features = _make_features(adx_14=15.0, hurst=0.32, sma_20=100.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime == MarketRegime.RANGE
        assert conviction > 0.7

    def test_range_weak_mean_reversion_low_conviction(self):
        """Hurst=0.44 should give low conviction in RANGE."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[0, 100, 101, 99, 100, 1000]] * 60)
        features = _make_features(adx_14=15.0, hurst=0.44, sma_20=100.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime == MarketRegime.RANGE
        assert conviction < 0.1

    def test_chop_always_low_conviction(self):
        """CHOP regime always has conviction=0.3."""
        rc = RegimeClassifier()
        # Need non-flat candles to avoid the flat-price RANGE guard
        # Use wobbling prices so flat guard doesn't trigger
        candles = [[i, 100 + (i % 3), 102, 99, 100 + (i % 3), 1000] for i in range(60)]
        snapshot = _make_snapshot(candles)
        # atr_pct > 80 AND adx < 20 → CHOP
        features = _make_features(adx_14=10.0, hurst=0.5, atr_pct=90.0, sma_20=100.0)
        regime, conviction = rc.classify_with_conviction(features, snapshot)
        assert regime == MarketRegime.CHOP
        assert conviction == CONVICTION_CHOP_DEFAULT

    def test_conviction_bounded_0_to_1(self):
        """Conviction should always be between 0.0 and 1.0."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(60)])
        for adx in [20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0]:
            features = _make_features(adx_14=adx, hurst=0.5, sma_20=95.0)
            _, conviction = rc.classify_with_conviction(features, snapshot)
            assert 0.0 <= conviction <= 1.0, f"Conviction {conviction} out of bounds for ADX={adx}"

    def test_trend_conviction_linear_scaling(self):
        """TREND conviction should scale linearly from ADX=25 to ADX=40."""
        rc = RegimeClassifier()
        snapshot = _make_snapshot([[i, 100, 101, 99, 100 + i * 0.1, 1000] for i in range(60)])

        # ADX=25 → conviction=0.0
        features = _make_features(adx_14=25.0, hurst=0.5, sma_20=95.0)
        _, conv_25 = rc.classify_with_conviction(features, snapshot)
        assert abs(conv_25 - 0.0) < 0.01

        # ADX=32.5 → conviction=0.5
        rc._adx_history.clear()
        features = _make_features(adx_14=32.5, hurst=0.5, sma_20=95.0)
        _, conv_32 = rc.classify_with_conviction(features, snapshot)
        assert abs(conv_32 - 0.5) < 0.05

        # ADX=40 → conviction=1.0
        rc._adx_history.clear()
        features = _make_features(adx_14=40.0, hurst=0.5, sma_20=95.0)
        _, conv_40 = rc.classify_with_conviction(features, snapshot)
        assert abs(conv_40 - 1.0) < 0.01


class TestGetCurrentRegimePerSymbol:
    """Test get_current_regime() reads per-symbol key first, falls back to global."""

    def setup_method(self):
        self.rc = RegimeClassifier()

    @pytest.mark.asyncio
    async def test_per_symbol_key_used_when_present(self):
        """When per-symbol regime exists, use it — not the global one."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:ETH:USDT": "TREND_BULL",
                "system:config:regime": json.dumps({"regime": "CHOP"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("ETH/USDT")
        assert regime == MarketRegime.TREND_BULL
        mock_redis.get.assert_any_call("system:regime:ETH:USDT")

    @pytest.mark.asyncio
    async def test_fallback_to_global_when_no_per_symbol(self):
        """When per-symbol key missing, fall back to global BTC regime."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:SOL:USDT": None,
                "system:config:regime": json.dumps({"regime": "RANGE"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("SOL/USDT")
        assert regime == MarketRegime.RANGE

    @pytest.mark.asyncio
    async def test_fallback_to_chop_when_nothing_in_redis(self):
        """When nothing in Redis, return CHOP (conservative)."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_no_redis_returns_chop(self):
        """When no Redis client, return CHOP."""
        self.rc._redis = None
        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_redis_read_error_returns_chop(self):
        """When Redis read fails, return CHOP."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("connection lost"))
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_per_symbol_takes_priority_over_global(self):
        """Per-symbol CHOP should override global TREND_BULL."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:DOGE:USDT": "CHOP",
                "system:config:regime": json.dumps({"regime": "TREND_BULL"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("DOGE/USDT")
        assert regime == MarketRegime.CHOP

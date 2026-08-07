"""Tests for Statistical Feature Engine — quantitative feature calculations."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from app.alpha.statistical_engine import (
    BREAKOUT_DISTANCE_PCT,
    BREAKOUT_VOLUME_SPIKE,
    VOL_HIGH_THRESHOLD,
    VOL_LOW_THRESHOLD,
    StatisticalFeatureEngine,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_ohlcv(
    n: int = 100,
    base_price: float = 100.0,
    trend: float = 0.0,
    vol_base: float = 1000.0,
) -> pd.DataFrame:
    """Create synthetic OHLCV DataFrame."""
    closes = [base_price + trend * i for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [vol_base + np.random.uniform(-100, 100) for _ in range(n)],
        }
    )


def _make_btc_ohlcv(n: int = 100, base_price: float = 50000.0) -> pd.DataFrame:
    """Create synthetic BTC OHLCV DataFrame with realistic volatility."""
    np.random.seed(42)
    returns = np.random.normal(0, 0.02, n)
    closes = [base_price]
    for r in returns[1:]:
        closes.append(closes[-1] * (1 + r))
    return pd.DataFrame(
        {
            "open": [c * 0.999 for c in closes],
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [50000.0 + np.random.uniform(-5000, 5000) for _ in range(n)],
        }
    )


def _make_correlated_ohlcv(btc_ohlcv: pd.DataFrame, beta: float = 1.0) -> pd.DataFrame:
    """Create OHLCV correlated with BTC at a given beta."""
    btc_returns = btc_ohlcv["close"].pct_change().fillna(0).values
    n = len(btc_returns)
    noise = np.random.normal(0, 0.005, n)
    coin_returns = beta * btc_returns + noise
    closes = [100.0]
    for r in coin_returns[1:]:
        closes.append(closes[-1] * (1 + r))
    return pd.DataFrame(
        {
            "open": [c * 0.999 for c in closes],
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1000.0 + np.random.uniform(-100, 100) for _ in range(n)],
        }
    )


# ------------------------------------------------------------------
# Beta calculation
# ------------------------------------------------------------------


class TestBetaCalculation:
    def test_beta_with_known_data(self):
        """Beta of coin that moves 2x BTC should be ~2."""
        np.random.seed(123)
        btc_returns = np.random.normal(0, 0.02, 100)
        coin_returns = 2.0 * btc_returns + np.random.normal(0, 0.001, 100)

        engine = StatisticalFeatureEngine()
        beta = engine._calculate_beta(coin_returns, btc_returns)
        assert abs(beta - 2.0) < 0.15  # Allow some noise tolerance

    def test_beta_with_zero_btc_variance(self):
        """Beta should be 0.0 when BTC returns are constant."""
        coin_returns = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
        btc_returns = np.array([0.0, 0.0, 0.0, 0.0, 0.0])

        engine = StatisticalFeatureEngine()
        beta = engine._calculate_beta(coin_returns, btc_returns)
        assert beta == 0.0

    def test_beta_with_insufficient_data(self):
        """Beta should be 0.0 with fewer than 2 data points."""
        engine = StatisticalFeatureEngine()
        assert engine._calculate_beta(np.array([0.01]), np.array([0.01])) == 0.0
        assert engine._calculate_beta(np.array([]), np.array([])) == 0.0

    def test_beta_negative_correlation(self):
        """Negative beta when coin moves opposite to BTC."""
        np.random.seed(456)
        btc_returns = np.random.normal(0, 0.02, 100)
        coin_returns = -1.0 * btc_returns + np.random.normal(0, 0.001, 100)

        engine = StatisticalFeatureEngine()
        beta = engine._calculate_beta(coin_returns, btc_returns)
        assert beta < 0.0

    def test_beta_window_truncation(self):
        """Beta should use at most 720 periods (30-day window)."""
        np.random.seed(789)
        n = 1000
        btc_returns = np.random.normal(0, 0.02, n)
        coin_returns = btc_returns + np.random.normal(0, 0.001, n)

        engine = StatisticalFeatureEngine()
        beta = engine._calculate_beta(coin_returns, btc_returns)
        assert isinstance(beta, float)


# ------------------------------------------------------------------
# Correlation calculation
# ------------------------------------------------------------------


class TestCorrelationCalculation:
    def test_perfect_correlation(self):
        """Perfectly correlated series should yield correlation ~1.0."""
        engine = StatisticalFeatureEngine()
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        corr = engine._calculate_correlation(data, data, 8)
        assert abs(corr - 1.0) < 0.001

    def test_zero_variance_returns_zero(self):
        """Zero variance in either series should yield 0.0."""
        engine = StatisticalFeatureEngine()
        coin = np.array([1.0, 2.0, 3.0, 4.0])
        btc = np.array([5.0, 5.0, 5.0, 5.0])
        corr = engine._calculate_correlation(coin, btc, 4)
        assert corr == 0.0

    def test_insufficient_window(self):
        """Window < 2 should yield 0.0."""
        engine = StatisticalFeatureEngine()
        data = np.array([1.0, 2.0])
        assert engine._calculate_correlation(data, data, 1) == 0.0

    def test_negative_correlation(self):
        """Opposite-direction series should yield negative correlation."""
        engine = StatisticalFeatureEngine()
        coin = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        btc = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        corr = engine._calculate_correlation(coin, btc, 5)
        assert corr < -0.99

    def test_short_window_24h(self):
        """24h window should use at most 24 data points."""
        engine = StatisticalFeatureEngine()
        np.random.seed(101)
        coin = np.random.normal(0, 0.01, 200)
        btc = np.random.normal(0, 0.01, 200)
        corr = engine._calculate_correlation(coin, btc, 24)
        assert -1.0 <= corr <= 1.0


# ------------------------------------------------------------------
# ATR calculation
# ------------------------------------------------------------------


class TestATRCalculation:
    def test_atr_positive(self):
        """ATR should be positive for non-flat data."""
        engine = StatisticalFeatureEngine()
        n = 30
        highs = np.array([105.0 + i for i in range(n)])
        lows = np.array([95.0 + i for i in range(n)])
        closes = np.array([100.0 + i for i in range(n)])
        atr = engine._calculate_atr(highs, lows, closes)
        assert atr > 0

    def test_atr_insufficient_data(self):
        """ATR should be 0.0 with fewer than period+1 candles."""
        engine = StatisticalFeatureEngine()
        highs = np.array([105.0] * 5)
        lows = np.array([95.0] * 5)
        closes = np.array([100.0] * 5)
        assert engine._calculate_atr(highs, lows, closes) == 0.0

    def test_atr_flat_market(self):
        """ATR should be 0.0 for completely flat market."""
        engine = StatisticalFeatureEngine()
        n = 20
        prices = np.array([100.0] * n)
        atr = engine._calculate_atr(prices, prices, prices)
        assert atr == 0.0

    def test_atr_range_bounded(self):
        """ATR should not exceed high-low range for trending data."""
        engine = StatisticalFeatureEngine()
        closes = np.arange(100.0, 130.0)
        highs = closes + 2.0
        lows = closes - 2.0
        atr = engine._calculate_atr(highs, lows, closes)
        assert 0.0 < atr <= 4.0


# ------------------------------------------------------------------
# Volume spike detection
# ------------------------------------------------------------------


class TestVolumeSpike:
    def test_volume_spike_high(self):
        """Volume much higher than SMA should produce high spike ratio."""
        engine = StatisticalFeatureEngine()
        volumes = np.array([100.0] * 20 + [500.0])
        sma = engine._sma(volumes, 20)
        spike = volumes[-1] / sma if sma > 0 else 1.0
        assert spike > 3.0

    def test_volume_spike_normal(self):
        """Volume near SMA should produce ratio near 1.0."""
        engine = StatisticalFeatureEngine()
        volumes = np.array([100.0] * 21)
        sma = engine._sma(volumes, 20)
        spike = volumes[-1] / sma if sma > 0 else 1.0
        assert abs(spike - 1.0) < 0.01

    def test_volume_trend_slope_positive(self):
        """Increasing volume should yield positive slope."""
        engine = StatisticalFeatureEngine()
        volumes = np.array([100.0 + i * 10 for i in range(20)])
        slope = engine._volume_trend_slope(volumes, 20)
        assert slope > 0

    def test_volume_trend_slope_zero(self):
        """Flat volume should yield zero slope."""
        engine = StatisticalFeatureEngine()
        volumes = np.array([100.0] * 20)
        slope = engine._volume_trend_slope(volumes, 20)
        assert abs(slope) < 0.001


# ------------------------------------------------------------------
# Volatility regime classification
# ------------------------------------------------------------------


class TestVolatilityRegime:
    def test_low_regime(self):
        engine = StatisticalFeatureEngine()
        assert engine.get_volatility_regime(1.0) == "LOW"
        assert engine.get_volatility_regime(0.0) == "LOW"

    def test_medium_regime(self):
        engine = StatisticalFeatureEngine()
        assert engine.get_volatility_regime(3.0) == "MEDIUM"
        assert engine.get_volatility_regime(VOL_LOW_THRESHOLD + 0.1) == "MEDIUM"

    def test_high_regime(self):
        engine = StatisticalFeatureEngine()
        assert engine.get_volatility_regime(6.0) == "HIGH"
        assert engine.get_volatility_regime(VOL_HIGH_THRESHOLD + 0.1) == "HIGH"

    def test_boundary_low_medium(self):
        engine = StatisticalFeatureEngine()
        assert engine.get_volatility_regime(VOL_LOW_THRESHOLD) == "MEDIUM"

    def test_boundary_medium_high(self):
        engine = StatisticalFeatureEngine()
        assert engine.get_volatility_regime(VOL_HIGH_THRESHOLD) == "MEDIUM"


# ------------------------------------------------------------------
# Breakout confirmation
# ------------------------------------------------------------------


class TestBreakoutConfirmation:
    def test_breakout_confirmed(self):
        engine = StatisticalFeatureEngine()
        assert engine.is_breakout_confirmed(
            BREAKOUT_VOLUME_SPIKE + 0.1, BREAKOUT_DISTANCE_PCT + 0.1
        ) is True

    def test_no_breakout_low_volume(self):
        engine = StatisticalFeatureEngine()
        assert engine.is_breakout_confirmed(1.0, BREAKOUT_DISTANCE_PCT + 1.0) is False

    def test_no_breakout_low_distance(self):
        engine = StatisticalFeatureEngine()
        assert engine.is_breakout_confirmed(BREAKOUT_VOLUME_SPIKE + 0.1, 0.5) is False

    def test_breakout_negative_distance(self):
        """Breakout should also trigger for large negative distance (below EMA)."""
        engine = StatisticalFeatureEngine()
        assert engine.is_breakout_confirmed(
            BREAKOUT_VOLUME_SPIKE + 0.1, -(BREAKOUT_DISTANCE_PCT + 0.1)
        ) is True


# ------------------------------------------------------------------
# Feature output structure
# ------------------------------------------------------------------


class TestFeatureOutputStructure:
    @pytest.mark.asyncio
    async def test_output_has_all_fields(self):
        """Feature dict should contain all required fields."""
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(100)
        btc = _make_btc_ohlcv(100)

        result = await engine.calculate_features("SOL/USDT", ohlcv, btc, funding_rate=0.0001)

        required_fields = [
            "symbol",
            "timestamp",
            "beta_30d",
            "correlation_24h",
            "correlation_7d",
            "atr_pct",
            "std_dev_returns",
            "volatility_regime",
            "volume_spike_ratio",
            "volume_trend_slope",
            "volume_regime",
            "distance_from_ema50_pct",
            "distance_from_vwap_pct",
            "price_vs_ema50",
            "funding_rate",
            "funding_rate_8h_avg",
            "annualized_funding_cost_pct",
            "breakout_confirmed",
            "overextended",
            "volume_confirmed",
        ]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_output_symbol_matches(self):
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(100)
        btc = _make_btc_ohlcv(100)

        result = await engine.calculate_features("ETH/USDT", ohlcv, btc)
        assert result["symbol"] == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_output_timestamp_is_iso(self):
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(100)
        btc = _make_btc_ohlcv(100)

        result = await engine.calculate_features("BTC/USDT", ohlcv, btc)
        # Should be parseable as ISO format
        datetime.fromisoformat(result["timestamp"])

    @pytest.mark.asyncio
    async def test_insufficient_candles_returns_defaults(self):
        """With fewer than MIN_CANDLES, should return default feature dict."""
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(10)
        btc = _make_btc_ohlcv(10)

        result = await engine.calculate_features("SOL/USDT", ohlcv, btc)
        assert result["beta_30d"] == 0.0
        assert result["volatility_regime"] == "MEDIUM"
        assert result["volume_spike_ratio"] == 1.0

    @pytest.mark.asyncio
    async def test_null_dataframe_returns_defaults(self):
        engine = StatisticalFeatureEngine()
        result = await engine.calculate_features("SOL/USDT", None, None)
        assert result["beta_30d"] == 0.0


# ------------------------------------------------------------------
# Funding rate metrics
# ------------------------------------------------------------------


class TestFundingRate:
    @pytest.mark.asyncio
    async def test_annualized_funding_cost(self):
        """Annualized cost = rate * 3 * 365 * 100."""
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(100)
        btc = _make_btc_ohlcv(100)

        result = await engine.calculate_features("SOL/USDT", ohlcv, btc, funding_rate=0.0001)
        expected = round(0.0001 * 3 * 365 * 100, 4)
        assert result["annualized_funding_cost_pct"] == expected

    @pytest.mark.asyncio
    async def test_zero_funding_rate(self):
        engine = StatisticalFeatureEngine()
        ohlcv = _make_ohlcv(100)
        btc = _make_btc_ohlcv(100)

        result = await engine.calculate_features("SOL/USDT", ohlcv, btc, funding_rate=0.0)
        assert result["funding_rate"] == 0.0
        assert result["annualized_funding_cost_pct"] == 0.0


# ------------------------------------------------------------------
# Redis caching
# ------------------------------------------------------------------


class TestRedisCaching:
    @pytest.mark.asyncio
    async def test_get_features_returns_none_without_redis(self):
        engine = StatisticalFeatureEngine(redis_client=None)
        result = await engine.get_features("SOL/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_features_returns_cached_data(self):
        mock_redis = AsyncMock()
        cached = {"symbol": "SOL/USDT", "beta_30d": 1.5}
        mock_redis.get = AsyncMock(return_value=json.dumps(cached))

        engine = StatisticalFeatureEngine(redis_client=mock_redis)
        result = await engine.get_features("SOL/USDT")
        assert result is not None
        assert result["symbol"] == "SOL/USDT"
        assert result["beta_30d"] == 1.5

    @pytest.mark.asyncio
    async def test_get_features_returns_none_on_missing_key(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        engine = StatisticalFeatureEngine(redis_client=mock_redis)
        result = await engine.get_features("SOL/USDT")
        assert result is None


# ------------------------------------------------------------------
# Volume regime
# ------------------------------------------------------------------


class TestVolumeRegime:
    def test_depressed(self):
        engine = StatisticalFeatureEngine()
        assert engine._get_volume_regime(0.3) == "DEPRESSED"

    def test_normal(self):
        engine = StatisticalFeatureEngine()
        assert engine._get_volume_regime(1.0) == "NORMAL"
        assert engine._get_volume_regime(2.5) == "NORMAL"

    def test_elevated(self):
        engine = StatisticalFeatureEngine()
        assert engine._get_volume_regime(3.5) == "ELEVATED"

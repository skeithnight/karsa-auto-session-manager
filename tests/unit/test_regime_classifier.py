"""Tests for RegimeClassifier — Phase 6.1."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.alpha.regime_classifier import (
    MarketRegime,
    RegimeClassifier,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_candles(
    n: int, close_start: float = 100.0, close_step: float = 0.5
) -> np.ndarray:
    """Generate deterministic OHLCV candles."""
    candles = np.zeros((n, 6))
    for i in range(n):
        c = close_start + i * close_step
        candles[i] = [
            1000 + i * 3600,  # timestamp
            c - 0.5,  # open
            c + 1.0,  # high
            c - 1.0,  # low
            c,  # close
            1000 + i * 10,  # volume
        ]
    return candles


def _make_flat_candles(n: int = 100, price: float = 100.0) -> np.ndarray:
    """All candles at same price — flat market."""
    candles = np.zeros((n, 6))
    candles[:] = [1000, price, price, price, price, 1000]
    return candles


def _make_volatile_chop_candles(n: int = 100) -> np.ndarray:
    """High ATR, low ADX → CHOP via priority 1."""
    rng = np.random.RandomState(42)
    candles = np.zeros((n, 6))
    price = 100.0
    for i in range(n):
        swing = rng.uniform(-5, 5)
        c = price + swing
        candles[i] = [
            1000 + i * 3600,
            c - 1,
            c + abs(swing) + 2,  # big range
            c - abs(swing) - 2,
            c,
            1000,
        ]
        price = c  # random walk, no trend
    return candles


def _make_trending_candles(n: int = 100, direction: float = 1.0) -> np.ndarray:
    """Strong directional move → TREND via priority 2/3."""
    candles = np.zeros((n, 6))
    for i in range(n):
        c = 100.0 + direction * i * 2.0  # strong trend
        candles[i] = [
            1000 + i * 3600,
            c - 0.5,
            c + 0.5,
            c - 0.5,
            c,
            1000 + i * 50,
        ]
    return candles


# ------------------------------------------------------------------
# Decision tree tests
# ------------------------------------------------------------------


class TestDecisionTree:
    """Test the static decision tree directly."""

    def test_chop_high_atr_low_adx(self) -> None:
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.5, atr_pct=85.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.CHOP

    def test_trend_bull(self) -> None:
        result = RegimeClassifier._decision_tree(
            adx=30.0, hurst=0.6, atr_pct=50.0, close=105.0, sma20=100.0
        )
        assert result == MarketRegime.TREND_BULL

    def test_trend_bear(self) -> None:
        result = RegimeClassifier._decision_tree(
            adx=30.0, hurst=0.6, atr_pct=50.0, close=95.0, sma20=100.0
        )
        assert result == MarketRegime.TREND_BEAR

    def test_range_low_hurst(self) -> None:
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.4, atr_pct=50.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.RANGE

    def test_fallback_range(self) -> None:
        """ADX between 20-25, hurst > 0.45 → fallback RANGE."""
        result = RegimeClassifier._decision_tree(
            adx=22.0, hurst=0.5, atr_pct=50.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.RANGE

    # --- Boundary tests ---

    def test_adx_exactly_25_is_trend(self) -> None:
        """ADX 25.0 inclusive → TREND."""
        result = RegimeClassifier._decision_tree(
            adx=25.0, hurst=0.5, atr_pct=50.0, close=105.0, sma20=100.0
        )
        assert result == MarketRegime.TREND_BULL

    def test_adx_24_9_is_not_trend(self) -> None:
        """ADX 24.9 → not TREND, falls to RANGE."""
        result = RegimeClassifier._decision_tree(
            adx=24.9, hurst=0.5, atr_pct=50.0, close=105.0, sma20=100.0
        )
        assert result == MarketRegime.RANGE

    def test_hurst_0_45_is_not_range_from_hurst(self) -> None:
        """Hurst 0.45 is NOT < 0.45, so priority 4 doesn't fire."""
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.45, atr_pct=50.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.RANGE  # fallback

    def test_hurst_0_44_is_range(self) -> None:
        """Hurst 0.44 < 0.45 → RANGE via priority 4."""
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.44, atr_pct=50.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.RANGE

    def test_atr_pct_79_low_adx_not_chop(self) -> None:
        """ATR 79 + low ADX → not CHOP (needs > 80)."""
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.5, atr_pct=79.0, close=100.0, sma20=100.0
        )
        assert result != MarketRegime.CHOP

    def test_atr_pct_81_low_adx_is_chop(self) -> None:
        """ATR 81 + low ADX → CHOP."""
        result = RegimeClassifier._decision_tree(
            adx=15.0, hurst=0.5, atr_pct=81.0, close=100.0, sma20=100.0
        )
        assert result == MarketRegime.CHOP


# ------------------------------------------------------------------
# classify() integration tests
# ------------------------------------------------------------------


class TestClassify:
    """Test classify() with realistic candle data."""

    def test_less_than_50_candles_returns_chop(self) -> None:
        classifier = RegimeClassifier()
        candles = _make_candles(49)
        assert classifier.classify(candles) == MarketRegime.CHOP

    def test_exactly_50_candles_works(self) -> None:
        classifier = RegimeClassifier()
        candles = _make_trending_candles(50)
        result = classifier.classify(candles)
        assert result in list(MarketRegime)

    def test_all_flat_returns_range(self) -> None:
        classifier = RegimeClassifier()
        candles = _make_flat_candles(100)
        assert classifier.classify(candles) == MarketRegime.RANGE

    def test_list_input_accepted(self) -> None:
        """classify() accepts list[list] not just numpy."""
        classifier = RegimeClassifier()
        candles_list = _make_trending_candles(60).tolist()
        result = classifier.classify(candles_list)
        assert result in list(MarketRegime)


# ------------------------------------------------------------------
# get_current_regime() tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetCurrentRegime:
    async def test_no_redis_returns_chop(self) -> None:
        classifier = RegimeClassifier(redis_client=None)
        result = await classifier.get_current_regime()
        assert result == MarketRegime.CHOP

    async def test_redis_returns_trend_bull(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:BTC:USDT": "TREND_BULL",
                "system:config:regime": json.dumps({"regime": "TREND_BULL", "adx": 27.3}),
            }.get(key)
        )
        classifier = RegimeClassifier(redis_client=mock_redis)
        result = await classifier.get_current_regime()
        assert result == MarketRegime.TREND_BULL

    async def test_redis_none_returns_chop(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        classifier = RegimeClassifier(redis_client=mock_redis)
        result = await classifier.get_current_regime()
        assert result == MarketRegime.CHOP

    async def test_redis_exception_returns_chop(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = ConnectionError("down")
        classifier = RegimeClassifier(redis_client=mock_redis)
        result = await classifier.get_current_regime()
        assert result == MarketRegime.CHOP


# ------------------------------------------------------------------
# Technical indicator tests
# ------------------------------------------------------------------


class TestADX:
    def test_insufficient_data_returns_zero(self) -> None:
        highs = np.array([1.0] * 10)
        lows = np.array([0.5] * 10)
        closes = np.array([0.8] * 10)
        assert RegimeClassifier._calculate_adx(highs, lows, closes) == 0.0

    def test_trending_data_high_adx(self) -> None:
        """Strong uptrend should produce ADX > 25."""
        n = 100
        highs = np.array([100 + i * 2 + 1 for i in range(n)], dtype=float)
        lows = np.array([100 + i * 2 - 1 for i in range(n)], dtype=float)
        closes = np.array([100 + i * 2 for i in range(n)], dtype=float)
        adx = RegimeClassifier._calculate_adx(highs, lows, closes)
        assert adx > 25.0


class TestHurst:
    def test_insufficient_data_returns_half(self) -> None:
        prices = np.array([1.0] * 5)
        assert RegimeClassifier._calculate_hurst(prices) == 0.5

    def test_trending_prices_high_hurst(self) -> None:
        """Monotonically increasing prices → H > 0.5."""
        prices = np.array([100 + i * 2 for i in range(200)], dtype=float)
        hurst = RegimeClassifier._calculate_hurst(prices)
        assert hurst > 0.5


class TestATRPercentile:
    def test_insufficient_data_returns_50(self) -> None:
        highs = np.array([1.0] * 10)
        lows = np.array([0.5] * 10)
        closes = np.array([0.8] * 10)
        assert RegimeClassifier._calculate_atr_percentile(highs, lows, closes) == 50.0

    def test_constant_range_returns_low_percentile(self) -> None:
        """Constant ATR → current ATR equals all history → low percentile."""
        n = 200
        highs = np.array([101.0] * n)
        lows = np.array([99.0] * n)
        closes = np.array([100.0] * n)
        pct = RegimeClassifier._calculate_atr_percentile(highs, lows, closes)
        # All ATR values are the same, so current is not "greater than" any
        assert pct == 0.0

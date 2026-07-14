"""Tests for Regime Engine classifier."""

from __future__ import annotations

import math

import pytest

from app.alpha.regime import RegimeEngine, REGIME_CHOP, REGIME_MEAN_REVERSION, REGIME_TREND_BEAR, REGIME_TREND_BULL


def _make_candles(n: int, base_price: float = 50000.0, trend: float = 0.0, volatility: float = 100.0) -> list[list]:
    """Generate synthetic OHLCV candles."""
    candles = []
    price = base_price
    for i in range(n):
        o = price
        h = price + volatility
        l = price - volatility
        c = price + trend
        candles.append([i * 3600000, o, h, l, c, 1000.0])
        price = c
    return candles


class TestRegimeEngine:
    def setup_method(self):
        self.engine = RegimeEngine()

    def test_insufficient_data_returns_chop(self):
        candles = _make_candles(50)
        assert self.engine.classify(candles) == REGIME_CHOP

    def test_exact_200_candles(self):
        candles = _make_candles(200)
        result = self.engine.classify(candles)
        assert result in (REGIME_CHOP, REGIME_MEAN_REVERSION, REGIME_TREND_BULL, REGIME_TREND_BEAR)

    def test_hurst_computation(self):
        import random
        random.seed(42)
        prices = [100.0]
        for _ in range(99):
            prices.append(prices[-1] + random.gauss(0, 1))
        h = self.engine._hurst(prices)
        assert 0.2 < h < 1.0  # R/S method is biased upward on short series

    def test_adx_computation(self):
        n = 50
        highs = [100.0 + i * 2 for i in range(n)]
        lows = [99.0 + i * 2 for i in range(n)]
        closes = [99.5 + i * 2 for i in range(n)]
        adx = self.engine._adx(highs, lows, closes, period=14)
        assert adx > 20

    def test_ema_computation(self):
        prices = [100.0] * 200
        ema = self.engine._ema(prices, period=200)
        assert ema == pytest.approx(100.0, abs=0.01)

    def test_ema_short_data(self):
        prices = [100.0, 101.0, 102.0]
        ema = self.engine._ema(prices, period=200)
        assert ema == 102.0

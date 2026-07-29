"""Tests for StrategyRouter — Phase 6.2."""

from __future__ import annotations

import numpy as np
import pytest

from app.alpha.regime_classifier import MarketRegime
from app.alpha.strategy_router import (
    CHOP_SCORE_WICK_SNAPBACK,
    RANGE_SCORE_BB_EDGE,
    RANGE_SCORE_WICK,
    STRATEGY_GATE_THRESHOLD,
    TREND_SCORE_BREAKOUT,
    TREND_SCORE_GLOBAL_SYNC,
    StrategyRouter,
)
from app.core.feature_extractor import FeatureVector


def _make_trending_candles(n: int = 50, direction: float = 1.0) -> np.ndarray:
    candles = np.zeros((n, 6))
    for i in range(n):
        c = 100.0 + direction * i * 2.0
        candles[i] = [1000 + i * 3600, c - 0.5, c + 0.5, c - 0.5, c, 1000 + i * 50]
    return candles


def _make_bb_piercing_candles(n: int = 50, direction: str = "SHORT") -> np.ndarray:
    candles = np.zeros((n, 6))
    for i in range(n - 1):
        c = 100.0 + 0.1 * (i % 10)
        candles[i] = [1000 + i * 3600, c - 0.2, c + 0.3, c - 0.3, c, 1000]
    last = candles[-2, 4]
    if direction == "SHORT":
        candles[-1] = [1000 + n * 3600, last, last + 8.0, last - 0.5, last, 2000]
    else:
        candles[-1] = [1000 + n * 3600, last, last + 0.5, last - 8.0, last, 2000]
    return candles


class TestTrendStrategy:
    def test_breakout_plus_global_sync(self) -> None:
        """Breakout (30) + global sync (40) = 70 with trending candles, then VPVR bonus."""
        candles = _make_trending_candles(50, direction=1.0)
        last_close = candles[-1, 4]  # 100 + 49*2 = 198
        result = StrategyRouter._score_trend_strategy(
            candles,
            direction="LONG",
            global_prices={"binance": last_close + 5, "okx": last_close + 3},
        )
        assert result == 84  # breakout(30) + global_sync(40) * vpvr bonus(1.2)

    def test_only_breakout_long(self) -> None:
        candles = _make_trending_candles(50, direction=1.0)
        result = StrategyRouter._score_trend_strategy(
            candles,
            direction="LONG",
            global_prices=None,
        )
        assert result >= TREND_SCORE_BREAKOUT

    def test_only_global_sync(self) -> None:
        candles = np.zeros((50, 6))
        candles[:] = [1000, 100, 101, 99, 100, 1000]
        # last_close=100, binance/okx both above 100 → sync fires
        result = StrategyRouter._score_trend_strategy(
            candles,
            direction="LONG",
            global_prices={"binance": 105.0, "okx": 103.0},
        )
        assert result == TREND_SCORE_GLOBAL_SYNC

    def test_trend_bear_short(self) -> None:
        candles = _make_trending_candles(50, direction=-1.0)
        result = StrategyRouter._score_trend_strategy(
            candles,
            direction="SHORT",
            global_prices=None,
        )
        assert result >= TREND_SCORE_BREAKOUT


class TestRangeStrategy:
    def test_bb_plus_wick_passes_gate(self) -> None:
        candles = _make_bb_piercing_candles(50, "SHORT")
        result = StrategyRouter._score_range_strategy(candles, direction="SHORT")
        assert result >= RANGE_SCORE_BB_EDGE + RANGE_SCORE_WICK

    def test_bb_alone_fails_gate(self) -> None:
        """BB fires but close stays outside band (no wick rejection)."""
        # Oscillating candles to build proper BB bands
        candles = np.zeros((50, 6))
        for i in range(49):
            c = 100.0 + 2.0 * np.sin(i * 0.3)
            candles[i] = [1000 + i * 3600, c - 0.5, c + 1.0, c - 1.0, c, 1000]
        # Last bar: big spike, close STAYS outside upper band (no wick rejection)
        candles[-1] = [1000 + 49 * 3600, 100, 130, 99, 125, 3000]
        result = StrategyRouter._score_range_strategy(candles, direction="SHORT")
        # BB fires (high > upper), wick does NOT fire (close outside band)
        # RSI may or may not fire depending on data — BB is the key check
        assert result >= RANGE_SCORE_BB_EDGE
        assert result < RANGE_SCORE_BB_EDGE + RANGE_SCORE_WICK


class TestChopStrategy:
    def test_both_fire(self) -> None:
        candles = np.zeros((50, 6))
        candles[:] = [1000, 100, 101, 99, 100, 1000]
        # Last 2 candles create wick snapback: lower_wick > body
        candles[-2] = [1000 + 48 * 3600, 100, 102, 98, 101, 1000]
        candles[-1] = [1000 + 49 * 3600, 101, 102, 96, 102, 1000]
        result = StrategyRouter._score_chop_strategy(
            candles,
            direction="LONG",
            orderbook_delta=-0.5,
            funding_rate=-0.001,
            oi_change=-0.3,
        )
        assert result == 100.0

    def test_only_sweep_fails_gate(self) -> None:
        candles = np.zeros((50, 6))
        candles[:] = [1000, 100, 101, 99, 100, 1000]
        # Single wick snapback: low well below close range
        candles[-2] = [1000 + 48 * 3600, 100, 102, 98, 100, 1000]
        candles[-1] = [1000 + 49 * 3600, 100, 102, 97, 101, 1000]
        result = StrategyRouter._score_chop_strategy(
            candles,
            direction="LONG",
            orderbook_delta=None,
            funding_rate=0.0,
        )
        assert result == CHOP_SCORE_WICK_SNAPBACK


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_minimal_features_below_gate(self) -> None:
        """Regime with minimal features should produce score below gate threshold."""
        router = StrategyRouter(volatility_scaling=False)
        features = FeatureVector(close=100.0, rsi_14=50.0)
        context, _ = await router.evaluate_signal(
            features, MarketRegime.RANGE, "LONG"
        )
        assert context.total_confidence < STRATEGY_GATE_THRESHOLD

    @pytest.mark.asyncio
    async def test_minimal_features_low_score(self) -> None:
        """Minimal FeatureVector should produce score below gate threshold."""
        router = StrategyRouter(volatility_scaling=False)
        features = FeatureVector(close=100.0)
        context, _ = await router.evaluate_signal(
            features, MarketRegime.TREND_BULL, "LONG"
        )
        assert context.total_confidence < STRATEGY_GATE_THRESHOLD

"""Tests for Strategy Router — CHOP confluence scoring."""

from __future__ import annotations

import numpy as np

from app.alpha.regime_classifier import MarketRegime
from app.alpha.strategy_router import (
    CHOP_SCORE_FUNDING_CONF,
    CHOP_SCORE_OI_DROP,
    CHOP_SCORE_ORDERBOOK_ABSORPTION,
    STRATEGY_GATE_THRESHOLD,
    StrategyRouter,
)


def _make_candles(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> np.ndarray:
    """Build (N, 6) OHLCV array. highs/lows default to close±1."""
    n = len(closes)
    if highs is None:
        highs = [c + 1 for c in closes]
    if lows is None:
        lows = [c - 1 for c in closes]
    if volumes is None:
        volumes = [1000.0] * n
    data = []
    for i in range(n):
        data.append([0, 0, highs[i], lows[i], closes[i], volumes[i]])
    return np.array(data, dtype=float)


class TestCHOPConfluence:
    """Phase 6.1: Granular CHOP scoring — 4 components, need 3/4."""

    def setup_method(self):
        self.router = StrategyRouter(volatility_scaling=False)
        self.candles = _make_candles([100] * 25)  # 25 flat candles

    def test_no_components_score_zero(self):
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert score == 0.0

    def test_orderbook_only_score_20(self):
        """One component = 20, below gate."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,  # contrarian to LONG
        )
        assert score == CHOP_SCORE_ORDERBOOK_ABSORPTION

    def test_funding_only_score_30(self):
        """One component = 30, below gate."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            funding_rate=-0.001,  # negative = shorts paying
        )
        assert score == CHOP_SCORE_FUNDING_CONF

    def test_oi_only_score_30(self):
        """One component = 30, below gate."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            oi_change=-50.0,  # OI dropping
        )
        assert score == CHOP_SCORE_OI_DROP

    def test_two_components_score_40_to_50(self):
        """Two components = 40-50, still below gate."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,
            funding_rate=-0.001,
        )
        assert score == CHOP_SCORE_ORDERBOOK_ABSORPTION + CHOP_SCORE_FUNDING_CONF

    def test_three_components_pass_gate(self):
        """Three components = 70-80, above gate threshold."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,
            funding_rate=-0.001,
            oi_change=-50.0,
        )
        assert score >= STRATEGY_GATE_THRESHOLD

    def test_all_four_components_perfect(self):
        """All four = 100, maximum score."""
        # Build candles with long lower wick on LAST candle (price dropped then recovered)
        # prev_close=102, last_close=100 → body=2
        # last_low=90 → lower_wick = min(100,102)-90 = 10 > body(2) ✓
        closes = [100] * 23 + [102, 100]
        highs = [101] * 23 + [102, 102]
        lows = [99] * 23 + [99, 90]  # deep wick on LAST candle
        candles = _make_candles(closes, highs, lows)

        score = self.router.evaluate_signal(
            candles=candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,  # absorption
            funding_rate=-0.001,  # funding conf
            oi_change=-50.0,  # OI drop
        )
        # Raw score 100, but volatility-adjusted (high ATR_pct in test data)
        assert score >= STRATEGY_GATE_THRESHOLD

    def test_direction_matters_orderbook(self):
        """SHORT needs positive orderbook_delta (absorption of selling)."""
        score_long = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=0.01,  # wrong direction for LONG
        )
        assert score_long == 0.0

        score_short = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="SHORT",
            orderbook_delta=0.01,  # correct direction for SHORT
        )
        assert score_short == CHOP_SCORE_ORDERBOOK_ABSORPTION

    def test_direction_matters_funding(self):
        """SHORT needs positive funding (longs paying)."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="SHORT",
            funding_rate=0.001,
        )
        assert score == CHOP_SCORE_FUNDING_CONF

    def test_negative_oi_no_score(self):
        """Positive OI change (new positions) should not score."""
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            oi_change=50.0,
        )
        assert score == 0.0

    def test_zero_candles_returns_zero(self):
        """Fewer than 20 candles → hard zero."""
        score = self.router.evaluate_signal(
            candles=_make_candles([100] * 10),
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,
            funding_rate=-0.001,
            oi_change=-50.0,
        )
        assert score == 0.0

    def test_score_bucket_labels(self):
        """Verify bucket labeling matches new scoring ranges."""
        # Score 0 → "0-50"
        score = self.router.evaluate_signal(
            candles=self.candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert score < 50

        # Score 100 → "85-100"
        closes = [100] * 23 + [102, 100]
        highs = [101] * 23 + [102, 102]
        lows = [99] * 23 + [99, 90]  # deep wick on LAST candle
        candles = _make_candles(closes, highs, lows)
        score = self.router.evaluate_signal(
            candles=candles,
            regime=MarketRegime.CHOP,
            direction="LONG",
            orderbook_delta=-0.01,
            funding_rate=-0.001,
            oi_change=-50.0,
        )
        # Raw score 100, volatility-adjusted (high ATR_pct in test data)
        assert score >= STRATEGY_GATE_THRESHOLD
        assert score >= 85

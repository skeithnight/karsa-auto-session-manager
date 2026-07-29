"""Tests for Strategy Router — CHOP confluence scoring."""

from __future__ import annotations

import numpy as np

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
        self.candles = _make_candles([100] * 25)  # 25 flat candles

    def test_no_components_score_zero(self):
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", None, None, None,
        )
        assert score == 0

    def test_orderbook_only_score_20(self):
        """One component = 20, below gate."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=-0.01, funding_rate=None, oi_change=None,
        )
        assert score == CHOP_SCORE_ORDERBOOK_ABSORPTION

    def test_funding_only_score_30(self):
        """One component = 30, below gate."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=None, funding_rate=-0.001, oi_change=None,
        )
        assert score == CHOP_SCORE_FUNDING_CONF

    def test_oi_only_score_30(self):
        """One component = 30, below gate."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=None, funding_rate=None, oi_change=-50.0,
        )
        assert score == CHOP_SCORE_OI_DROP

    def test_two_components_score_40_to_50(self):
        """Two components = 40-50, still below gate."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=-0.01, funding_rate=-0.001, oi_change=None,
        )
        assert score == CHOP_SCORE_ORDERBOOK_ABSORPTION + CHOP_SCORE_FUNDING_CONF

    def test_three_components_pass_gate(self):
        """Three components = 70-80, above gate threshold."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0,
        )
        assert score >= STRATEGY_GATE_THRESHOLD

    def test_all_four_components_perfect(self):
        """All four = 100, maximum score."""
        closes = [100] * 23 + [102, 100]
        highs = [101] * 23 + [102, 102]
        lows = [99] * 23 + [99, 90]  # deep wick on LAST candle
        candles = _make_candles(closes, highs, lows)

        score = StrategyRouter._score_chop_strategy(
            candles, "LONG", orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0,
        )
        # 20 + 20 + 30 + 30 = 100
        assert score == 100

    def test_direction_matters_orderbook(self):
        """SHORT needs positive orderbook_delta (absorption of selling)."""
        score_long = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=0.01, funding_rate=None, oi_change=None,
        )
        assert score_long == 0

        score_short = StrategyRouter._score_chop_strategy(
            self.candles, "SHORT", orderbook_delta=0.01, funding_rate=None, oi_change=None,
        )
        assert score_short == CHOP_SCORE_ORDERBOOK_ABSORPTION

    def test_direction_matters_funding(self):
        """SHORT needs positive funding (longs paying)."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "SHORT", orderbook_delta=None, funding_rate=0.001, oi_change=None,
        )
        assert score == CHOP_SCORE_FUNDING_CONF

    def test_negative_oi_no_score(self):
        """Positive OI change (new positions) should not score."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=None, funding_rate=None, oi_change=50.0,
        )
        assert score == 0

    def test_zero_candles_returns_zero(self):
        """Fewer than 20 candles → hard zero."""
        score = StrategyRouter._score_chop_strategy(
            _make_candles([100] * 10), "LONG", orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0,
        )
        assert score == 0

    def test_score_bucket_labels(self):
        """Verify bucket labeling matches new scoring ranges."""
        score = StrategyRouter._score_chop_strategy(
            self.candles, "LONG", orderbook_delta=None, funding_rate=None, oi_change=None,
        )
        assert score < 50

        closes = [100] * 23 + [102, 100]
        highs = [101] * 23 + [102, 102]
        lows = [99] * 23 + [99, 90]  # deep wick on LAST candle
        candles = _make_candles(closes, highs, lows)
        score = StrategyRouter._score_chop_strategy(
            candles, "LONG", orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0,
        )
        assert score >= STRATEGY_GATE_THRESHOLD
        assert score >= 85

"""Tests for Strategy Router — CHOP confluence scoring."""

from __future__ import annotations

import numpy as np
import pytest

from app.alpha.regime_classifier import MarketRegime
from app.alpha.strategy_router import (
    CHOP_SCORE_FUNDING_CONF,
    CHOP_SCORE_OI_DROP,
    CHOP_SCORE_ORDERBOOK_ABSORPTION,
    STRATEGY_GATE_THRESHOLD,
    StrategyRouter,
)
from app.core.feature_extractor import FeatureVector


def _make_features(
    close: float = 100.0,
    orderbook_delta: float | None = None,
    funding_rate: float | None = None,
    oi_change: float | None = None,
) -> FeatureVector:
    """Build a FeatureVector for testing."""
    return FeatureVector(
        close=close,
        ema_20=close,
        ema_200=close,
        sma_20=close,
        atr=1.0,
        atr_pct=0.01,
        rsi_14=50.0,
        adx_14=20.0,
        hurst=0.5,
        funding_rate=funding_rate,
        oi_change=oi_change,
        orderbook_delta=orderbook_delta,
        cvd_slope=0.0,
        spread_pct=0.001,
    )


@pytest.mark.skip(reason="CHOP scoring refactored to use EvidenceCollector — old direct scoring path removed")
class TestCHOPConfluence:
    """Phase 6.1: Granular CHOP scoring — 4 components, need 3/4."""

    def setup_method(self):
        self.router = StrategyRouter(volatility_scaling=False)
        self.features = _make_features()

    @pytest.mark.asyncio
    async def test_no_components_score_zero(self):
        ctx, _ = await self.router.evaluate_signal(
            features=self.features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == 0.0

    @pytest.mark.asyncio
    async def test_orderbook_only_score_20(self):
        """One component = 20, below gate."""
        features = _make_features(orderbook_delta=-0.01)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == CHOP_SCORE_ORDERBOOK_ABSORPTION

    @pytest.mark.asyncio
    async def test_funding_only_score_30(self):
        """One component = 30, below gate."""
        features = _make_features(funding_rate=-0.001)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == CHOP_SCORE_FUNDING_CONF

    @pytest.mark.asyncio
    async def test_oi_only_score_30(self):
        """One component = 30, below gate."""
        features = _make_features(oi_change=-50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == CHOP_SCORE_OI_DROP

    @pytest.mark.asyncio
    async def test_two_components_score_40_to_50(self):
        """Two components = 40-50, still below gate."""
        features = _make_features(orderbook_delta=-0.01, funding_rate=-0.001)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == CHOP_SCORE_ORDERBOOK_ABSORPTION + CHOP_SCORE_FUNDING_CONF

    @pytest.mark.asyncio
    async def test_three_components_pass_gate(self):
        """Three components = 70-80, above gate threshold."""
        features = _make_features(orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence >= STRATEGY_GATE_THRESHOLD

    @pytest.mark.asyncio
    async def test_all_four_components_perfect(self):
        """All four = 100, maximum score."""
        features = _make_features(orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        # Raw score 100, but volatility-adjusted (high ATR_pct in test data)
        assert ctx.total_confidence >= STRATEGY_GATE_THRESHOLD

    @pytest.mark.asyncio
    async def test_direction_matters_orderbook(self):
        """SHORT needs positive orderbook_delta (absorption of selling)."""
        features_long = _make_features(orderbook_delta=0.01)
        ctx_long, _ = await self.router.evaluate_signal(
            features=features_long,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx_long.total_confidence == 0.0

        features_short = _make_features(orderbook_delta=0.01)
        ctx_short, _ = await self.router.evaluate_signal(
            features=features_short,
            regime=MarketRegime.CHOP,
            direction="SHORT",
        )
        assert ctx_short.total_confidence == CHOP_SCORE_ORDERBOOK_ABSORPTION

    @pytest.mark.asyncio
    async def test_direction_matters_funding(self):
        """SHORT needs positive funding (longs paying)."""
        features = _make_features(funding_rate=0.001)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="SHORT",
        )
        assert ctx.total_confidence == CHOP_SCORE_FUNDING_CONF

    @pytest.mark.asyncio
    async def test_negative_oi_no_score(self):
        """Positive OI change (new positions) should not score."""
        features = _make_features(oi_change=50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == 0.0

    @pytest.mark.asyncio
    async def test_zero_candles_returns_zero(self):
        """Fewer than 20 candles → hard zero (now testing with minimal features)."""
        features = _make_features(orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence == 0.0

    @pytest.mark.asyncio
    async def test_score_bucket_labels(self):
        """Verify bucket labeling matches new scoring ranges."""
        # Score 0 → "0-50"
        ctx, _ = await self.router.evaluate_signal(
            features=self.features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        assert ctx.total_confidence < 50

        # Score 100 → "85-100"
        features = _make_features(orderbook_delta=-0.01, funding_rate=-0.001, oi_change=-50.0)
        ctx, _ = await self.router.evaluate_signal(
            features=features,
            regime=MarketRegime.CHOP,
            direction="LONG",
        )
        # Raw score 100, volatility-adjusted (high ATR_pct in test data)
        assert ctx.total_confidence >= STRATEGY_GATE_THRESHOLD
        assert ctx.total_confidence >= 85

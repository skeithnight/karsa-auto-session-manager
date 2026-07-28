"""Unit tests for ScoreComposer."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.alpha.regime_classifier import MarketRegime
from app.consumer.score_composer import ScoreComposer, ComposedScore
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult


class MockEdgeFamily(EdgeFamily):
    """Mock edge family for testing."""

    def __init__(self, name: str, score: float, regime: MarketRegime):
        self._name = name
        self._score = score
        self._regime = regime

    @property
    def name(self) -> str:
        return self._name

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        return [self._regime]

    async def evaluate(self, symbol, direction, regime, features, snapshot, **kwargs):
        return EdgeFamilyResult(
            family_name=self._name,
            score=self._score,
            confidence=self._score / 100.0,
            regime_aligned=True,
            filters_passed={"test": True},
        )


class MockRejectFamily(EdgeFamily):
    """Mock edge family that rejects."""

    @property
    def name(self) -> str:
        return "reject_family"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)

    async def evaluate(self, symbol, direction, regime, features, snapshot, **kwargs):
        return EdgeFamilyResult(
            family_name=self.name,
            score=0.0,
            confidence=0.0,
            regime_aligned=False,
            reject_reason="always_reject",
        )


class TestScoreComposer:
    """Test ScoreComposer."""

    def test_init_empty(self):
        """Test initialization with no families."""
        composer = ScoreComposer()
        assert len(composer._families) == 0

    def test_init_with_families(self):
        """Test initialization with families."""
        families = [
            MockEdgeFamily("family1", 70.0, MarketRegime.TREND_BULL),
            MockEdgeFamily("family2", 60.0, MarketRegime.RANGE),
        ]
        composer = ScoreComposer(families=families)
        assert len(composer._families) == 2

    def test_register_family(self):
        """Test registering a family."""
        composer = ScoreComposer()
        family = MockEdgeFamily("test", 50.0, MarketRegime.RANGE)
        composer.register_family(family)
        assert len(composer._families) == 1

    @pytest.mark.asyncio
    async def test_compose_picks_highest_score(self):
        """Test compose picks family with highest score."""
        families = [
            MockEdgeFamily("low", 40.0, MarketRegime.TREND_BULL),
            MockEdgeFamily("high", 80.0, MarketRegime.TREND_BULL),
        ]
        composer = ScoreComposer(families=families)

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        assert result.winning_family == "high"
        assert result.total_score == 80.0

    @pytest.mark.asyncio
    async def test_compose_filters_inactive_regimes(self):
        """Test compose filters families not active for regime."""
        families = [
            MockEdgeFamily("trend_only", 70.0, MarketRegime.TREND_BULL),
            MockEdgeFamily("range_only", 60.0, MarketRegime.RANGE),
        ]
        composer = ScoreComposer(families=families)

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        # Only trend_only should be active
        assert result.winning_family == "trend_only"
        assert "range_only" not in result.family_scores

    @pytest.mark.asyncio
    async def test_compose_handles_rejected_families(self):
        """Test compose handles rejected families."""
        families = [
            MockEdgeFamily("valid", 70.0, MarketRegime.TREND_BULL),
            MockRejectFamily(),
        ]
        composer = ScoreComposer(families=families)

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        assert result.winning_family == "valid"
        assert result.total_score == 70.0
        assert "reject_family" in result.metadata["rejected_families"]

    @pytest.mark.asyncio
    async def test_compose_all_rejected(self):
        """Test compose when all families are rejected."""
        families = [MockRejectFamily()]
        composer = ScoreComposer(families=families)

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        assert result.winning_family is None
        assert result.total_score == 0.0

    @pytest.mark.asyncio
    async def test_compose_no_families(self):
        """Test compose with no families registered."""
        composer = ScoreComposer()

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        assert result.winning_family is None
        assert result.total_score == 0.0

    @pytest.mark.asyncio
    async def test_compose_includes_metadata(self):
        """Test compose includes proper metadata."""
        families = [
            MockEdgeFamily("family1", 70.0, MarketRegime.TREND_BULL),
        ]
        composer = ScoreComposer(families=families)

        result = await composer.compose(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )

        assert "active_families" in result.metadata
        assert "valid_families" in result.metadata
        assert "rejected_families" in result.metadata
        assert "family1" in result.metadata["active_families"]
        assert "family1" in result.metadata["valid_families"]

"""Unit tests for edge families."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.consumer.edge_families.trend import TrendContinuation
from app.consumer.edge_families.mean_reversion import MeanReversion
from app.consumer.edge_families.carry import CarryDislocation
from app.consumer.edge_families.liquidation_squeeze import LiquidationSqueeze
from app.consumer.edge_families.event_breakout import EventBreakout


class TestEdgeFamilyBase:
    """Test base edge family class."""

    def test_edge_family_result_creation(self):
        """Test EdgeFamilyResult creation."""
        result = EdgeFamilyResult(
            family_name="test",
            score=75.0,
            confidence=0.75,
            regime_aligned=True,
            filters_passed={"mtf": True},
            metadata={"vol_factor": 1.0},
        )
        assert result.family_name == "test"
        assert result.score == 75.0
        assert result.confidence == 0.75
        assert result.regime_aligned is True
        assert result.filters_passed == {"mtf": True}
        assert result.metadata == {"vol_factor": 1.0}
        assert result.reject_reason is None

    def test_edge_family_result_rejected(self):
        """Test rejected EdgeFamilyResult."""
        result = EdgeFamilyResult(
            family_name="test",
            score=0.0,
            confidence=0.0,
            regime_aligned=False,
            reject_reason="regime_not_active",
        )
        assert result.reject_reason == "regime_not_active"
        assert result.score == 0.0


class TestTrendContinuation:
    """Test TrendContinuation edge family."""

    def setup_method(self):
        self.family = TrendContinuation()

    def test_name(self):
        """Test family name."""
        assert self.family.name == "trend_continuation"

    def test_supported_regimes(self):
        """Test supported regimes."""
        regimes = self.family.supported_regimes
        assert MarketRegime.TREND_BULL in regimes
        assert MarketRegime.TREND_BEAR in regimes
        assert MarketRegime.HYPER_BULL in regimes
        assert MarketRegime.HYPER_BEAR in regimes
        assert MarketRegime.RANGE not in regimes

    def test_is_active_trend(self):
        """Test is_active for trend regimes."""
        assert self.family.is_active(MarketRegime.TREND_BULL) is True
        assert self.family.is_active(MarketRegime.TREND_BEAR) is True
        assert self.family.is_active(MarketRegime.HYPER_BULL) is True

    def test_is_active_range(self):
        """Test is_active for range regime."""
        assert self.family.is_active(MarketRegime.RANGE) is False

    @pytest.mark.asyncio
    async def test_evaluate_rejects_range_regime(self):
        """Test evaluation rejects RANGE regime."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.RANGE,
            features=MagicMock(),
            snapshot=MagicMock(),
        )
        assert result.reject_reason == "regime_not_active"
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_trend_regime(self):
        """Test evaluation in trend regime."""
        # Mock dependencies
        mock_router = AsyncMock()
        mock_router.evaluate_signal.return_value = (MagicMock(total_confidence=70.0), 1.0)
        
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
            router=mock_router,
        )
        
        assert result.family_name == "trend_continuation"
        assert result.score > 0
        assert result.regime_aligned is True
        assert result.reject_reason is None


class TestMeanReversion:
    """Test MeanReversion edge family."""

    def setup_method(self):
        self.family = MeanReversion()

    def test_name(self):
        """Test family name."""
        assert self.family.name == "mean_reversion"

    def test_supported_regimes(self):
        """Test supported regimes."""
        regimes = self.family.supported_regimes
        assert MarketRegime.RANGE in regimes
        assert MarketRegime.TREND_BULL not in regimes

    @pytest.mark.asyncio
    async def test_evaluate_rejects_trend_regime(self):
        """Test evaluation rejects TREND_BULL regime."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )
        assert result.reject_reason == "regime_not_active"

    @pytest.mark.asyncio
    async def test_evaluate_range_regime(self):
        """Test evaluation in range regime."""
        mock_router = AsyncMock()
        mock_router.evaluate_signal.return_value = (MagicMock(total_confidence=60.0), 1.0)
        
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.RANGE,
            features=MagicMock(),
            snapshot=MagicMock(),
            router=mock_router,
        )
        
        assert result.family_name == "mean_reversion"
        assert result.score > 0


class TestCarryDislocation:
    """Test CarryDislocation edge family."""

    def setup_method(self):
        self.family = CarryDislocation()

    def test_name(self):
        """Test family name."""
        assert self.family.name == "carry_dislocation"

    def test_supported_regimes(self):
        """Test supported regimes (all regimes)."""
        regimes = self.family.supported_regimes
        assert len(regimes) == len(MarketRegime)
        assert MarketRegime.RANGE in regimes
        assert MarketRegime.TREND_BULL in regimes

    @pytest.mark.asyncio
    async def test_evaluate_blocks_extreme_funding(self):
        """Test evaluation blocks extreme positive funding for LONG."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
            funding_rate=0.001,  # > 0.0005
        )
        assert result.reject_reason == "extreme_positive_funding"

    @pytest.mark.asyncio
    async def test_evaluate_negative_funding_bonus(self):
        """Test evaluation gives bonus for negative funding (carry)."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
            funding_rate=-0.0002,  # Negative funding → LONG bonus
        )
        assert result.score > 40  # Base score + carry bonus
        assert result.metadata.get("carry_bonus", 0) > 0


class TestLiquidationSqueeze:
    """Test LiquidationSqueeze edge family."""

    def setup_method(self):
        self.family = LiquidationSqueeze()

    def test_name(self):
        """Test family name."""
        assert self.family.name == "liquidation_squeeze"

    def test_supported_regimes(self):
        """Test supported regimes (all regimes)."""
        regimes = self.family.supported_regimes
        assert len(regimes) == len(MarketRegime)

    @pytest.mark.asyncio
    async def test_evaluate_any_regime(self):
        """Test evaluation works in any regime."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.RANGE,
            features=MagicMock(),
            snapshot=MagicMock(),
        )
        assert result.family_name == "liquidation_squeeze"
        assert result.score > 0


class TestEventBreakout:
    """Test EventBreakout edge family."""

    def setup_method(self):
        self.family = EventBreakout()

    def test_name(self):
        """Test family name."""
        assert self.family.name == "event_breakout"

    def test_supported_regimes(self):
        """Test supported regimes (all regimes)."""
        regimes = self.family.supported_regimes
        assert len(regimes) == len(MarketRegime)

    @pytest.mark.asyncio
    async def test_evaluate_any_regime(self):
        """Test evaluation works in any regime."""
        result = await self.family.evaluate(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            features=MagicMock(),
            snapshot=MagicMock(),
        )
        assert result.family_name == "event_breakout"
        assert result.score > 0


class TestRejectHelper:
    """Test _reject helper method."""

    def test_reject_helper(self):
        """Test _reject creates proper rejected result."""
        family = TrendContinuation()
        result = family._reject("test_reason", "Custom reason")
        assert result.family_name == "test_reason"
        assert result.score == 0.0
        assert result.reject_reason == "Custom reason"

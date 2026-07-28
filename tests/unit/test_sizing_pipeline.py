"""Unit tests for SizingPipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from app.consumer.sizing_pipeline import SizingPipeline, SizingResult


class TestSizingResult:
    """Test SizingResult dataclass."""

    def test_creation(self):
        """Test SizingResult creation."""
        result = SizingResult(
            amount=Decimal("0.001"),
            risk_pct=Decimal("0.01"),
            kelly_fraction=0.5,
            drawdown_mult=1.0,
            conviction_mult=1.0,
            macro_mult=1.0,
            session_mult=1.0,
            uncertainty_factor=1.0,
            garch_factor=1.0,
        )
        assert result.amount == Decimal("0.001")
        assert result.risk_pct == Decimal("0.01")
        assert result.kelly_fraction == 0.5
        assert result.drawdown_mult == 1.0


class TestSizingPipeline:
    """Test SizingPipeline."""

    def test_init(self):
        """Test initialization."""
        pipeline = SizingPipeline()
        assert pipeline._trade_memory is None
        assert pipeline._redis is None

    def test_init_with_deps(self):
        """Test initialization with dependencies."""
        mock_memory = MagicMock()
        mock_redis = MagicMock()
        pipeline = SizingPipeline(trade_memory=mock_memory, redis_client=mock_redis)
        assert pipeline._trade_memory is mock_memory
        assert pipeline._redis is mock_redis

    @pytest.mark.asyncio
    async def test_calculate_basic(self):
        """Test basic calculation."""
        pipeline = SizingPipeline()
        
        # Mock profile
        mock_profile = MagicMock()
        mock_profile.size_multiplier = Decimal("1.0")
        
        result = await pipeline.calculate(
            symbol="BTC/USDT",
            wallet_balance=Decimal("10000"),
            entry_price=Decimal("50000"),
            sl_price=Decimal("49000"),
            score=75.0,
            profile=mock_profile,
            session_mult=1.0,
        )
        
        assert isinstance(result, SizingResult)
        assert result.amount > Decimal("0")
        assert result.risk_pct > Decimal("0")

    @pytest.mark.asyncio
    async def test_calculate_with_redis(self):
        """Test calculation with Redis for multipliers."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # No data
        
        pipeline = SizingPipeline(redis_client=mock_redis)
        
        mock_profile = MagicMock()
        mock_profile.size_multiplier = Decimal("1.0")
        
        result = await pipeline.calculate(
            symbol="BTC/USDT",
            wallet_balance=Decimal("10000"),
            entry_price=Decimal("50000"),
            sl_price=Decimal("49000"),
            score=75.0,
            profile=mock_profile,
            session_mult=1.0,
        )
        
        assert result.amount > Decimal("0")

    @pytest.mark.asyncio
    async def test_kelly_fraction_with_memory(self):
        """Test Kelly fraction with trade memory."""
        mock_memory = AsyncMock()
        mock_memory.get_recent.return_value = [
            {"pnl_pct": 0.05},
            {"pnl_pct": -0.02},
            {"pnl_pct": 0.03},
        ]
        
        pipeline = SizingPipeline(trade_memory=mock_memory)
        
        kelly = await pipeline._kelly_fraction("BTC/USDT", 75.0)
        assert kelly >= 0
        assert kelly <= 1

    @pytest.mark.asyncio
    async def test_kelly_fraction_without_memory(self):
        """Test Kelly fraction without trade memory."""
        pipeline = SizingPipeline()
        
        kelly = await pipeline._kelly_fraction("BTC/USDT", 75.0)
        assert kelly >= 0

    @pytest.mark.asyncio
    async def test_conviction_multiplier_default(self):
        """Test conviction multiplier default value."""
        pipeline = SizingPipeline()
        
        mult = await pipeline._conviction_multiplier()
        assert mult == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_conviction_multiplier_with_redis(self):
        """Test conviction multiplier from Redis."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = b'{"conviction": 0.8}'
        
        pipeline = SizingPipeline(redis_client=mock_redis)
        
        mult = await pipeline._conviction_multiplier()
        assert mult == Decimal("0.8")

    @pytest.mark.asyncio
    async def test_macro_multiplier_default(self):
        """Test macro multiplier default value."""
        pipeline = SizingPipeline()
        
        mult = await pipeline._macro_multiplier()
        assert mult == 1.0

    @pytest.mark.asyncio
    async def test_garch_targeting_default(self):
        """Test GARCH targeting default value."""
        pipeline = SizingPipeline()
        
        factor = await pipeline._garch_targeting()
        assert factor == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_calculate_caps_risk_pct(self):
        """Test calculation caps risk_pct at 2%."""
        pipeline = SizingPipeline()
        
        mock_profile = MagicMock()
        mock_profile.size_multiplier = Decimal("10.0")  # Large multiplier
        
        result = await pipeline.calculate(
            symbol="BTC/USDT",
            wallet_balance=Decimal("10000"),
            entry_price=Decimal("50000"),
            sl_price=Decimal("49000"),
            score=75.0,
            profile=mock_profile,
            session_mult=1.0,
        )
        
        assert result.risk_pct <= Decimal("0.020")

    @pytest.mark.asyncio
    async def test_calculate_caps_amount_at_40pct(self):
        """Test calculation caps amount at 40% notional."""
        pipeline = SizingPipeline()
        
        mock_profile = MagicMock()
        mock_profile.size_multiplier = Decimal("100.0")  # Very large multiplier
        
        result = await pipeline.calculate(
            symbol="BTC/USDT",
            wallet_balance=Decimal("10000"),
            entry_price=Decimal("50000"),
            sl_price=Decimal("49000"),
            score=75.0,
            profile=mock_profile,
            session_mult=1.0,
        )
        
        # Amount should be capped at 40% of wallet / entry_price
        max_amount = (Decimal("10000") * Decimal("0.40")) / Decimal("50000")
        assert result.amount <= max_amount

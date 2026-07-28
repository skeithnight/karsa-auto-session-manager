"""Unit tests for ExecutionAttribution."""

import pytest
from unittest.mock import AsyncMock
from decimal import Decimal

from app.execution.execution_attribution import ExecutionAttribution, FillRecord, AttributionMetrics


class TestExecutionAttribution:
    """Test ExecutionAttribution."""

    def setup_method(self):
        self.attribution = ExecutionAttribution()

    def test_init(self):
        """Test initialization."""
        assert self.attribution._redis is None
        assert self.attribution._max_records == 1000
        assert len(self.attribution._records) == 0

    @pytest.mark.asyncio
    async def test_record_fill(self):
        """Test recording a fill."""
        fill_data = {
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "maker",
            "entry_price": 50000,
            "fill_price": 50001,
            "amount": 0.001,
            "regime": "TREND_BULL",
            "edge_family": "trend_continuation",
            "signal_score": 75.0,
            "reprice_attempts": 0,
            "fill_latency_ms": 100,
        }

        await self.attribution.record_fill(fill_data)
        assert len(self.attribution._records) == 1
        assert self.attribution._records[0].symbol == "BTC/USDT"
        assert self.attribution._records[0].slippage_bps > 0

    @pytest.mark.asyncio
    async def test_get_attribution_report_empty(self):
        """Test report with no fills."""
        report = await self.attribution.get_attribution_report()
        assert report.total_fills == 0
        assert report.maker_fill_rate == 0.0

    @pytest.mark.asyncio
    async def test_get_attribution_report_with_fills(self):
        """Test report with fills."""
        # Record maker fills
        for i in range(5):
            await self.attribution.record_fill({
                "symbol": "BTC/USDT",
                "side": "buy",
                "order_type": "maker",
                "entry_price": 50000,
                "fill_price": 50000 + i,
                "amount": 0.001,
                "regime": "TREND_BULL",
                "edge_family": "trend_continuation",
            })

        # Record taker fills
        for i in range(3):
            await self.attribution.record_fill({
                "symbol": "ETH/USDT",
                "side": "sell",
                "order_type": "taker",
                "entry_price": 3000,
                "fill_price": 3000 - i * 2,
                "amount": 0.01,
                "regime": "RANGE",
                "edge_family": "mean_reversion",
            })

        report = await self.attribution.get_attribution_report()
        assert report.total_fills == 8
        assert report.maker_fills == 5
        assert report.taker_fills == 3
        assert report.maker_fill_rate == 5 / 8
        assert report.avg_slippage_bps > 0

    @pytest.mark.asyncio
    async def test_slippage_by_regime(self):
        """Test slippage tracking by regime."""
        await self.attribution.record_fill({
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "maker",
            "entry_price": 50000,
            "fill_price": 50001,
            "amount": 0.001,
            "regime": "TREND_BULL",
        })

        await self.attribution.record_fill({
            "symbol": "ETH/USDT",
            "side": "sell",
            "order_type": "taker",
            "entry_price": 3000,
            "fill_price": 2997,
            "amount": 0.01,
            "regime": "RANGE",
        })

        report = await self.attribution.get_attribution_report()
        assert "TREND_BULL" in report.slippage_by_regime
        assert "RANGE" in report.slippage_by_regime

    @pytest.mark.asyncio
    async def test_slippage_by_family(self):
        """Test slippage tracking by family."""
        await self.attribution.record_fill({
            "symbol": "BTC/USDT",
            "side": "buy",
            "order_type": "maker",
            "entry_price": 50000,
            "fill_price": 50001,
            "amount": 0.001,
            "regime": "TREND_BULL",
            "edge_family": "trend_continuation",
        })

        report = await self.attribution.get_attribution_report()
        assert "trend_continuation" in report.slippage_by_family

    def test_should_use_taker_high_urgency(self):
        """Test taker recommendation for high urgency."""
        result = self.attribution.should_use_taker("TREND_BULL", urgency=0.9)
        assert result is True

    def test_should_use_taker_low_slippage_regime(self):
        """Test taker recommendation for low slippage regime."""
        result = self.attribution.should_use_taker("TREND_BULL", urgency=0.5)
        assert result is True  # Default regime has no slippage data

    @pytest.mark.asyncio
    async def test_max_records_trimmed(self):
        """Test that old records are trimmed."""
        self.attribution._max_records = 5
        for i in range(10):
            await self.attribution.record_fill({
                "symbol": "BTC/USDT",
                "side": "buy",
                "order_type": "maker",
                "entry_price": 50000,
                "fill_price": 50000 + i,
                "amount": 0.001,
                "regime": "TREND_BULL",
            })

        assert len(self.attribution._records) == 5

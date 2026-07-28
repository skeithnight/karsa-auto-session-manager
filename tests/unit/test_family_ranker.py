"""Unit tests for FamilyRanker."""

import pytest
from unittest.mock import AsyncMock
from decimal import Decimal

from app.risk.family_ranker import FamilyRanker, FamilyMetrics, FamilyAllocation


class TestFamilyRanker:
    """Test FamilyRanker."""

    def setup_method(self):
        self.ranker = FamilyRanker()

    def test_init(self):
        """Test initialization."""
        assert self.ranker._redis is None
        assert self.ranker.MAX_FAMILY_ALLOCATION == 0.40
        assert self.ranker.MIN_FAMILY_ALLOCATION == 0.05

    @pytest.mark.asyncio
    async def test_get_family_metrics_insufficient_trades(self):
        """Test metrics with insufficient trades."""
        mock_store = AsyncMock()
        mock_store.get_recent_trades.return_value = []

        metrics = await self.ranker.get_family_metrics(mock_store, "trend_continuation")
        assert metrics.trade_count == 0
        assert metrics.win_rate == 0.0

    @pytest.mark.asyncio
    async def test_get_family_metrics_with_trades(self):
        """Test metrics with sufficient trades."""
        mock_store = AsyncMock()
        mock_store.get_recent_trades.return_value = [
            {"edge_family": "trend_continuation", "pnl_pct": 0.05, "holding_time_minutes": 120},
            {"edge_family": "trend_continuation", "pnl_pct": -0.02, "holding_time_minutes": 60},
            {"edge_family": "trend_continuation", "pnl_pct": 0.03, "holding_time_minutes": 180},
            {"edge_family": "trend_continuation", "pnl_pct": 0.04, "holding_time_minutes": 90},
            {"edge_family": "trend_continuation", "pnl_pct": -0.01, "holding_time_minutes": 45},
            {"edge_family": "trend_continuation", "pnl_pct": 0.06, "holding_time_minutes": 200},
            {"edge_family": "trend_continuation", "pnl_pct": 0.02, "holding_time_minutes": 150},
            {"edge_family": "trend_continuation", "pnl_pct": -0.03, "holding_time_minutes": 30},
            {"edge_family": "trend_continuation", "pnl_pct": 0.05, "holding_time_minutes": 120},
            {"edge_family": "trend_continuation", "pnl_pct": 0.01, "holding_time_minutes": 60},
            {"edge_family": "trend_continuation", "pnl_pct": 0.04, "holding_time_minutes": 90},
        ]

        metrics = await self.ranker.get_family_metrics(mock_store, "trend_continuation")
        assert metrics.trade_count == 11
        assert metrics.win_rate > 0.5
        assert metrics.expectancy > 0

    @pytest.mark.asyncio
    async def test_rank_families(self):
        """Test family ranking."""
        mock_store = AsyncMock()
        mock_store.get_recent_trades.return_value = [
            {"edge_family": "trend_continuation", "pnl_pct": 0.05},
            {"edge_family": "trend_continuation", "pnl_pct": 0.03},
            {"edge_family": "trend_continuation", "pnl_pct": -0.02},
            {"edge_family": "trend_continuation", "pnl_pct": 0.04},
            {"edge_family": "trend_continuation", "pnl_pct": -0.01},
            {"edge_family": "trend_continuation", "pnl_pct": 0.06},
            {"edge_family": "trend_continuation", "pnl_pct": 0.02},
            {"edge_family": "trend_continuation", "pnl_pct": -0.03},
            {"edge_family": "trend_continuation", "pnl_pct": 0.05},
            {"edge_family": "trend_continuation", "pnl_pct": 0.01},
            {"edge_family": "mean_reversion", "pnl_pct": 0.02},
            {"edge_family": "mean_reversion", "pnl_pct": -0.01},
            {"edge_family": "mean_reversion", "pnl_pct": 0.03},
            {"edge_family": "mean_reversion", "pnl_pct": 0.01},
            {"edge_family": "mean_reversion", "pnl_pct": -0.02},
            {"edge_family": "mean_reversion", "pnl_pct": 0.04},
            {"edge_family": "mean_reversion", "pnl_pct": 0.02},
            {"edge_family": "mean_reversion", "pnl_pct": -0.01},
            {"edge_family": "mean_reversion", "pnl_pct": 0.03},
            {"edge_family": "mean_reversion", "pnl_pct": 0.01},
        ]

        ranked = await self.ranker.rank_families(mock_store)
        assert len(ranked) >= 1
        # Should be sorted by risk-adjusted return
        if len(ranked) > 1:
            assert ranked[0].risk_adjusted_return >= ranked[1].risk_adjusted_return

    def test_allocate_capital(self):
        """Test capital allocation."""
        families = [
            FamilyMetrics(family_name="trend", risk_adjusted_return=3.0, expectancy=0.05, trade_count=50),
            FamilyMetrics(family_name="carry", risk_adjusted_return=1.0, expectancy=0.03, trade_count=30),
        ]

        allocations = self.ranker.allocate_capital(families, Decimal("10000"), max_families=2)
        assert len(allocations) == 2
        assert allocations[0].family_name == "trend"
        # First family should get more allocation (higher risk-adjusted return)
        assert allocations[0].allocation_pct >= allocations[1].allocation_pct

    def test_allocate_capital_empty(self):
        """Test capital allocation with no families."""
        allocations = self.ranker.allocate_capital([], Decimal("10000"))
        assert len(allocations) == 0

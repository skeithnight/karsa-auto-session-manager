"""Tests for Dynamic EV Threshold (Phase 1: Filter Collapse)."""

import pytest
import asyncio
from app.alpha.ev_threshold import DynamicThreshold, _get_session_label


class TestDynamicThreshold:
    """Test DynamicThreshold calibration."""

    def setup_method(self):
        self.threshold = DynamicThreshold()

    @pytest.mark.asyncio
    async def test_base_threshold(self):
        """Default threshold should be 0.50 minus session adjustment."""
        result = await self.threshold.get_threshold(hour_utc=10)  # LDN session (no adjustment)
        assert result == 0.50

    @pytest.mark.asyncio
    async def test_severe_drawdown_raises_threshold(self):
        """Severe drawdown (>10%) should raise threshold."""
        result = await self.threshold.get_threshold(
            drawdown_pct=0.15, hour_utc=10,  # LDN session (no adjustment)
        )
        assert result > 0.50, f"Expected > 0.50, got {result}"
        assert result == pytest.approx(0.58, abs=1e-9)  # 0.50 + 0.08

    @pytest.mark.asyncio
    async def test_moderate_drawdown_raises_threshold(self):
        """Moderate drawdown (>5%) should raise threshold."""
        result = await self.threshold.get_threshold(
            drawdown_pct=0.07, hour_utc=10,  # LDN session (no adjustment)
        )
        assert result > 0.50, f"Expected > 0.50, got {result}"
        assert result == 0.54  # 0.50 + 0.04

    @pytest.mark.asyncio
    async def test_cold_streak_raises_threshold(self):
        """Cold streak (<35% win rate) should raise threshold."""
        result = await self.threshold.get_threshold(
            recent_win_rate=0.25, hour_utc=10,  # LDN session (no adjustment)
        )
        assert result > 0.50, f"Expected > 0.50, got {result}"
        assert result == 0.55  # 0.50 + 0.05

    @pytest.mark.asyncio
    async def test_asia_session_raises_threshold(self):
        """ASIA session should raise threshold."""
        result = await self.threshold.get_threshold(hour_utc=3)
        assert result > 0.50, f"Expected > 0.50, got {result}"
        assert result == pytest.approx(0.54, abs=1e-9)  # 0.50 + 0.04

    @pytest.mark.asyncio
    async def test_ldn_ny_overlap_lowers_threshold(self):
        """LDN_NY overlap should lower threshold."""
        result = await self.threshold.get_threshold(hour_utc=14)
        assert result == 0.47  # base 0.50 + (-0.03) = 0.47

    @pytest.mark.asyncio
    async def test_threshold_clamped_to_min(self):
        """Threshold should not go below 0.40."""
        # Even with negative adjustments, should stay at min
        result = await self.threshold.get_threshold(
            drawdown_pct=0.0, recent_win_rate=1.0, hour_utc=14,
        )
        assert result >= 0.40

    @pytest.mark.asyncio
    async def test_threshold_clamped_to_max(self):
        """Threshold should not go above 0.65."""
        result = await self.threshold.get_threshold(
            drawdown_pct=0.20, recent_win_rate=0.1, hour_utc=3,
        )
        assert result <= 0.65

    @pytest.mark.asyncio
    async def test_combined_adjustments(self):
        """Multiple adjustments should stack up to max ceiling."""
        result = await self.threshold.get_threshold(
            drawdown_pct=0.15,  # +0.08
            recent_win_rate=0.25,  # +0.05
            hour_utc=3,  # +0.04
        )
        # 0.50 + 0.08 + 0.05 + 0.04 = 0.67 -> clamped to 0.65
        assert result == 0.65


class TestSessionLabel:
    """Test session label mapping."""

    def test_ldn_ny_overlap(self):
        assert _get_session_label(14) == "LDN_NY_OVERLAP"

    def test_asia(self):
        assert _get_session_label(3) == "ASIA"

    def test_london(self):
        assert _get_session_label(9) == "LDN"

    def test_new_york(self):
        assert _get_session_label(18) == "NY"

    def test_pacific(self):
        assert _get_session_label(22) == "PACIFIC"

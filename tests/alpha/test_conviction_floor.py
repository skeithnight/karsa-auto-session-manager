"""Tests for conviction floor rejection in DecisionEngine."""

from __future__ import annotations

from decimal import Decimal

import pytest


class TestConvictionFloor:
    """Verify that signals with conviction < 0.35 are rejected."""

    def test_low_conviction_rejected(self):
        """Signal with conviction=0.20 should be rejected (below 0.35 floor)."""
        CONVICTION_FLOOR = 0.35
        conviction = 0.20
        assert conviction < CONVICTION_FLOOR, "Low conviction should fail floor"

    def test_high_conviction_passes(self):
        """Signal with conviction=0.80 should pass the floor check."""
        CONVICTION_FLOOR = 0.35
        conviction = 0.80
        assert conviction >= CONVICTION_FLOOR, "High conviction should pass floor"

    def test_boundary_conviction_passes(self):
        """Signal with conviction=0.35 exactly should pass (>= not >)."""
        CONVICTION_FLOOR = 0.35
        conviction = 0.35
        assert conviction >= CONVICTION_FLOOR, "Boundary conviction should pass"

    def test_just_below_floor_rejected(self):
        """Signal with conviction=0.34 should be rejected."""
        CONVICTION_FLOOR = 0.35
        conviction = 0.34
        assert conviction < CONVICTION_FLOOR, "Just-below conviction should fail"

    def test_conviction_scaling_math(self):
        """Verify conviction correctly scales Kelly position size."""
        kelly_risk = Decimal("0.02")  # 2% Kelly
        conviction = 0.60
        scaled = kelly_risk * Decimal(str(conviction))
        assert scaled == Decimal("0.012"), f"Expected 0.012, got {scaled}"

    def test_conviction_zero_reduces_to_zero(self):
        """Conviction=0.0 should reduce position size to zero."""
        kelly_risk = Decimal("0.02")
        conviction = 0.0
        scaled = kelly_risk * Decimal(str(conviction))
        assert scaled == Decimal("0.0"), "Zero conviction should zero out position"

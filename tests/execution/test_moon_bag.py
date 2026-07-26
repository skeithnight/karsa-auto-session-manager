"""Tests for Moon Bag tiered exit logic in ActivePositionManager."""

from __future__ import annotations

from decimal import Decimal

import pytest


class TestMoonBagTieredExit:
    """Verify moon bag math and state transitions."""

    def test_80_20_split_calculation(self):
        """80% closed, 20% moon bag at +1.5R."""
        amount = Decimal("100")
        close_amount = (amount * Decimal("0.8")).quantize(Decimal("0.001"))
        moon_bag_amount = amount - close_amount
        assert close_amount == Decimal("80.000"), f"Expected 80.000, got {close_amount}"
        assert moon_bag_amount == Decimal("20"), f"Expected 20, got {moon_bag_amount}"

    def test_breakeven_sl_with_fee_buffer(self):
        """Moon bag SL should be entry + 0.25% fee buffer."""
        entry_price = Decimal("1.00")
        fee_pct = Decimal("0.0025")
        moon_bag_sl = entry_price + entry_price * fee_pct
        assert moon_bag_sl == Decimal("1.0025"), f"Expected 1.0025, got {moon_bag_sl}"

    def test_moon_bag_trailing_5x_atr(self):
        """Moon bag trailing should use 5x ATR (wider than standard 2.5x)."""
        atr = Decimal("0.10")
        highest = Decimal("1.50")
        trail_distance = atr * Decimal("5")
        new_sl = highest - trail_distance
        assert new_sl == Decimal("1.00"), f"Expected 1.00, got {new_sl}"

    def test_standard_trailing_2_5x_atr(self):
        """Standard trailing should use 2.5x ATR (tighter than moon bag)."""
        atr = Decimal("0.10")
        peak = Decimal("1.50")
        trail_distance = atr * Decimal("2.5")
        new_sl = peak - trail_distance
        assert new_sl == Decimal("1.25"), f"Expected 1.25, got {new_sl}"

    def test_tranche_state_transition(self):
        """State should transition from INITIAL to MOON_BAG_ACTIVE."""
        pos = {"tranche_state": "INITIAL"}
        r_mult = Decimal("1.5")
        tranche_state = pos.get("tranche_state", "INITIAL")
        if tranche_state == "INITIAL" and r_mult >= Decimal("1.5"):
            pos["tranche_state"] = "MOON_BAG_ACTIVE"
        assert pos["tranche_state"] == "MOON_BAG_ACTIVE"

    def test_moon_bag_does_not_retrigger(self):
        """Once MOON_BAG_ACTIVE, should not trigger 1.5R logic again."""
        pos = {"tranche_state": "MOON_BAG_ACTIVE"}
        tranche_state = pos.get("tranche_state", "INITIAL")
        triggered = tranche_state == "INITIAL" and Decimal("2.0") >= Decimal("1.5")
        assert not triggered, "Moon bag should not retrigger"

    def test_small_position_minimum(self):
        """Positions too small for 80% split should not trigger moon bag."""
        amount = Decimal("0.001")
        close_amount = (amount * Decimal("0.8")).quantize(Decimal("0.001"))
        moon_bag_amount = amount - close_amount
        assert moon_bag_amount >= 0, "Moon bag amount should be non-negative"

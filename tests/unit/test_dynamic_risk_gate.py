"""Tests for DynamicRiskGate — Phase 6.3."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.alpha.regime_classifier import MarketRegime
from app.risk.dynamic_risk_gate import (
    CHOP_MAX_HOLD_MINS,
    CHOP_SIZE_MULT,
    RANGE_MAX_HOLD_MINS,
    RANGE_SIZE_MULT,
    TREND_MAX_HOLD_MINS,
    TREND_SIZE_MULT,
    DynamicRiskGate,
    RiskProfile,
)


class TestGetProfile:
    def test_trend_bull_profile(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.TREND_BULL)
        assert p.size_multiplier == TREND_SIZE_MULT == Decimal("1.0")
        assert p.use_post_only is False
        assert p.max_hold_time_mins == TREND_MAX_HOLD_MINS == 1440
        assert p.take_profit_type == "TRAILING"
        assert p.stop_loss_type == "WIDE"

    def test_trend_bear_profile(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.TREND_BEAR)
        assert p.size_multiplier == TREND_SIZE_MULT
        assert p.use_post_only is False

    def test_range_profile(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.RANGE)
        assert p.size_multiplier == RANGE_SIZE_MULT == Decimal("0.7")
        assert p.use_post_only is True
        assert p.max_hold_time_mins == RANGE_MAX_HOLD_MINS == 240
        assert p.take_profit_type == "FIXED"

    def test_chop_profile(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.CHOP)
        assert p.size_multiplier == CHOP_SIZE_MULT == Decimal("0.3")
        assert p.use_post_only is True
        assert p.max_hold_time_mins == CHOP_MAX_HOLD_MINS == 30
        assert p.take_profit_type == "SCALP"

    def test_unknown_regime_falls_back_to_chop(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile("FAKE_REGIME")  # type: ignore[arg-type]
        assert p.size_multiplier == CHOP_SIZE_MULT


class TestRiskProfileSerialization:
    def test_round_trip_json(self) -> None:
        gate = DynamicRiskGate()
        original = gate.get_profile(MarketRegime.TREND_BULL)
        json_str = original.to_json()
        restored = RiskProfile.from_json(json_str)
        assert restored == original

    def test_json_contains_string_decimals(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.RANGE)
        json_str = p.to_json()
        assert '"size_multiplier": "0.7"' in json_str
        assert '"trail_atr_mult": "2.0"' in json_str

    def test_frozen_dataclass(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.CHOP)
        with pytest.raises(AttributeError):
            p.size_multiplier = Decimal("0.5")  # type: ignore[misc]


class TestMinimumNotional:
    def test_size_multiplier_times_price_below_minimum(self) -> None:
        gate = DynamicRiskGate()
        p = gate.get_profile(MarketRegime.CHOP)
        base_size = Decimal("0.001")
        price = Decimal("30000")
        notional = base_size * p.size_multiplier * price
        assert notional < Decimal("50")  # 0.001 * 0.3 * 30000 = 9

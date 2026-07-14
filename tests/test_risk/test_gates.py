"""Tests for Risk Gate."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.risk.gates import RiskGate


class TestRiskGate:
    def setup_method(self):
        self.gate = RiskGate(
            min_24h_volume=Decimal("1000000"),
            max_spread_pct=Decimal("0.005"),
            daily_drawdown_limit=-0.02,
        )

    def test_liquidity_pass(self):
        assert self.gate.check_liquidity(Decimal("2000000")) is True

    def test_liquidity_fail(self):
        assert self.gate.check_liquidity(Decimal("500000")) is False

    def test_spread_pass(self):
        # 0.3% spread
        assert self.gate.check_spread_health(Decimal("64000"), Decimal("64192")) is True

    def test_spread_fail(self):
        # 1% spread
        assert self.gate.check_spread_health(Decimal("64000"), Decimal("64640")) is False

    def test_spread_zero_bid(self):
        assert self.gate.check_spread_health(Decimal("0"), Decimal("64000")) is False

    def test_circuit_breaker_pass(self):
        assert self.gate.check_circuit_breaker() is True

    def test_circuit_breaker_fail(self):
        self.gate.daily_pnl = Decimal("-0.03")
        assert self.gate.check_circuit_breaker() is False

    def test_evaluate_all_pass(self):
        result = self.gate.evaluate(Decimal("2000000"), Decimal("64000"), Decimal("64192"))
        assert result["passed"] is True
        assert result["failed_gate"] is None

    def test_evaluate_liquidity_fails_first(self):
        result = self.gate.evaluate(Decimal("100"), Decimal("64000"), Decimal("64192"))
        assert result["passed"] is False
        assert result["failed_gate"] == "liquidity"

    def test_update_pnl(self):
        self.gate.update_pnl(Decimal("100"))
        assert self.gate.daily_pnl == Decimal("100")

    def test_reset_daily(self):
        self.gate.daily_pnl = Decimal("500")
        self.gate.reset_daily()
        assert self.gate.daily_pnl == Decimal("0")

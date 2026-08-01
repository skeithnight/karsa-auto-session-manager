"""Sprint 1 Tests: Drawdown-Adaptive Sizing (Anti-Martingale) & Velocity Breaker Math.

Verifies:
1. KellySizer.apply_drawdown_adaptive() returns correct multipliers at each threshold
2. Drawdown velocity calculation math is correct
3. Edge cases: zero equity, zero peak, extreme drawdown
"""

from decimal import Decimal

import pytest

from app.risk.kelly_sizer import (
    DD_MODERATE_MULT,
    DD_MODERATE_THRESHOLD,
    DD_NEAR_PEAK_MULT,
    DD_NEAR_PEAK_THRESHOLD,
    DD_SEVERE_MULT,
    DD_SEVERE_THRESHOLD,
    MAX_RISK_PCT,
    MIN_RISK_PCT,
    KellySizer,
)


class TestDrawdownAdaptiveSizing:
    """Test the Anti-Martingale drawdown adjustment to Kelly risk."""

    def setup_method(self):
        self.sizer = KellySizer()
        self.base_risk = Decimal("0.010")  # 1.0% base risk (within bounds)

    def test_no_drawdown_gets_near_peak_boost(self):
        """dd=0% is < near_peak_threshold → 1.25x (house money mode)."""
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("1000"),
            equity_peak=Decimal("1000"),
        )
        expected = (self.base_risk * DD_NEAR_PEAK_MULT).quantize(Decimal("0.0001"))
        assert result == expected

    def test_near_peak_boost(self):
        """dd < 2% → 1.25x multiplier (house money mode)."""
        # 1% drawdown: peak=1000, current=990
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("990"),
            equity_peak=Decimal("1000"),
        )
        expected = (self.base_risk * DD_NEAR_PEAK_MULT).quantize(Decimal("0.0001"))
        assert result == expected

    def test_normal_range_no_adjustment(self):
        """dd between 2% and 5% → no adjustment (base risk)."""
        # 3% drawdown: peak=1000, current=970
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("970"),
            equity_peak=Decimal("1000"),
        )
        assert result == self.base_risk

    def test_moderate_drawdown(self):
        """dd > 5% → 0.50x multiplier."""
        # 7% drawdown: peak=1000, current=930
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("930"),
            equity_peak=Decimal("1000"),
        )
        expected = (self.base_risk * DD_MODERATE_MULT).quantize(Decimal("0.0001"))
        assert result == expected

    def test_severe_drawdown(self):
        """dd > 10% → 0.25x multiplier (may hit MIN floor)."""
        # 15% drawdown: peak=1000, current=850
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("850"),
            equity_peak=Decimal("1000"),
        )
        # 0.010 * 0.25 = 0.0025, clamped to MIN_RISK_PCT (0.005)
        assert result >= MIN_RISK_PCT

    def test_extreme_drawdown(self):
        """dd > 10% even at 50% drawdown → 0.25x (capped at MIN floor)."""
        # 50% drawdown: peak=1000, current=500
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("500"),
            equity_peak=Decimal("1000"),
        )
        assert result >= MIN_RISK_PCT
        assert result <= MAX_RISK_PCT

    def test_boundary_just_above_5_percent(self):
        """dd=5.1% → moderate (0.50x)."""
        # peak=1000, current=949 → dd=5.1%
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("949"),
            equity_peak=Decimal("1000"),
        )
        expected = (self.base_risk * DD_MODERATE_MULT).quantize(Decimal("0.0001"))
        assert result == expected

    def test_boundary_just_above_10_percent(self):
        """dd=10.1% → severe (0.25x)."""
        # peak=1000, current=899 → dd=10.1%
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("899"),
            equity_peak=Decimal("1000"),
        )
        assert result >= MIN_RISK_PCT

    def test_zero_peak_returns_base(self):
        """Zero equity peak → return base risk (no adjustment)."""
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("1000"),
            equity_peak=Decimal("0"),
        )
        assert result == self.base_risk

    def test_zero_equity_returns_base(self):
        """Zero current equity → return base risk (no adjustment)."""
        result = self.sizer.apply_drawdown_adaptive(
            base_risk_pct=self.base_risk,
            current_equity=Decimal("0"),
            equity_peak=Decimal("1000"),
        )
        assert result == self.base_risk

    def test_result_always_within_bounds(self):
        """Result is always between MIN_RISK_PCT and MAX_RISK_PCT."""
        # Test at various drawdown levels with a base risk within bounds
        for equity_pct in [100, 99, 95, 90, 80, 50]:
            equity = Decimal(str(equity_pct))
            peak = Decimal("100")
            result = self.sizer.apply_drawdown_adaptive(
                base_risk_pct=Decimal("0.015"),  # 1.5% — within bounds
                current_equity=equity,
                equity_peak=peak,
            )
            assert result >= MIN_RISK_PCT, f"Result {result} below MIN_RISK_PCT at {equity_pct}% equity"
            assert result <= MAX_RISK_PCT, f"Result {result} above MAX_RISK_PCT at {equity_pct}% equity"

    def test_custom_thresholds(self):
        """Custom thresholds work correctly."""
        custom_sizer = KellySizer(
            dd_severe_threshold=Decimal("0.20"),  # 20% severe
            dd_moderate_threshold=Decimal("0.10"),  # 10% moderate
            dd_near_peak_threshold=Decimal("0.05"),  # 5% near peak
            dd_severe_mult=Decimal("0.10"),
            dd_moderate_mult=Decimal("0.40"),
            dd_near_peak_mult=Decimal("1.50"),
        )
        # 15% drawdown → moderate (between 10% and 20%)
        base = Decimal("0.020")  # Use higher base to avoid MIN floor
        result = custom_sizer.apply_drawdown_adaptive(
            base_risk_pct=base,
            current_equity=Decimal("850"),
            equity_peak=Decimal("1000"),
        )
        expected = (base * Decimal("0.40")).quantize(Decimal("0.0001"))
        assert result == expected


class TestVelocityBreakerMath:
    """Test the drawdown velocity calculation math (pure logic, no Redis)."""

    def test_rolling_1h_pnl_calculation(self):
        """Verify rolling 1h PnL sums correctly."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)

        # Simulate trades: 3 losses in last hour
        trades = [
            {"exit_time": (now - timedelta(minutes=30)).isoformat(), "realized_pnl": "-5.00"},
            {"exit_time": (now - timedelta(minutes=20)).isoformat(), "realized_pnl": "-3.00"},
            {"exit_time": (now - timedelta(minutes=10)).isoformat(), "realized_pnl": "-2.00"},
            {"exit_time": (now - timedelta(hours=2)).isoformat(), "realized_pnl": "-10.00"},  # outside 1h window
        ]

        rolling_1h_pnl = Decimal("0")
        for t in trades:
            exit_time = datetime.fromisoformat(t["exit_time"])
            if exit_time.tzinfo is None:
                exit_time = exit_time.replace(tzinfo=UTC)
            if exit_time >= one_hour_ago:
                rolling_1h_pnl += Decimal(t["realized_pnl"])

        assert rolling_1h_pnl == Decimal("-10.00")  # Only the 3 recent trades

    def test_velocity_threshold_trigger(self):
        """Loss exceeding -1.5% of equity triggers velocity breaker."""
        rolling_1h_pnl = Decimal("-20.00")
        equity = Decimal("1000.00")
        loss_pct = rolling_1h_pnl / equity
        threshold = Decimal("-0.015")  # -1.5%

        assert loss_pct == Decimal("-0.020")
        assert loss_pct < threshold  # Should trigger pause

    def test_velocity_threshold_exact(self):
        """Loss exactly at -1.5% does NOT trigger (must be strictly less than)."""
        rolling_1h_pnl = Decimal("-15.00")
        equity = Decimal("1000.00")
        loss_pct = rolling_1h_pnl / equity
        threshold = Decimal("-0.015")

        # loss_pct == threshold, NOT < threshold — should NOT trigger
        assert loss_pct == threshold
        assert not (loss_pct < threshold)

    def test_velocity_no_trigger(self):
        """Small loss should not trigger velocity breaker."""
        rolling_1h_pnl = Decimal("-5.00")
        equity = Decimal("1000.00")
        loss_pct = rolling_1h_pnl / equity
        threshold = Decimal("-0.015")

        assert loss_pct == Decimal("-0.005")
        assert loss_pct >= threshold  # Should NOT trigger

    def test_config_thresholds_loaded(self):
        """Config thresholds load correctly from Settings."""
        from app.core.config import Settings

        s = Settings()
        assert float(s.velocity_1h_loss_pct) == -0.015
        assert int(s.velocity_pause_seconds) == 7200
        assert float(s.dd_severe_threshold) == 0.10
        assert float(s.dd_moderate_threshold) == 0.05
        assert float(s.dd_near_peak_threshold) == 0.02
        assert float(s.dd_severe_mult) == 0.25
        assert float(s.dd_moderate_mult) == 0.50
        assert float(s.dd_near_peak_mult) == 1.25

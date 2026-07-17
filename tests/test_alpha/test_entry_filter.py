"""Tests for Entry Filter."""

from __future__ import annotations

from datetime import datetime, timezone


from app.alpha.entry_filter import EntryFilter


class TestEntryFilter:
    def setup_method(self):
        self.filt = EntryFilter()

    def test_all_checks_pass(self):
        ok, reason = self.filt.check(
            regime="TREND_BULL",
            spread_pct=0.001,
            bid_depth=100.0,
            ask_depth=120.0,
            has_position=False,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True
        assert reason == "passed"

    def test_chop_passes_entry_filter(self):
        """Phase 6: CHOP no longer hard-blocked. StrategyRouter gates CHOP signals."""
        ok, reason = self.filt.check(
            regime="CHOP",
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True
        assert reason == "passed"

    def test_spread_too_wide(self):
        ok, reason = self.filt.check(spread_pct=0.01)
        assert ok is False
        assert "spread" in reason

    def test_depth_ratio_too_low(self):
        ok, reason = self.filt.check(bid_depth=100.0, ask_depth=50.0)
        assert ok is False
        assert "depth" in reason

    def test_depth_ratio_too_high(self):
        ok, reason = self.filt.check(bid_depth=50.0, ask_depth=100.0)
        assert ok is False
        assert "depth" in reason

    def test_blocked_hour(self):
        ok, reason = self.filt.check(
            now_utc=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        )
        assert ok is False
        assert "blocked" in reason

    def test_existing_position(self):
        ok, reason = self.filt.check(has_position=True)
        assert ok is False
        assert "existing position" in reason

    def test_none_values_skip_optional_checks(self):
        ok, reason = self.filt.check(
            regime="TREND_BULL",
            spread_pct=None,
            bid_depth=None,
            ask_depth=None,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True

    def test_depth_zero_bid(self):
        ok, reason = self.filt.check(bid_depth=0.0, ask_depth=100.0)
        assert ok is False


class TestRegimeDependentSpread:
    """Phase 6.1: Spread limits vary by regime."""

    def setup_method(self):
        self.filt = EntryFilter()

    def test_trend_tight_spread(self):
        """TREND: 0.10% max — spread at 0.15% should fail."""
        ok, reason = self.filt.check(
            regime="TREND_BULL",
            spread_pct=0.0015,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is False
        assert "spread" in reason

    def test_trend_tight_spread_pass(self):
        """TREND: 0.10% max — spread at 0.08% should pass."""
        ok, reason = self.filt.check(
            regime="TREND_BULL",
            spread_pct=0.0008,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True

    def test_chop_wide_spread(self):
        """CHOP: 0.30% max — spread at 0.25% should pass."""
        ok, reason = self.filt.check(
            regime="CHOP",
            spread_pct=0.0025,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True

    def test_chop_wide_spread_fail(self):
        """CHOP: 0.30% max — spread at 0.35% should fail."""
        ok, reason = self.filt.check(
            regime="CHOP",
            spread_pct=0.0035,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is False
        assert "spread" in reason

    def test_range_medium_spread(self):
        """RANGE: 0.15% max — spread at 0.20% should fail."""
        ok, reason = self.filt.check(
            regime="RANGE",
            spread_pct=0.0020,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is False
        assert "spread" in reason

    def test_unknown_regime_uses_default(self):
        """Unknown regime falls back to max_spread_pct (0.3%)."""
        ok, reason = self.filt.check(
            regime="UNKNOWN",
            spread_pct=0.0025,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True

    def test_none_regime_uses_default(self):
        """No regime falls back to max_spread_pct."""
        ok, reason = self.filt.check(
            spread_pct=0.0025,
            now_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        assert ok is True

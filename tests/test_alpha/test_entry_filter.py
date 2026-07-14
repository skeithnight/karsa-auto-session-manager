"""Tests for Entry Filter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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

    def test_chop_rejects(self):
        ok, reason = self.filt.check(regime="CHOP")
        assert ok is False
        assert "CHOP" in reason

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

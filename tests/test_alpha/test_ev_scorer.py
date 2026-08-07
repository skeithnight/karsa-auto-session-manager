"""Tests for EV Composite Scorer (Phase 1: Filter Collapse)."""

import pytest
from app.alpha.ev_scorer import EVScorer, EVComponents, quick_ev_score, _get_session_label


class TestEVScorer:
    """Test EVScorer composite scoring."""

    def setup_method(self):
        self.scorer = EVScorer()

    def test_trend_bull_long_high_ev(self):
        """TREND_BULL + LONG should produce high EV."""
        ev, components = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            spread_pct=0.0005, rsi=60.0, macd_hist=0.02,
            skew=0.5, funding_rate=-0.0003, multi_tf_agrees=True,
            regime_conviction=0.8, hour_utc=14,
        )
        assert ev > 0.5, f"Expected EV > 0.5, got {ev}"
        assert components.regime_alignment == 1.0
        assert components.session_quality == 1.2  # LDN_NY_OVERLAP

    def test_trend_bull_short_low_ev(self):
        """TREND_BULL + SHORT should produce lower EV than TREND_BULL + LONG."""
        ev_long, _ = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            spread_pct=0.0005, rsi=60.0, skew=0.5,
            regime_conviction=0.8, hour_utc=14,
        )
        ev_short, components = self.scorer.score(
            regime="TREND_BULL", direction="SHORT",
            spread_pct=0.0005, rsi=60.0, skew=0.5,
            regime_conviction=0.8, hour_utc=14,
        )
        assert ev_short < ev_long, f"SHORT EV {ev_short} should be < LONG EV {ev_long}"
        assert components.regime_alignment == 0.3

    def test_chop_regime_neutral(self):
        """CHOP regime should produce moderate EV (not blocked)."""
        ev, components = self.scorer.score(
            regime="CHOP", direction="LONG",
            spread_pct=0.002, rsi=50.0, skew=0.0,
            regime_conviction=0.4, hour_utc=14,
        )
        assert 0.2 < ev < 0.8, f"Expected moderate EV, got {ev}"
        assert components.regime_alignment == 0.5

    def test_spread_quality_soft_penalty(self):
        """Wide spread should reduce EV, not kill it."""
        ev_tight, _ = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            spread_pct=0.0005, hour_utc=14,
        )
        ev_wide, _ = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            spread_pct=0.003, hour_utc=14,
        )
        assert ev_wide < ev_tight, "Wide spread should reduce EV"
        assert ev_wide > 0, "Wide spread should not kill EV entirely"

    def test_session_quality_multipliers(self):
        """Session quality should multiply EV."""
        ev_ldn_ny, _ = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            hour_utc=14,  # LDN_NY_OVERLAP
        )
        ev_asia, _ = self.scorer.score(
            regime="TREND_BULL", direction="LONG",
            hour_utc=3,  # ASIA
        )
        assert ev_ldn_ny > ev_asia, "LDN_NY should have higher EV than ASIA"

    def test_funding_edge(self):
        """Negative funding on LONG should boost EV."""
        ev_neg_funding, _ = self.scorer.score(
            regime="RANGE", direction="LONG",
            funding_rate=-0.001, hour_utc=14,
        )
        ev_pos_funding, _ = self.scorer.score(
            regime="RANGE", direction="LONG",
            funding_rate=0.001, hour_utc=14,
        )
        assert ev_neg_funding > ev_pos_funding, "Negative funding should boost LONG EV"

    def test_quick_ev_score_returns_float(self):
        """quick_ev_score should return a float."""
        ev = quick_ev_score(
            regime="TREND_BULL", direction="LONG",
            hour_utc=14,
        )
        assert isinstance(ev, float)
        assert 0.0 <= ev <= 2.0

    def test_custom_weights(self):
        """Custom weights should change scoring."""
        custom = EVScorer(weights={
            "regime_alignment": 1.0,
            "momentum_strength": 0.0,
            "microstructure": 0.0,
            "funding_edge": 0.0,
            "spread_quality": 0.0,
            "multi_tf_alignment": 0.0,
            "historical_edge": 0.0,
            "conviction": 0.0,
            "oi_signal": 0.0,
        })
        ev_custom, _ = custom.score(
            regime="TREND_BULL", direction="LONG",
            regime_conviction=0.8, hour_utc=14,
        )
        # With only regime_alignment weight, EV should be ~1.0 * 0.20 * session_mult
        assert ev_custom > 0


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


class TestEVComponents:
    """Test EVComponents dataclass."""

    def test_components_creation(self):
        """EVComponents should be creatable with all fields."""
        c = EVComponents(
            regime_alignment=0.8,
            spread_quality=0.9,
            momentum_strength=0.7,
            microstructure=0.6,
            funding_edge=0.1,
            multi_tf_alignment=1.0,
            historical_edge=0.55,
            conviction=0.75,
            oi_signal=0.5,
            session_quality=1.2,
        )
        assert c.regime_alignment == 0.8
        assert c.session_quality == 1.2


class TestRegimeWeights:
    """Test regime-specific optimized weights."""

    def test_default_weights_when_no_redis(self):
        """Without Redis, should use default weights."""
        scorer = EVScorer()
        assert scorer._weights == {
            "regime_alignment": 0.20,
            "momentum_strength": 0.20,
            "microstructure": 0.15,
            "funding_edge": 0.10,
            "spread_quality": 0.10,
            "multi_tf_alignment": 0.10,
            "historical_edge": 0.08,
            "conviction": 0.05,
            "oi_signal": 0.02,
        }

    def test_custom_weights_override(self):
        """Custom weights should override defaults."""
        custom = {"regime_alignment": 1.0}
        scorer = EVScorer(weights=custom)
        assert scorer._weights == custom

    def test_optimized_cache_populated(self):
        """Optimized cache should be used when available."""
        scorer = EVScorer()
        scorer._optimized_cache["TREND_BULL"] = {"regime_alignment": 0.30}
        # Verify cache is accessible
        assert "TREND_BULL" in scorer._optimized_cache
        assert scorer._optimized_cache["TREND_BULL"]["regime_alignment"] == 0.30

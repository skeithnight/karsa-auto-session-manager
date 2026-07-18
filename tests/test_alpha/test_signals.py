"""Tests for Signal Generator."""

from __future__ import annotations

from decimal import Decimal

from app.alpha.signals import SignalGenerator


class TestSignalGenerator:
    def setup_method(self):
        self.gen = SignalGenerator(min_skew=0.3, min_confidence=0.6, position_size=Decimal("0.001"))

    def test_long_signal(self):
        # All signals aligned LONG: skew positive, lead-lag positive, funding negative, OI rising
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
        )
        assert signal is not None
        assert signal.direction == "LONG"
        assert signal.symbol == "BTC/USDT:USDT"

    def test_short_signal(self):
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), -0.5,
            lead_lag_delta=-0.003, funding_rate=0.0005, oi_change=-100.0,
        )
        assert signal is not None
        assert signal.direction == "SHORT"

    def test_flat_below_threshold(self):
        signal = self.gen.generate("BTC/USDT:USDT", Decimal("64000"), 0.1)
        assert signal is None

    def test_no_vwap_returns_none(self):
        signal = self.gen.generate("BTC/USDT:USDT", None, 0.5)
        assert signal is None

    def test_low_confidence_returns_none(self):
        signal = self.gen.generate("BTC/USDT:USDT", Decimal("64000"), 0.35)
        assert signal is None

    def test_signal_to_dict(self):
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.7,
            lead_lag_delta=0.004, funding_rate=-0.0004, oi_change=50.0,
        )
        assert signal is not None
        d = signal.to_dict()
        assert d["direction"] == "LONG"
        assert d["symbol"] == "BTC/USDT:USDT"
        assert "id" in d

    def test_chop_with_strategy_score_can_generate(self):
        """Phase 6: CHOP no longer hard-blocked. With strong strategy score, signal passes."""
        # CHOP multiplier=0.5, so raw confidence gets halved.
        # Strategy score blending (0.6*conf + 0.4*strategy_norm) pushes it above threshold.
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.8,
            regime="CHOP", lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
            strategy_score=85.0,
        )
        assert signal is not None
        assert signal.direction in ("LONG", "SHORT")

    def test_chop_without_strategy_score_low_confidence(self):
        """CHOP without strategy score: 0.5x multiplier keeps confidence below threshold."""
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            regime="CHOP", lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
        )
        assert signal is None

    def test_trend_regime_boosts_confidence(self):
        signal_bull = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            regime="TREND_BULL", lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
        )
        signal_none = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            regime=None, lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
        )
        assert signal_bull is not None and signal_none is not None
        assert signal_bull.confidence > signal_none.confidence

    def test_lead_lag_contradicts_skew(self):
        # Skew says LONG but lead-lag strongly contradicts
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            lead_lag_delta=-0.004, funding_rate=-0.0005, oi_change=100.0,
        )
        assert signal is None

    def test_composite_metrics_included(self):
        signal = self.gen.generate(
            "BTC/USDT:USDT", Decimal("64000"), 0.5,
            lead_lag_delta=0.003, funding_rate=-0.0005, oi_change=100.0,
        )
        assert signal is not None
        assert "s_skew" in signal.metrics
        assert "s_lead_lag" in signal.metrics
        assert "s_funding" in signal.metrics
        assert "s_oi" in signal.metrics
        assert "regime" in signal.metrics

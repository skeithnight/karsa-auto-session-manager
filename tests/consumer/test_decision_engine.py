"""Tests for app.consumer.decision_engine.DecisionEngine.

Mock RegimeClassifier, StrategyRouter, DynamicRiskGate to test
pipeline logic without real indicator math.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np

from app.alpha.regime_classifier import MarketRegime
from app.consumer.decision_engine import DecisionEngine, TradeSignal


def _make_candles(n: int = 100, start: float = 1000.0) -> list[list]:
    """Generate n candles starting at a given price, uptrend."""
    return [
        [1700000000000 + i * 3600000, start + i * 0.5, start + i * 0.5 + 2.0,
         start + i * 0.5 - 1.0, start + i * 0.5 + 1.0, 1000.0 + i]
        for i in range(n)
    ]


def _build_engine(
    regime: MarketRegime = MarketRegime.RANGE,
    score: float = 80.0,
    gate: float = 65.0,
) -> DecisionEngine:
    """Build a DecisionEngine with mocked sub-components."""
    classifier = MagicMock()
    classifier.classify.return_value = regime

    router = MagicMock()
    router.evaluate_signal.return_value = score

    risk_gate = MagicMock()
    risk_gate.get_profile.return_value = MagicMock(
        regime=regime.value,
        size_multiplier=Decimal("1.0"),
        take_profit_type="TRAILING",
        stop_loss_type="WIDE",
        max_hold_time_mins=1440,
        use_post_only=False,
        trail_atr_mult=Decimal("3.0"),
        sl_atr_buffer=Decimal("1.5"),
        to_json=lambda: '{"test": true}',
    )

    return DecisionEngine(
        classifier=classifier,
        router=router,
        risk_gate=risk_gate,
        gate_threshold=gate,
    )


class TestDecisionEngineInsufficientData:
    def test_returns_none_for_fewer_than_50_candles(self) -> None:
        engine = _build_engine()
        result = engine.evaluate("BTC/USDT", _make_candles(30))
        assert result is None


class TestDecisionEngineScoreGate:
    def test_returns_none_when_below_gate(self) -> None:
        engine = _build_engine(score=40.0, gate=65.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is None

    def test_returns_signal_when_above_gate(self) -> None:
        engine = _build_engine(score=80.0, gate=65.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.score == 80.0


class TestDecisionEngineDirections:
    def test_trend_bull_only_long(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.direction == "LONG"

    def test_trend_bear_only_short(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BEAR, score=80.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.direction == "SHORT"

    def test_range_tries_both_directions(self) -> None:
        engine = _build_engine(regime=MarketRegime.RANGE, score=80.0)
        # evaluate_signal mock returns 80 for first direction tested
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        # RANGE → ["LONG", "SHORT"] — first one that passes gate is taken
        assert result.direction in ("LONG", "SHORT")


class TestDecisionEngineSignalFields:
    def test_signal_has_required_fields(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert isinstance(result, TradeSignal)
        assert result.symbol == "BTC/USDT"
        assert result.direction == "LONG"
        assert result.regime == MarketRegime.TREND_BULL
        assert isinstance(result.entry_price, Decimal)
        assert isinstance(result.sl_price, Decimal)
        assert isinstance(result.amount, Decimal)
        assert result.amount > 0

    def test_trend_regime_has_no_fixed_tp(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        # Mock risk_gate to return TRAILING TP
        engine._risk_gate.get_profile.return_value.take_profit_type = "TRAILING"
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.tp_price is None  # TRAILING → no fixed TP

    def test_range_regime_has_fixed_tp(self) -> None:
        engine = _build_engine(regime=MarketRegime.RANGE, score=80.0)
        engine._risk_gate.get_profile.return_value.take_profit_type = "FIXED"
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.tp_price is not None


class TestDecisionEngineATR:
    def test_atr_positive_with_enough_data(self) -> None:
        engine = _build_engine()
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.atr > Decimal("0")

    def test_atr_zero_with_insufficient_data(self) -> None:
        atr = DecisionEngine._calculate_atr(np.array([[0]*6]*5, dtype=np.float64))
        assert atr == Decimal("0")


class TestDecisionEngineEntrySlippage:
    def test_long_entry_above_close(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        close = Decimal(str(_make_candles(100)[-1][4]))
        assert result.entry_price > close

    def test_short_entry_below_close(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BEAR, score=80.0)
        result = engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        close = Decimal(str(_make_candles(100)[-1][4]))
        assert result.entry_price < close

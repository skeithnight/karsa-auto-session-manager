"""Tests for app.consumer.decision_engine.DecisionEngine.

Mock RegimeClassifier, StrategyRouter, DynamicRiskGate to test
pipeline logic without real indicator math.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pytest

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
    # Mock MarketAnalyzer (used for regime classification)
    analyzer = MagicMock()
    analyzer.current_state = MagicMock()
    analyzer.current_state.regime = regime.value

    router = MagicMock()
    # evaluate_signal is async, returns (DecisionContext, vol_factor)
    mock_context = MagicMock()
    mock_context.total_confidence = score
    mock_context.evidence = []
    mock_context.features = MagicMock()
    mock_context.features.cvd_slope = 0.0
    mock_context.features.spread_pct = 0.0
    mock_context.features.atr_pct = 50.0
    mock_context.features.liquidity_walls = {}
    mock_context.to_dict.return_value = {"total_confidence": score}

    async def _mock_evaluate_signal(*args, **kwargs):
        return (mock_context, 1.0)

    router.evaluate_signal = _mock_evaluate_signal

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

    engine = DecisionEngine(
        analyzer=analyzer,
        router=router,
        risk_gate=risk_gate,
        gate_threshold=gate,
    )
    # Override the router that __init__ created with our mock
    engine._router = router
    return engine


@pytest.mark.asyncio
class TestDecisionEngineInsufficientData:
    async def test_returns_none_for_fewer_than_50_candles(self) -> None:
        engine = _build_engine()
        result = await engine.evaluate("BTC/USDT", _make_candles(30))
        assert result is None


@pytest.mark.asyncio
class TestDecisionEngineScoreGate:
    async def test_returns_none_when_below_gate(self) -> None:
        engine = _build_engine(score=40.0, gate=65.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is None

    async def test_returns_signal_when_above_gate(self) -> None:
        engine = _build_engine(score=80.0, gate=65.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.score == 80.0


@pytest.mark.asyncio
class TestDecisionEngineDirections:
    async def test_trend_bull_only_long(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.direction == "LONG"

    async def test_trend_bear_only_short(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BEAR, score=80.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.direction == "SHORT"

    async def test_range_tries_both_directions(self) -> None:
        engine = _build_engine(regime=MarketRegime.RANGE, score=80.0)
        # evaluate_signal mock returns 80 for first direction tested
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        # RANGE → ["LONG", "SHORT"] — first one that passes gate is taken
        assert result.direction in ("LONG", "SHORT")


@pytest.mark.asyncio
class TestDecisionEngineSignalFields:
    async def test_signal_has_required_fields(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert isinstance(result, TradeSignal)
        assert result.symbol == "BTC/USDT"
        assert result.direction == "LONG"
        assert result.regime == MarketRegime.TREND_BULL
        assert isinstance(result.entry_price, Decimal)
        assert isinstance(result.sl_price, Decimal)
        assert isinstance(result.amount, Decimal)
        assert result.amount > 0

    async def test_trend_regime_has_no_fixed_tp(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        # Mock risk_gate to return TRAILING TP
        engine._risk_gate.get_profile.return_value.take_profit_type = "TRAILING"
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.tp_price is None  # TRAILING → no fixed TP

    async def test_range_regime_has_fixed_tp(self) -> None:
        engine = _build_engine(regime=MarketRegime.RANGE, score=80.0)
        engine._risk_gate.get_profile.return_value.take_profit_type = "FIXED"
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.tp_price is not None


@pytest.mark.asyncio
class TestDecisionEngineATR:
    async def test_atr_positive_with_enough_data(self) -> None:
        engine = _build_engine()
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        assert result.atr > Decimal("0")

    async def test_atr_zero_with_insufficient_data(self) -> None:
        atr = DecisionEngine._calculate_atr(np.array([[0]*6]*5, dtype=np.float64))
        assert atr == Decimal("0")


@pytest.mark.asyncio
class TestDecisionEngineEntrySlippage:
    async def test_long_entry_above_close(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=80.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        close = Decimal(str(_make_candles(100)[-1][4]))
        assert result.entry_price > close

    async def test_short_entry_below_close(self) -> None:
        engine = _build_engine(regime=MarketRegime.TREND_BEAR, score=80.0)
        result = await engine.evaluate("BTC/USDT", _make_candles(100))
        assert result is not None
        close = Decimal(str(_make_candles(100)[-1][4]))
        assert result.entry_price < close


@pytest.mark.asyncio
class TestDipBuyerBoost:
    """Test dip-buyer gate reduction (Phase 3)."""

    def _make_dip_candles(self) -> list[list]:
        """Build 100 candles: strong rally then -7% dip for dip-buy candidate."""
        candles = []
        # First 52 candles: flat at 80
        for i in range(52):
            close = 80.0
            candles.append(
                [1700000000000 + i * 3600000, close - 1, close + 1, close - 1, close, 1000.0]
            )
        # Candles 53-100: ramp from 80 to 120 (+50%), then dip to ~111.6 (-7% from 120)
        # closes[-48] = 80, closes[-1] = 111.6, move_48h = 39.5% > 15% ✓
        # high_48h = 121, dip_from_high = 7.8% (in 5-10% zone) ✓
        for i in range(48):
            if i < 30:
                close = 80.0 + (i + 1) * (40.0 / 30)  # Ramp to 120
            else:
                close = 120.0 - (i - 29) * (8.4 / 18)  # Dip to 111.6
            candles.append(
                [1700000000000 + (52 + i) * 3600000, close - 1, close + 1, close - 1, close, 1000.0]
            )
        return candles

    async def test_dip_buyer_reduces_gate(self) -> None:
        """Strong performer in dip zone should get 10% gate reduction."""
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=70.0, gate=75.0)
        candles = self._make_dip_candles()
        # Score 70 < gate 75, but with 10% reduction → effective gate = 67.5
        result = await engine.evaluate("BTC/USDT", candles)
        assert result is not None  # Would have been rejected without dip-buy boost

    async def test_dip_buyer_no_boost_when_not_in_dip(self) -> None:
        """Strong performer NOT in dip zone should NOT get boost."""
        engine = _build_engine(regime=MarketRegime.TREND_BULL, score=70.0, gate=75.0)
        # Build candles with +20% but no dip (still near highs)
        candles = []
        for i in range(52):
            close = 100.0
            candles.append(
                [1700000000000 + i * 3600000, close - 1, close + 1, close - 1, close, 1000.0]
            )
        for i in range(48):
            close = 100.0 + (i + 1) * (20.0 / 48)  # Ramp to 120, no dip
            candles.append(
                [1700000000000 + (52 + i) * 3600000, close - 1, close + 1, close - 1, close, 1000.0]
            )
        result = await engine.evaluate("BTC/USDT", candles)
        assert result is None  # Score 70 < gate 75, no boost

"""Unit tests for DecisionEngineV2."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
import numpy as np

from app.alpha.regime_classifier import MarketRegime
from app.consumer.decision_engine_v2 import DecisionEngineV2, TradeSignal


class TestDecisionEngineV2:
    """Test DecisionEngineV2."""

    def setup_method(self):
        self.analyzer = MagicMock()
        self.analyzer.current_state = MagicMock(regime="TREND_BULL")
        self.router = AsyncMock()
        self.router.evaluate_signal.return_value = (MagicMock(total_confidence=75.0), 1.0)
        self.risk_gate = MagicMock()
        self.risk_gate.get_profile.return_value = MagicMock(
            sl_atr_buffer=Decimal("2.0"),
            trail_atr_mult=Decimal("1.5"),
            take_profit_type="TRAILING",
            use_post_only=True,
            size_multiplier=Decimal("1.0"),
        )
        self.redis = AsyncMock()

    def test_init(self):
        """Test initialization."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
        )
        assert engine._analyzer == self.analyzer
        assert engine._router == self.router
        assert engine._risk_gate == self.risk_gate
        assert len(engine._score_composer._families) == 5

    def test_init_with_deps(self):
        """Test initialization with dependencies."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
            redis_client=self.redis,
        )
        assert engine._redis == self.redis

    def test_determine_directions_trend_bull(self):
        """Test direction determination for TREND_BULL."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
        )
        directions = engine._determine_directions(MarketRegime.TREND_BULL)
        assert directions == ["LONG"]

    def test_determine_directions_trend_bear(self):
        """Test direction determination for TREND_BEAR."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
        )
        directions = engine._determine_directions(MarketRegime.TREND_BEAR)
        assert directions == ["SHORT"]

    def test_determine_directions_range(self):
        """Test direction determination for RANGE."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
        )
        directions = engine._determine_directions(MarketRegime.RANGE)
        assert directions == ["LONG", "SHORT"]

    def test_calculate_atr(self):
        """Test ATR calculation."""
        arr = np.array([
            [1, 100, 105, 95, 102, 1000],
            [2, 102, 108, 98, 105, 1200],
            [3, 105, 110, 100, 108, 1100],
            [4, 108, 112, 103, 110, 1300],
            [5, 110, 115, 105, 112, 1400],
        ], dtype=np.float64)
        
        atr = DecisionEngineV2._calculate_atr(arr, period=3)
        assert atr > Decimal("0")

    def test_calculate_atr_insufficient_data(self):
        """Test ATR with insufficient data."""
        arr = np.array([
            [1, 100, 105, 95, 102, 1000],
            [2, 102, 108, 98, 105, 1200],
        ], dtype=np.float64)
        
        atr = DecisionEngineV2._calculate_atr(arr, period=3)
        assert atr == Decimal("0")

    def test_build_signal(self):
        """Test signal building."""
        engine = DecisionEngineV2(
            analyzer=self.analyzer,
            router=self.router,
            risk_gate=self.risk_gate,
        )
        
        arr = np.array([
            [1, 100, 105, 95, 102, 1000],
            [2, 102, 108, 98, 105, 1200],
            [3, 105, 110, 100, 108, 1100],
            [4, 108, 112, 103, 110, 1300],
            [5, 110, 115, 105, 112, 1400],
        ], dtype=np.float64)
        
        profile = MagicMock(
            sl_atr_buffer=Decimal("2.0"),
            trail_atr_mult=Decimal("1.5"),
            take_profit_type="TRAILING",
            use_post_only=True,
        )
        
        from app.consumer.score_composer import ComposedScore
        composed = ComposedScore(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            total_score=75.0,
            family_scores={},
            winning_family="trend_continuation",
            confidence=0.75,
            regime_aligned=True,
        )
        
        from app.consumer.sizing_pipeline import SizingResult
        sizing = SizingResult(
            amount=Decimal("0.001"),
            risk_pct=Decimal("0.01"),
            kelly_fraction=0.5,
            drawdown_mult=1.0,
            conviction_mult=1.0,
            macro_mult=1.0,
            session_mult=1.0,
            uncertainty_factor=1.0,
            garch_factor=1.0,
        )
        
        signal = engine._build_signal(
            symbol="BTC/USDT",
            direction="LONG",
            regime=MarketRegime.TREND_BULL,
            score=75.0,
            arr=arr,
            entry_price=Decimal("102.01"),
            sl_price=Decimal("98.0"),
            atr=Decimal("5.0"),
            sizing=sizing,
            profile=profile,
            composed=composed,
            stage_timings={},
        )
        
        assert signal.symbol == "BTC/USDT"
        assert signal.direction == "LONG"
        assert signal.regime == MarketRegime.TREND_BULL
        assert signal.score == 75.0
        assert signal.winning_family == "trend_continuation"
        assert signal.amount == Decimal("0.001")

"""Signal Generator — produces TradingSignal from GlobalState."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4

from loguru import logger

from app.alpha.metrics import AlphaMetrics
from app.alpha.regime import REGIME_CHOP


class TradingSignal:
    """Represents a trading signal."""

    def __init__(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        size: Decimal,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.debug(f"TradingSignal.__init__: entering symbol={symbol}")
        self.id = str(uuid4())
        self.symbol = symbol
        self.direction = direction  # "LONG", "SHORT", "FLAT"
        self.confidence = confidence  # 0.0 - 1.0
        self.size = size
        self.metrics = metrics or {}
        self.generated_at = datetime.now(timezone.utc)
        logger.debug("TradingSignal.__init__: returning")

    def to_dict(self) -> Dict[str, Any]:
        logger.debug("to_dict: entering")
        result = {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "size": str(self.size),
            "metrics": self.metrics,
            "generated_at": self.generated_at.isoformat(),
        }
        logger.debug("to_dict: returning dict")
        return result


class SignalGenerator:
    """Generates trading signals from market state."""

    def __init__(
        self,
        min_skew: float = 0.3,
        min_confidence: float = 0.6,
        position_size: Decimal = Decimal("0.001"),
    ) -> None:
        logger.debug("SignalGenerator.__init__: entering")
        self.min_skew = min_skew
        self.min_confidence = min_confidence
        self.position_size = position_size
        logger.debug("SignalGenerator.__init__: returning")

    # Regime confidence modifiers (Phase 1C)
    REGIME_MULTIPLIERS = {
        "TREND_BULL": 1.2,
        "TREND_BEAR": 1.2,
        "MEAN_REVERSION": 0.8,
        "CHOP": 0.0,  # Force FLAT
    }

    # Composite weights (Phase 2D)
    W_SKEW = 0.4
    W_LEAD_LAG = 0.3
    W_FUNDING = 0.2
    W_OI = 0.1

    def generate(
        self,
        symbol: str,
        global_vwap: Optional[Decimal],
        aggregate_skew: float,
        regime: Optional[str] = None,
        lead_lag_delta: Optional[float] = None,
        funding_rate: Optional[float] = None,
        oi_change: Optional[float] = None,
    ) -> Optional[TradingSignal]:
        """Generate trading signal from composite multi-signal confidence.

        confidence = regime_mult × (0.4×S_skew + 0.3×S_lead_lag + 0.2×S_funding + 0.1×S_oi)
        Direction: AND-gate — skew direction wins, lead-lag must agree or be neutral.
        """
        logger.debug(f"generate: entering symbol={symbol}")
        if global_vwap is None:
            logger.debug(f"No VWAP for {symbol} — skipping signal")
            return None

        # CHOP regime: no trades, period
        if regime == REGIME_CHOP:
            logger.debug(f"Signal {symbol}: CHOP regime — forcing FLAT")
            return None

        # --- Individual signal scores (all normalized to [-1, 1]) ---
        # Skew: direct normalization
        s_skew = max(-1.0, min(1.0, aggregate_skew / 0.8))

        # Lead-lag: positive delta = lead outperforming → LONG bias
        s_lead_lag = 0.0
        if lead_lag_delta is not None:
            s_lead_lag = max(-1.0, min(1.0, lead_lag_delta / 0.005))

        # Funding: contrarian — negative funding → LONG bias
        s_funding = 0.0
        if funding_rate is not None:
            s_funding = max(-1.0, min(1.0, -funding_rate / 0.0003))

        # OI: binary — rising = 1.0, falling = -1.0
        s_oi = 0.0
        if oi_change is not None:
            s_oi = 1.0 if oi_change > 0 else -1.0 if oi_change < 0 else 0.0

        # --- Composite confidence ---
        raw_score = (
            self.W_SKEW * s_skew
            + self.W_LEAD_LAG * s_lead_lag
            + self.W_FUNDING * s_funding
            + self.W_OI * s_oi
        )

        confidence = abs(raw_score)

        # Direction from skew (primary), lead-lag must not contradict
        if s_skew > 0:
            direction = "LONG"
            if s_lead_lag < -0.3:
                logger.debug(f"Signal {symbol}: lead-lag contradicts LONG — skipping")
                return None
        elif s_skew < 0:
            direction = "SHORT"
            if s_lead_lag > 0.3:
                logger.debug(f"Signal {symbol}: lead-lag contradicts SHORT — skipping")
                return None
        else:
            direction = "FLAT"

        # Apply regime multiplier
        regime_mult = self.REGIME_MULTIPLIERS.get(regime, 1.0) if regime else 1.0
        confidence *= regime_mult
        confidence = min(confidence, 1.0)

        if direction == "FLAT" or confidence < self.min_confidence:
            logger.debug(f"Signal {symbol}: {direction} (conf={confidence:.2f}) — below threshold")
            return None

        signal = TradingSignal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            size=self.position_size,
            metrics={
                "global_vwap": str(global_vwap),
                "aggregate_skew": aggregate_skew,
                "regime": regime or "UNKNOWN",
                "regime_mult": regime_mult,
                "s_skew": s_skew,
                "s_lead_lag": s_lead_lag,
                "s_funding": s_funding,
                "s_oi": s_oi,
                "raw_score": raw_score,
            },
        )

        logger.info(f"Signal generated: {symbol} {direction} (conf={confidence:.2f}) regime={regime}")
        logger.debug(f"generate: returning TradingSignal")
        return signal

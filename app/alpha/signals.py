"""Signal Generator — produces TradingSignal from GlobalState."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from loguru import logger


class TradingSignal:
    """Represents a trading signal."""

    def __init__(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        size: Decimal,
        metrics: dict[str, Any] | None = None,
        atr: Decimal | None = None,
    ) -> None:
        logger.debug(f"TradingSignal.__init__: entering symbol={symbol}")
        self.id = str(uuid4())
        self.symbol = symbol
        self.direction = direction  # "LONG", "SHORT", "FLAT"
        self.confidence = confidence  # 0.0 - 1.0
        self.size = size
        self.metrics = metrics or {}
        self.generated_at = datetime.now(UTC)
        self.atr = atr  # 1H ATR for position lifecycle (trailing stop, partial TP)
        logger.debug("TradingSignal.__init__: returning")

    def to_dict(self) -> dict[str, Any]:
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

    # Regime confidence modifiers (Phase 1C → Phase 6 adaptive)
    REGIME_MULTIPLIERS = {
        "TREND_BULL": 1.2,
        "TREND_BEAR": 1.2,
        "MEAN_REVERSION": 0.8,
        "CHOP": 0.5,  # Phase 6: micro-scalp allowed, reduced confidence
    }

    # Composite weights — regime-dependent (Phase 2D → Phase 6 enhanced)
    # Trend: follow skew/lead-lag, suppress contrarian funding
    # Mean-reversion: amplify contrarian funding, reduce lead-lag
    # CHOP: funding-heavy contrarian, minimal lead-lag
    REGIME_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
        "TREND_BULL": (0.4, 0.3, 0.05, 0.25),
        "TREND_BEAR": (0.4, 0.3, 0.05, 0.25),
        "MEAN_REVERSION": (0.3, 0.2, 0.4, 0.1),
        "CHOP": (0.2, 0.1, 0.5, 0.2),  # Phase 6: contrarian micro-scalp
    }
    DEFAULT_WEIGHTS = (0.4, 0.3, 0.2, 0.1)  # fallback: skew, lead_lag, funding, oi

    def generate(
        self,
        symbol: str,
        global_vwap: Decimal | None,
        aggregate_skew: float,
        regime: str | None = None,
        lead_lag_delta: float | None = None,
        funding_rate: float | None = None,
        oi_change: float | None = None,
        strategy_score: float | None = None,
    ) -> TradingSignal | None:
        """Generate trading signal from composite multi-signal confidence.

        confidence = regime_mult × (0.4×S_skew + 0.3×S_lead_lag + 0.2×S_funding + 0.1×S_oi)
        Direction: AND-gate — skew direction wins, lead-lag must agree or be neutral.
        """
        logger.debug(f"generate: entering symbol={symbol}")
        if global_vwap is None:
            logger.debug(f"No VWAP for {symbol} — skipping signal")
            return None

        # CHOP regime: Phase 6 — allowed via StrategyRouter micro-scalp gate.
        # No longer hard-blocked. CHOP scoring uses funding-heavy contrarian weights.

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
        # Dead-band: require meaningful OI change (>0.1% relative) to avoid noise
        OI_DEAD_BAND = 0.001  # 0.1% minimum relative OI change
        s_oi = 0.0
        if oi_change is not None and abs(oi_change) > OI_DEAD_BAND:
            s_oi = 1.0 if oi_change > 0 else -1.0

        # --- Direction from skew (primary), lead-lag contradiction kills ---
        LEAD_LAG_HARD_KILL = 0.5  # strong contradiction → no trade

        if s_skew > 0:
            direction = "LONG"
            if s_lead_lag < -LEAD_LAG_HARD_KILL:
                from app.core import metrics as m

                m.signals_skipped.labels(symbol=symbol, reason="lead_lag_kill").inc()
                m.signals_killed_total.labels(stage="signal_gen", reason="lead_lag_kill").inc()
                logger.debug(
                    f"Signal {symbol}: lead-lag KILL LONG (s_ll={s_lead_lag:.2f})"
                )
                return None
        elif s_skew < 0:
            direction = "SHORT"
            if s_lead_lag > LEAD_LAG_HARD_KILL:
                from app.core import metrics as m

                m.signals_skipped.labels(symbol=symbol, reason="lead_lag_kill").inc()
                m.signals_killed_total.labels(stage="signal_gen", reason="lead_lag_kill").inc()
                logger.debug(
                    f"Signal {symbol}: lead-lag KILL SHORT (s_ll={s_lead_lag:.2f})"
                )
                return None
        else:
            direction = "FLAT"

        # In MEAN_REVERSION: direction is contrarian to skew (buy exhaustion, sell euphoria)
        if regime == "MEAN_REVERSION":
            if s_skew < -0.3 and s_funding > 0.2:  # oversold + positive funding → LONG
                direction = "LONG"
            elif (
                s_skew > 0.3 and s_funding < -0.2
            ):  # overbought + negative funding → SHORT
                direction = "SHORT"
            else:
                direction = "FLAT"  # insufficient conviction in MR → no trade

        # --- Confidence calculation ---
        if regime == "CHOP" and strategy_score is not None:
            # CHOP short-circuit: StrategyRouter IS the gate.
            # Skip composite confidence (skew/lead-lag/funding/oi weights
            # don't apply to micro-scalp).  strategy_score already gated at
            # 65 in main.py — only scores ≥ 65 reach here.
            raw_score = strategy_score / 100.0  # for metrics dict
            confidence = min(raw_score, 1.0)
            regime_mult = 1.0
        else:
            # Composite confidence (regime-dependent weights)
            w_skew, w_lead_lag, w_funding, w_oi = self.REGIME_WEIGHTS.get(
                regime, self.DEFAULT_WEIGHTS
            )
            raw_score = (
                w_skew * s_skew
                + w_lead_lag * s_lead_lag
                + w_funding * s_funding
                + w_oi * s_oi
            )

            # Lead-lag soft penalty (hard kill already applied above)
            if s_skew > 0 and s_lead_lag < -0.3:
                raw_score *= 0.7
                logger.debug(
                    f"Signal {symbol}: lead-lag contradicts LONG — penalized 0.7x"
                )
            elif s_skew < 0 and s_lead_lag > 0.3:
                raw_score *= 0.7
                logger.debug(
                    f"Signal {symbol}: lead-lag contradicts SHORT — penalized 0.7x"
                )

            confidence = abs(raw_score)

            # Apply regime multiplier
            regime_mult = self.REGIME_MULTIPLIERS.get(regime, 1.0) if regime else 1.0
            confidence *= regime_mult
            confidence = min(confidence, 1.0)

            # Phase 6: blend StrategyRouter score (0-100) into confidence.
            # Weight: 40% strategy_score normalized + 60% composite confidence.
            if strategy_score is not None and strategy_score > 0:
                normalized_strategy = min(strategy_score / 100.0, 1.0)
                confidence = 0.6 * confidence + 0.4 * normalized_strategy
                confidence = min(confidence, 1.0)

        from app.core import metrics as m

        m.signal_confidence.labels(symbol=symbol).observe(confidence)

        if direction == "FLAT" or confidence < self.min_confidence:
            m.signals_skipped.labels(symbol=symbol, reason="low_confidence").inc()
            kill_reason = "flat_direction" if direction == "FLAT" else "low_confidence"
            m.signals_killed_total.labels(stage="confidence_gate", reason=kill_reason).inc()
            logger.debug(
                f"Signal {symbol}: {direction} (conf={confidence:.2f}) — below threshold"
            )
            return None

        m.signal_confidence_passed_total.labels(regime=regime or "UNKNOWN").inc()

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
                "strategy_score": strategy_score,
            },
        )

        logger.info(
            f"Signal generated: {symbol} {direction} (conf={confidence:.2f}) regime={regime}"
        )
        logger.debug("generate: returning TradingSignal")
        return signal

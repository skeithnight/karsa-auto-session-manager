"""Statistical Learning (Sprint 4).

Provides Trade Fatigue Detection and Confidence Calibration.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.core.decision_context import DecisionContext


@dataclass
class CalibrationResult:
    calibrated_confidence: float
    fatigue_penalty: float
    calibration_multiplier: float


class StatisticalLearning:
    """Applies high-level statistical corrections to raw probabilistic decisions."""

    def __init__(self, trade_memory: object) -> None:
        self.memory = trade_memory

    async def calibrate(self, context: DecisionContext) -> CalibrationResult:
        """Apply Trade Fatigue and Confidence Calibration to a DecisionContext."""
        fatigue_penalty = await self._detect_trade_fatigue(context)
        calibration_multiplier = await self._calculate_confidence_calibration(context)

        # Apply fatigue penalty (subtract) and calibration (multiply)
        raw_conf = context.total_confidence

        # We apply fatigue penalty first, then scale by calibration multiplier
        calibrated = (raw_conf - fatigue_penalty) * calibration_multiplier

        # Floor at 0, cap at 100
        calibrated = max(0.0, min(100.0, calibrated))

        if fatigue_penalty > 0:
            context.add_evidence(
                "trade_fatigue",
                -1.0,
                fatigue_penalty,
                f"Trade fatigue detected (penalty: {fatigue_penalty:.1f})"
            )

        if calibration_multiplier != 1.0:
            # We don't add this as linear evidence since it's a multiplier,
            # but we can record it as informational evidence or adjust total directly.
            context.total_confidence = calibrated

        logger.info(f"StatisticalLearning: {context.symbol} Raw={raw_conf:.1f} Calibrated={calibrated:.1f}")

        return CalibrationResult(
            calibrated_confidence=calibrated,
            fatigue_penalty=fatigue_penalty,
            calibration_multiplier=calibration_multiplier
        )

    async def _detect_trade_fatigue(self, context: DecisionContext) -> float:
        """Detect if the system has over-traded this symbol recently with poor results.

        Returns a penalty value (0.0 to 50.0).
        """
        if not hasattr(self.memory, 'get_recent'):
            return 0.0

        trades = await self.memory.get_recent(context.symbol, count=10) # type: ignore
        if len(trades) < 5:
            return 0.0

        # Count trades in the last N hours
        # In this simplified version, we just look at the last 5 trades.
        # If 4 of the last 5 trades were losses or breakeven, fatigue is high.
        recent_5 = trades[:5]
        losses = sum(1 for t in recent_5 if t.get("pnl_pct", 0.0) <= 0.0)

        if losses >= 4:
            return 25.0
        elif losses == 5:
            return 40.0

        return 0.0

    async def _calculate_confidence_calibration(self, context: DecisionContext) -> float:
        """Calibrate confidence scores based on recent outcome accuracy.

        If high-confidence trades are failing, we apply a penalty multiplier (< 1.0).
        If high-confidence trades are succeeding, we apply a bonus multiplier (> 1.0).
        """
        if not hasattr(self.memory, 'get_recent'):
            return 1.0

        trades = await self.memory.get_recent(context.symbol, count=20) # type: ignore
        if len(trades) < 10:
            return 1.0

        high_conf_trades = [t for t in trades if t.get("confidence", 0.0) > 75.0]
        if not high_conf_trades:
            return 1.0

        high_conf_wins = sum(1 for t in high_conf_trades if t.get("pnl_pct", 0.0) > 0.0)
        high_conf_win_rate = high_conf_wins / len(high_conf_trades)

        if high_conf_win_rate < 0.3:
            # Overconfident: scale down
            return 0.7
        elif high_conf_win_rate > 0.6:
            # Underconfident: scale up
            return 1.2

        return 1.0

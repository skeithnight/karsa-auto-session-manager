"""Fractional Kelly Criterion Position Sizing.

Calculates dynamic risk % per trade based on historical performance (win rate & payoff ratio).
Uses Fractional Kelly (25%) to prevent over-betting and minimize drawdown.
"""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

KELLY_FRACTION = Decimal("0.25")  # 25% of full Kelly
MIN_TRADES = 15  # Minimum sample size
MIN_RISK_PCT = Decimal("0.005")  # 0.5% floor
MAX_RISK_PCT = Decimal("0.020")  # 2.0% ceiling


class KellySizer:
    """Calculates optimal position size using Fractional Kelly Criterion."""

    def __init__(self, fraction: Decimal = KELLY_FRACTION) -> None:
        self.fraction = fraction

    def calculate_risk_pct(
        self,
        wins: int,
        losses: int,
        avg_win_usd: float,
        avg_loss_usd: float,
        fallback_score: float = 75.0,
    ) -> Decimal:
        """Calculate Fractional Kelly risk percentage.

        Formula:
            Full Kelly K = W - (1 - W) / R
            where W = win_rate, R = avg_win / avg_loss
            Fractional Kelly = K * fraction

        Args:
            wins: Count of winning trades.
            losses: Count of losing trades.
            avg_win_usd: Average profit on winning trades in USD.
            avg_loss_usd: Average loss on losing trades in USD (positive number).
            fallback_score: Strategy score for tiered fallback if sample size is small.

        Returns:
            Decimal risk percentage (e.g., Decimal("0.012") for 1.2%).
        """
        total = wins + losses
        if total < MIN_TRADES or avg_loss_usd <= 0 or avg_win_usd <= 0:
            # Fallback to confidence-tiered sizing
            if fallback_score >= 90:
                return Decimal("0.015")
            if fallback_score >= 80:
                return Decimal("0.010")
            return Decimal("0.005")

        win_rate = Decimal(str(wins / total))
        loss_rate = Decimal("1.0") - win_rate

        payoff_ratio = Decimal(str(avg_win_usd)) / Decimal(str(avg_loss_usd))

        full_kelly = win_rate - (loss_rate / payoff_ratio)

        if full_kelly <= Decimal("0"):
            logger.info("KellySizer: Negative Kelly (%.4f), defaulting to MIN_RISK_PCT", float(full_kelly))
            return MIN_RISK_PCT

        frac_kelly = (full_kelly * self.fraction).quantize(Decimal("0.0001"))
        bounded_risk = max(MIN_RISK_PCT, min(MAX_RISK_PCT, frac_kelly))

        logger.info(
            "KellySizer: W=%.2f R=%.2f FullKelly=%.4f FracKelly=%.4f -> FinalRisk=%.4f",
            float(win_rate),
            float(payoff_ratio),
            float(full_kelly),
            float(frac_kelly),
            float(bounded_risk),
        )

        return bounded_risk

"""Fractional Kelly Criterion Position Sizing.

Calculates dynamic risk % per trade based on historical performance (win rate & payoff ratio).
Uses Fractional Kelly (25%) to prevent over-betting and minimize drawdown.

Sprint 1: Drawdown-Adaptive Sizing (Anti-Martingale)
  Adjusts Kelly fraction based on account drawdown state:
  - dd > 10% → 0.25x (severe drawdown, preserve capital)
  - dd > 5%  → 0.50x (moderate drawdown, reduce exposure)
  - dd < 2%  → 1.25x (near equity peak, "house money" mode)
"""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

KELLY_FRACTION = Decimal("0.25")  # 25% of full Kelly
MIN_TRADES = 15  # Minimum sample size
MIN_RISK_PCT = Decimal("0.005")  # 0.5% floor
MAX_RISK_PCT = Decimal("0.020")  # 2.0% ceiling

# Sprint 1: Drawdown-Adaptive thresholds and multipliers
DD_SEVERE_THRESHOLD = Decimal("0.10")   # >10% drawdown
DD_MODERATE_THRESHOLD = Decimal("0.05")  # >5% drawdown
DD_NEAR_PEAK_THRESHOLD = Decimal("0.02") # <2% drawdown
DD_SEVERE_MULT = Decimal("0.25")
DD_MODERATE_MULT = Decimal("0.50")
DD_NEAR_PEAK_MULT = Decimal("1.25")


class KellySizer:
    """Calculates optimal position size using Fractional Kelly Criterion."""

    def __init__(
        self,
        fraction: Decimal = KELLY_FRACTION,
        dd_severe_threshold: Decimal = DD_SEVERE_THRESHOLD,
        dd_moderate_threshold: Decimal = DD_MODERATE_THRESHOLD,
        dd_near_peak_threshold: Decimal = DD_NEAR_PEAK_THRESHOLD,
        dd_severe_mult: Decimal = DD_SEVERE_MULT,
        dd_moderate_mult: Decimal = DD_MODERATE_MULT,
        dd_near_peak_mult: Decimal = DD_NEAR_PEAK_MULT,
    ) -> None:
        self.fraction = fraction
        self.dd_severe_threshold = dd_severe_threshold
        self.dd_moderate_threshold = dd_moderate_threshold
        self.dd_near_peak_threshold = dd_near_peak_threshold
        self.dd_severe_mult = dd_severe_mult
        self.dd_moderate_mult = dd_moderate_mult
        self.dd_near_peak_mult = dd_near_peak_mult

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

    def apply_drawdown_adaptive(
        self,
        base_risk_pct: Decimal,
        current_equity: Decimal,
        equity_peak: Decimal,
    ) -> Decimal:
        """Sprint 1: Apply Anti-Martingale drawdown adjustment to Kelly risk.

        Reduces position size during drawdowns, increases near equity highs.
        Prevents the "death spiral" where losing streaks compound on shrinking equity.

        Args:
            base_risk_pct: Risk % from calculate_risk_pct().
            current_equity: Current account equity (wallet balance).
            equity_peak: Highest equity seen (stored in Redis global:state:equity_peak).

        Returns:
            Adjusted risk percentage with drawdown multiplier applied.
        """
        if equity_peak <= 0 or current_equity <= 0:
            return base_risk_pct

        drawdown = (equity_peak - current_equity) / equity_peak

        if drawdown > self.dd_severe_threshold:
            # SEVERE drawdown: quarter size to preserve capital
            multiplier = self.dd_severe_mult
            logger.warning(
                "KellySizer DD: SEVERE drawdown %.1f%% — applying %.0fx multiplier (risk %.4f → %.4f)",
                float(drawdown * 100), float(multiplier),
                float(base_risk_pct), float(base_risk_pct * multiplier),
            )
        elif drawdown > self.dd_moderate_threshold:
            # MODERATE drawdown: half size
            multiplier = self.dd_moderate_mult
            logger.info(
                "KellySizer DD: MODERATE drawdown %.1f%% — applying %.0fx multiplier",
                float(drawdown * 100), float(multiplier),
            )
        elif drawdown < self.dd_near_peak_threshold:
            # NEAR PEAK: "house money" mode — slightly increase size
            multiplier = self.dd_near_peak_mult
            logger.info(
                "KellySizer DD: NEAR PEAK (dd=%.1f%%) — applying %.2fx multiplier (house money)",
                float(drawdown * 100), float(multiplier),
            )
        else:
            # Normal range: no adjustment
            return base_risk_pct

        adjusted = (base_risk_pct * multiplier).quantize(Decimal("0.0001"))
        return max(MIN_RISK_PCT, min(MAX_RISK_PCT, adjusted))

    # ─── Sprint 2: Half-Kelly with Uncertainty ────────────────────────

    def apply_uncertainty_adjustment(
        self,
        base_risk_pct: Decimal,
        wins: int,
        losses: int,
        win_rate_history: list[float] | None = None,
    ) -> Decimal:
        """Sprint 2: Reduce Kelly when uncertainty is high.

        When sample size is small or win rate variance is high, the Kelly estimate
        is unreliable. This method reduces position size to account for estimation
        uncertainty — preventing over-betting on noisy statistics.

        Args:
            base_risk_pct: Risk % from calculate_risk_pct().
            wins: Count of winning trades.
            losses: Count of losing trades.
            win_rate_history: Optional list of historical win rates (for variance calc).

        Returns:
            Adjusted risk percentage with uncertainty factor applied.
        """
        try:
            from app.core.config import get_settings
            settings = get_settings()

            uncertainty_factor = Decimal(settings.half_kelly_uncertainty_factor)
            min_trades = settings.half_kelly_min_trades_for_confidence
            variance_threshold = Decimal(settings.half_kelly_variance_threshold)

            total = wins + losses

            # Small sample size: high uncertainty
            if total < min_trades:
                # Scale uncertainty by sample size: fewer trades = more uncertainty
                sample_confidence = Decimal(str(total)) / Decimal(str(min_trades))
                adjustment = Decimal("0.5") + (sample_confidence * Decimal("0.5"))
                adjusted = (base_risk_pct * adjustment).quantize(Decimal("0.0001"))
                logger.info(
                    "KellySizer UNCERTAINTY: small sample (n=%d < %d) — risk %.4f → %.4f",
                    total, min_trades, float(base_risk_pct), float(adjusted),
                )
                return max(MIN_RISK_PCT, min(MAX_RISK_PCT, adjusted))

            # High win rate variance: unstable edge
            if win_rate_history and len(win_rate_history) >= 10:
                import statistics
                variance = Decimal(str(statistics.variance(win_rate_history[-20:])))
                if variance > variance_threshold:
                    adjusted = (base_risk_pct * uncertainty_factor).quantize(Decimal("0.0001"))
                    logger.info(
                        "KellySizer UNCERTAINTY: high variance (%.4f > %.4f) — risk %.4f → %.4f",
                        float(variance), float(variance_threshold),
                        float(base_risk_pct), float(adjusted),
                    )
                    return max(MIN_RISK_PCT, min(MAX_RISK_PCT, adjusted))

            # Low uncertainty: full Kelly
            return base_risk_pct

        except Exception as e:
            logger.debug(f"Uncertainty adjustment failed: {e}")
            return base_risk_pct

    # ─── Sprint 3: GARCH Volatility Targeting ────────────────────────

    def apply_volatility_targeting(
        self,
        base_risk_pct: Decimal,
        forecasted_vol: float | None = None,
        historical_vol: float | None = None,
    ) -> Decimal:
        """Sprint 3: Adjust Kelly based on GARCH volatility forecast.

        If forecasted vol is significantly higher than historical average,
        reduce position size to account for incoming volatility. If lower,
        slightly increase size.

        Args:
            base_risk_pct: Risk % from previous sizing stages.
            forecasted_vol: GARCH forecasted annualized vol (or None to skip).
            historical_vol: Historical average annualized vol (or None to skip).

        Returns:
            Adjusted risk percentage with volatility targeting applied.
        """
        try:
            if forecasted_vol is None or historical_vol is None or historical_vol <= 0:
                return base_risk_pct

            from app.risk.garch_volatility_forecaster import GARCHVolatilityForecaster
            forecaster = GARCHVolatilityForecaster()
            multiplier = forecaster.get_volatility_multiplier(forecasted_vol, historical_vol)

            if multiplier == 1.0:
                return base_risk_pct

            adjusted = (base_risk_pct * Decimal(str(multiplier))).quantize(Decimal("0.0001"))
            logger.info(
                "KellySizer GARCH: forecasted_vol=%.4f historical_vol=%.4f ratio=%.2f → "
                "multiplier=%.2f risk %.4f → %.4f",
                forecasted_vol, historical_vol,
                forecasted_vol / historical_vol,
                multiplier, float(base_risk_pct), float(adjusted),
            )
            return max(MIN_RISK_PCT, min(MAX_RISK_PCT, adjusted))

        except Exception as e:
            logger.debug(f"Volatility targeting failed: {e}")
            return base_risk_pct

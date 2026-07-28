"""Portfolio Evaluator (v3.5 Prototype).

Evaluates portfolio state and its implications for position management.

This evaluator answers: "Is the portfolio in a good state to take new positions,
or should we be defensive?"

Architecture:
    Evidence → Feature Graph → Independent Evaluators → Fusion → Recommendation

Usage:
    evaluator = PortfolioEvaluator()
    result = evaluator.evaluate(portfolio_snapshot)

    # Result contains:
    # - direction: 1.0 (favorable) or -1.0 (unfavorable)
    # - weight: strength of the signal (0-100)
    # - confidence: how sure we are (0-1)
    # - reason: human-readable explanation
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.alpha.evaluators.trend_evaluator import EvaluatorResult
from app.core.portfolio_snapshot import PortfolioSnapshot


class PortfolioEvaluator:
    """Evaluates portfolio state for position management.

    Uses multiple portfolio metrics:
    - Drawdown level
    - Number of open positions
    - Sector concentration
    - Correlation between positions
    - Cash reserve ratio

    Each component contributes to the final score independently.
    """

    # Weight for each component
    WEIGHTS = {
        "drawdown": 30.0,
        "position_count": 20.0,
        "sector_concentration": 20.0,
        "correlation": 15.0,
        "cash_reserve": 15.0,
    }

    def evaluate(
        self,
        portfolio: PortfolioSnapshot,
    ) -> EvaluatorResult:
        """Evaluate portfolio state.

        Args:
            portfolio: Current portfolio snapshot

        Returns:
            EvaluatorResult with direction, weight, confidence, reason
        """
        contributions: dict[str, float] = {}

        # 1. Drawdown
        drawdown_score = self._evaluate_drawdown(portfolio)
        contributions["drawdown"] = drawdown_score

        # 2. Position Count
        position_score = self._evaluate_position_count(portfolio)
        contributions["position_count"] = position_score

        # 3. Sector Concentration
        sector_score = self._evaluate_sector_concentration(portfolio)
        contributions["sector_concentration"] = sector_score

        # 4. Correlation
        corr_score = self._evaluate_correlation(portfolio)
        contributions["correlation"] = corr_score

        # 5. Cash Reserve
        cash_score = self._evaluate_cash_reserve(portfolio)
        contributions["cash_reserve"] = cash_score

        # Compute weighted total
        total_score = 0.0
        total_weight = 0.0
        for component, score in contributions.items():
            weight = self.WEIGHTS.get(component, 10.0)
            total_score += score * weight
            total_weight += weight

        if total_weight > 0:
            normalized_score = total_score / total_weight
        else:
            normalized_score = 0.0

        # Extract direction and magnitude
        direction = 1.0 if normalized_score > 0 else (-1.0 if normalized_score < 0 else 0.0)
        weight = min(abs(normalized_score), 100.0)

        # Compute confidence
        confidence = self._compute_confidence(contributions)

        # Generate reason
        reason = self._generate_reason(contributions, direction, weight)

        return EvaluatorResult(
            direction=direction,
            weight=weight,
            confidence=confidence,
            reason=reason,
            contributions=contributions,
            metadata={
                "equity": float(portfolio.equity),
                "drawdown": float(portfolio.drawdown),
                "position_count": len(portfolio.positions),
            },
        )

    def _evaluate_drawdown(self, portfolio: PortfolioSnapshot) -> float:
        """Evaluate drawdown level.

        Low drawdown = favorable for new positions
        High drawdown = defensive mode

        Returns:
            Score from -100 to +100
        """
        drawdown = float(portfolio.drawdown)

        # Drawdown scoring (positive = favorable)
        if drawdown < 0.02:  # < 2%
            return 50.0  # Very healthy
        elif drawdown < 0.05:  # < 5%
            return 30.0  # Healthy
        elif drawdown < 0.10:  # < 10%
            return 0.0  # Normal
        elif drawdown < 0.15:  # < 15%
            return -30.0  # Elevated
        elif drawdown < 0.20:  # < 20%
            return -60.0  # High
        else:  # > 20%
            return -80.0  # Critical

    def _evaluate_position_count(self, portfolio: PortfolioSnapshot) -> float:
        """Evaluate number of open positions.

        Few positions = room for more
        Many positions = defensive

        Returns:
            Score from -100 to +100
        """
        count = len(portfolio.positions)

        # Position count scoring
        if count == 0:
            return 40.0  # Full capacity
        elif count <= 2:
            return 30.0  # Good capacity
        elif count <= 4:
            return 10.0  # Moderate
        elif count <= 6:
            return -20.0  # Getting full
        elif count <= 8:
            return -50.0  # Nearly full
        else:
            return -80.0  # Overextended

    def _evaluate_sector_concentration(self, portfolio: PortfolioSnapshot) -> float:
        """Evaluate sector concentration.

        Diversified = favorable
        Concentrated = risky

        Returns:
            Score from -100 to +100
        """
        if not portfolio.sector_exposure:
            return 20.0  # No exposure data = neutral

        # Calculate Herfindahl-Hirschman Index (HHI)
        total_exposure = sum(abs(v) for v in portfolio.sector_exposure.values())
        if total_exposure == 0:
            return 20.0

        hhi = sum((abs(v) / total_exposure) ** 2 for v in portfolio.sector_exposure.values())

        # HHI scoring (lower = more diversified)
        if hhi < 0.15:  # Very diversified
            return 40.0
        elif hhi < 0.25:  # Diversified
            return 20.0
        elif hhi < 0.35:  # Moderate
            return 0.0
        elif hhi < 0.50:  # Concentrated
            return -30.0
        else:  # Highly concentrated
            return -60.0

    def _evaluate_correlation(self, portfolio: PortfolioSnapshot) -> float:
        """Evaluate correlation between positions.

        Low correlation = favorable (diversification)
        High correlation = risky (concentrated bets)

        Returns:
            Score from -100 to +100
        """
        if not portfolio.symbol_correlation:
            return 10.0  # No correlation data = neutral

        # Average correlation
        avg_corr = sum(portfolio.symbol_correlation.values()) / len(portfolio.symbol_correlation)

        # Correlation scoring (lower = better)
        if avg_corr < 0.3:  # Low correlation
            return 40.0
        elif avg_corr < 0.5:  # Moderate
            return 20.0
        elif avg_corr < 0.7:  # High
            return -20.0
        else:  # Very high
            return -50.0

    def _evaluate_cash_reserve(self, portfolio: PortfolioSnapshot) -> float:
        """Evaluate cash reserve ratio.

        High cash = favorable for new positions
        Low cash = defensive

        Returns:
            Score from -100 to +100
        """
        if portfolio.equity == 0:
            return 0.0

        cash_ratio = float(portfolio.cash / portfolio.equity)

        # Cash ratio scoring
        if cash_ratio > 0.5:  # > 50% cash
            return 50.0  # Very healthy
        elif cash_ratio > 0.3:  # > 30%
            return 30.0  # Healthy
        elif cash_ratio > 0.2:  # > 20%
            return 10.0  # Normal
        elif cash_ratio > 0.1:  # > 10%
            return -20.0  # Low
        elif cash_ratio > 0.05:  # > 5%
            return -50.0  # Very low
        else:  # < 5%
            return -80.0  # Critical

    def _compute_confidence(self, contributions: dict[str, float]) -> float:
        """Compute confidence based on component agreement."""
        if not contributions:
            return 0.0

        # Count direction agreement
        positive = sum(1 for v in contributions.values() if v > 0)
        negative = sum(1 for v in contributions.values() if v < 0)
        total = len(contributions)

        # Agreement ratio
        max_agreement = max(positive, negative)
        agreement_ratio = max_agreement / total if total > 0 else 0.0

        # Magnitude consistency
        values = [abs(v) for v in contributions.values() if v != 0]
        if values:
            avg_magnitude = sum(values) / len(values)
            magnitude_factor = min(avg_magnitude / 30.0, 1.0)
        else:
            magnitude_factor = 0.0

        # Combined confidence
        confidence = (agreement_ratio * 0.5) + (magnitude_factor * 0.5)

        return min(confidence, 1.0)

    def _generate_reason(
        self,
        contributions: dict[str, float],
        direction: float,
        weight: float,
    ) -> str:
        """Generate human-readable reason for the evaluation."""
        if weight < 10:
            return "Neutral portfolio state"

        # Find top contributors
        sorted_contribs = sorted(
            contributions.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )

        state_str = "favorable" if direction > 0 else "unfavorable"

        # Build reason from top contributors
        reasons = []
        for name, value in sorted_contribs[:2]:
            if abs(value) > 10:
                if value > 0:
                    reasons.append(f"{name} healthy")
                else:
                    reasons.append(f"{name} stressed")

        if not reasons:
            return f"Moderate portfolio state"

        return f"{state_str.title()}: {', '.join(reasons)}"

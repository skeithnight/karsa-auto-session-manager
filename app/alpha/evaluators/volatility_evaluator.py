"""Volatility Evaluator (v3.5 Prototype).

Evaluates volatility regime and its implications for position management.

This evaluator answers: "Is the market volatile enough for trend trades,
or should we expect choppy/ranging behavior?"

Architecture:
    Evidence → Feature Graph → Independent Evaluators → Fusion → Recommendation

Usage:
    evaluator = VolatilityEvaluator()
    result = evaluator.evaluate(feature_vector, market_snapshot)

    # Result contains:
    # - direction: 1.0 (favorable volatility) or -1.0 (unfavorable)
    # - weight: strength of the signal (0-100)
    # - confidence: how sure we are (0-1)
    # - reason: human-readable explanation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.alpha.evaluators.trend_evaluator import EvaluatorResult
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


class VolatilityEvaluator:
    """Evaluates volatility regime for position management.

    Uses multiple volatility indicators:
    - ATR percentile (current vs historical)
    - ATR% (normalized volatility)
    - Hurst exponent (trend vs mean-reversion)
    - Price range (high-low spread)

    Each component contributes to the final score independently.
    """

    # Weight for each component
    WEIGHTS = {
        "atr_percentile": 30.0,
        "atr_pct": 25.0,
        "hurst": 25.0,
        "price_range": 20.0,
    }

    def evaluate(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> EvaluatorResult:
        """Evaluate volatility from feature vector.

        Args:
            features: Extracted feature vector
            snapshot: Optional market snapshot for additional data

        Returns:
            EvaluatorResult with direction, weight, confidence, reason
        """
        contributions: dict[str, float] = {}

        # 1. ATR Percentile
        atr_pct_score = self._evaluate_atr_percentile(features)
        contributions["atr_percentile"] = atr_pct_score

        # 2. ATR% (Normalized Volatility)
        atr_norm_score = self._evaluate_atr_pct(features)
        contributions["atr_pct"] = atr_norm_score

        # 3. Hurst Exponent
        hurst_score = self._evaluate_hurst(features)
        contributions["hurst"] = hurst_score

        # 4. Price Range
        range_score = self._evaluate_price_range(features, snapshot)
        contributions["price_range"] = range_score

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
                "atr": features.atr,
                "atr_pct": features.atr_pct,
                "hurst": features.hurst,
            },
        )

    def _evaluate_atr_percentile(self, features: FeatureVector) -> float:
        """Evaluate ATR percentile (current vs historical).

        High percentile = elevated volatility = good for trend trades
        Low percentile = compressed volatility = expect breakout

        Returns:
            Score from -100 to +100
        """
        atr = features.atr
        if atr is None:
            return 0.0

        # Use ATR as proxy for percentile (simplified)
        # In production, would compare to historical ATR distribution
        atr_f = float(atr)

        # Thresholds based on typical BTC ATR
        if atr_f > 2000:  # Very high volatility
            return 60.0
        elif atr_f > 1000:  # High volatility
            return 40.0
        elif atr_f > 500:  # Normal volatility
            return 20.0
        elif atr_f > 200:  # Low volatility
            return -20.0
        else:  # Very low volatility
            return -40.0

    def _evaluate_atr_pct(self, features: FeatureVector) -> float:
        """Evaluate normalized volatility (ATR%).

        ATR% = ATR / Close * 100

        High ATR% = volatile market
        Low ATR% = calm market

        Returns:
            Score from -100 to +100
        """
        atr_pct = features.atr_pct
        if atr_pct is None:
            return 0.0

        atr_pct_f = float(atr_pct)

        # Thresholds for ATR%
        if atr_pct_f > 5.0:  # Very high
            return 60.0
        elif atr_pct_f > 3.0:  # High
            return 40.0
        elif atr_pct_f > 2.0:  # Normal
            return 20.0
        elif atr_pct_f > 1.0:  # Low
            return -20.0
        else:  # Very low
            return -40.0

    def _evaluate_hurst(self, features: FeatureVector) -> float:
        """Evaluate Hurst exponent.

        H > 0.5 = trending (favorable for trend trades)
        H < 0.5 = mean-reverting (unfavorable for trend trades)
        H ≈ 0.5 = random walk

        Returns:
            Score from -100 to +100
        """
        hurst = features.hurst
        if hurst is None:
            return 0.0

        hurst_f = float(hurst)

        # Hurst scoring
        if hurst_f > 0.7:  # Strong trending
            return 60.0
        elif hurst_f > 0.6:  # Moderate trending
            return 40.0
        elif hurst_f > 0.5:  # Slight trending
            return 20.0
        elif hurst_f > 0.4:  # Random walk
            return 0.0
        elif hurst_f > 0.3:  # Slight mean-reversion
            return -20.0
        else:  # Strong mean-reversion
            return -40.0

    def _evaluate_price_range(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> float:
        """Evaluate price range (high-low spread).

        Wide range = volatile
        Narrow range = calm

        Returns:
            Score from -100 to +100
        """
        if snapshot is None:
            return 0.0

        try:
            highs = snapshot.get_high_prices()
            lows = snapshot.get_low_prices()
            closes = snapshot.get_close_prices()

            if len(highs) < 5 or len(closes) < 5:
                return 0.0

            # Recent range (last 5 bars)
            recent_high = float(np.max(highs[-5:]))
            recent_low = float(np.min(lows[-5:]))
            current_price = float(closes[-1])

            if current_price == 0:
                return 0.0

            range_pct = (recent_high - recent_low) / current_price * 100

            # Scoring
            if range_pct > 10.0:  # Very wide
                return 50.0
            elif range_pct > 5.0:  # Wide
                return 30.0
            elif range_pct > 2.0:  # Normal
                return 10.0
            elif range_pct > 1.0:  # Narrow
                return -20.0
            else:  # Very narrow
                return -40.0

        except Exception:
            return 0.0

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
        confidence = (agreement_ratio * 0.6) + (magnitude_factor * 0.4)

        return min(confidence, 1.0)

    def _generate_reason(
        self,
        contributions: dict[str, float],
        direction: float,
        weight: float,
    ) -> str:
        """Generate human-readable reason for the evaluation."""
        if weight < 10:
            return "Neutral volatility signal"

        # Find top contributors
        sorted_contribs = sorted(
            contributions.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )

        vol_str = "high" if direction > 0 else "low"

        # Build reason from top contributors
        reasons = []
        for name, value in sorted_contribs[:2]:
            if abs(value) > 10:
                if value > 0:
                    reasons.append(f"{name} elevated")
                else:
                    reasons.append(f"{name} compressed")

        if not reasons:
            return f"Moderate volatility"

        return f"{vol_str.title()} volatility: {', '.join(reasons)}"

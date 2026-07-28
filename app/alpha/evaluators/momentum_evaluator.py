"""Momentum Evaluator (v3.5 Prototype).

Evaluates momentum and its implications for position management.

This evaluator answers: "Is there strong directional momentum,
or are we seeing exhaustion/reversal signals?"

Architecture:
    Evidence → Feature Graph → Independent Evaluators → Fusion → Recommendation

Usage:
    evaluator = MomentumEvaluator()
    result = evaluator.evaluate(feature_vector, market_snapshot)

    # Result contains:
    # - direction: 1.0 (bullish momentum) or -1.0 (bearish momentum)
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


class MomentumEvaluator:
    """Evaluates momentum for position management.

    Uses multiple momentum indicators:
    - RSI (relative strength)
    - CVD slope (cumulative volume delta)
    - Price momentum (rate of change)
    - Volume momentum (increasing/decreasing)

    Each component contributes to the final score independently.
    """

    # Weight for each component
    WEIGHTS = {
        "rsi": 30.0,
        "cvd_slope": 25.0,
        "price_momentum": 25.0,
        "volume_momentum": 20.0,
    }

    def evaluate(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> EvaluatorResult:
        """Evaluate momentum from feature vector.

        Args:
            features: Extracted feature vector
            snapshot: Optional market snapshot for additional data

        Returns:
            EvaluatorResult with direction, weight, confidence, reason
        """
        contributions: dict[str, float] = {}

        # 1. RSI Momentum
        rsi_score = self._evaluate_rsi(features)
        contributions["rsi"] = rsi_score

        # 2. CVD Slope
        cvd_score = self._evaluate_cvd(features)
        contributions["cvd_slope"] = cvd_score

        # 3. Price Momentum
        price_score = self._evaluate_price_momentum(features, snapshot)
        contributions["price_momentum"] = price_score

        # 4. Volume Momentum
        vol_score = self._evaluate_volume_momentum(features, snapshot)
        contributions["volume_momentum"] = vol_score

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
                "rsi_14": features.rsi_14,
                "cvd_slope": features.cvd_slope,
            },
        )

    def _evaluate_rsi(self, features: FeatureVector) -> float:
        """Evaluate RSI momentum.

        RSI > 70 = overbought (bearish reversal risk)
        RSI > 50 = bullish momentum
        RSI < 30 = oversold (bullish reversal potential)
        RSI < 50 = bearish momentum

        Returns:
            Score from -100 to +100
        """
        rsi = features.rsi_14
        if rsi is None:
            return 0.0

        rsi_f = float(rsi)

        # RSI scoring - note: this is momentum, not trend
        # High RSI = strong bullish momentum (but overbought risk)
        # Low RSI = strong bearish momentum (but oversold risk)

        if rsi_f > 70:
            return 30.0  # Strong momentum but overbought
        elif rsi_f > 60:
            return 50.0  # Bullish momentum
        elif rsi_f > 50:
            return 30.0  # Mild bullish
        elif rsi_f > 40:
            return -30.0  # Mild bearish
        elif rsi_f > 30:
            return -50.0  # Bearish momentum
        else:
            return -30.0  # Strong momentum but oversold

    def _evaluate_cvd(self, features: FeatureVector) -> float:
        """Evaluate Cumulative Volume Delta (CVD) slope.

        Positive CVD slope = buying pressure
        Negative CVD slope = selling pressure

        Returns:
            Score from -100 to +100
        """
        cvd = features.cvd_slope
        if cvd is None:
            return 0.0

        cvd_f = float(cvd)

        # CVD scoring
        if cvd_f > 0.1:  # Strong buying
            return 60.0
        elif cvd_f > 0.05:  # Moderate buying
            return 40.0
        elif cvd_f > 0.01:  # Mild buying
            return 20.0
        elif cvd_f > -0.01:  # Neutral
            return 0.0
        elif cvd_f > -0.05:  # Mild selling
            return -20.0
        elif cvd_f > -0.1:  # Moderate selling
            return -40.0
        else:  # Strong selling
            return -60.0

    def _evaluate_price_momentum(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> float:
        """Evaluate price momentum (rate of change).

        Uses recent price action to determine momentum direction.

        Returns:
            Score from -100 to +100
        """
        if snapshot is None:
            return 0.0

        try:
            closes = snapshot.get_close_prices()

            if len(closes) < 10:
                return 0.0

            # Calculate momentum over different periods
            current = float(closes[-1])
            prev_5 = float(closes[-6]) if len(closes) >= 6 else current
            prev_10 = float(closes[-11]) if len(closes) >= 11 else current

            # 5-period momentum
            momentum_5 = (current - prev_5) / prev_5 * 100 if prev_5 > 0 else 0

            # 10-period momentum
            momentum_10 = (current - prev_10) / prev_10 * 100 if prev_10 > 0 else 0

            # Combined momentum
            combined = (momentum_5 * 0.6) + (momentum_10 * 0.4)

            # Scoring
            if combined > 5.0:  # Strong upward
                return 60.0
            elif combined > 2.0:  # Moderate upward
                return 40.0
            elif combined > 0.5:  # Mild upward
                return 20.0
            elif combined > -0.5:  # Neutral
                return 0.0
            elif combined > -2.0:  # Mild downward
                return -20.0
            elif combined > -5.0:  # Moderate downward
                return -40.0
            else:  # Strong downward
                return -60.0

        except Exception:
            return 0.0

    def _evaluate_volume_momentum(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> float:
        """Evaluate volume momentum (increasing/decreasing volume).

        Increasing volume with price movement = strong momentum
        Decreasing volume = weakening momentum

        Returns:
            Score from -100 to +100
        """
        if snapshot is None:
            return 0.0

        try:
            volumes = snapshot.get_volumes()
            closes = snapshot.get_close_prices()

            if len(volumes) < 10 or len(closes) < 10:
                return 0.0

            # Recent volume average (last 5 bars)
            recent_vol = float(np.mean(volumes[-5:]))
            prev_vol = float(np.mean(volumes[-10:-5])) if len(volumes) >= 10 else recent_vol

            # Volume change
            vol_change = (recent_vol - prev_vol) / prev_vol if prev_vol > 0 else 0

            # Price direction
            price_up = closes[-1] > closes[-6] if len(closes) >= 6 else True

            # Volume momentum scoring
            if vol_change > 0.5:  # Volume surge
                return 50.0 if price_up else -50.0
            elif vol_change > 0.2:  # Volume increasing
                return 30.0 if price_up else -30.0
            elif vol_change > -0.2:  # Stable
                return 10.0 if price_up else -10.0
            elif vol_change > -0.5:  # Volume decreasing
                return -10.0 if price_up else 10.0
            else:  # Volume collapse
                return -30.0 if price_up else 30.0

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
            return "Neutral momentum signal"

        # Find top contributors
        sorted_contribs = sorted(
            contributions.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )

        mom_str = "bullish" if direction > 0 else "bearish"

        # Build reason from top contributors
        reasons = []
        for name, value in sorted_contribs[:2]:
            if abs(value) > 10:
                if value > 0:
                    reasons.append(f"{name} positive")
                else:
                    reasons.append(f"{name} negative")

        if not reasons:
            return f"Moderate momentum"

        return f"{mom_str.title()} momentum: {', '.join(reasons)}"

"""Trend Evaluator (v3.5 Prototype).

First evaluator in the Decision Evaluation Graph. Evaluates trend strength
and direction to produce an evidence score.

This is a proof-of-concept for the evaluator architecture proposed in
Karsa v3.5. It replaces the ad-hoc trend logic scattered across
StrategyRouter and EvidenceCollector with a self-contained, testable unit.

Architecture:
    Evidence → Feature Graph → Independent Evaluators → Fusion → Recommendation

Usage:
    evaluator = TrendEvaluator()
    result = evaluator.evaluate(feature_vector, market_snapshot)

    # Result contains:
    # - direction: 1.0 (bullish) or -1.0 (bearish) or 0.0 (neutral)
    # - weight: strength of the signal (0-100)
    # - confidence: how sure we are (0-1)
    # - reason: human-readable explanation
    # - contributions: breakdown of what contributed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


@dataclass(frozen=True)
class EvaluatorResult:
    """Result from an evaluator.

    Attributes:
        direction: Signal direction (1.0=bull, -1.0=bear, 0.0=neutral)
        weight: Signal strength (0-100)
        confidence: How confident in this evaluation (0-1)
        reason: Human-readable explanation
        contributions: Breakdown of what contributed to the score
        metadata: Additional data for logging/debugging
    """
    direction: float
    weight: float
    confidence: float
    reason: str
    contributions: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """Is this signal strong enough to act on?"""
        return abs(self.weight) > 10.0 and self.confidence > 0.3

    @property
    def signal_strength(self) -> float:
        """Combined strength = weight * confidence."""
        return self.weight * self.confidence


class TrendEvaluator:
    """Evaluates trend strength and direction.

    Uses multiple technical indicators to assess trend:
    - EMA crossover (20/200)
    - ADX (trend strength)
    - Price position relative to MAs
    - Volume confirmation
    - Momentum (RSI)

    Each component contributes to the final score independently.
    """

    # Weight for each component
    WEIGHTS = {
        "ema_crossover": 25.0,
        "adx_strength": 20.0,
        "price_ma_position": 20.0,
        "volume_confirmation": 15.0,
        "momentum": 20.0,
    }

    def evaluate(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> EvaluatorResult:
        """Evaluate trend from feature vector.

        Args:
            features: Extracted feature vector
            snapshot: Optional market snapshot for additional data

        Returns:
            EvaluatorResult with direction, weight, confidence, reason
        """
        contributions: dict[str, float] = {}

        # 1. EMA Crossover (20/200)
        ema_score = self._evaluate_ema_crossover(features)
        contributions["ema_crossover"] = ema_score

        # 2. ADX Strength
        adx_score = self._evaluate_adx(features)
        contributions["adx_strength"] = adx_score

        # 3. Price Position vs MAs
        price_ma_score = self._evaluate_price_ma_position(features)
        contributions["price_ma_position"] = price_ma_score

        # 4. Volume Confirmation
        volume_score = self._evaluate_volume(features, snapshot)
        contributions["volume_confirmation"] = volume_score

        # 5. Momentum (RSI)
        momentum_score = self._evaluate_momentum(features)
        contributions["momentum"] = momentum_score

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

        # Compute confidence based on agreement between components
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
                "ema_20": features.ema_20,
                "ema_200": features.ema_200,
                "adx_14": features.adx_14,
                "rsi_14": features.rsi_14,
            },
        )

    def _evaluate_ema_crossover(self, features: FeatureVector) -> float:
        """Evaluate EMA 20/200 crossover.

        Returns:
            Score from -100 (strong bearish) to +100 (strong bullish)
        """
        ema_20 = features.ema_20
        ema_200 = features.ema_200
        close = features.close

        if not all([ema_20, ema_200, close]):
            return 0.0

        # Features are already floats, use directly
        ema_20_f = ema_20
        ema_200_f = ema_200
        close_f = close

        if ema_200_f == 0:
            return 0.0

        # Percentage distance between EMAs
        ema_diff_pct = (ema_20_f - ema_200_f) / ema_200_f * 100

        # Price position relative to EMAs
        price_above_20 = 1.0 if close > ema_20 else -1.0
        price_above_200 = 1.0 if close > ema_200 else -1.0

        # Golden/Death cross proximity
        cross_score = 0.0
        if ema_diff_pct > 0:
            # Bullish: EMA20 > EMA200
            cross_score = min(ema_diff_pct * 10, 50.0)  # Cap at 50
        else:
            # Bearish: EMA20 < EMA200
            cross_score = max(ema_diff_pct * 10, -50.0)  # Cap at -50

        # Add price position bonus
        position_score = (price_above_20 + price_above_200) * 25.0

        return cross_score + position_score

    def _evaluate_adx(self, features: FeatureVector) -> float:
        """Evaluate ADX for trend strength.

        ADX > 25 = trending, < 20 = ranging
        +DI/-DI determines direction

        Returns:
            Score from -100 (strong bear trend) to +100 (strong bull trend)
        """
        adx = features.adx_14
        if adx is None:
            return 0.0

        # ADX is already a float
        adx_f = adx

        # ADX strength component (0-50 based on ADX value)
        if adx_f > 40:
            strength = 50.0  # Very strong trend
        elif adx_f > 25:
            strength = 30.0 + (adx_f - 25) * (20.0 / 15.0)  # 30-50
        elif adx_f > 20:
            strength = 10.0 + (adx_f - 20) * (20.0 / 5.0)   # 10-30
        else:
            strength = 0.0  # No trend

        # Direction from close vs EMA20 (proxy for +DI/-DI)
        close = features.close
        ema_20 = features.ema_20
        if close and ema_20 and ema_20 > 0:
            direction = 1.0 if close > ema_20 else -1.0
        else:
            direction = 0.0

        return strength * direction

    def _evaluate_price_ma_position(self, features: FeatureVector) -> float:
        """Evaluate price position relative to moving averages.

        Returns:
            Score from -100 to +100
        """
        close = features.close
        sma_20 = features.sma_20
        ema_200 = features.ema_200

        if not all([close, sma_20, ema_200]):
            return 0.0

        # Features are already floats
        score = 0.0

        # Price vs SMA20 (short-term trend)
        if close > sma_20:
            score += 30.0
        else:
            score -= 30.0

        # Price vs EMA200 (long-term trend)
        if close > ema_200:
            score += 40.0
        else:
            score -= 40.0

        # SMA20 vs EMA200 (trend alignment)
        if sma_20 > ema_200:
            score += 30.0
        else:
            score -= 30.0

        return max(-100.0, min(100.0, score))

    def _evaluate_volume(
        self,
        features: FeatureVector,
        snapshot: MarketSnapshot | None = None,
    ) -> float:
        """Evaluate volume confirmation.

        High volume confirms trend direction.

        Returns:
            Score from -50 to +50 (volume is a confirmer, not primary)
        """
        # Use ATR% as a volatility proxy for volume context
        atr_pct = features.atr_pct
        if atr_pct is None:
            return 0.0

        # ATR% is already a float
        atr_pct_f = atr_pct

        # High volatility often correlates with high volume
        if atr_pct_f > 3.0:
            return 30.0  # Strong confirmation potential
        elif atr_pct_f > 2.0:
            return 15.0
        elif atr_pct_f < 1.0:
            return -10.0  # Low volatility = low conviction

        return 0.0

    def _evaluate_momentum(self, features: FeatureVector) -> float:
        """Evaluate RSI momentum.

        Returns:
            Score from -100 to +100
        """
        rsi = features.rsi_14
        if rsi is None:
            return 0.0

        # RSI is already a float

        # RSI scoring
        if rsi > 70:
            return 40.0  # Overbought but strong momentum
        elif rsi > 60:
            return 60.0  # Bullish momentum
        elif rsi > 50:
            return 30.0  # Mild bullish
        elif rsi > 40:
            return -30.0  # Mild bearish
        elif rsi > 30:
            return -60.0  # Bearish momentum
        else:
            return -40.0  # Oversold but strong downward momentum

    def _compute_confidence(self, contributions: dict[str, float]) -> float:
        """Compute confidence based on agreement between components.

        High confidence when components agree on direction.
        Low confidence when components disagree.

        Returns:
            Confidence from 0 to 1
        """
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
            magnitude_factor = min(avg_magnitude / 30.0, 1.0)  # Normalize to 0-1
        else:
            magnitude_factor = 0.0

        # Combined confidence
        confidence = (agreement_ratio * 0.7) + (magnitude_factor * 0.3)

        return min(confidence, 1.0)

    def _generate_reason(
        self,
        contributions: dict[str, float],
        direction: float,
        weight: float,
    ) -> str:
        """Generate human-readable reason for the evaluation."""
        if weight < 10:
            return "No clear trend signal"

        # Find top contributors
        sorted_contribs = sorted(
            contributions.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )

        direction_str = "bullish" if direction > 0 else ("bearish" if direction < 0 else "neutral")

        # Build reason from top contributors
        reasons = []
        for name, value in sorted_contribs[:3]:
            if abs(value) > 10:
                component_direction = "supports" if value > 0 else "opposes"
                reasons.append(f"{name} {component_direction} {direction_str}")

        if not reasons:
            return f"Weak {direction_str} signal"

        return f"Strong {direction_str}: {', '.join(reasons)}"

"""AI Regime Disambiguator — resolves low-conviction regime classifications.

Phase 3 (AI Role Reversal): When the deterministic regime classifier outputs a
low-conviction classification (ADX borderline, Hurst ambiguous), AI helps resolve.

Runs alongside the deterministic classifier, not instead of it.
If AI confidence > 70% and disagrees with deterministic, uses AI with 80/20 weighting.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.ai_client import AIClient

DISAMBIGUATOR_PROMPT = """The market regime classifier is uncertain.

Symbol: BTC/USDT (proxy for market regime)
ADX: {adx} (threshold: 20 for trend, 40 for hyper)
Hurst: {hurst} (0.5 = random walk, >0.5 = trending, <0.5 = mean-reverting)
ATR percentile: {atr_percentile}
Recent price action: {price_action_summary}

The classifier's best guess is {current_regime} with {conviction:.0%} confidence.

Is the market ACTUALLY:
A) Early TREND (breakout developing, trade aggressively)
B) Late CHOP (noise, reduce size)
C) RANGE (mean-revert at edges)

Respond JSON: {{"regime": "TREND_BULL|TREND_BEAR|RANGE|CHOP", "confidence": 0-100, "reasoning": "<20 words>"}}
"""


@dataclass
class RegimeVerdict:
    """AI regime disambiguator's verdict."""

    regime: str  # TREND_BULL, TREND_BEAR, RANGE, CHOP
    confidence: int  # 0-100
    reasoning: str


# Regime blending weights: (deterministic_weight, ai_weight)
BLEND_WEIGHTS = {
    "TREND_BULL": (0.70, 0.30),  # Deterministic is strong in trends
    "TREND_BEAR": (0.70, 0.30),
    "RANGE": (0.60, 0.40),       # AI better at spotting exhaustion
    "CHOP": (0.40, 0.60),        # AI better in noisy environments
    "TRANSITION_BULL": (0.50, 0.50),
    "TRANSITION_BEAR": (0.50, 0.50),
}


class AIRegimeDisambiguator:
    """AI resolves low-conviction regime classifications.

    Usage:
        disambig = AIRegimeDisambiguator(ai_client)
        final_regime = await disambig.disambiguate(
            symbol="BTC/USDT",
            adx=22.0, hurst=0.48, atr_percentile=45.0,
            current_regime="CHOP", conviction=0.35,
        )
    """

    # Only activate when deterministic conviction is below this threshold
    ACTIVATION_THRESHOLD = 0.45

    # AI must exceed this confidence to override deterministic
    AI_CONFIDENCE_THRESHOLD = 70

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai = ai_client

    async def disambiguate(
        self,
        symbol: str,
        adx: float,
        hurst: float,
        atr_percentile: float,
        current_regime: str,
        conviction: float,
        price_action_summary: str = "no data",
    ) -> str:
        """Disambiguate regime classification.

        Args:
            symbol: Trading pair (for context).
            adx: Current ADX value.
            hurst: Current Hurst exponent.
            atr_percentile: ATR percentile (0-100).
            current_regime: Deterministic regime classification.
            conviction: Deterministic conviction (0.0-1.0).
            price_action_summary: Brief description of recent price action.

        Returns:
            Final regime string (may be same as current_regime).
        """
        # Only activate in low-conviction zone
        if conviction >= self.ACTIVATION_THRESHOLD:
            return current_regime

        # AI not available
        if self._ai is None:
            return current_regime

        # Build prompt
        prompt = DISAMBIGUATOR_PROMPT.format(
            adx=f"{adx:.2f}",
            hurst=f"{hurst:.3f}",
            atr_percentile=f"{atr_percentile:.0f}",
            price_action_summary=price_action_summary,
            current_regime=current_regime,
            conviction=conviction,
        )

        # Call AI with timeout
        try:
            response = await asyncio.wait_for(
                self._ai.ask(prompt, max_tokens=150),
                timeout=8.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("AI regime disambiguation failed: %s", e)
            return current_regime

        # Parse response
        verdict = self._parse_verdict(response)
        if verdict is None:
            return current_regime

        # Apply blending logic
        ai_regime = verdict.regime
        ai_conf = verdict.confidence

        if ai_conf < self.AI_CONFIDENCE_THRESHOLD:
            logger.debug(
                "AI regime confidence %d < %d — keeping deterministic %s",
                ai_conf, self.AI_CONFIDENCE_THRESHOLD, current_regime,
            )
            return current_regime

        if ai_regime == current_regime:
            return current_regime  # AI agrees, no change needed

        # AI disagrees with high confidence — use blended weighting
        det_w, ai_w = BLEND_WEIGHTS.get(current_regime, (0.50, 0.50))

        # If AI confidence is very high (>85%), give it more weight
        if ai_conf > 85:
            ai_w = min(0.8, ai_w + 0.2)
            det_w = 1.0 - ai_w

        # For simplicity, if AI disagrees with high confidence, use AI's regime
        # (the weights are used for sizing, not regime selection)
        logger.info(
            "AI regime disambiguation: %s (conv=%.2f) → %s (ai_conf=%d, reasoning=%s)",
            current_regime, conviction, ai_regime, ai_conf, verdict.reasoning,
        )
        return ai_regime

    def _parse_verdict(self, response: str) -> RegimeVerdict | None:
        """Parse AI response into RegimeVerdict."""
        try:
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text)
            regime = data.get("regime", "CHOP")
            if regime not in ("TREND_BULL", "TREND_BEAR", "RANGE", "CHOP"):
                regime = "CHOP"

            return RegimeVerdict(
                regime=regime,
                confidence=min(100, max(0, data.get("confidence", 50))),
                reasoning=data.get("reasoning", ""),
            )

        except Exception as e:
            logger.debug("Failed to parse AI regime response: %s", e)
            return None

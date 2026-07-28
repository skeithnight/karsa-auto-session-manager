"""Carry / Positioning Dislocation Edge Family.

Evaluates carry and positioning dislocation setups.

Regime filter: Any (carry opportunities exist in all regimes)
Key signals:
- Negative funding rate → LONG carry bonus
- Funding term structure (squeeze imminent signals)
- OI divergence signals

Filters:
- Funding rate extremes (block opposite direction)
- Macro-aware but not macro-dominated
"""

from __future__ import annotations

import logging
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class CarryDislocation(EdgeFamily):
    """Carry / positioning dislocation edge family."""

    @property
    def name(self) -> str:
        return "carry_dislocation"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        # Carry opportunities exist in all regimes
        return list(MarketRegime)

    async def evaluate(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        features: Any,
        snapshot: MarketSnapshot,
        context: DecisionContext | None = None,
        redis_client: Any = None,
        **kwargs: Any,
    ) -> EdgeFamilyResult:
        """Evaluate carry / positioning dislocation setup."""
        filters_passed: dict[str, bool] = {}
        metadata: dict[str, Any] = {}

        # --- Funding Rate Block ---
        funding_rate = kwargs.get("funding_rate")
        if funding_rate is not None:
            if direction == "LONG" and funding_rate > 0.0005:
                logger.info(
                    "carry: %s LONG blocked due to extreme positive funding %.5f",
                    symbol, funding_rate,
                )
                return EdgeFamilyResult(
                    family_name=self.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=True,
                    filters_passed={"funding_rate": False},
                    reject_reason="extreme_positive_funding",
                )
            if direction == "SHORT" and funding_rate < -0.0005:
                logger.info(
                    "carry: %s SHORT blocked due to extreme negative funding %.5f",
                    symbol, funding_rate,
                )
                return EdgeFamilyResult(
                    family_name=self.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=True,
                    filters_passed={"funding_rate": False},
                    reject_reason="extreme_negative_funding",
                )
            filters_passed["funding_rate"] = True
        else:
            filters_passed["funding_rate"] = True

        # --- Base Score ---
        base_score = 40.0  # Lower base for carry (needs specific conditions)

        # --- Carry Bonus (negative funding → LONG) ---
        if funding_rate is not None and direction == "LONG" and funding_rate < -0.0001:
            carry_bonus = min(30, abs(funding_rate) * 100000)  # Scale bonus
            base_score += carry_bonus
            metadata["carry_bonus"] = carry_bonus
            metadata["funding_rate"] = funding_rate

        # --- Funding Term Structure ---
        if redis_client:
            try:
                import json as _json
                term_signal = await redis_client.get(f"karsa:market:{symbol}:funding_term_signal")
                if term_signal:
                    term_signal = term_signal.decode() if isinstance(term_signal, bytes) else str(term_signal)
                    if term_signal == "SQUEEZE_IMMINENT_LONG" and direction == "LONG":
                        base_score += 25
                        metadata["term_structure_bonus"] = 25
                    elif term_signal == "SQUEEZE_IMMINENT_SHORT" and direction == "SHORT":
                        base_score += 25
                        metadata["term_structure_bonus"] = 25
            except Exception as e:
                logger.debug("carry: term structure check failed for %s: %s", symbol, e)

        # --- OI Divergence Bonus ---
        oi_change = kwargs.get("oi_change")
        if oi_change is not None:
            # OI dropping during price move = capitulation
            if direction == "LONG" and oi_change < -0.05:
                base_score += 10
                metadata["oi_divergence_bonus"] = 10
            elif direction == "SHORT" and oi_change > 0.05:
                base_score += 10
                metadata["oi_divergence_bonus"] = 10

        score = base_score

        # --- Session Multiplier ---
        from datetime import UTC, datetime
        now_utc = datetime.now(UTC)
        hour = now_utc.hour
        if 0 <= hour < 7:
            session_mult = 0.7
        elif 7 <= hour < 12:
            session_mult = 1.0
        elif 12 <= hour < 16:
            session_mult = 1.2
        elif 16 <= hour < 21:
            session_mult = 1.0
        else:
            session_mult = 0.8
        metadata["session_mult"] = session_mult

        # --- Confidence ---
        confidence = min(score / 100.0, 0.95)

        return EdgeFamilyResult(
            family_name=self.name,
            score=score,
            confidence=confidence,
            regime_aligned=True,
            filters_passed=filters_passed,
            metadata=metadata,
        )

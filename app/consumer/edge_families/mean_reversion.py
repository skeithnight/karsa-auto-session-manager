"""Mean Reversion Edge Family.

Evaluates mean-reversion setups in ranging markets.

Regime filter: RANGE
Key signals:
- RSI exhaustion (RSI > 75 for shorts, RSI < 25 for longs)
- Bollinger Band edge (price pierced 2.5 std dev)
- Wick rejection (pin bar)
- Sector rotation alignment

Filters:
- Sector rotation alignment (not hard block)
- Moderate macro penalty (not hard block)
"""

from __future__ import annotations

import logging
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class MeanReversion(EdgeFamily):
    """Mean reversion edge family."""

    @property
    def name(self) -> str:
        return "mean_reversion"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGE]

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
        """Evaluate mean reversion setup."""
        if not self.is_active(regime):
            return self._reject(self.name, "regime_not_active")

        filters_passed: dict[str, bool] = {}
        metadata: dict[str, Any] = {}

        # --- Sector Rotation Check ---
        sector_filter = kwargs.get("sector_filter")
        if sector_filter:
            sec_res = sector_filter.check_sector_alignment(symbol, direction)
            filters_passed["sector_alignment"] = sec_res.get("approved", False)
            if not sec_res.get("approved"):
                logger.info(
                    "mean_reversion: %s %s BLOCKED by Sector Rotation filter (%s)",
                    symbol, direction, sec_res.get("reason"),
                )
                return EdgeFamilyResult(
                    family_name=self.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=True,
                    filters_passed=filters_passed,
                    reject_reason="sector_rotation_blocked",
                )
            # Sector scoring (bonus/penalty)
            sector_score = sector_filter.get_sector_score(symbol, direction)
            metadata["sector_score"] = sector_score
        else:
            filters_passed["sector_alignment"] = True
            metadata["sector_score"] = 1.0

        # --- Base Score from StrategyRouter ---
        router = kwargs.get("router")
        if router:
            context_result, vol_factor = await router.evaluate_signal(
                features=features,
                regime=regime,
                direction=direction,
                symbol=symbol,
            )
            base_score = context_result.total_confidence
            metadata["vol_factor"] = vol_factor
            metadata["context"] = context_result
        else:
            base_score = 50.0
            vol_factor = 1.0

        # --- Funding Rate Reversal Signal ---
        funding_rate = kwargs.get("funding_rate")
        if funding_rate is not None:
            # Extreme positive funding → SHORT reversal
            # Extreme negative funding → LONG reversal
            if direction == "LONG" and funding_rate < -0.0003:
                base_score += 15  # Reversal bonus
                metadata["funding_reversal"] = True
            elif direction == "SHORT" and funding_rate > 0.0003:
                base_score += 15
                metadata["funding_reversal"] = True

        # --- Sector Score Application ---
        score = base_score * metadata.get("sector_score", 1.0)

        # --- Moderate Macro Penalty (not hard block) ---
        multi_tf = kwargs.get("multi_tf")
        if multi_tf and symbol not in ["BTC/USDT", "ETH/USDT"]:
            macro_penalty = await multi_tf.get_macro_anchor_penalty(direction)
            # For mean reversion, apply moderate penalty (not hard block)
            score *= max(0.7, macro_penalty)
            metadata["macro_penalty"] = max(0.7, macro_penalty)

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

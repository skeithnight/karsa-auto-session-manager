"""Liquidation / Squeeze Edge Family.

Evaluates liquidation cascade and squeeze setups.

Regime filter: Any (squeezes can happen in any regime)
Key signals:
- Liquidation heatmap signal
- Cross-asset momentum alignment
- OI delta patterns

Filters:
- Allow explicit exemption path (squeeze can begin while anchors disagree)
- Cross-asset alignment (not hard block)
"""

from __future__ import annotations

import logging
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class LiquidationSqueeze(EdgeFamily):
    """Liquidation / squeeze edge family."""

    @property
    def name(self) -> str:
        return "liquidation_squeeze"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        # Squeezes can happen in any regime
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
        """Evaluate liquidation / squeeze setup."""
        filters_passed: dict[str, bool] = {}
        metadata: dict[str, Any] = {}

        # --- Base Score ---
        base_score = 35.0  # Lower base (needs specific signals)

        # --- Liquidation Heatmap Bonus ---
        router = kwargs.get("router")
        if router and redis_client:
            try:
                from app.core.config import get_settings
                config = get_settings()
                liq_bonus, liq_reason = await router.evaluate_liquidation_heatmap(
                    symbol=symbol,
                    direction=direction,
                    redis_client=redis_client,
                    config=config,
                )
                if liq_bonus > 0:
                    base_score += liq_bonus
                    metadata["liq_heatmap_bonus"] = liq_bonus
                    metadata["liq_heatmap_reason"] = liq_reason
            except Exception as e:
                logger.debug("liquidation_squeeze: heatmap check failed for %s: %s", symbol, e)

        # --- Cross-Asset Momentum Bonus ---
        if router and redis_client:
            try:
                from app.core.config import get_settings
                config = get_settings()
                xa_bonus, xa_reason = await router.evaluate_cross_asset_momentum(
                    symbol=symbol,
                    direction=direction,
                    redis_client=redis_client,
                    config=config,
                )
                if xa_bonus > 0:
                    base_score += xa_bonus
                    metadata["cross_asset_bonus"] = xa_bonus
                    metadata["cross_asset_reason"] = xa_reason
            except Exception as e:
                logger.debug("liquidation_squeeze: cross-asset failed for %s: %s", symbol, e)

        # --- Squeeze Momentum Check ---
        # Check if price is near liquidation levels
        if redis_client:
            try:
                import json as _json
                state_raw = await redis_client.get(f"global:state:{symbol}")
                if state_raw:
                    state = _json.loads(state_raw)
                    liq_long = state.get("liq_long_usd", 0)
                    liq_short = state.get("liq_short_usd", 0)
                    if liq_long > 100000 and direction == "LONG":
                        base_score += 10
                        metadata["squeeze_near_liq_long"] = True
                    elif liq_short > 100000 and direction == "SHORT":
                        base_score += 10
                        metadata["squeeze_near_liq_short"] = True
            except Exception as e:
                logger.debug("liquidation_squeeze: state check failed for %s: %s", symbol, e)

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

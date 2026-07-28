"""Trend Continuation Edge Family.

Evaluates trend-following setups in trending regimes.

Regime filter: TREND_BULL/BEAR, HYPER_BULL/BEAR
Key signals:
- Breakout confirmation (price > 20-period high/low)
- Volume surge
- Global exchange sync (Binance + OKX)
- MTF alignment (4H EMA)
- Macro anchor confirmation

Filters:
- Macro momentum hard block (for non-BTC/ETH)
- Multi-timeframe trend alignment
- Session volatility filter
"""

from __future__ import annotations

import logging
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class TrendContinuation(EdgeFamily):
    """Trend continuation edge family."""

    @property
    def name(self) -> str:
        return "trend_continuation"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        return [
            MarketRegime.TREND_BULL,
            MarketRegime.TREND_BEAR,
            MarketRegime.HYPER_BULL,
            MarketRegime.HYPER_BEAR,
        ]

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
        """Evaluate trend continuation setup."""
        # Check regime alignment
        if not self.is_active(regime):
            return self._reject(self.name, "regime_not_active")

        filters_passed: dict[str, bool] = {}
        metadata: dict[str, Any] = {}

        # --- MTF Alignment Check ---
        multi_tf = kwargs.get("multi_tf")
        if multi_tf:
            mtf_res = await multi_tf.check(symbol, direction)
            filters_passed["mtf_alignment"] = not mtf_res.get("blocked", False)
            if mtf_res.get("blocked"):
                logger.info(
                    "trend: %s %s blocked by MTF filter",
                    symbol, direction,
                )
                return EdgeFamilyResult(
                    family_name=self.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=True,
                    filters_passed=filters_passed,
                    reject_reason="mtf_alignment_blocked",
                )
        else:
            filters_passed["mtf_alignment"] = True

        # --- Macro Momentum Hard Block ---
        if multi_tf and symbol not in ["BTC/USDT", "ETH/USDT"]:
            mom_block = await multi_tf.check_macro_momentum_block(symbol, direction)
            filters_passed["macro_momentum"] = not mom_block.get("blocked", False)
            if mom_block.get("blocked"):
                logger.warning(
                    "trend: %s %s HARD BLOCKED by Macro Momentum filter (%s)",
                    symbol, direction, mom_block.get("reason"),
                )
                return EdgeFamilyResult(
                    family_name=self.name,
                    score=0.0,
                    confidence=0.0,
                    regime_aligned=True,
                    filters_passed=filters_passed,
                    reject_reason="macro_momentum_hard_block",
                )
        else:
            filters_passed["macro_momentum"] = True

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

        # --- ELO Adjustment ---
        elo = await self._get_elo(symbol, direction, regime, redis_client)
        elo_factor = 1.0 + (elo - 1500.0) / 2000.0
        elo_factor = max(0.85, min(1.15, elo_factor))
        metadata["elo"] = elo
        metadata["elo_factor"] = elo_factor

        # --- Momentum Exemption ---
        arr = kwargs.get("arr")
        momentum_exemption = False
        if arr is not None and len(arr) >= 24:
            close_now = float(arr[-1][4])
            close_24h_ago = float(arr[-24][4])
            pct_change = (close_now - close_24h_ago) / close_24h_ago
            if (direction == "LONG" and pct_change > 0.08) or \
               (direction == "SHORT" and pct_change < -0.08):
                momentum_exemption = True
                vol_factor = 1.0  # Bypass volatility penalty
                metadata["momentum_exemption"] = True

        # --- Calculate Final Score ---
        score = base_score * elo_factor
        if not momentum_exemption:
            score *= vol_factor

        # --- Macro Anchor Penalty ---
        if multi_tf and symbol not in ["BTC/USDT", "ETH/USDT"] and not momentum_exemption:
            macro_penalty = await multi_tf.get_macro_anchor_penalty(direction)
            score *= macro_penalty
            metadata["macro_penalty"] = macro_penalty

        # --- Session Multiplier ---
        from datetime import UTC, datetime
        now_utc = datetime.now(UTC)
        hour = now_utc.hour
        if 0 <= hour < 7:
            session_mult, session_name = 0.7, "ASIA"
        elif 7 <= hour < 12:
            session_mult, session_name = 1.0, "LONDON"
        elif 12 <= hour < 16:
            session_mult, session_name = 1.2, "LDN_NY_OVERLAP"
        elif 16 <= hour < 21:
            session_mult, session_name = 1.0, "NEW_YORK"
        else:
            session_mult, session_name = 0.8, "PACIFIC"

        metadata["session_mult"] = session_mult
        metadata["session_name"] = session_name

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

    async def _get_elo(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        redis_client: Any,
    ) -> float:
        """Read ELO rating for a strategy from Redis."""
        if redis_client is None:
            return 1500.0
        try:
            import json as _json
            strategy_key = f"{regime.value}:{direction}"
            raw = await redis_client.get(f"karsa:elo:{strategy_key}")
            if raw:
                data = _json.loads(raw)
                return data.get("elo", 1500.0)
        except Exception:
            pass
        return 1500.0

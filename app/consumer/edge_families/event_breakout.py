"""Event Breakout Edge Family.

Evaluates event-driven breakout setups.

Regime filter: Any (events can trigger breakouts in any regime)
Key signals:
- HMM regime prediction (breakout imminent)
- Token unlock calendar (sell pressure)
- Volatility floor check

Filters:
- HMM confidence threshold
- Token unlock window check
"""

from __future__ import annotations

import logging
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class EventBreakout(EdgeFamily):
    """Event breakout edge family."""

    @property
    def name(self) -> str:
        return "event_breakout"

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        # Events can trigger breakouts in any regime
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
        """Evaluate event breakout setup."""
        filters_passed: dict[str, bool] = {}
        metadata: dict[str, Any] = {}

        # --- Base Score ---
        base_score = 40.0

        # --- HMM Regime Prediction ---
        if redis_client:
            try:
                from app.core.config import get_settings
                settings = get_settings()
                hmm_raw = await redis_client.get(settings.hmm_redis_key)
                if hmm_raw:
                    import json as _json
                    hmm_data = _json.loads(hmm_raw)
                    hmm_signal = hmm_data.get("signal")
                    hmm_confidence = hmm_data.get("confidence", 0.0)

                    if hmm_signal == "HMM_BREAKOUT_IMMINENT" and direction == "LONG":
                        scaled_bonus = settings.hmm_score_breakout_bonus * hmm_confidence
                        base_score += scaled_bonus
                        metadata["hmm_breakout_bonus"] = scaled_bonus
                        metadata["hmm_confidence"] = hmm_confidence
                    elif hmm_signal == "HMM_CHOP_IMMINENT":
                        scaled_penalty = settings.hmm_score_chop_penalty * hmm_confidence
                        base_score -= scaled_penalty
                        metadata["hmm_chop_penalty"] = scaled_penalty

                    # Regime confidence bonus
                    hmm_probs = hmm_data.get("probabilities", {})
                    if hmm_probs and hmm_confidence > 0.6:
                        high_vol_prob = hmm_probs.get("HIGH_VOL", 0.0)
                        low_vol_prob = hmm_probs.get("LOW_VOL", 0.0)
                        if high_vol_prob > 0.7 and direction == "SHORT":
                            conf_bonus = 10.0 * high_vol_prob
                            base_score += conf_bonus
                            metadata["hmm_vol_bonus"] = conf_bonus
                        elif low_vol_prob > 0.7 and direction == "LONG":
                            conf_bonus = 8.0 * low_vol_prob
                            base_score += conf_bonus
                            metadata["hmm_vol_bonus"] = conf_bonus
            except Exception as e:
                logger.debug("event_breakout: HMM check failed for %s: %s", symbol, e)

        # --- Token Unlock Penalty ---
        if redis_client:
            try:
                from app.core.config import get_settings
                from datetime import datetime, timedelta, timezone

                settings = get_settings()
                unlock_window = timedelta(hours=settings.unlock_window_hours)
                impact_threshold = float(settings.unlock_impact_threshold_pct)
                penalty = settings.unlock_penalty_score

                unlock_key = f"karsa:unlock:{symbol}"
                raw_unlock = await redis_client.get(unlock_key)
                if raw_unlock:
                    import json as _json
                    unlock_data = _json.loads(raw_unlock)
                    unlock_time_str = unlock_data.get("unlock_time")
                    unlock_pct = unlock_data.get("unlock_pct_of_supply", 0)

                    if unlock_time_str and unlock_pct >= impact_threshold:
                        unlock_time = datetime.fromisoformat(unlock_time_str)
                        if unlock_time.tzinfo is None:
                            unlock_time = unlock_time.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        time_to_unlock = unlock_time - now

                        if timedelta(0) <= time_to_unlock <= unlock_window:
                            base_score -= penalty
                            metadata["token_unlock_penalty"] = penalty
                            filters_passed["token_unlock"] = False
                        else:
                            filters_passed["token_unlock"] = True
                    else:
                        filters_passed["token_unlock"] = True
                else:
                    filters_passed["token_unlock"] = True
            except Exception as e:
                logger.debug("event_breakout: token unlock check failed for %s: %s", symbol, e)
                filters_passed["token_unlock"] = True
        else:
            filters_passed["token_unlock"] = True

        # --- Volatility Floor Check ---
        vol_floor_penalty = 1.0
        if redis_client:
            try:
                import json as _json
                vol_floor_raw = await redis_client.get("system:vol:floor:threshold")
                if vol_floor_raw:
                    vol_floor = _json.loads(vol_floor_raw)
                    threshold = vol_floor.get("threshold", 0)
                    current_btc_atr = vol_floor.get("current_atr", 0)
                    if threshold > 0 and current_btc_atr > 0 and current_btc_atr < threshold:
                        vol_floor_penalty = 0.5
                        metadata["vol_floor_penalty"] = True
            except Exception as e:
                logger.debug("event_breakout: vol floor check failed for %s: %s", symbol, e)

        score = base_score * vol_floor_penalty

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

    # Note: EventBreakout intentionally does NOT have macro filters
    # because events (token unlocks, HMM signals) can override macro context.
    # This aligns with the quant trader review:
    # "Event breakout should allow explicit exemption path"

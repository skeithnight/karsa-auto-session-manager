"""Hybrid Decision Engine — combines statistical guardrails with AI recommendations.

Architecture:
  1. Compute statistical features (StatisticalFeatureEngine)
  2. Hard guardrails (10 rules) — if ANY fails, BLOCK (no AI needed)
  3. AI evaluation (NineRouterService) — if timeout/error, use statistical-only
  4. Soft guardrails (5 rules) — downgrade position size
  5. Final position sizing — volatility-based
  6. Output: HybridDecision with action, size, reasoning, features_snapshot
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    import pandas as pd

from app.ai.dto import AIDecisionDTO
from app.ai.nine_router_service import NineRouterService
from app.alpha.regime_classifier import MarketRegime
from app.alpha.rejected_signal_tracker import RejectedSignalTracker
from app.alpha.statistical_engine import StatisticalFeatureEngine
from app.core.decision_context import DecisionContext
from app.core.feature_extractor import FeatureVector

# ---------------------------------------------------------------------------
# Guardrail Threshold Constants
# ---------------------------------------------------------------------------

# Hard Guardrails
AI_CONFIDENCE_MIN = 60
BTC_BETA_THRESHOLD = 1.2
FUNDING_RATE_LONG_THRESHOLD = 0.0001  # 0.01% per 8h (as decimal)
BREAKOUT_VOLUME_MIN = 1.2
EMA50_OVEREXTENSION_HARD_PCT = 10.0
MAX_CONCURRENT_POSITIONS = 5
CORRELATION_HARD_THRESHOLD = 0.85
ATR_EXTREME_PCT = 8.0
AI_TIMEOUT_SECONDS = 10.0

# Soft Guardrails
BETA_SOFT_THRESHOLD = 1.5
CORRELATION_SOFT_THRESHOLD = 0.8
BTC_SIDEWAYS_REGIMES = frozenset({"RANGE", "CHOP"})
VOLUME_SPIKE_SOFT_LOW = 1.2
VOLUME_SPIKE_SOFT_HIGH = 1.5
EMA50_SOFT_LOW_PCT = 5.0
EMA50_SOFT_HIGH_PCT = 10.0

# Kill Zone: hard block Asia dead hours (02:00-06:00 UTC)
# Unless funding edge > 5% annualized or score > 85
KILL_ZONE_START = 2  # 02:00 UTC
KILL_ZONE_END = 6    # 06:00 UTC
KILL_ZONE_FUNDING_EDGE_MIN = 5.0  # 5% annualized
KILL_ZONE_SCORE_MIN = 85

# Position size numeric values
SIZE_MAP: dict[str, float] = {
    "BLOCK": 0.0,
    "QUARTER": 0.25,
    "HALF": 0.50,
    "FULL": 1.00,
}

DOWNGRADE_ORDER: list[str] = ["FULL", "HALF", "QUARTER", "BLOCK"]


# ---------------------------------------------------------------------------
# Output Dataclass
# ---------------------------------------------------------------------------


@dataclass
class HybridDecision:
    """Output of the HybridDecisionEngine.

    Contains the trading action, position size, confidence, risk assessment,
    and full reasoning chain with guardrail trigger details.
    """

    action: str  # LONG, SHORT, BLOCK
    size: str  # BLOCK, QUARTER, HALF, FULL
    size_pct: float  # 0.0, 0.25, 0.50, 1.00
    confidence: int  # 0-100
    risk_level: str  # LOW, MEDIUM, HIGH
    entry_strategy: str  # MARKET, LIMIT_RETEST, WAIT_PULLBACK
    stop_loss_strategy: str  # TIGHT, NORMAL, WIDE
    reasoning: str
    guardrails_triggered: list[str] = field(default_factory=list)
    features_snapshot: dict = field(default_factory=dict)
    ai_decision: AIDecisionDTO | None = None


# ---------------------------------------------------------------------------
# Regime Conversion Helpers
# ---------------------------------------------------------------------------

_SIMPLE_TO_REGIME: dict[str, MarketRegime] = {
    "TREND": MarketRegime.TREND_BULL,
    "TREND_BULL": MarketRegime.TREND_BULL,
    "TREND_BEAR": MarketRegime.TREND_BEAR,
    "RANGE": MarketRegime.RANGE,
    "CHOP": MarketRegime.CHOP,
    "SNIPER": MarketRegime.SNIPER,
    "TRANSITION_BULL": MarketRegime.TRANSITION_BULL,
    "TRANSITION_BEAR": MarketRegime.TRANSITION_BEAR,
}


def _to_market_regime(regime: str | MarketRegime) -> MarketRegime:
    """Convert a string regime to MarketRegime enum.

    Accepts both simple names ('TREND', 'RANGE', 'CHOP') and full
    enum values ('TREND_BULL', 'TREND_BEAR', etc.).
    """
    if isinstance(regime, MarketRegime):
        return regime
    upper = regime.upper().strip()
    if upper in _SIMPLE_TO_REGIME:
        return _SIMPLE_TO_REGIME[upper]
    # Fallback: try direct enum construction
    try:
        return MarketRegime(upper)
    except ValueError:
        logger.warning(f"_to_market_regime: unknown regime '{regime}', defaulting to RANGE")
        return MarketRegime.RANGE


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class HybridDecisionEngine:
    """Combines statistical guardrails with AI recommendations.

    Pipeline:
        features -> hard guardrails -> AI -> soft guardrails -> sizing -> decision

    If hard guardrails block, AI is never called. If AI times out or errors,
    the engine falls back to statistical-only decision making.
    """

    def __init__(
        self,
        statistical_engine: StatisticalFeatureEngine,
        ai_service: NineRouterService,
        rejected_tracker: RejectedSignalTracker | None = None,
    ) -> None:
        self._stat_engine = statistical_engine
        self._ai_service = ai_service
        self._rejected_tracker = rejected_tracker
        logger.debug("HybridDecisionEngine initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        symbol: str,
        regime: str,
        btc_regime: str,
        ohlcv: pd.DataFrame,
        btc_ohlcv: pd.DataFrame,
        direction: str = "LONG",
        funding_rate: float = 0.0,
        concurrent_positions: int = 0,
        existing_correlations: dict[str, float] | None = None,
        current_price: float = 0.0,
    ) -> HybridDecision:
        """Evaluate a trading opportunity through the full hybrid pipeline.

        Args:
            symbol: Trading pair symbol (e.g. "SOL/USDT").
            regime: Market regime string ("TREND", "RANGE", "CHOP", etc.).
            btc_regime: BTC market regime string.
            ohlcv: DataFrame with columns [open, high, low, close, volume].
            btc_ohlcv: BTC DataFrame for beta/correlation reference.
            direction: Proposed trade direction ("LONG" or "SHORT").
            funding_rate: Current funding rate as decimal.
            concurrent_positions: Number of currently open positions.
            existing_correlations: Map of symbol -> correlation with existing positions.
            current_price: Current price for feature vector (extracted from ohlcv if 0).

        Returns:
            HybridDecision with action, sizing, reasoning, and full context.
        """
        logger.info(f"HybridDecisionEngine: evaluating {symbol} (regime={regime}, dir={direction})")

        # 1. Compute statistical features
        features = await self._stat_engine.calculate_features(
            symbol=symbol,
            ohlcv=ohlcv,
            btc_ohlcv=btc_ohlcv,
            funding_rate=funding_rate,
        )

        # 2. Hard guardrails — if any fails, BLOCK immediately
        hard_triggered = self._check_hard_guardrails(
            features=features,
            regime=regime,
            btc_regime=btc_regime,
            concurrent_positions=concurrent_positions,
            existing_correlations=existing_correlations or {},
        )

        if hard_triggered:
            reason = f"Hard guardrails blocked: {', '.join(hard_triggered)}"
            logger.info(f"HybridDecisionEngine: {symbol} BLOCKED — {reason}")
            await self._track_rejected(symbol, direction, reason, 0.0, regime)
            return HybridDecision(
                action="BLOCK",
                size="BLOCK",
                size_pct=SIZE_MAP["BLOCK"],
                confidence=0,
                risk_level="HIGH",
                entry_strategy="WAIT_PULLBACK",
                stop_loss_strategy="NORMAL",
                reasoning=reason,
                guardrails_triggered=hard_triggered,
                features_snapshot=features,
            )

        # 2b. Kill Zone check — hard block Asia dead hours (02:00-06:00 UTC)
        # Allow only extreme funding edge or very high score
        hour_utc = datetime.now(timezone.utc).hour
        if KILL_ZONE_START <= hour_utc < KILL_ZONE_END:
            funding_annualized = features.get("annualized_funding_cost_pct", 0.0)
            ev_score = features.get("ev_score", 0.0)
            if funding_annualized < KILL_ZONE_FUNDING_EDGE_MIN and ev_score < KILL_ZONE_SCORE_MIN:
                reason = f"Kill zone active ({KILL_ZONE_START:02d}:00-{KILL_ZONE_END:02d}:00 UTC) — funding {funding_annualized:.1f}% < {KILL_ZONE_FUNDING_EDGE_MIN}% and score {ev_score:.0f} < {KILL_ZONE_SCORE_MIN}"
                logger.info(f"HybridDecisionEngine: {symbol} BLOCKED — {reason}")
                await self._track_rejected(symbol, direction, reason, ev_score, regime)
                return HybridDecision(
                    action="BLOCK",
                    size="BLOCK",
                    size_pct=SIZE_MAP["BLOCK"],
                    confidence=0,
                    risk_level="HIGH",
                    entry_strategy="WAIT_PULLBACK",
                    stop_loss_strategy="NORMAL",
                    reasoning=reason,
                    guardrails_triggered=["KILL_ZONE"],
                    features_snapshot=features,
                )

        # 3. AI evaluation — fall back to statistical-only on failure
        ai_decision, ai_source = await self._get_ai_decision(
            symbol, features, regime, direction, current_price=current_price,
        )
        ai_timed_out = ai_source == "timeout"

        if ai_timed_out:
            logger.warning(f"HybridDecisionEngine: AI timeout for {symbol}, using statistical fallback")

        # 3b. Post-AI hard guardrails (GR-01 through GR-04)
        post_ai_triggered: list[str] = []

        # GR-01: AI confidence < 60
        if ai_decision is not None:
            post_ai_triggered.extend(self._check_ai_confidence(ai_decision.confidence_score))

        # GR-02 through GR-04
        post_ai_triggered.extend(
            self._check_post_ai_guardrails(
                features=features,
                btc_regime=btc_regime,
                funding_rate=funding_rate,
                direction=direction,
            )
        )

        if post_ai_triggered:
            reason = f"Post-AI guardrails blocked: {', '.join(post_ai_triggered)}"
            logger.info(f"HybridDecisionEngine: {symbol} BLOCKED — {reason}")
            ev_score = features.get("ev_score", 0.0)
            await self._track_rejected(symbol, direction, reason, ev_score, regime)
            return HybridDecision(
                action="BLOCK",
                size="BLOCK",
                size_pct=SIZE_MAP["BLOCK"],
                confidence=0,
                risk_level="HIGH",
                entry_strategy="WAIT_PULLBACK",
                stop_loss_strategy="NORMAL",
                reasoning=reason,
                guardrails_triggered=post_ai_triggered,
                features_snapshot=features,
                ai_decision=ai_decision,
            )

        # 4. Determine action from AI or regime
        action = self._determine_action(ai_decision, regime, direction)

        if action == "BLOCK":
            reason = "AI recommended BLOCK" if ai_decision else "Regime indicates no clear direction"
            logger.info(f"HybridDecisionEngine: {symbol} BLOCKED — {reason}")
            return HybridDecision(
                action="BLOCK",
                size="BLOCK",
                size_pct=SIZE_MAP["BLOCK"],
                confidence=0,
                risk_level="HIGH",
                entry_strategy="WAIT_PULLBACK",
                stop_loss_strategy="NORMAL",
                reasoning=reason,
                features_snapshot=features,
                ai_decision=ai_decision,
            )

        # 5. Determine initial position size from AI
        initial_size = self._get_ai_position_size(ai_decision)

        # 6. Soft guardrails — may downgrade size
        soft_triggered, final_size = self._apply_soft_guardrails(features, initial_size)

        # 7. Compute confidence, risk, entry/SL strategy
        confidence = self._compute_confidence(ai_decision, features)
        risk_level = self._assess_risk_level(features, soft_triggered)
        entry_strategy = self._determine_entry_strategy(features, regime)
        stop_loss_strategy = self._determine_stop_loss(features, regime)

        # 8. Build reasoning
        reasoning = self._build_reasoning(
            action=action,
            size=final_size,
            ai_decision=ai_decision,
            features=features,
            soft_triggered=soft_triggered,
            ai_timed_out=ai_timed_out,
        )

        logger.info(
            f"HybridDecisionEngine: {symbol} {action} {final_size} "
            f"(confidence={confidence}, risk={risk_level})"
        )

        return HybridDecision(
            action=action,
            size=final_size,
            size_pct=SIZE_MAP[final_size],
            confidence=confidence,
            risk_level=risk_level,
            entry_strategy=entry_strategy,
            stop_loss_strategy=stop_loss_strategy,
            reasoning=reasoning,
            guardrails_triggered=soft_triggered,
            features_snapshot=features,
            ai_decision=ai_decision,
        )

    # ------------------------------------------------------------------
    # Feature Vector builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_feature_vector(features: dict, current_price: float) -> FeatureVector:
        """Map statistical engine output to FeatureVector for AI service."""
        close = current_price if current_price > 0 else features.get("close", 0.0)
        return FeatureVector(
            close=close,
            ema_20=features.get("ema_20"),
            ema_200=features.get("ema_200"),
            sma_20=features.get("sma_20"),
            atr=features.get("atr_pct", 0.0),
            atr_pct=features.get("atr_pct", 0.0),
            rsi_14=features.get("rsi_14"),
            adx_14=features.get("adx_14"),
            hurst=features.get("hurst"),
            funding_rate=features.get("funding_rate", 0.0),
            oi_change=features.get("oi_change"),
            orderbook_delta=features.get("orderbook_delta"),
            cvd_slope=features.get("cvd_slope"),
            spread_pct=features.get("spread_pct"),
            market_quality_score=features.get("market_quality_score"),
            candle_quality_score=features.get("candle_quality_score"),
            noise_score=features.get("noise_score"),
            liquidity_score=features.get("liquidity_score"),
        )

    # ------------------------------------------------------------------
    # Hard Guardrails
    # ------------------------------------------------------------------

    def _check_hard_guardrails(
        self,
        features: dict,
        regime: str,
        btc_regime: str,
        concurrent_positions: int,
        existing_correlations: dict[str, float],
    ) -> list[str]:
        """Evaluate all hard guardrails. Returns list of triggered rule IDs."""
        triggered: list[str] = []

        # GR-05: Distance from EMA50 > 10% (Converted to soft guardrail for high-volatility pumper setups)
        # dist_ema50 handled in _apply_soft_guardrails (SG-05) to downgrade size, not hard block

        # GR-06: Concurrent positions >= 5
        if concurrent_positions >= MAX_CONCURRENT_POSITIONS:
            triggered.append("GR-06")

        # GR-07: Correlation with existing > 0.85
        max_corr = max(existing_correlations.values()) if existing_correlations else 0.0
        if max_corr > CORRELATION_HARD_THRESHOLD:
            triggered.append("GR-07")

        # GR-08: ATR > 8% (Converted to soft guardrail for high-volatility pumper setups)
        # atr_pct handled in _apply_soft_guardrails (SG-08) to downgrade size, not hard block

        # GR-09: Market regime = CHOP — sizing reduction, not hard block
        # CHOP is 35-45% of market; blocking it kills half of trading hours
        # Instead, let soft guardrails handle CHOP sizing (QUARTER size)

        # GR-10 is handled in _get_ai_decision (timeout detection)

        return triggered

    def _check_post_ai_guardrails(
        self,
        features: dict,
        btc_regime: str,
        funding_rate: float,
        direction: str,
    ) -> list[str]:
        """Guardrails that require AI output or funding rate context.

        GR-01: AI confidence < 60
        GR-02: BTC downtrend + beta > 1.2
        GR-03: Funding rate > 0.01%/8h (LONG only)
        GR-04: Volume spike < 1.2x on breakout
        """
        triggered: list[str] = []

        # GR-02: BTC downtrend + beta > 1.2
        btc_upper = btc_regime.upper().strip()
        is_btc_downtrend = btc_upper in ("TREND_BEAR", "HYPER_BEAR")
        beta = features.get("beta_30d", 0.0)
        if is_btc_downtrend and beta > BTC_BETA_THRESHOLD:
            triggered.append("GR-02")

        # GR-03: Funding rate > 0.01%/8h for LONG
        if direction.upper() == "LONG" and funding_rate > FUNDING_RATE_LONG_THRESHOLD:
            triggered.append("GR-03")

        # GR-04: Volume spike < 1.2x on breakout
        if features.get("breakout_confirmed", False):
            vol_spike = features.get("volume_spike_ratio", 1.0)
            if vol_spike < BREAKOUT_VOLUME_MIN:
                triggered.append("GR-04")

        return triggered

    def _check_ai_confidence(self, confidence: int) -> list[str]:
        """GR-01: AI confidence < 60."""
        if confidence < AI_CONFIDENCE_MIN:
            return ["GR-01"]
        return []

    # ------------------------------------------------------------------
    # Rejected Signal Tracking
    # ------------------------------------------------------------------

    async def _track_rejected(
        self,
        symbol: str,
        direction: str,
        reason: str,
        ev_score: float,
        regime: str,
    ) -> None:
        """Log rejected signal for calibration analysis.

        Fire-and-forget: failures are logged but never propagate.
        """
        if self._rejected_tracker is None:
            return
        try:
            await self._rejected_tracker.track(
                symbol=symbol,
                direction=direction,
                reject_reason=reason,
                hypothetical_ev=ev_score,
                regime=regime,
            )
        except Exception as exc:
            logger.debug(f"HybridDecisionEngine: rejected tracker error (non-fatal): {exc}")

    # ------------------------------------------------------------------
    # AI Evaluation
    # ------------------------------------------------------------------

    async def _get_ai_decision(
        self,
        symbol: str,
        features: dict,
        regime: str,
        direction: str,
        current_price: float = 0.0,
    ) -> tuple[AIDecisionDTO | None, str]:
        """Call AI service and return (decision, source).

        Source is 'ai' on success, 'timeout' on timeout, 'fallback' on error.
        """
        market_regime = _to_market_regime(regime)
        price = current_price if current_price > 0 else features.get("close", 0.0)
        feature_vector = self._build_feature_vector(features, price)

        context = DecisionContext(
            symbol=symbol,
            regime=market_regime,
            direction=direction,
            features=feature_vector,
        )

        try:
            decision = await self._ai_service.analyze_market(context)
            if decision is not None:
                return decision, "ai"
            return None, "fallback"
        except Exception as exc:
            error_msg = str(exc).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                logger.warning(f"HybridDecisionEngine: AI timeout for {symbol}: {exc}")
                return None, "timeout"
            logger.error(f"HybridDecisionEngine: AI error for {symbol}: {exc}")
            return None, "fallback"

    # ------------------------------------------------------------------
    # Action & Sizing
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_action(
        ai_decision: AIDecisionDTO | None,
        regime: str,
        direction: str,
    ) -> str:
        """Determine trading action from AI decision or regime heuristic.

        RANGE override: AI can override RANGE block only with high confidence (>= 70).
        This prevents low-conviction RANGE entries while allowing strong setups.
        """
        regime_upper = regime.upper().strip()

        if ai_decision is not None:
            if ai_decision.position_size.value == "BLOCK":
                return "BLOCK"

            # RANGE override requires high confidence
            if regime_upper == "RANGE":
                if ai_decision.confidence_score < 70:
                    return "BLOCK"
                logger.debug(
                    f"AI overriding RANGE block with confidence={ai_decision.confidence_score}"
                )

            return direction.upper()

        # No AI — use regime heuristic
        # RANGE & CHOP: allow trading with reduced sizing (soft guardrails handle position sizing)
        return direction.upper()

    @staticmethod
    def _get_ai_position_size(ai_decision: AIDecisionDTO | None) -> str:
        """Map AI position_size enum to engine size string."""
        if ai_decision is None:
            return "QUARTER"
        return ai_decision.position_size.value

    # ------------------------------------------------------------------
    # Soft Guardrails
    # ------------------------------------------------------------------

    def _apply_soft_guardrails(
        self,
        features: dict,
        initial_size: str,
    ) -> tuple[list[str], str]:
        """Apply soft guardrails, downgrading size as needed.

        Returns (list_of_triggered_rules, final_size_string).
        """
        triggered: list[str] = []
        current_idx = DOWNGRADE_ORDER.index(initial_size) if initial_size in DOWNGRADE_ORDER else 1

        # SG-01: Beta > 1.5
        if features.get("beta_30d", 0.0) > BETA_SOFT_THRESHOLD:
            triggered.append("SG-01")

        # SG-02: Extreme correlation > 0.95 (altcoins naturally correlate with BTC 0.8-0.9)
        if features.get("correlation_24h", 0.0) > 0.95:
            triggered.append("SG-02")

        # SG-03: BTC sideways
        btc_corr = features.get("correlation_24h", 0.0)
        # Heuristic: low BTC correlation in a non-trending market suggests sideways
        regime_upper = features.get("regime", "RANGE").upper()
        if regime_upper in BTC_SIDEWAYS_REGIMES and abs(btc_corr) < 0.3:
            triggered.append("SG-03")

        # SG-04: Volume spike 1.2-1.5x
        vol_spike = features.get("volume_spike_ratio", 1.0)
        if VOLUME_SPIKE_SOFT_LOW <= vol_spike < VOLUME_SPIKE_SOFT_HIGH:
            triggered.append("SG-04")

        # SG-05: Distance from EMA50 5-10%
        dist_ema50 = abs(features.get("distance_from_ema50_pct", 0.0))
        if EMA50_SOFT_LOW_PCT <= dist_ema50 <= EMA50_SOFT_HIGH_PCT:
            triggered.append("SG-05")

        # Apply downgrades (each triggered rule = one downgrade level)
        final_idx = min(current_idx + len(triggered), len(DOWNGRADE_ORDER) - 1)
        final_size = DOWNGRADE_ORDER[final_idx]

        return triggered, final_size

    # ------------------------------------------------------------------
    # Confidence & Risk
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(
        ai_decision: AIDecisionDTO | None,
        features: dict,
    ) -> int:
        """Compute blended confidence score (0-100).

        Combines AI confidence with statistical signal quality.
        If no AI, uses statistical-only scoring.
        """
        if ai_decision is not None:
            ai_conf = ai_decision.confidence_score
            # Statistical bonus: volume confirmation, overextension discount
            stat_bonus = 0
            if features.get("volume_confirmed", False):
                stat_bonus += 5
            if features.get("overextended", False):
                stat_bonus -= 10
            # Blend: 70% AI, 30% statistical
            blended = int(ai_conf * 0.7 + (50 + stat_bonus) * 0.3)
            return max(0, min(100, blended))

        # Statistical-only confidence
        score = 50
        if features.get("volume_confirmed", False):
            score += 10
        if features.get("breakout_confirmed", False):
            score += 15
        if features.get("overextended", False):
            score -= 15
        atr_pct = features.get("atr_pct", 0.0)
        if atr_pct > ATR_EXTREME_PCT:
            score -= 10
        return max(0, min(100, score))

    @staticmethod
    def _assess_risk_level(features: dict, soft_triggered: list[str]) -> str:
        """Assess overall risk level from features and triggered guardrails."""
        risk_score = 0

        atr_pct = features.get("atr_pct", 0.0)
        if atr_pct > ATR_EXTREME_PCT:
            risk_score += 3
        elif atr_pct > 5.0:
            risk_score += 2
        elif atr_pct > 3.0:
            risk_score += 1

        beta = abs(features.get("beta_30d", 1.0))
        if beta > 2.0:
            risk_score += 2
        elif beta > 1.5:
            risk_score += 1

        risk_score += len(soft_triggered)

        if risk_score >= 4:
            return "HIGH"
        if risk_score >= 2:
            return "MEDIUM"
        return "LOW"

    # ------------------------------------------------------------------
    # Entry / Stop-Loss Strategy
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_entry_strategy(features: dict, regime: str) -> str:
        """Determine entry strategy based on features and regime."""
        if features.get("breakout_confirmed", False):
            return "MARKET"

        regime_upper = regime.upper().strip()
        if regime_upper == "RANGE":
            return "LIMIT_RETEST"

        dist_ema50 = abs(features.get("distance_from_ema50_pct", 0.0))
        if dist_ema50 > 5.0:
            return "WAIT_PULLBACK"

        return "MARKET"

    @staticmethod
    def _determine_stop_loss(features: dict, regime: str) -> str:
        """Determine stop-loss strategy based on volatility and regime."""
        atr_pct = features.get("atr_pct", 0.0)

        if regime.upper().strip() == "CHOP" or atr_pct > ATR_EXTREME_PCT:
            return "TIGHT"

        volatility_regime = features.get("volatility_regime", "MEDIUM")
        if volatility_regime == "HIGH":
            return "WIDE"

        return "NORMAL"

    # ------------------------------------------------------------------
    # Reasoning Builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_reasoning(
        action: str,
        size: str,
        ai_decision: AIDecisionDTO | None,
        features: dict,
        soft_triggered: list[str],
        ai_timed_out: bool,
    ) -> str:
        """Build human-readable reasoning chain."""
        parts: list[str] = []

        # AI contribution
        if ai_timed_out:
            parts.append("AI timed out — using statistical-only analysis")
        elif ai_decision is not None:
            parts.append(
                f"AI recommends {ai_decision.position_size.value} "
                f"(confidence={ai_decision.confidence_score}%): {ai_decision.reasoning}"
            )
        else:
            parts.append("No AI decision available — statistical-only")

        # Key statistical signals
        beta = features.get("beta_30d", 0.0)
        corr = features.get("correlation_24h", 0.0)
        atr_pct = features.get("atr_pct", 0.0)
        vol_spike = features.get("volume_spike_ratio", 1.0)
        dist_ema50 = features.get("distance_from_ema50_pct", 0.0)

        stats = (
            f"Stats: beta={beta:.2f}, corr_24h={corr:.2f}, "
            f"atr={atr_pct:.1f}%, vol_spike={vol_spike:.1f}x, "
            f"ema50_dist={dist_ema50:.1f}%"
        )
        parts.append(stats)

        # Soft guardrail impact
        if soft_triggered:
            parts.append(f"Soft guardrails applied: {', '.join(soft_triggered)} → size {size}")
        else:
            parts.append(f"No soft guardrails triggered → size {size}")

        return " | ".join(parts)

"""EV Composite Scorer — replaces 25 binary filters with single expected value score.

Phase 1 (Filter Collapse): Every signal factor contributes to a weighted EV score.
Binary kill-chains are replaced by soft scoring components. Only system-safety
filters (spoofing, stale data, circuit breaker, portfolio risk) remain as hard gates.

Architecture:
    EVComponents (dataclass) → EVScorer.score() → float (0.0 to 1.0+)
    Session quality is a multiplier, not a component.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Weights calibrated from backtest (Phase 4 will auto-calibrate via logistic regression)
WEIGHTS = {
    "regime_alignment": 0.20,
    "momentum_strength": 0.20,
    "microstructure": 0.15,
    "funding_edge": 0.10,
    "spread_quality": 0.10,
    "multi_tf_alignment": 0.10,
    "historical_edge": 0.08,
    "conviction": 0.05,
    "oi_signal": 0.02,
}

# Session quality multipliers (not components — multiplied on top of base EV)
SESSION_MULTIPLIERS = {
    "LDN_NY_OVERLAP": 1.20,  # 12:00-16:00 UTC — best liquidity
    "LDN": 1.10,             # 07:00-12:00 UTC
    "NY": 1.10,              # 13:00-21:00 UTC
    "ASIA": 0.70,            # 00:00-07:00 UTC — thin liquidity
    "PACIFIC": 0.60,         # 21:00-00:00 UTC — dead zone
    "DEFAULT": 1.00,
}

# Regime alignment scores: how well does direction match regime?
_REGIME_ALIGN = {
    "TREND_BULL": {"LONG": 1.0, "SHORT": 0.3},
    "TREND_BEAR": {"LONG": 0.3, "SHORT": 1.0},
    "HYPER_BULL": {"LONG": 0.9, "SHORT": 0.2},
    "HYPER_BEAR": {"LONG": 0.2, "SHORT": 0.9},
    "RANGE": {"LONG": 0.6, "SHORT": 0.6},
    "CHOP": {"LONG": 0.5, "SHORT": 0.5},
    "MEAN_REVERSION": {"LONG": 0.7, "SHORT": 0.7},
    "TRANSITION_BULL": {"LONG": 1.0, "SHORT": 0.1},
    "TRANSITION_BEAR": {"LONG": 0.1, "SHORT": 1.0},
}


def _get_session_label(hour_utc: int) -> str:
    """Map UTC hour to session label."""
    if 12 <= hour_utc < 16:
        return "LDN_NY_OVERLAP"
    if 7 <= hour_utc < 13:
        return "LDN"
    if 13 <= hour_utc < 21:
        return "NY"
    if 0 <= hour_utc < 7:
        return "ASIA"
    return "PACIFIC"


@dataclass
class EVComponents:
    """All factors contributing to expected value scoring.

    Each component is 0.0-1.0 unless noted otherwise.
    """

    regime_alignment: float       # How well direction matches regime
    spread_quality: float         # Spread relative to regime norms (soft penalty)
    momentum_strength: float      # RSI + MACD + EMA confluence
    microstructure: float         # Orderbook skew + depth + CVD
    funding_edge: float           # Carry profit/cost (-0.5 to +0.5)
    multi_tf_alignment: float     # 4H trend alignment (soft penalty)
    historical_edge: float        # Symbol's historical win rate
    conviction: float             # Regime classifier confidence
    oi_signal: float              # Open interest change signal
    session_quality: float        # Time-of-day multiplier


class EVScorer:
    """Replaces 25 binary filters with a single composite EV score.

    Usage:
        scorer = EVScorer()
        ev = scorer.score(
            regime="TREND_BULL", direction="LONG",
            spread_pct=0.001, rsi=55.0, macd_hist=0.02,
            ema_dist=0.003, skew=0.4, cvd_slope=0.1,
            funding_rate=-0.0003, multi_tf_agrees=True,
            historical_win_rate=0.55, regime_conviction=0.75,
            oi_change_pct=0.02, hour_utc=14,
        )
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        redis_client: object | None = None,
    ) -> None:
        self._weights = weights or WEIGHTS.copy()
        self._redis = redis_client
        self._optimized_cache: dict[str, dict[str, float]] = {}

    async def get_weights_for_regime(self, regime: str | None) -> dict[str, float]:
        """Get weights for a regime, checking Redis for optimized values."""
        if not regime or self._redis is None:
            return self._weights

        # Check cache first
        if regime in self._optimized_cache:
            return self._optimized_cache[regime]

        # Try to load from Redis
        try:
            import json
            raw = await self._redis.get(f"karsa:ev_weights:{regime}")  # type: ignore[attr-defined]
            if raw:
                data = raw.decode() if isinstance(raw, bytes) else str(raw)
                optimal = json.loads(data)
                weights = optimal.get("weights", self._weights)
                self._optimized_cache[regime] = weights
                logger.debug("Loaded optimized weights for %s from Redis", regime)
                return weights
        except Exception as e:
            logger.debug("Failed to load optimized weights for %s: %s", regime, e)

        return self._weights

    def score(
        self,
        regime: str | None,
        direction: str,
        # Spread
        spread_pct: float = 0.001,
        # Momentum
        rsi: float = 50.0,
        macd_hist: float = 0.0,
        ema_dist: float = 0.0,
        # Microstructure
        skew: float = 0.0,
        cvd_slope: float = 0.0,
        depth_ratio: float = 1.0,
        # Funding
        funding_rate: float = 0.0,
        # Multi-TF
        multi_tf_agrees: bool = True,
        # Historical
        historical_win_rate: float = 0.5,
        # Regime classifier
        regime_conviction: float = 0.5,
        # OI
        oi_change_pct: float = 0.0,
        # Session
        hour_utc: int | None = None,
    ) -> tuple[float, EVComponents]:
        """Compute composite EV score with session multiplier.

        Returns:
            (ev_score, components) — score is 0.0 to ~1.2 (session can boost above 1.0)
        """
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        components = EVComponents(
            regime_alignment=self._score_regime(regime, direction),
            spread_quality=self._score_spread(spread_pct, regime),
            momentum_strength=self._score_momentum(rsi, macd_hist, ema_dist),
            microstructure=self._score_microstructure(skew, cvd_slope, depth_ratio),
            funding_edge=self._score_funding(funding_rate, direction),
            multi_tf_alignment=1.0 if multi_tf_agrees else 0.5,
            historical_edge=max(0.0, min(1.5, historical_win_rate * 1.5)),
            conviction=max(0.0, min(1.0, regime_conviction)),
            oi_signal=self._score_oi(oi_change_pct),
            session_quality=self._score_session(hour_utc),
        )

        # Weighted sum of components — use regime-specific weights if available
        weights = self._weights
        if regime and regime in self._optimized_cache:
            weights = self._optimized_cache[regime]
        base_ev = sum(
            weights[k] * getattr(components, k)
            for k in weights
        )

        # Session quality is a multiplier, not a component
        final_ev = base_ev * components.session_quality

        logger.debug(
            "EVScorer: %s %s ev=%.3f base=%.3f session=%.2f regime_align=%.2f",
            direction, regime or "UNKNOWN", final_ev, base_ev,
            components.session_quality, components.regime_alignment,
        )

        return round(final_ev, 4), components

    def apply_gas_adjustment(
        self,
        raw_ev: float,
        gas_cost_usd: float,
        trade_notional_usd: float = 1000.0,
        venue: str = "DEX",
    ) -> float:
        """Deduct projected EVM gas costs from raw EV score for DEX venues.
        
        CEX venues (Bybit, Hyperliquid) incur no EVM gas adjustment.
        """
        if venue != "DEX" or trade_notional_usd <= 0:
            return raw_ev

        gas_bps = (gas_cost_usd / trade_notional_usd) * 10000.0
        # 100 bps spread penalty reduces EV score by 0.10
        ev_penalty = gas_bps / 1000.0
        adjusted_ev = max(0.0, raw_ev - ev_penalty)
        return round(adjusted_ev, 4)

    # ─── Component Scorers ──────────────────────────────────────────

    def _score_regime(self, regime: str | None, direction: str) -> float:
        """Score regime alignment (0.0-1.0)."""
        if not regime:
            return 0.5
        align = _REGIME_ALIGN.get(regime, {})
        return align.get(direction, 0.5)

    def _score_spread(self, spread_pct: float, regime: str | None) -> float:
        """Soft penalty for wide spreads (0.0-1.0).

        Wider spread = lower score. Regime-dependent: HYPER allows wider.
        """
        # Regime-adjusted reference spread
        ref = {
            "HYPER_BULL": 0.005, "HYPER_BEAR": 0.005,
            "CHOP": 0.003,
            "TREND_BULL": 0.001, "TREND_BEAR": 0.001,
            "RANGE": 0.0015,
        }.get(regime or "", 0.002)

        if spread_pct <= 0:
            return 1.0
        # Ratio of reference to actual: tight spread = high score
        ratio = ref / max(spread_pct, 0.0001)
        return max(0.0, min(1.0, ratio))

    def _score_momentum(self, rsi: float, macd_hist: float, ema_dist: float) -> float:
        """Score momentum confluence (0.0-1.0).

        RSI: 45-65 is neutral zone, >65 bullish, <35 bearish.
        MACD: positive = bullish momentum.
        EMA: positive = above EMA = bullish.
        """
        # RSI component (neutral at 50, extremes are overbought/oversold)
        rsi_score = 0.5
        if 45 <= rsi <= 65:
            rsi_score = 0.6  # neutral = healthy trend
        elif rsi > 65:
            rsi_score = min(1.0, 0.5 + (rsi - 65) / 70)  # bullish momentum
        elif rsi < 35:
            rsi_score = max(0.0, 0.5 - (35 - rsi) / 70)  # bearish momentum
        elif 35 < rsi < 45:
            rsi_score = 0.4  # slight bearish lean

        # MACD component
        macd_score = max(0.0, min(1.0, 0.5 + macd_hist * 10))

        # EMA distance component
        ema_score = max(0.0, min(1.0, 0.5 + ema_dist * 50))

        return (rsi_score + macd_score + ema_score) / 3.0

    def _score_microstructure(
        self, skew: float, cvd_slope: float, depth_ratio: float
    ) -> float:
        """Score orderbook microstructure (0.0-1.0).

        Skew: positive = more bids = bullish.
        CVD slope: positive = more buying = bullish.
        Depth ratio: ~1.0 = balanced = healthy.
        """
        # Skew: normalized to [-1, 1], positive is bullish
        skew_score = max(0.0, min(1.0, 0.5 + skew * 0.5))

        # CVD slope: normalized
        cvd_score = max(0.0, min(1.0, 0.5 + cvd_slope * 2))

        # Depth ratio: 1.0 = balanced, deviations reduce score
        depth_score = max(0.0, 1.0 - abs(depth_ratio - 1.0))

        return (skew_score + cvd_score + depth_score) / 3.0

    def _score_funding(self, funding_rate: float, direction: str) -> float:
        """Score funding edge (-0.5 to +0.5).

        Negative funding on LONG = carry income (positive edge).
        Positive funding on SHORT = carry income.
        """
        if direction == "LONG":
            # Negative funding = we get paid to hold long
            return max(-0.5, min(0.5, -funding_rate * 1000))
        else:
            # Positive funding = we get paid to hold short
            return max(-0.5, min(0.5, funding_rate * 1000))

    def _score_oi(self, oi_change_pct: float) -> float:
        """Score open interest change (0.0-1.0).

        Rising OI + direction = conviction. Dead band at ±0.1%.
        """
        DEAD_BAND = 0.001
        if abs(oi_change_pct) < DEAD_BAND:
            return 0.5  # neutral
        if oi_change_pct > 0:
            return min(1.0, 0.5 + oi_change_pct * 5)
        else:
            return max(0.0, 0.5 + oi_change_pct * 5)

    def _score_session(self, hour_utc: int) -> float:
        """Session quality multiplier (0.5-1.2)."""
        label = _get_session_label(hour_utc)
        return SESSION_MULTIPLIERS.get(label, 1.0)


# ─── Convenience ──────────────────────────────────────────────────

_default_scorer = EVScorer()


def quick_ev_score(
    regime: str | None,
    direction: str,
    spread_pct: float = 0.001,
    rsi: float = 50.0,
    skew: float = 0.0,
    funding_rate: float = 0.0,
    multi_tf_agrees: bool = True,
    regime_conviction: float = 0.5,
    hour_utc: int | None = None,
) -> float:
    """Quick EV score with sensible defaults. Returns just the float."""
    ev, _ = _default_scorer.score(
        regime=regime, direction=direction, spread_pct=spread_pct,
        rsi=rsi, skew=skew, funding_rate=funding_rate,
        multi_tf_agrees=multi_tf_agrees, regime_conviction=regime_conviction,
        hour_utc=hour_utc,
    )
    return ev

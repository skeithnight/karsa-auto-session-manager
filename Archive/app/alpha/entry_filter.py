"""Entry Filter — pre-entry quality checklist before risk gate.

Checks: spread (regime-dependent), book depth, time-of-day, existing position.
Regime gating delegated to StrategyRouter (Phase 6 adaptive multi-strategy).

Phase 6.1: Regime-dependent spread limits. CHOP allows wider spread because
liquidity sweeps naturally widen the book during micro-structure events.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

# Regime-dependent spread limits (max spread_pct allowed)
REGIME_SPREAD_LIMITS: dict[str, float] = {
    "HYPER_BULL": 0.005,  # 0.50% — wide limit for massive momentum, reliant on SOR slippage cap
    "HYPER_BEAR": 0.005,
    "TREND_BULL": 0.001,  # 0.10% — tight for clean breakouts
    "TREND_BEAR": 0.001,  # 0.10% — tight for clean breakouts
    "RANGE": 0.0015,  # 0.15% — standard for mean-reversion
    "CHOP": 0.003,  # 0.30% — relaxed for liquidity sweep events
    "MEAN_REVERSION": 0.0015,
}


class EntryFilter:
    """Pre-entry gate between signal generation and risk gate.

    Returns (passed: bool, reason: str).

    Phase 6: CHOP regime no longer hard-blocked here. StrategyRouter
    scores CHOP signals (micro-scalp) and gates at 65+ threshold.
    Phase 6.1: Spread limits are regime-dependent.
    """

    def __init__(
        self,
        max_spread_pct: float = 0.003,
        min_depth_ratio: float = 0.7,
        max_depth_ratio: float = 1.4,
        blocked_hour_start: int = 3,
        blocked_hour_end: int = 5,
        min_atr: float = 0.0,
        max_atr: float = float("inf"),
    ) -> None:
        logger.debug("EntryFilter.__init__: entering")
        self.max_spread_pct = max_spread_pct
        self.min_depth_ratio = min_depth_ratio
        self.max_depth_ratio = max_depth_ratio
        self.blocked_hour_start = blocked_hour_start
        self.blocked_hour_end = blocked_hour_end
        self.min_atr = min_atr
        self.max_atr = max_atr
        logger.debug("EntryFilter.__init__: returning")

    def check(
        self,
        regime: str | None = None,
        spread_pct: float | None = None,
        bid_depth: float | None = None,
        ask_depth: float | None = None,
        has_position: bool = False,
        now_utc: datetime | None = None,
        atr: float | None = None,
        direction: str | None = None,
        is_spoofing_bid: bool = False,
        is_spoofing_ask: bool = False,
    ) -> tuple[bool, str]:
        """Run all entry checks. Returns (passed, reason).

        Args:
            regime: current market regime (CHOP allowed — StrategyRouter gates)
            spread_pct: (ask - bid) / mid price
            bid_depth: total bid volume near top of book
            ask_depth: total ask volume near top of book
            has_position: whether we already hold this symbol
            now_utc: current time (injectable for testing)
            direction: LONG or SHORT signal direction
            is_spoofing_bid: whether fake bid support was detected
            is_spoofing_ask: whether fake ask resistance was detected
        """
        logger.debug("check: entering")

        # 0. Microstructure Spoofing Hard Rejection
        if direction in ("LONG", "buy") and is_spoofing_bid:
            logger.warning("EntryFilter: rejecting LONG signal due to bid spoofing (fake support)")
            return False, "spoofing detected on bid side"
        if direction in ("SHORT", "sell") and is_spoofing_ask:
            logger.warning("EntryFilter: rejecting SHORT signal due to ask spoofing (fake resistance)")
            return False, "spoofing detected on ask side"

        # 1. Spread check (regime-dependent)
        effective_spread_limit = REGIME_SPREAD_LIMITS.get(
            regime or "", self.max_spread_pct
        )
        if spread_pct is not None and spread_pct > effective_spread_limit:
            logger.debug(
                f"check: returning False (spread {spread_pct:.4f} > {effective_spread_limit} [{regime}])"
            )
            return (
                False,
                f"spread {spread_pct:.4f} > {effective_spread_limit} [{regime}]",
            )

        # 3. ATR volatility filter (skip dead or chaotic markets)
        if atr is not None:
            if atr < self.min_atr:
                logger.debug(f"check: returning False (ATR {atr:.6f} < {self.min_atr})")
                return False, f"ATR {atr:.6f} below minimum"
            if atr > self.max_atr:
                logger.debug(f"check: returning False (ATR {atr:.6f} > {self.max_atr})")
                return False, f"ATR {atr:.6f} above maximum"

        # 4. Book depth ratio
        if bid_depth is not None and ask_depth is not None:
            if bid_depth > 0:
                ratio = ask_depth / bid_depth
            else:
                ratio = float("inf")
            if ratio < self.min_depth_ratio or ratio > self.max_depth_ratio:
                logger.debug(
                    f"check: returning False (depth ratio {ratio:.2f} out of [{self.min_depth_ratio}, {self.max_depth_ratio}])"
                )
                return False, f"depth ratio {ratio:.2f} out of range"

        # 4. Time-of-day (00:00–06:00 UTC blocked — dead Asian session)
        t = now_utc or datetime.now(UTC)
        if self.blocked_hour_start <= t.hour < self.blocked_hour_end:
            logger.debug(f"check: returning False (blocked hour {t.hour})")
            return False, f"blocked hour {t.hour}:00 UTC"

        # 5. Existing position
        if has_position:
            logger.debug("check: returning False (existing position)")
            return False, "existing position"

        logger.debug("check: returning True")
        return True, "passed"


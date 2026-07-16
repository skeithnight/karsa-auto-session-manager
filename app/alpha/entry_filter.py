"""Entry Filter — pre-entry quality checklist before risk gate.

Checks: spread, book depth, time-of-day, existing position.
Regime gating delegated to StrategyRouter (Phase 6 adaptive multi-strategy).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from loguru import logger


class EntryFilter:
    """Pre-entry gate between signal generation and risk gate.

    Returns (passed: bool, reason: str).

    Phase 6: CHOP regime no longer hard-blocked here. StrategyRouter
    scores CHOP signals (micro-scalp) and gates at 65+ threshold.
    """

    def __init__(
        self,
        max_spread_pct: float = 0.003,
        min_depth_ratio: float = 0.7,
        max_depth_ratio: float = 1.4,
        blocked_hour_start: int = 0,
        blocked_hour_end: int = 6,
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
        regime: Optional[str] = None,
        spread_pct: Optional[float] = None,
        bid_depth: Optional[float] = None,
        ask_depth: Optional[float] = None,
        has_position: bool = False,
        now_utc: Optional[datetime] = None,
        atr: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Run all entry checks. Returns (passed, reason).

        Args:
            regime: current market regime (CHOP allowed — StrategyRouter gates)
            spread_pct: (ask - bid) / mid price
            bid_depth: total bid volume near top of book
            ask_depth: total ask volume near top of book
            has_position: whether we already hold this symbol
            now_utc: current time (injectable for testing)
        """
        logger.debug("check: entering")

        # Regime check removed — StrategyRouter handles regime-specific gating.
        # CHOP signals now scored by micro-scalp strategy (funding + liquidity sweep).

        # 1. Spread check
        if spread_pct is not None and spread_pct > self.max_spread_pct:
            logger.debug(
                f"check: returning False (spread {spread_pct:.4f} > {self.max_spread_pct})"
            )
            return False, f"spread {spread_pct:.4f} > {self.max_spread_pct}"

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
        t = now_utc or datetime.now(timezone.utc)
        if self.blocked_hour_start <= t.hour < self.blocked_hour_end:
            logger.debug(f"check: returning False (blocked hour {t.hour})")
            return False, f"blocked hour {t.hour}:00 UTC"

        # 5. Existing position
        if has_position:
            logger.debug("check: returning False (existing position)")
            return False, "existing position"

        logger.debug("check: returning True")
        return True, "passed"

"""Entry Filter — pre-entry quality checklist before risk gate.

Checks: regime, spread, book depth, time-of-day, existing position.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from loguru import logger

from app.alpha.regime import REGIME_CHOP


class EntryFilter:
    """Pre-entry gate between signal generation and risk gate.

    Returns (passed: bool, reason: str).
    """

    def __init__(
        self,
        max_spread_pct: float = 0.003,
        min_depth_ratio: float = 0.7,
        max_depth_ratio: float = 1.4,
        blocked_hour_start: int = 0,
        blocked_hour_end: int = 1,
    ) -> None:
        logger.debug("EntryFilter.__init__: entering")
        self.max_spread_pct = max_spread_pct
        self.min_depth_ratio = min_depth_ratio
        self.max_depth_ratio = max_depth_ratio
        self.blocked_hour_start = blocked_hour_start
        self.blocked_hour_end = blocked_hour_end
        logger.debug("EntryFilter.__init__: returning")

    def check(
        self,
        regime: Optional[str] = None,
        spread_pct: Optional[float] = None,
        bid_depth: Optional[float] = None,
        ask_depth: Optional[float] = None,
        has_position: bool = False,
        now_utc: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Run all entry checks. Returns (passed, reason).

        Args:
            regime: current market regime
            spread_pct: (ask - bid) / mid price
            bid_depth: total bid volume near top of book
            ask_depth: total ask volume near top of book
            has_position: whether we already hold this symbol
            now_utc: current time (injectable for testing)
        """
        logger.debug("check: entering")

        # 1. Regime check
        if regime == REGIME_CHOP:
            logger.debug("check: returning False (CHOP regime)")
            return False, "CHOP regime"

        # 2. Spread check
        if spread_pct is not None and spread_pct > self.max_spread_pct:
            logger.debug(f"check: returning False (spread {spread_pct:.4f} > {self.max_spread_pct})")
            return False, f"spread {spread_pct:.4f} > {self.max_spread_pct}"

        # 3. Book depth ratio
        if bid_depth is not None and ask_depth is not None:
            if bid_depth > 0:
                ratio = ask_depth / bid_depth
            else:
                ratio = float("inf")
            if ratio < self.min_depth_ratio or ratio > self.max_depth_ratio:
                logger.debug(f"check: returning False (depth ratio {ratio:.2f} out of [{self.min_depth_ratio}, {self.max_depth_ratio}])")
                return False, f"depth ratio {ratio:.2f} out of range"

        # 4. Time-of-day (00:00–01:00 UTC blocked)
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

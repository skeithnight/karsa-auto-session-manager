"""Smart Cooldown — condition-based re-entry instead of flat timer.

Phase 6 (Execution Velocity): Replaces 45-min flat cooldown with regime/skew-aware
re-entry logic. Minimum 5 minutes, maximum 20 minutes, shorter if regime changed.
"""

from __future__ import annotations

import time
import logging
from typing import Any

from loguru import logger

# Regime-dependent cooldowns (seconds)
REGIME_COOLDOWNS = {
    "CHOP": 10,         # Fast regime, fast cooldown
    "RANGE": 30,        # Medium regime
    "TREND_BULL": 60,   # Slow regime, avoid over-trading the trend
    "TREND_BEAR": 60,
    "HYPER_BULL": 5,    # Extreme moves — every second matters
    "HYPER_BEAR": 5,
    "TRANSITION_BULL": 15,  # Breakout — need to re-enter quickly if first failed
    "TRANSITION_BEAR": 15,
}

MIN_COOLDOWN = 300      # 5 minutes minimum
MAX_COOLDOWN = 1200     # 20 minutes maximum


class SmartCooldown:
    """Condition-based cooldown replacing flat 45-min timer.

    Usage:
        cooldown = SmartCooldown()
        if cooldown.should_block(symbol, last_loss_time, current_regime, loss_regime):
            return  # Still cooling
        # Ready to trade
    """

    def __init__(self) -> None:
        self._loss_regimes: dict[str, str] = {}  # symbol -> regime at time of loss
        self._loss_skews: dict[str, float] = {}  # symbol -> skew at time of loss

    def record_loss(
        self, symbol: str, regime: str, skew: float = 0.0
    ) -> None:
        """Record loss context for condition-based cooldown."""
        self._loss_regimes[symbol] = regime
        self._loss_skews[symbol] = skew

    def should_block(
        self,
        symbol: str,
        last_loss_time: float,
        current_regime: str,
        current_skew: float = 0.0,
    ) -> bool:
        """Check if re-entry should be blocked.

        Args:
            symbol: Trading pair.
            last_loss_time: Time of last loss (time.time() format).
            current_regime: Current market regime.
            current_skew: Current orderbook skew.

        Returns:
            True if should block (still cooling), False if re-entry allowed.
        """
        elapsed = time.time() - last_loss_time

        # Minimum cooldown: 5 minutes (let the dust settle)
        if elapsed < MIN_COOLDOWN:
            return True

        # Hard cap: 20 minutes max cooldown (not 45)
        if elapsed > MAX_COOLDOWN:
            return False

        # If regime has changed since the loss, allow re-entry
        loss_regime = self._loss_regimes.get(symbol, "")
        if current_regime != loss_regime:
            logger.debug(
                "SmartCooldown: %s regime changed %s → %s, allowing re-entry",
                symbol, loss_regime, current_regime,
            )
            return False

        # If skew has flipped direction, the setup is different
        loss_skew = self._loss_skews.get(symbol, 0.0)
        if (current_skew > 0 and loss_skew < 0) or (current_skew < 0 and loss_skew > 0):
            logger.debug(
                "SmartCooldown: %s skew flipped %.2f → %.2f, allowing re-entry",
                symbol, loss_skew, current_skew,
            )
            return False

        # Same regime, same direction, < 20 min → still cooling
        return True

    def get_cooldown_for_regime(self, regime: str) -> int:
        """Get cooldown duration for a regime (for logging/display)."""
        return REGIME_COOLDOWNS.get(regime, 30)

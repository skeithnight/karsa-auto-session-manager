"""Circuit Breaker — hard stop on drawdown breach, loss tracker, latency monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from loguru import logger


class CircuitBreaker:
    """Global circuit breaker — flatten all + halt on breach."""

    def __init__(
        self,
        daily_drawdown_limit: Decimal = Decimal("-0.02"),
        max_consecutive_losses: int = 3,
        loss_pause_minutes: int = 60,
        max_latency_ms: int = 1500,
    ) -> None:
        logger.debug("CircuitBreaker.__init__: entering")
        self.daily_drawdown_limit = daily_drawdown_limit
        self.max_consecutive_losses = max_consecutive_losses
        self.loss_pause_minutes = loss_pause_minutes
        self.max_latency_ms = max_latency_ms

        self.daily_pnl = Decimal("0")
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reason: Optional[str] = None
        self.paused_until: Optional[datetime] = None
        self.on_halt: Optional[Callable] = None  # Callback for halt sequence
        logger.debug("CircuitBreaker.__init__: returning")

    def update_pnl(self, pnl: Decimal) -> bool:
        """
        Update daily PnL. Returns True if circuit breaker triggered.
        """
        logger.debug(f"update_pnl: entering pnl={pnl}")
        self.daily_pnl += pnl

        if self.daily_pnl <= self.daily_drawdown_limit:
            self.halted = True
            self.halt_reason = f"Drawdown breached: {self.daily_pnl} (limit: {self.daily_drawdown_limit})"
            logger.critical(self.halt_reason)
            logger.debug("update_pnl: returning True (halted)")
            return True

        logger.debug("update_pnl: returning False")
        return False

    def record_loss(self) -> bool:
        """
        Record a consecutive loss. Returns True if max losses reached.
        """
        logger.debug("record_loss: entering")
        self.consecutive_losses += 1

        if self.consecutive_losses >= self.max_consecutive_losses:
            from datetime import timedelta
            self.paused_until = datetime.now(timezone.utc) + timedelta(minutes=self.loss_pause_minutes)
            logger.warning(
                f"Max consecutive losses ({self.max_consecutive_losses}) — "
                f"paused until {self.paused_until}"
            )
            logger.debug("record_loss: returning True (max losses)")
            return True

        logger.debug("record_loss: returning False")
        return False

    def record_win(self) -> None:
        """Reset consecutive loss counter on win."""
        logger.debug("record_win: entering")
        self.consecutive_losses = 0
        logger.debug("record_win: returning None")

    def check_latency(self, latency_ms: int) -> bool:
        """
        Check execution latency. Returns True if too slow.
        """
        logger.debug(f"check_latency: entering latency_ms={latency_ms}")
        if latency_ms > self.max_latency_ms:
            logger.warning(f"High latency: {latency_ms}ms > {self.max_latency_ms}ms")
            logger.debug("check_latency: returning True (high latency)")
            return True
        logger.debug("check_latency: returning False")
        return False

    def is_paused(self) -> bool:
        """Check if currently paused from consecutive losses."""
        logger.debug("is_paused: entering")
        if self.paused_until is None:
            logger.debug("is_paused: returning False (no pause)")
            return False

        if datetime.now(timezone.utc) >= self.paused_until:
            self.paused_until = None
            logger.debug("is_paused: returning False (pause expired)")
            return False

        logger.debug("is_paused: returning True")
        return True

    def is_halted(self) -> bool:
        """Check if globally halted."""
        logger.debug(f"is_halted: entering returning {self.halted}")
        return self.halted

    def reset(self) -> None:
        """Reset all circuit breaker state."""
        logger.debug("reset: entering")
        self.daily_pnl = Decimal("0")
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reason = None
        self.paused_until = None
        logger.info("Circuit breaker reset")
        logger.debug("reset: returning None")

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state."""
        logger.debug("get_state: entering")
        result = {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "daily_pnl": str(self.daily_pnl),
            "consecutive_losses": self.consecutive_losses,
            "paused_until": self.paused_until.isoformat() if self.paused_until else None,
        }
        logger.debug("get_state: returning dict")
        return result

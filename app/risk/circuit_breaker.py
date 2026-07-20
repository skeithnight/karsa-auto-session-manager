"""Circuit Breaker — hard stop on drawdown breach, loss tracker.

Responsibilities:
- Daily drawdown limit (% and absolute USD cap)
- Consecutive loss pause
- Redis-persisted state survives restarts
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core import metrics


class CircuitBreaker:
    """Global circuit breaker — flatten all + halt on breach."""

    def __init__(
        self,
        daily_drawdown_limit: Decimal = Decimal("-0.02"),
        max_consecutive_losses: int = 3,
        loss_pause_minutes: int = 60,
        max_daily_loss_usd: Decimal | None = None,
        alert_service: Any | None = None,
        redis_client: Any | None = None,
    ) -> None:
        """Callers: main.py.
        max_daily_loss_usd: absolute USD loss cap (optional).
        alert_service: Telegram push on halt.
        redis_client: optional persistence for restart recovery."""
        self.daily_drawdown_limit = daily_drawdown_limit
        self.max_consecutive_losses = max_consecutive_losses
        self.loss_pause_minutes = loss_pause_minutes
        self.max_daily_loss_usd = max_daily_loss_usd
        self.alert_service = alert_service
        self.redis = redis_client

        self.daily_pnl = Decimal("0")
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reason: str | None = None
        self.paused_until: datetime | None = None
        self.on_halt: Callable | None = None  # Callback for halt sequence

        metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="drawdown").set(0)
        metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="usd_loss_cap").set(0)
        metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="consecutive_losses").set(0)

    async def restore(self) -> None:
        """Restore state from Redis on startup. No-op if no redis or no saved state."""
        if not self.redis:
            return
        try:
            state = await self.redis.get_circuit_breaker()
            if not state:
                return
            if state.get("status") == "TRIGGERED":
                self.halted = True
                self.halt_reason = state.get("reason", "Restored from Redis")
                logger.warning(
                    f"Circuit breaker restored as HALTED: {self.halt_reason}"
                )
                metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="drawdown").set(2)
        except Exception as e:
            logger.error(f"Circuit breaker restore failed: {e}")

    async def _persist(self, status: str, reason: str | None = None) -> None:
        """Write state to Redis. Best-effort — logs error, never raises."""
        if not self.redis:
            return
        try:
            await self.redis.set_circuit_breaker(status, reason)
        except Exception as e:
            logger.error(f"Circuit breaker persist failed: {e}")

    def update_pnl(self, pnl: Decimal) -> bool:
        """Update daily PnL. Returns True if circuit breaker triggered.

        Checks both relative drawdown limit and absolute USD cap."""
        self.daily_pnl += pnl

        # Relative drawdown check
        if self.daily_pnl <= self.daily_drawdown_limit:
            self.halted = True
            self.halt_reason = (
                f"Drawdown breached: {self.daily_pnl} "
                f"(limit: {self.daily_drawdown_limit})"
            )
            metrics.circuit_breaker_trips.labels(breaker_name="drawdown").inc()
            metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="drawdown").set(2)
            logger.critical(self.halt_reason)
            self._fire_alert(self.halt_reason)
            self._persist_sync("TRIGGERED", self.halt_reason)
            return True

        # Absolute USD loss cap
        if (
            self.max_daily_loss_usd is not None
            and self.daily_pnl <= -self.max_daily_loss_usd
        ):
            self.halted = True
            self.halt_reason = (
                f"USD loss cap breached: {self.daily_pnl} "
                f"(cap: -{self.max_daily_loss_usd})"
            )
            metrics.circuit_breaker_trips.labels(breaker_name="usd_loss_cap").inc()
            metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="usd_loss_cap").set(2)
            logger.critical(self.halt_reason)
            self._fire_alert(self.halt_reason)
            self._persist_sync("TRIGGERED", self.halt_reason)
            return True

        return False

    def record_loss(self) -> bool:
        """Record a consecutive loss. Returns True if max losses reached."""
        self.consecutive_losses += 1

        if self.consecutive_losses >= self.max_consecutive_losses:
            from datetime import timedelta

            self.paused_until = datetime.now(UTC) + timedelta(
                minutes=self.loss_pause_minutes
            )
            metrics.circuit_breaker_trips.labels(
                breaker_name="consecutive_losses"
            ).inc()
            metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="consecutive_losses").set(1)
            logger.warning(
                f"Max consecutive losses ({self.max_consecutive_losses}) — "
                f"paused until {self.paused_until}"
            )
            return True

        return False

    def record_win(self) -> None:
        """Reset consecutive loss counter on win."""
        self.consecutive_losses = 0

    def is_paused(self) -> bool:
        """Check if currently paused from consecutive losses."""
        if self.paused_until is None:
            return False

        if datetime.now(UTC) >= self.paused_until:
            self.paused_until = None
            metrics.circuit_breaker_state.labels(symbol="GLOBAL", reason="consecutive_losses").set(0)
            return False

        return True

    def is_halted(self) -> bool:
        """Check if globally halted."""
        return self.halted

    def reset(self) -> None:
        """Reset all circuit breaker state."""
        self.daily_pnl = Decimal("0")
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reason = None
        self.paused_until = None
        logger.info("Circuit breaker reset")

    def get_state(self) -> dict[str, Any]:
        """Get current circuit breaker state."""
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "daily_pnl": str(self.daily_pnl),
            "consecutive_losses": self.consecutive_losses,
            "paused_until": (
                self.paused_until.isoformat() if self.paused_until else None
            ),
        }

    def _fire_alert(self, reason: str) -> None:
        """Fire Telegram alert. Wrapped task with error handler."""
        if not self.alert_service:
            return
        task = asyncio.ensure_future(
            self.alert_service.send(f"🚨 <b>CIRCUIT BREAKER</b>\n{reason}")
        )
        task.add_done_callback(self._alert_done)

    @staticmethod
    def _alert_done(task: asyncio.Task) -> None:
        if task.exception():
            logger.error(f"Circuit breaker alert failed: {task.exception()}")

    def _persist_sync(self, status: str, reason: str | None = None) -> None:
        """Schedule Redis persist without blocking sync context."""
        if not self.redis:
            return
        task = asyncio.ensure_future(self._persist(status, reason))
        task.add_done_callback(
            lambda t: t.exception() and logger.error(f"Persist error: {t.exception()}")
        )

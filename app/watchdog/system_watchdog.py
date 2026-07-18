"""System Watchdog — API desync detection and emergency global halt.

Monitors Bybit API state consistency and triggers emergency halt via Redis
when critical desyncs are detected:
  1. Position count desync — Redis positions vs Bybit API positions
  2. Balance staleness — no balance update for > 60s
  3. Orderbook feed desync — no orderbook update for > 30s

On any critical desync: writes karsa:global_halt to Redis.

Redis keys read:
  karsa:global_halt              — Emergency halt flag

Redis keys written:
  karsa:global_halt              — Emergency halt reason
  system:watchdog:status         — Watchdog status JSON
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from loguru import logger

REDIS_HALT_KEY = "karsa:global_halt"
REDIS_WATCHDOG_STATUS_KEY = "system:watchdog:status"
BALANCE_STALE_S = 60
ORDERBOOK_STALE_S = 30
CHECK_INTERVAL_S = 15


class SystemWatchdog:
    """Monitors API desyncs and triggers emergency global halt."""

    def __init__(
        self,
        redis_client: Any,
        bybit_client: Any,
        position_store: Any,
        alert_service: Any = None,
        check_interval_s: int = CHECK_INTERVAL_S,
    ) -> None:
        self._redis = redis_client
        self._bybit = bybit_client
        self._positions = position_store
        self._alert = alert_service
        self._interval = check_interval_s
        self._running = False
        self._halted = False
        self._last_balance_ts: float = 0.0
        self._last_orderbook_ts: float = 0.0
        self._status: dict[str, Any] = {}

    async def start(self) -> None:
        """Main watchdog loop."""
        self._running = True
        logger.info("SystemWatchdog: starting, interval=%ds", self._interval)

        while self._running:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SystemWatchdog: check cycle failed")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Stop the watchdog loop."""
        self._running = False
        logger.info("SystemWatchdog: stopped")

    async def _check_all(self) -> None:
        """Run all desync checks."""
        now = time.time()
        desyncs: list[str] = []

        if await self._is_already_halted():
            return

        pos_desync = await self._check_position_desync()
        if pos_desync:
            desyncs.append(pos_desync)

        bal_desync = self._check_balance_staleness(now)
        if bal_desync:
            desyncs.append(bal_desync)

        ob_desync = self._check_orderbook_staleness(now)
        if ob_desync:
            desyncs.append(ob_desync)

        self._status = {
            "last_check": datetime.now(UTC).isoformat(),
            "desyncs": desyncs,
            "halted": self._halted,
        }
        await self._write_status()

        if desyncs:
            await self._trigger_halt(desyncs)

    async def _check_position_desync(self) -> str | None:
        """Compare Redis position count vs Bybit API position count."""
        try:
            redis_positions = await self._positions.list_all()
            redis_count = len(redis_positions) if redis_positions else 0
            bybit_positions = await self._bybit.fetch_positions()
            bybit_count = len(bybit_positions) if bybit_positions else 0
            if redis_count != bybit_count:
                return f"position_desync: redis={redis_count} bybit={bybit_count}"
            return None
        except Exception:
            logger.debug("SystemWatchdog: position desync check failed")
            return None

    def _check_balance_staleness(self, now: float) -> str | None:
        """Check if balance has been updated recently."""
        if self._last_balance_ts == 0.0:
            return None
        elapsed = now - self._last_balance_ts
        if elapsed > BALANCE_STALE_S:
            return f"balance_stale: {elapsed:.0f}s"
        return None

    def _check_orderbook_staleness(self, now: float) -> str | None:
        """Check if orderbook feed is still active."""
        if self._last_orderbook_ts == 0.0:
            return None
        elapsed = now - self._last_orderbook_ts
        if elapsed > ORDERBOOK_STALE_S:
            return f"orderbook_stale: {elapsed:.0f}s"
        return None

    async def _trigger_halt(self, desyncs: list[str]) -> None:
        """Write global halt to Redis and fire alert."""
        reason = "watchdog_desync: " + "; ".join(desyncs)
        self._halted = True
        try:
            await self._redis.set(REDIS_HALT_KEY, reason)
            logger.critical("SystemWatchdog: EMERGENCY HALT — %s", reason)
            if self._alert is not None:
                with contextlib.suppress(Exception):
                    await self._alert.send(f"\U0001f6a8 WATCHDOG HALT: {reason}")
        except Exception:
            logger.exception("SystemWatchdog: failed to write halt key")

    async def _is_already_halted(self) -> bool:
        """Check if halt already set by another process."""
        try:
            halt = await self._redis.get(REDIS_HALT_KEY)
            if halt:
                self._halted = True
                return True
            return False
        except Exception:
            return False

    async def _write_status(self) -> None:
        """Write watchdog status to Redis."""
        try:
            await self._redis.set(
                REDIS_WATCHDOG_STATUS_KEY,
                json.dumps(self._status, default=str),
            )
        except Exception:
            logger.debug("SystemWatchdog: status write failed")

    def record_balance_update(self) -> None:
        """Call after each successful balance fetch."""
        self._last_balance_ts = time.time()

    def record_orderbook_update(self) -> None:
        """Call after each orderbook data update."""
        self._last_orderbook_ts = time.time()

    def get_status(self) -> dict[str, Any]:
        """Return current watchdog status."""
        return dict(self._status)

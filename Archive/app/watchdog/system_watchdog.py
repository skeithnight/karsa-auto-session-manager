"""System Watchdog — API desync detection and emergency global halt.

Monitors Bybit API state consistency and triggers emergency halt via Redis
when critical desyncs are detected:
  1. Position content desync — orphaned, phantom, or qty mismatch between Redis and Bybit
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
        """Compare position content (symbol, side, qty) between Redis and Bybit."""
        try:
            redis_positions = await self._positions.list_all() or []
            bybit_positions = await self._bybit.fetch_positions() or []
        except Exception:
            logger.debug("SystemWatchdog: position desync check failed")
            return None

        # Build symbol:side keyed maps
        redis_map: dict[str, dict] = {}
        for p in redis_positions:
            sym = p.get("symbol", "")
            side = p.get("side", "")
            qty = float(p.get("amount", p.get("qty", 0)))
            if qty != 0.0 and sym:
                redis_map[f"{sym}:{side}"] = {"symbol": sym, "side": side, "qty": qty}

        bybit_map: dict[str, dict] = {}
        for p in bybit_positions:
            sym = p.get("symbol", "")
            side = p.get("side", "")
            qty = float(p.get("contracts", p.get("amount", 0)))
            if qty != 0.0 and sym:
                bybit_map[f"{sym}:{side}"] = {"symbol": sym, "side": side, "qty": qty}

        desyncs: list[str] = []

        # Positions on Bybit but not in Redis (orphaned)
        for key, pos in bybit_map.items():
            if key not in redis_map:
                desyncs.append(f"orphan:{pos['symbol']}:{pos['side']}:{pos['qty']}")

        # Positions in Redis but not on Bybit (phantom)
        for key, pos in redis_map.items():
            if key not in bybit_map:
                desyncs.append(f"phantom:{pos['symbol']}:{pos['side']}:{pos['qty']}")

        # Qty mismatch for matching positions
        for key in redis_map:
            if key in bybit_map:
                r_qty = redis_map[key]["qty"]
                b_qty = bybit_map[key]["qty"]
                if abs(r_qty - b_qty) > 1e-8:
                    desyncs.append(f"qty_mismatch:{key}:redis={r_qty}:bybit={b_qty}")

        if desyncs:
            return "position_content_desync: " + "; ".join(desyncs)
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

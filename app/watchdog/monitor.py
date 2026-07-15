"""Watchdog — heartbeat monitor, latency tracker, health checks.

Responsibilities (per ARCHITECTURE.md §5):
1. WebSocket Heartbeat Monitor — pause Alpha Bridge on stale data
2. Execution Latency Tracker — switch SOR to market-only if >1500ms avg
3. Event Loop Lag Monitor — flatten positions on sustained lag
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from app.core import metrics


class Watchdog:
    """Monitors system health — heartbeats, latency, event loop lag."""

    def __init__(
        self,
        redis_client: Any,
        alpha_paused: Optional[asyncio.Event] = None,
        sor: Any = None,
        kill_switch: Optional[asyncio.Event] = None,
        check_interval: int = 10,
    ) -> None:
        logger.debug("Watchdog.__init__: entering")
        self.redis = redis_client
        self.alpha_paused = alpha_paused
        self.sor = sor
        self.kill_switch = kill_switch
        self.check_interval = check_interval
        self.last_heartbeat: Optional[datetime] = None
        self.max_heartbeat_age: int = 30  # seconds
        self.max_event_loop_lag: float = 0.1  # 100ms
        self.running = False

        # Latency tracking
        self._latency_samples: deque[float] = deque(maxlen=20)
        self._max_latency_avg: float = 1.5  # 1500ms threshold

        # Event loop lag streak tracking
        self._high_lag_streak: int = 0
        self._max_lag_streak: int = 3  # 3 consecutive >100ms = 30s sustained

        # Critical task liveness registry
        self._critical_tasks: Dict[str, asyncio.Task] = {}

        logger.debug("Watchdog.__init__: returning")

    async def start(self) -> None:
        """Start watchdog loop with auto-restart on crash."""
        logger.debug("start: entering")
        self.running = True
        max_restarts = 3
        restart_count = 0

        logger.info("Watchdog started")
        while self.running:
            try:
                await self._check_health()
                await asyncio.sleep(self.check_interval)
                restart_count = 0  # Reset on successful cycle
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                logger.debug(f"start: error={e}")
                restart_count += 1
                if restart_count >= max_restarts:
                    logger.critical(
                        f"Watchdog crashed {max_restarts}x in a row — giving up"
                    )
                    self.running = False
                    break
                logger.warning(f"Watchdog restart {restart_count}/{max_restarts}")
                await asyncio.sleep(self.check_interval)
        logger.debug("start: returning None")

    async def stop(self) -> None:
        """Stop watchdog loop."""
        logger.debug("stop: entering")
        self.running = False
        logger.info("Watchdog stopped")
        logger.debug("stop: returning None")

    def record_latency(self, seconds: float) -> None:
        """Record an execution latency sample (signal → fill)."""
        self._latency_samples.append(seconds)

    async def _check_health(self) -> None:
        """Run all health checks."""
        logger.debug("_check_health: entering")
        await self._check_heartbeat()
        await self._check_event_loop_lag()
        self._check_latency()
        self._check_critical_tasks()
        logger.debug("_check_health: returning None")

    async def _check_heartbeat(self) -> None:
        """Check per-exchange heartbeats. Pause/resume Alpha Bridge on stale data."""
        logger.debug("_check_heartbeat: entering")
        heartbeats = await self.redis.get_exchange_heartbeats()
        if not heartbeats:
            logger.warning("No heartbeats found in Redis")
            logger.debug("_check_heartbeat: returning None")
            return

        now = datetime.now(timezone.utc)
        stale_exchanges = []
        for exchange, ts in heartbeats.items():
            try:
                age = (now - datetime.fromisoformat(ts)).total_seconds()
                metrics.heartbeat_age.labels(exchange=exchange).set(age)
                if age > self.max_heartbeat_age:
                    stale_exchanges.append(f"{exchange}({age:.0f}s)")
            except (ValueError, TypeError):
                stale_exchanges.append(f"{exchange}(invalid)")

        if stale_exchanges:
            logger.warning(f"Stale exchanges: {', '.join(stale_exchanges)}")
            if self.alpha_paused and not self.alpha_paused.is_set():
                self.alpha_paused.set()
                metrics.alpha_bridge_paused.set(1)
                logger.warning("Alpha Bridge PAUSED — stale data")
        else:
            if self.alpha_paused and self.alpha_paused.is_set():
                self.alpha_paused.clear()
                metrics.alpha_bridge_paused.set(0)
                logger.info("Alpha Bridge RESUMED — heartbeats fresh")
        logger.debug("_check_heartbeat: returning None")

    async def _check_event_loop_lag(self) -> None:
        """Check event loop responsiveness. Flatten on sustained lag."""
        logger.debug("_check_event_loop_lag: entering")
        start = asyncio.get_event_loop().time()
        await asyncio.sleep(0)
        lag = asyncio.get_event_loop().time() - start
        metrics.event_loop_lag.set(lag * 1000)

        if lag > self.max_event_loop_lag:
            self._high_lag_streak += 1
            logger.warning(f"Event loop lag: {lag*1000:.1f}ms (streak: {self._high_lag_streak})")
            if self._high_lag_streak >= self._max_lag_streak:
                logger.critical(
                    f"Event loop lag sustained {self._high_lag_streak}x — flattening positions"
                )
                if self.sor:
                    try:
                        await self.sor.cancel_all_positions()
                        metrics.positions_flattened_total.labels(reason="event_loop_lag").inc()
                    except Exception as e:
                        logger.error(f"Flatten failed: {e}")
                if self.kill_switch:
                    self.kill_switch.set()
        else:
            self._high_lag_streak = 0
        logger.debug("_check_event_loop_lag: returning None")

    def _check_latency(self) -> None:
        """Check average execution latency. Switch SOR to market-only if high."""
        if not self._latency_samples or not self.sor:
            return
        avg = sum(self._latency_samples) / len(self._latency_samples)
        metrics.execution_latency.observe(avg)
        if avg > self._max_latency_avg:
            if not self.sor.skip_to_market:
                logger.warning(f"High execution latency: {avg:.1f}s — SOR switching to market-only")
                self.sor.skip_to_market = True
        else:
            if self.sor.skip_to_market:
                logger.info(f"Latency recovered: {avg:.1f}s — SOR resuming normal routing")
                self.sor.skip_to_market = False

    def register_critical_task(self, name: str, task: asyncio.Task) -> None:
        """Register a critical task for liveness monitoring."""
        self._critical_tasks[name] = task
        metrics.critical_task_dead.labels(task=name).set(0)
        logger.info(f"Watchdog registered critical task: {name}")

    def _check_critical_tasks(self) -> None:
        """Check all critical tasks are alive. Set metric + log on death."""
        for name, task in self._critical_tasks.items():
            if task.done():
                metrics.critical_task_dead.labels(task=name).set(1)
                exc = task.exception() if not task.cancelled() else None
                logger.critical(f"Critical task DEAD: {name} exc={exc}")
            else:
                metrics.critical_task_dead.labels(task=name).set(0)

    def get_status(self) -> Dict[str, Any]:
        """Get watchdog status."""
        logger.debug("get_status: entering")
        result = {
            "running": self.running,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "check_interval": self.check_interval,
            "alpha_paused": self.alpha_paused.is_set() if self.alpha_paused else False,
            "high_lag_streak": self._high_lag_streak,
            "latency_samples": len(self._latency_samples),
            "skip_to_market": self.sor.skip_to_market if self.sor else False,
        }
        logger.debug("get_status: returning dict")
        return result

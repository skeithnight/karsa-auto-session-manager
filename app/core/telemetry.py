"""Centralized telemetry — per-service health heartbeats + deep metrics.

Each microservice starts a TelemetryEmitter that writes a heartbeat JSON
payload to Redis every 30 s with TTL 120 s. Staleness is detected when the
key expires or its last_update is > 120 s old.

Key hierarchy:
  health:{service_name}  (String, TTL 120 s, JSON payload)

Commander reads all health:* keys to build a per-service health panel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger("karsa.telemetry")


SERVICE_HEARTBEAT_TTL: int = 120       # key expires 120 s after last write
SERVICE_HEARTBEAT_INTERVAL: float = 30  # background task writes every 30 s
STALE_AFTER_SECONDS: float = 80        # considered stale if last_update > 80 s ago
DEAD_AFTER_SECONDS: float = 180        # considered dead if > 180 s


@dataclass
class ServiceHealth:
    """Snapshot of a single microservice's health."""

    service_name: str
    last_heartbeat: str = ""
    last_update: float = 0.0
    status: str = "unknown"
    last_candle_ts: str = ""
    candles_ingested: int = 0
    positions_open: int = 0
    signals_fired: int = 0
    orders_placed: int = 0
    error_count: int = 0
    memory_mb: float = 0.0
    uptime_s: float = 0.0
    python_version: str = ""
    service_version: str = ""
    exchange_state: str = ""


def _read_rss_mb() -> float:
    """Return approximate RSS memory in MB, or 0.0 if unavailable."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return float(line.strip().split()[1]) / 1024.0
    except (FileNotFoundError, OSError, IndexError):
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 * 1024.0)
    except (ImportError, AttributeError):
        return 0.0


def _default_health(service_name: str) -> dict:
    """Build a default health payload dict."""
    return {
        "service_name": service_name,
        "last_heartbeat": datetime.now(UTC).isoformat(),
        "last_candle_ts": "",
        "candles_ingested": 0,
        "positions_open": 0,
        "signals_fired": 0,
        "orders_placed": 0,
        "error_count": 0,
        "memory_mb": _read_rss_mb(),
        "uptime_s": 0.0,
        "python_version": __import__("sys").version,
        "service_version": "0.1.0",
        "exchange_state": "",
    }


class TelemetryEmitter:
    """Periodically writes service health heartbeat to Redis.

    Usage:
        emitter = TelemetryEmitter(redis_client, "data-engine")
        await emitter.start()
        emitter.record_candle()
        ...
        await emitter.stop()
    """

    def __init__(
        self,
        redis_client: object,
        service_name: str,
        interval: float = SERVICE_HEARTBEAT_INTERVAL,
    ) -> None:
        self._redis = redis_client
        self.service_name = service_name
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._start_ts = time.monotonic()

        self.candles_ingested: int = 0
        self.signals_fired: int = 0
        self.orders_placed: int = 0
        self.error_count: int = 0
        self.last_candle_ts: str = ""
        self.positions_open: int = 0
        self.exchange_state: str = ""

    # ── Public counters ────────────

    def record_candle(self, ts: str = "") -> None:
        self.candles_ingested += 1
        if ts:
            self.last_candle_ts = ts

    def record_signal(self) -> None:
        self.signals_fired += 1

    def record_order(self) -> None:
        self.orders_placed += 1

    def record_error(self) -> None:
        self.error_count += 1

    # ── Lifecycle ──────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"telemetry-{self.service_name}",
        )
        logger.info("Telemetry started for %s", self.service_name)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._write_heartbeat()
        logger.info("Telemetry stopped for %s", self.service_name)

    async def heartbeat_now(self) -> None:
        await self._write_heartbeat()

    # ── Internal ───────────────────

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self._write_heartbeat()
            except Exception:
                logger.exception("Telemetry heartbeat write failed")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise

    async def _write_heartbeat(self) -> None:
        payload = _default_health(self.service_name)
        payload["last_heartbeat"] = datetime.now(UTC).isoformat()
        payload["last_candle_ts"] = self.last_candle_ts
        payload["candles_ingested"] = self.candles_ingested
        payload["positions_open"] = self.positions_open
        payload["signals_fired"] = self.signals_fired
        payload["orders_placed"] = self.orders_placed
        payload["error_count"] = self.error_count
        payload["memory_mb"] = _read_rss_mb()
        payload["uptime_s"] = time.monotonic() - self._start_ts
        payload["exchange_state"] = self.exchange_state

        key = f"health:{self.service_name}"
        with contextlib.suppress(AttributeError, Exception):
            await self._redis.setex(key, SERVICE_HEARTBEAT_TTL, json.dumps(payload))


# ── Commander-facing health reader ──────────────────────────


async def get_all_services_health(redis_client: object) -> dict[str, ServiceHealth]:
    """Read all health:* keys and return a dict keyed by service name.

    Status computed from heartbeat age:
      - fresh: < 80 s
      - stale: 80-180 s
      - dead:  > 180 s or key missing
    """
    result: dict[str, ServiceHealth] = {}

    try:
        keys: list[str] = await redis_client.keys("health:*")
    except Exception:
        logger.warning("Failed to scan health:* keys")
        return result

    for key in keys:
        service_name = key.split(":", 1)[1] if ":" in key else key
        try:
            raw = await redis_client.get(key)
        except Exception:
            result[service_name] = ServiceHealth(service_name=service_name, status="unknown")
            continue

        if not raw:
            result[service_name] = ServiceHealth(service_name=service_name, status="dead")
            continue

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            result[service_name] = ServiceHealth(service_name=service_name, status="dead")
            continue

        h = ServiceHealth(
            service_name=data.get("service_name", service_name),
            last_heartbeat=data.get("last_heartbeat", ""),
            last_candle_ts=data.get("last_candle_ts", ""),
            candles_ingested=data.get("candles_ingested", 0),
            positions_open=data.get("positions_open", 0),
            signals_fired=data.get("signals_fired", 0),
            orders_placed=data.get("orders_placed", 0),
            error_count=data.get("error_count", 0),
            memory_mb=float(data.get("memory_mb", 0)),
            uptime_s=float(data.get("uptime_s", 0)),
            python_version=data.get("python_version", ""),
            service_version=data.get("service_version", ""),
            exchange_state=data.get("exchange_state", ""),
        )
        try:
            hb = datetime.fromisoformat(h.last_heartbeat)
            age_s = (datetime.now(UTC) - hb).total_seconds()
        except (ValueError, TypeError):
            age_s = float("inf")

        if age_s < STALE_AFTER_SECONDS:
            h.status = "fresh"
        elif age_s < DEAD_AFTER_SECONDS:
            h.status = "stale"
        else:
            h.status = "dead"

        result[service_name] = h

    return result


def format_health_summary(services: dict[str, ServiceHealth]) -> str:
    """Build HTML summary block for the Commander dashboard."""
    if not services:
        return "No telemetry data."

    lines: list[str] = []
    for name, h in sorted(services.items()):
        icon = {"fresh": "✅", "stale": "⚠️", "dead": "❌", "unknown": "❓"}.get(h.status, "❓")
        mem = f"{h.memory_mb:.0f} MB" if h.memory_mb else "N/A"
        lines.append(
            f"{icon} <b>{name}</b>\n"
            f"    Status: {h.status.upper()}  │  Mem: {mem}  │  "
            f"Orders: {h.orders_placed}  │  Candles: {h.candles_ingested}\n"
            f"    Positions: {h.positions_open}  │  "
            f"Last candle: {h.last_candle_ts or '—'}\n"
        )
    return "\n".join(lines)

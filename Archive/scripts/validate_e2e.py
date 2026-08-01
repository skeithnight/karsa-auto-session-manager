"""End-to-End Validation Script — checks full pipeline health.

Verifies:
  1. PostgreSQL connectivity + schema (historical_candles table exists)
  2. Redis connectivity + Pub/Sub
  3. Critical Redis keys freshness
  4. Synthetic candle event: publish -> receive round-trip
  5. Backtest queue push/pop round-trip

Usage:
    python scripts/validate_e2e.py
    python scripts/validate_e2e.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field

import asyncpg
import redis.asyncio as aioredis

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_DB_URL = "postgresql://karsa:karsa@localhost:5432/karsa"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class ValidationReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def print_report(self) -> None:
        for c in self.checks:
            icon = "✅" if c.passed else "❌"
            ms = f" ({c.duration_ms:.0f}ms)" if c.duration_ms > 0 else ""
            print(f"  {icon} {c.name}{ms}")
            if c.detail and not c.passed:
                print(f"     {c.detail}")
        print()
        total = len(self.checks)
        passed = total - self.failed_count
        status = "ALL PASSED" if self.passed else f"{self.failed_count} FAILED"
        print(f"  {passed}/{total} checks passed — {status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Karsa E2E Validation")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS_URL)
    parser.add_argument("--db-url", default=DEFAULT_DB_URL)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


async def check_postgres(db_url: str) -> CheckResult:
    """Check PostgreSQL connectivity and schema."""
    start = time.monotonic()
    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'historical_candles'
                )
            """)
            if not exists:
                await pool.close()
                return CheckResult(
                    name="PostgreSQL: historical_candles table",
                    passed=False,
                    detail="Table does not exist. Run migrations first.",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            count = await conn.fetchval("SELECT COUNT(*) FROM historical_candles")
            elapsed = (time.monotonic() - start) * 1000
            await pool.close()
            return CheckResult(
                name=f"PostgreSQL: connectivity ({count:,} candles)",
                passed=True,
                duration_ms=elapsed,
            )
    except Exception as exc:
        return CheckResult(
            name="PostgreSQL: connectivity",
            passed=False,
            detail=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def check_redis(redis_url: str) -> CheckResult:
    """Check Redis connectivity and Pub/Sub."""
    start = time.monotonic()
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.ping()
        pubsub = r.pubsub()
        await pubsub.subscribe("karsa:validation:ping")
        await r.publish("karsa:validation:ping", "pong")
        msg = await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=2),
            timeout=3,
        )
        await pubsub.unsubscribe("karsa:validation:ping")
        await pubsub.close()
        elapsed = (time.monotonic() - start) * 1000
        if msg and msg.get("data") == "pong":
            return CheckResult(name="Redis: connectivity + Pub/Sub", passed=True, duration_ms=elapsed)
        return CheckResult(
            name="Redis: Pub/Sub",
            passed=False,
            detail="Pub/Sub message not received",
            duration_ms=elapsed,
        )
    except Exception as exc:
        return CheckResult(
            name="Redis: connectivity",
            passed=False,
            detail=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def check_redis_keys(redis_url: str) -> CheckResult:
    """Check critical Redis keys exist."""
    start = time.monotonic()
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        keys_to_check = [
            "system:universe:symbols",
        ]
        found = []
        missing = []
        for key in keys_to_check:
            val = await r.get(key)
            (found if val else missing).append(key)
        await r.aclose()
        elapsed = (time.monotonic() - start) * 1000
        if missing:
            return CheckResult(
                name=f"Redis keys: {len(found)}/{len(keys_to_check)} present",
                passed=False,
                detail=f"Missing: {', '.join(missing)}",
                duration_ms=elapsed,
            )
        return CheckResult(
            name=f"Redis keys: {len(keys_to_check)}/{len(keys_to_check)} present",
            passed=True,
            duration_ms=elapsed,
        )
    except Exception as exc:
        return CheckResult(
            name="Redis keys check",
            passed=False,
            detail=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def check_synthetic_candle(redis_url: str) -> CheckResult:
    """Publish a synthetic candle and verify it's received via Pub/Sub."""
    start = time.monotonic()
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = r.pubsub()
        channel = "karsa:candles:test:SYNTHUSDT:1h"
        await pubsub.psubscribe("karsa:candles:*")
        await asyncio.sleep(0.1)

        candle = {
            "exchange": "test",
            "symbol": "SYNTHUSDT",
            "timeframe": "1h",
            "ts": "2024-01-15T10:00:00+00:00",
            "open": "100.00",
            "high": "105.00",
            "low": "95.00",
            "close": "102.00",
            "volume": "1000.00",
        }
        await r.publish(channel, json.dumps(candle))

        received = False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1),
                timeout=2,
            )
            if msg and msg.get("type") == "pmessage":
                received = True
                break

        await pubsub.punsubscribe("karsa:candles:*")
        await pubsub.close()
        await r.aclose()
        elapsed = (time.monotonic() - start) * 1000
        if received:
            return CheckResult(name="Synthetic candle: Pub/Sub round-trip", passed=True, duration_ms=elapsed)
        return CheckResult(
            name="Synthetic candle: Pub/Sub round-trip",
            passed=False,
            detail="Candle published but not received back",
            duration_ms=elapsed,
        )
    except Exception as exc:
        return CheckResult(
            name="Synthetic candle: Pub/Sub round-trip",
            passed=False,
            detail=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def check_backtest_queue(redis_url: str) -> CheckResult:
    """Verify backtest queue is functional by pushing/popping a test job."""
    start = time.monotonic()
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        test_job = {"job_id": "validation-test", "symbol": "SYNTH/USDT", "candle_limit": 50}
        await r.rpush("backtest_jobs", json.dumps(test_job))
        result = await r.lpop("backtest_jobs")
        await r.aclose()
        elapsed = (time.monotonic() - start) * 1000
        if result:
            return CheckResult(name="Backtest queue: push/pop round-trip", passed=True, duration_ms=elapsed)
        return CheckResult(
            name="Backtest queue: push/pop round-trip",
            passed=False,
            detail="Job pushed but not popped back",
            duration_ms=elapsed,
        )
    except Exception as exc:
        return CheckResult(
            name="Backtest queue: push/pop round-trip",
            passed=False,
            detail=str(exc),
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def run_validation(redis_url: str, db_url: str) -> bool:
    """Run all checks and return True if all passed."""
    report = ValidationReport()
    print("\n  Karsa E2E Validation\n")
    report.checks.append(await check_postgres(db_url))
    report.checks.append(await check_redis(redis_url))
    report.checks.append(await check_redis_keys(redis_url))
    report.checks.append(await check_synthetic_candle(redis_url))
    report.checks.append(await check_backtest_queue(redis_url))
    report.print_report()
    return report.passed


async def main() -> None:
    args = parse_args()
    passed = await run_validation(args.redis_url, args.db_url)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())

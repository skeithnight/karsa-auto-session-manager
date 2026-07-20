"""Backtest Orchestrator — submit jobs, track progress, fetch results.

Provides the Commander with an async interface to:
  - Push backtest jobs to Redis ``backtest_jobs`` queue (RPUSH)
  - Track job progress via telemetry keys
  - Fetch completed results from ``backtest_results`` table
  - Subscribe to completion events via Redis pub/sub
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.core.database import DatabaseEngine
from app.core.redis_client import RedisClient

QUEUE_KEY = "backtest_jobs"
EVENT_CHANNEL = "karsa:events:backtest_complete"
TELEMETRY_PREFIX = "backtest:job:"
RESULTS_TTL_S = 3600  # 1 hour TTL for telemetry keys


@dataclass
class BacktestJobSpec:
    """Specification for a backtest job submission."""

    symbol: str
    candle_limit: int = 500
    start_time: str | None = None
    end_time: str | None = None
    bulk_job_id: str | None = None


@dataclass
class BacktestJobStatus:
    """Status of a submitted backtest job."""

    job_id: str
    status: str  # pending | running | completed | failed
    symbol: str
    trades_taken: int = 0
    total_pnl: str = "0"
    error: str = ""
    submitted_at: str = ""
    completed_at: str = ""


@dataclass
class BacktestTradeResult:
    """Single trade result from backtest_results table."""

    job_id: str
    symbol: str
    direction: str
    regime: str
    score: float
    entry_price: Decimal
    exit_price: Decimal | None
    exit_reason: str | None
    sl_price: Decimal | None
    tp_price: Decimal | None
    amount: Decimal
    size_multiplier: Decimal
    pnl_gross: Decimal
    pnl_net: Decimal
    total_fees: Decimal
    total_funding: Decimal
    bars_held: int
    entry_time: datetime | None
    exit_time: datetime | None
    trade_taken: bool


class BacktestOrchestrator:
    """Manages backtest job lifecycle from Commander side."""

    def __init__(self, redis_client: RedisClient, db_engine: DatabaseEngine) -> None:
        self._redis = redis_client
        self._db = db_engine

    async def submit_job(self, spec: BacktestJobSpec) -> str:
        """Submit a backtest job to Redis queue.

        Args:
            spec: BacktestJobSpec with symbol, candle_limit, date range.

        Returns:
            job_id (UUID string) for tracking.
        """
        job_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "job_id": job_id,
            "symbol": spec.symbol,
            "candle_limit": spec.candle_limit,
        }
        if spec.start_time:
            payload["start_time"] = spec.start_time
        if spec.end_time:
            payload["end_time"] = spec.end_time
        if spec.bulk_job_id:
            payload["bulk_job_id"] = spec.bulk_job_id

        # Store telemetry key for job tracking
        telemetry_key = f"{TELEMETRY_PREFIX}{job_id}"
        status_data = {
            "status": "pending",
            "symbol": spec.symbol,
            "submitted_at": datetime.now(UTC).isoformat(),
        }
        await self._redis.redis.setex(
            telemetry_key, RESULTS_TTL_S, json.dumps(status_data)
        )

        # Push to worker queue (RPUSH = FIFO with BLPOP)
        await self._redis.redis.rpush(QUEUE_KEY, json.dumps(payload))
        logger.info("backtest_job_submitted job_id=%s symbol=%s", job_id, spec.symbol)
        return job_id

    async def get_job_status(self, job_id: str) -> BacktestJobStatus:
        """Check job status from telemetry key or results table.

        Returns BacktestJobStatus with current state.
        """
        telemetry_key = f"{TELEMETRY_PREFIX}{job_id}"

        # Check telemetry first (fast path)
        raw = await self._redis.redis.get(telemetry_key)
        if raw:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if isinstance(data, dict):
                return BacktestJobStatus(
                    job_id=job_id,
                    status=data.get("status", "unknown"),
                    symbol=data.get("symbol", ""),
                    trades_taken=data.get("trades_taken", 0),
                    total_pnl=data.get("total_pnl", "0"),
                    error=data.get("error", ""),
                    submitted_at=data.get("submitted_at", ""),
                    completed_at=data.get("completed_at", ""),
                )

        # Fallback: check if results exist in DB (job completed but telemetry expired)
        try:
            async with self._db.engine.connect() as conn:
                row = await conn.execute(
                    text("""
                        SELECT COUNT(*) as cnt,
                               COALESCE(SUM(pnl_net), 0) as total_pnl
                        FROM backtest_results
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
                result = row.fetchone()
                if result and result[0] > 0:
                    return BacktestJobStatus(
                        job_id=job_id,
                        status="completed",
                        symbol="",
                        trades_taken=result[0],
                        total_pnl=str(result[1] or 0),
                    )
        except Exception as exc:
            logger.error("get_job_status_db_failed job_id=%s error=%s", job_id, exc)

        return BacktestJobStatus(job_id=job_id, status="unknown", symbol="")

    async def get_job_results(self, job_id: str) -> list[BacktestTradeResult]:
        """Fetch all trade results for a completed job from backtest_results.

        Returns list of BacktestTradeResult sorted by entry_time.
        """
        results: list[BacktestTradeResult] = []
        try:
            async with self._db.engine.connect() as conn:
                rows = await conn.execute(
                    text("""
                        SELECT job_id, symbol, direction, regime, score,
                               entry_price, exit_price, exit_reason,
                               sl_price, tp_price, amount, size_multiplier,
                               pnl_gross, pnl_net, total_fees, total_funding,
                               bars_held, entry_time, exit_time
                        FROM backtest_results
                        WHERE job_id = :job_id
                        ORDER BY entry_time ASC
                    """),
                    {"job_id": job_id},
                )
                for row in rows:
                    results.append(
                        BacktestTradeResult(
                            job_id=row[0],
                            symbol=row[1],
                            direction=row[2],
                            regime=row[3] or "",
                            score=float(row[4]) if row[4] else 0.0,
                            entry_price=Decimal(str(row[5]))
                            if row[5]
                            else Decimal("0"),
                            exit_price=Decimal(str(row[6])) if row[6] else None,
                            exit_reason=row[7],
                            sl_price=Decimal(str(row[8])) if row[8] else None,
                            tp_price=Decimal(str(row[9])) if row[9] else None,
                            amount=Decimal(str(row[10])) if row[10] else Decimal("0"),
                            size_multiplier=Decimal(str(row[11]))
                            if row[11]
                            else Decimal("1"),
                            pnl_gross=Decimal(str(row[12]))
                            if row[12]
                            else Decimal("0"),
                            pnl_net=Decimal(str(row[13])) if row[13] else Decimal("0"),
                            total_fees=Decimal(str(row[14]))
                            if row[14]
                            else Decimal("0"),
                            total_funding=Decimal(str(row[15]))
                            if row[15]
                            else Decimal("0"),
                            bars_held=row[16] or 0,
                            entry_time=row[17],
                            exit_time=row[18],
                            trade_taken=True,
                        )
                    )
        except Exception as exc:
            logger.error("get_job_results_failed job_id=%s error=%s", job_id, exc)
        return results

    async def submit_bulk_job(self, symbols: list[str], candle_limit: int = 500) -> str:
        """Submit multiple jobs under a single bulk_job_id."""
        bulk_id = str(uuid.uuid4())

        # We store the list of job_ids in a Redis set to track progress
        set_key = f"{TELEMETRY_PREFIX}bulk:{bulk_id}"

        for sym in symbols:
            spec = BacktestJobSpec(
                symbol=sym, candle_limit=candle_limit, bulk_job_id=bulk_id
            )
            job_id = await self.submit_job(spec)
            await self._redis.redis.sadd(set_key, job_id)

        await self._redis.redis.expire(set_key, RESULTS_TTL_S * 24)  # 24h TTL for bulk
        logger.info(
            "bulk_backtest_submitted bulk_id=%s count=%d", bulk_id, len(symbols)
        )
        return bulk_id

    async def get_bulk_job_status(self, bulk_id: str) -> dict[str, Any]:
        """Check overall status of a bulk backtest."""
        set_key = f"{TELEMETRY_PREFIX}bulk:{bulk_id}"
        job_ids = await self._redis.redis.smembers(set_key)
        if not job_ids:
            return {
                "status": "unknown",
                "total": 0,
                "completed": 0,
                "failed": 0,
                "pending": 0,
            }

        total = len(job_ids)
        completed = 0
        failed = 0

        # check status of each
        for j_bytes in job_ids:
            job_id = j_bytes.decode() if isinstance(j_bytes, bytes) else j_bytes
            st = await self.get_job_status(job_id)
            if st.status == "completed":
                completed += 1
            elif st.status == "failed":
                failed += 1

        pending = total - completed - failed
        status = "completed" if pending == 0 else "running"
        return {
            "status": status,
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
        }

    async def list_active_bulk_jobs(self) -> list[dict[str, Any]]:
        """List currently tracked bulk backtest jobs."""
        pattern = f"{TELEMETRY_PREFIX}bulk:*"
        keys = await self._redis.redis.keys(pattern)
        results = []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode()
            bulk_id = key.split("bulk:")[-1]
            status = await self.get_bulk_job_status(bulk_id)
            status["bulk_id"] = bulk_id
            results.append(status)
        return results

    async def get_bulk_job_results(self, bulk_id: str) -> list[BacktestTradeResult]:
        """Fetch all trade results across all jobs in a bulk run."""
        set_key = f"{TELEMETRY_PREFIX}bulk:{bulk_id}"
        job_ids_raw = await self._redis.redis.smembers(set_key)
        if not job_ids_raw:
            return []

        job_ids = [j.decode() if isinstance(j, bytes) else j for j in job_ids_raw]

        results: list[BacktestTradeResult] = []
        try:
            async with self._db.engine.connect() as conn:
                for chunk_idx in range(
                    0, len(job_ids), 50
                ):  # batch query to avoid massive IN clauses
                    chunk = job_ids[chunk_idx : chunk_idx + 50]
                    rows = await conn.execute(
                        text(f"""
                            SELECT job_id, symbol, direction, regime, score,
                                   entry_price, exit_price, exit_reason,
                                   sl_price, tp_price, amount, size_multiplier,
                                   pnl_gross, pnl_net, total_fees, total_funding,
                                   bars_held, entry_time, exit_time
                            FROM backtest_results
                            WHERE job_id IN ({",".join([":j" + str(i) for i in range(len(chunk))])})
                            ORDER BY entry_time ASC
                        """),
                        {f"j{i}": jid for i, jid in enumerate(chunk)},
                    )

                    for row in rows:
                        results.append(
                            BacktestTradeResult(
                                job_id=row[0],
                                symbol=row[1],
                                direction=row[2],
                                regime=row[3] or "",
                                score=float(row[4]) if row[4] else 0.0,
                                entry_price=Decimal(str(row[5]))
                                if row[5]
                                else Decimal("0"),
                                exit_price=Decimal(str(row[6])) if row[6] else None,
                                exit_reason=row[7],
                                sl_price=Decimal(str(row[8])) if row[8] else None,
                                tp_price=Decimal(str(row[9])) if row[9] else None,
                                amount=Decimal(str(row[10]))
                                if row[10]
                                else Decimal("0"),
                                size_multiplier=Decimal(str(row[11]))
                                if row[11]
                                else Decimal("1"),
                                pnl_gross=Decimal(str(row[12]))
                                if row[12]
                                else Decimal("0"),
                                pnl_net=Decimal(str(row[13]))
                                if row[13]
                                else Decimal("0"),
                                total_fees=Decimal(str(row[14]))
                                if row[14]
                                else Decimal("0"),
                                total_funding=Decimal(str(row[15]))
                                if row[15]
                                else Decimal("0"),
                                bars_held=row[16] or 0,
                                entry_time=row[17],
                                exit_time=row[18],
                                trade_taken=True,
                            )
                        )
                return results
        except Exception as exc:
            logger.error(
                "get_bulk_job_results_failed bulk_id=%s error=%s", bulk_id, exc
            )

        return results

    async def list_recent_jobs(self, limit: int = 10) -> list[BacktestJobStatus]:
        """List recent backtest jobs from results table.

        Returns up to ``limit`` most recent unique job_ids with summary stats.
        """
        jobs: list[BacktestJobStatus] = []
        try:
            async with self._db.engine.connect() as conn:
                rows = await conn.execute(
                    text("""
                        SELECT job_id, symbol,
                               COUNT(*) as trades_taken,
                               COALESCE(SUM(pnl_net), 0) as total_pnl,
                               MIN(entry_time) as started,
                               MAX(exit_time) as finished
                        FROM backtest_results
                        GROUP BY job_id, symbol
                        ORDER BY MAX(exit_time) DESC NULLS LAST
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )
                for row in rows:
                    jobs.append(
                        BacktestJobStatus(
                            job_id=row[0],
                            status="completed",
                            symbol=row[1] or "",
                            trades_taken=row[2] or 0,
                            total_pnl=str(row[3] or 0),
                            submitted_at=str(row[4] or ""),
                            completed_at=str(row[5] or ""),
                        )
                    )
        except Exception as exc:
            logger.error("list_recent_jobs_failed error=%s", exc)
        return jobs

    async def update_job_telemetry(  # noqa: PLR0913
        self,
        job_id: str,
        status: str,
        symbol: str = "",
        trades_taken: int = 0,
        total_pnl: str = "0",
        error: str = "",
    ) -> None:
        """Update telemetry key for a job (called by event listener)."""
        telemetry_key = f"{TELEMETRY_PREFIX}{job_id}"
        data = {
            "status": status,
            "symbol": symbol,
            "trades_taken": trades_taken,
            "total_pnl": total_pnl,
            "error": error,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self._redis.redis.setex(
                telemetry_key, RESULTS_TTL_S, json.dumps(data)
            )
        except Exception as exc:
            logger.error("update_job_telemetry_failed job_id=%s error=%s", job_id, exc)

    async def listen_for_completion(self, timeout_s: int = 30) -> dict[str, Any] | None:
        """Subscribe to backtest_complete events.

        Returns first event dict or ``None`` on timeout.
        Non-blocking with timeout — use in a polling pattern from Commander.
        """
        try:
            pubsub = self._redis.redis.pubsub()
            await pubsub.subscribe(EVENT_CHANNEL)
            try:
                import asyncio

                deadline = asyncio.get_event_loop().time() + timeout_s
                while asyncio.get_event_loop().time() < deadline:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if msg and msg["type"] == "message":
                        data: dict[str, Any] = json.loads(msg["data"])
                        # Update telemetry on completion
                        await self.update_job_telemetry(
                            job_id=data.get("job_id", ""),
                            status="completed" if data.get("success") else "failed",
                            symbol=data.get("symbol", ""),
                            trades_taken=data.get("trades_taken", 0),
                            total_pnl=data.get("total_pnl", "0"),
                            error=data.get("error", ""),
                        )
                        return data
            finally:
                await pubsub.unsubscribe(EVENT_CHANNEL)
                await pubsub.close()
        except Exception as exc:
            logger.error("listen_for_completion_failed error=%s", exc)
        return None

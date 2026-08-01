"""Backtest Worker — long-lived Redis BLPOP listener.

Pulls jobs from `backtest_jobs` Redis queue, loads historical candles
from PostgreSQL, runs BacktestEngine, saves results.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.backtest.engine import BacktestEngine, BacktestReport
from app.core.config import get_settings
from app.core.database import DatabaseEngine
from app.core.redis_client import RedisClient
from app.risk.dynamic_risk_gate import DynamicRiskGate

QUEUE_KEY = "backtest_jobs"
EVENT_CHANNEL = "karsa:events:backtest_complete"
BLPOP_TIMEOUT_S = 30
ERROR_BACKOFF_S = 5
DEFAULT_CANDLE_LIMIT = 500


class BacktestWorker:
    """Long-lived worker that listens for backtest jobs via Redis BLPOP."""

    def __init__(
        self,
        redis_client: RedisClient,
        db_engine: DatabaseEngine,
        engine: BacktestEngine,
    ) -> None:
        self._redis = redis_client
        self._db = db_engine
        self._engine = engine

    async def start(self) -> None:
        """Main loop — BLPOP + process + publish."""
        logger.info("BacktestWorker: starting BLPOP loop on backtest_jobs")
        while True:
            try:
                result = await self._redis.redis.blpop(
                    QUEUE_KEY, timeout=BLPOP_TIMEOUT_S
                )
                if result is None:
                    continue

                _queue_key, raw = result
                job = json.loads(raw)
                logger.info(
                    f"BacktestWorker: received job {job.get('job_id', 'unknown')}"
                )
                await self._process_job(job)

            except asyncio.CancelledError:
                logger.info("BacktestWorker: cancelled")
                raise
            except Exception:
                logger.exception("BacktestWorker: loop error")
                await asyncio.sleep(ERROR_BACKOFF_S)

    async def _process_job(self, job: dict[str, Any]) -> None:
        """Process a single backtest job."""
        job_id = job.get("job_id", str(uuid.uuid4()))
        symbol = job.get("symbol", "")
        candle_limit = job.get("candle_limit", DEFAULT_CANDLE_LIMIT)
        start_time = job.get("start_time")
        end_time = job.get("end_time")

        if not symbol:
            logger.error(f"BacktestWorker: job {job_id} missing symbol")
            await self._publish_result(job_id, symbol, [], False, "missing symbol")
            return

        try:
            candles = await self._load_candles(
                symbol, candle_limit, start_time, end_time
            )
            if len(candles) < 50:
                logger.warning(
                    f"BacktestWorker: only {len(candles)} candles for {symbol} (< 50)"
                )
                await self._publish_result(
                    job_id, symbol, [], False, "insufficient candles"
                )
                return

            reports = await self._engine.run(
                symbol=symbol, candles=candles, job_id=job_id
            )
            await self._save_results(job_id, reports)
            taken = sum(1 for r in reports if r.trade_taken)
            total_pnl = sum(r.pnl_net for r in reports if r.trade_taken)
            logger.info(
                f"BacktestWorker: job {job_id} — {len(reports)} reports, {taken} trades, pnl={total_pnl}"
            )
            await self._publish_result(job_id, symbol, reports, True)

        except Exception:
            logger.exception(f"BacktestWorker: job {job_id} failed")
            await self._publish_result(job_id, symbol, [], False, "internal error")

    async def _load_candles(
        self,
        symbol: str,
        limit: int,
        start_time: str | None,
        end_time: str | None,
    ) -> list[list]:
        """Pull historical candles from PostgreSQL."""
        # DB stores symbols as BTC/USDT — use original format
        conditions = ["symbol = :symbol"]
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}

        if start_time:
            conditions.append("ts >= :start_time")
            params["start_time"] = start_time
        if end_time:
            conditions.append("ts <= :end_time")
            params["end_time"] = end_time

        query = f"""
            SELECT EXTRACT(EPOCH FROM ts) * 1000 AS ts_ms,
                   open, high, low, close, volume
            FROM historical_candles
            WHERE {" AND ".join(conditions)}
            ORDER BY ts ASC
            LIMIT :limit
        """
        async with self._db.engine.connect() as conn:
            rows = (await conn.execute(text(query), params)).fetchall()

        return [
            [
                float(r[0]),
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
            ]
            for r in rows
        ]

    async def _save_results(self, job_id: str, reports: list[BacktestReport]) -> None:
        """Save BacktestReport objects to backtest_results table (only actual trades)."""
        reports = [r for r in reports if r.trade_taken]
        if not reports:
            return
        async with self._db.engine.begin() as conn:
            for r in reports:
                await conn.execute(
                    text("""
                        INSERT INTO backtest_results (
                            job_id, symbol, direction, regime, score,
                            entry_price, exit_price, exit_reason,
                            sl_price, tp_price, amount, size_multiplier,
                            pnl_gross, pnl_net, total_fees, total_funding,
                            bars_held, entry_time, exit_time, risk_profile_json
                        ) VALUES (
                            :job_id, :symbol, :direction, :regime, :score,
                            :entry_price, :exit_price, :exit_reason,
                            :sl_price, :tp_price, :amount, :size_multiplier,
                            :pnl_gross, :pnl_net, :total_fees, :total_funding,
                            :bars_held, :entry_time, :exit_time, :risk_profile_json
                        )
                    """),
                    {
                        "job_id": job_id,
                        "symbol": r.symbol,
                        "direction": r.direction,
                        "regime": r.regime.value
                        if hasattr(r.regime, "value")
                        else str(r.regime),
                        "score": round(r.score, 2),
                        "entry_price": str(r.entry_price),
                        "exit_price": str(r.exit_price) if r.exit_price else None,
                        "exit_reason": r.exit_reason,
                        "sl_price": str(r.sl_price) if r.sl_price else None,
                        "tp_price": str(r.tp_price) if r.tp_price else None,
                        "amount": str(r.amount),
                        "size_multiplier": str(r.size_multiplier),
                        "pnl_gross": str(r.pnl_gross),
                        "pnl_net": str(r.pnl_net),
                        "total_fees": str(r.total_fees),
                        "total_funding": str(r.total_funding),
                        "bars_held": r.bars_held,
                        "entry_time": r.entry_time,
                        "exit_time": r.exit_time,
                        "risk_profile_json": r.risk_profile.to_json(),
                    },
                )

    async def _publish_result(
        self,
        job_id: str,
        symbol: str,
        reports: list[BacktestReport],
        success: bool,
        error: str = "",
    ) -> None:
        event = {
            "event": "backtest_complete",
            "job_id": job_id,
            "symbol": symbol,
            "success": success,
            "trades_taken": sum(1 for r in reports if r.trade_taken),
            "total_pnl": str(sum(r.pnl_net for r in reports if r.trade_taken)),
            "error": error,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await self._redis.redis.publish(EVENT_CHANNEL, json.dumps(event))


async def main() -> None:
    """Entrypoint for backtest worker container."""
    settings = get_settings()
    db = DatabaseEngine()
    await db.connect(settings.postgres_url)
    redis = RedisClient()
    await redis.connect(socket_timeout=45.0)  # long timeout for BLPOP

    engine = BacktestEngine(
        regime_classifier=RegimeClassifier(),
        strategy_router=StrategyRouter(),
        risk_gate=DynamicRiskGate(),
        slippage_pct=Decimal(settings.shadow_slippage_pct),
        taker_fee_pct=Decimal(settings.shadow_taker_fee_pct),
        maker_fee_pct=Decimal(settings.shadow_maker_fee_pct),
    )

    worker = BacktestWorker(redis_client=redis, db_engine=db, engine=engine)
    try:
        await worker.start()
    finally:
        await redis.disconnect()
        await db.dispose()


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))

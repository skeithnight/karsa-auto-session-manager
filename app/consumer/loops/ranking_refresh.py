"""Ranking Refresh Loop.

Refreshes strategy ranking every hour by reading recent trades
and computing ranking decision via RankingEngine.

Writes to Redis: karsa:ranking:decision (plain string: PROMOTE/NEEDS_MORE_EVIDENCE/REJECT)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ranking_refresh_loop(
    trade_store: Any,
    ranking_engine: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 3600,
) -> None:
    """Refresh strategy ranking every hour.

    Reads recent trades and computes ranking decision.
    Writes to Redis: karsa:ranking:decision

    Args:
        trade_store: TradeStore instance for reading trades.
        ranking_engine: RankingEngine instance for computing ranking.
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 3600).
    """
    while not shutdown_event.is_set():
        try:
            # Get recent trades (TradeStore uses `limit` parameter)
            trades = await trade_store.get_recent_trades(limit=200)
            if not trades or len(trades) < 10:
                logger.debug("ranking_refresh: insufficient trades (%d)", len(trades) if trades else 0)
                await asyncio.sleep(interval_s)
                continue

            # Convert trades to metrics format expected by RankingEngine
            from app.research.metrics_engine import MetricsEngine
            trade_dicts = [
                {"pnl_pct": float(t.get("realized_pnl", 0)), "entry_time": t.get("entry_time", "")}
                for t in trades
            ]
            metrics_result = MetricsEngine.compute(trade_dicts)

            # Evaluate ranking
            from app.research.ranking_engine import RankingEngine, PromotionPolicy
            policy = PromotionPolicy()
            ranking = RankingEngine.evaluate(metrics_result, stats=None, policy=policy)

            # Extract plain decision string (remove emoji if present)
            raw_decision = ranking.get("decision", "NEEDS_MORE_EVIDENCE")
            # Normalize: strip emoji and whitespace
            decision = raw_decision.replace("✅", "").replace("❌", "").replace("⚠️", "").strip()

            # Write plain string to Redis
            await redis_client.set("karsa:ranking:decision", decision)

            # Write full details separately
            import json as _json
            ranking["_decision_plain"] = decision  # Add plain version
            await redis_client.set("karsa:ranking:details", _json.dumps(ranking))

            logger.info("ranking_refresh: decision=%s (trades=%d)", decision, len(trades))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("ranking_refresh_loop failed: %s", e)

        await asyncio.sleep(interval_s)

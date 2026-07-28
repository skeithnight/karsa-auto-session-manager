"""AI Outcome Logger — records AI decisions + eventual trade outcomes for calibration.

Logs to Redis with 30-day TTL. Each record captures:
  - AI confidence, direction, reasoning, features seen
  - Whether the trade was taken
  - Final PnL when trade closes

Usage:
  1. After AI decision:  await logger.log_decision(symbol, confidence, ...)
  2. On trade close:     await logger.log_outcome(symbol, trade_id, pnl, ...)

Analysis:
  Run `python -m app.alpha.ai_outcome_report` for calibration charts.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from loguru import logger as log_logger


class AIOoutcomeLogger:
    """Logs AI decisions and outcomes to Redis for calibration analysis."""

    REDIS_PREFIX = "karsa:ai_outcome:"
    DECISION_TTL = 86400 * 30  # 30 days

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def log_decision(
        self,
        symbol: str,
        direction: str,
        ai_confidence: int,
        reasoning: str,
        decision_recommendation: str,
        model_used: str,
        features: dict[str, Any] | None = None,
        trade_taken: bool = False,
        trade_id: int | None = None,
    ) -> None:
        """Record an AI decision. Called right after analyst/judge returns."""
        record = {
            "ts": time.time(),
            "symbol": symbol,
            "direction": direction,
            "ai_confidence": ai_confidence,
            "reasoning": reasoning,
            "recommendation": decision_recommendation,
            "model": model_used,
            "features": features or {},
            "trade_taken": trade_taken,
            "trade_id": trade_id,
            "outcome": None,  # filled in on trade close
        }
        key = f"{self.REDIS_PREFIX}{symbol}:{int(time.time())}"
        try:
            await self.redis.set(
                key, json.dumps(record, default=str), ex=self.DECISION_TTL
            )
            log_logger.debug(f"AI outcome logged: {symbol} conf={ai_confidence} rec={decision_recommendation}")
        except Exception as e:
            log_logger.debug(f"AI outcome log failed: {e}")

    async def log_outcome(
        self,
        symbol: str,
        trade_id: int | None,
        pnl_usdt: Decimal,
        pnl_pct: float,
        exit_reason: str,
    ) -> None:
        """Update the most recent AI decision for this symbol with trade outcome.

        Scans recent decisions for this symbol, finds the one matching trade_id
        (or the most recent unmatched one), and patches the outcome field.
        """
        try:
            # Find recent decisions for this symbol
            pattern = f"{self.REDIS_PREFIX}{symbol}:*"
            keys = []
            async for k in self.redis.scan_iter(match=pattern, count=50):
                keys.append(k)
            # Sort by timestamp (key format includes timestamp)
            keys.sort(reverse=True)

            for key in keys:
                raw = await self.redis.get(key)
                if not raw:
                    continue
                record = json.loads(raw)
                # Match by trade_id if provided, otherwise match most recent without outcome
                if record.get("outcome") is not None:
                    continue
                if trade_id and record.get("trade_id") != trade_id:
                    continue
                # Patch outcome
                record["outcome"] = {
                    "pnl_usdt": str(pnl_usdt),
                    "pnl_pct": round(pnl_pct, 4),
                    "exit_reason": exit_reason,
                    "closed_at": time.time(),
                }
                await self.redis.set(key, json.dumps(record, default=str), ex=self.DECISION_TTL)
                log_logger.debug(f"AI outcome updated: {symbol} pnl={pnl_pct:.2f}% reason={exit_reason}")
                return
            log_logger.debug(f"AI outcome: no matching decision found for {symbol} trade_id={trade_id}")
        except Exception as e:
            log_logger.debug(f"AI outcome update failed: {e}")

    async def get_calibration_data(
        self, symbol: str | None = None, limit: int = 500
    ) -> list[dict]:
        """Fetch logged decisions with outcomes for calibration analysis."""
        results = []
        pattern = f"{self.REDIS_PREFIX}*" if not symbol else f"{self.REDIS_PREFIX}{symbol}:*"
        try:
            async for key in self.redis.scan_iter(match=pattern, count=limit):
                raw = await self.redis.get(key)
                if raw:
                    record = json.loads(raw)
                    if record.get("outcome") is not None:
                        results.append(record)
        except Exception as e:
            log_logger.debug(f"AI calibration data fetch failed: {e}")
        return results[:limit]

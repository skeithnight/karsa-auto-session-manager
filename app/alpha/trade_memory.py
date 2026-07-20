"""Trade memory — store recent trades per symbol for AI context injection.

Redis sorted set per symbol. Max 20 entries, FIFO eviction.
On close: write entry. Before AI call: retrieve last 3 matching symbol + regime.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

from loguru import logger

from app.core import metrics
from app.core.redis_client import RedisClient

MAX_ENTRIES_PER_SYMBOL = 20
RETRIEVE_COUNT = 3


class TradeMemory:
    """Store and retrieve recent trade history for AI prompt injection.

    ponytail: direct Redis sorted set access, no ORM. Same pattern as PositionStore.
    """

    def __init__(self, redis_client: RedisClient) -> None:
        self.redis = redis_client

    def _key(self, symbol: str) -> str:
        return f"karsa:memory:{symbol}"

    async def store(
        self,
        symbol: str,
        pnl_pct: Decimal,
        hold_duration_min: int,
        regime: str,
        exit_reason: str,
        entry_confidence: Decimal,
    ) -> None:
        """Store a closed trade in memory."""
        entry = {
            "pnl_pct": float(pnl_pct),
            "hold_min": hold_duration_min,
            "regime": regime,
            "exit": exit_reason,
            "confidence": float(entry_confidence),
        }
        score = time.time()
        key = self._key(symbol)

        try:
            await self.redis.zadd(key, {json.dumps(entry): score})
            # FIFO eviction: keep only latest MAX_ENTRIES
            await self.redis.zremrangebyrank(key, 0, -(MAX_ENTRIES_PER_SYMBOL + 1))
            metrics.trade_memory_stored.labels(symbol=symbol).inc()
            logger.info(
                f"Trade memory: stored {symbol} pnl={pnl_pct}% exit={exit_reason}"
            )
        except Exception as e:
            logger.error(f"Trade memory store failed for {symbol}: {e}")

    async def get_recent(
        self, symbol: str, regime: str | None = None, count: int = RETRIEVE_COUNT
    ) -> list[dict]:
        """Get recent trades for symbol, optionally filtered by regime.

        Returns newest-first list of trade dicts.
        """
        key = self._key(symbol)
        try:
            raw = await self.redis.zrevrange(key, 0, count * 3 - 1)
            if not raw:
                return []

            entries = []
            for item in raw:
                try:
                    if isinstance(item, bytes):
                        item = item.decode()
                    entry = json.loads(item)
                    if regime and entry.get("regime") != regime:
                        continue
                    entries.append(entry)
                    if len(entries) >= count:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

            return entries
        except Exception as e:
            logger.error(f"Trade memory read failed for {symbol}: {e}")
            return []

    def format_prompt(self, symbol: str, trades: list[dict]) -> str:
        """Format recent trades as prompt prefix for AI analyst."""
        if not trades:
            return ""

        lines = [f"Recent trades for {symbol}:"]
        for i, t in enumerate(trades, 1):
            pnl = t.get("pnl_pct", 0)
            hold = t.get("hold_min", 0)
            exit_r = t.get("exit", "unknown")
            conf = t.get("confidence", 0)
            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(
                f"  {i}. {pnl_sign}{pnl:.1f}% ({hold}min, {exit_r}, conf={conf:.2f})"
            )

        return "\n".join(lines)

    async def get_prompt_context(self, symbol: str, regime: str | None = None) -> str:
        """Convenience: fetch + format in one call."""
        trades = await self.get_recent(symbol, regime=regime)
        if trades:
            metrics.trade_memory_injected.labels(symbol=symbol).inc()
        return self.format_prompt(symbol, trades)

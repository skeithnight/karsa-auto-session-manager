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
            logger.warning(f"Trade memory: failed to store {symbol}: {e}")

    async def is_in_cooldown(self, symbol: str, cooldown_mins: int = 45) -> bool:
        """Check if symbol is in a post-loss cooldown period to prevent whipsawing."""
        key = self._key(symbol)
        try:
            # Get the single most recent trade with its score (timestamp)
            if not self.redis.redis:
                return False
            recent = await self.redis.redis.zrevrange(key, 0, 0, withscores=True)
            if not recent:
                return False

            entry_str, timestamp = recent[0]
            now = time.time()
            if (now - timestamp) > (cooldown_mins * 60):
                return False  # cooldown expired

            entry = json.loads(entry_str)
            pnl_pct = entry.get("pnl_pct", 0.0)

            # If the last trade was a real loss (< -0.5%), enforce cooldown
            if pnl_pct < -0.5:
                logger.info(f"Cooldown active for {symbol}: last trade was {pnl_pct}% pnl")
                return True

            return False
        except Exception as e:
            logger.warning(f"Cooldown check failed for {symbol}: {e}")
            return False

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

    async def get_symbol_performance_multiplier(self, symbol: str) -> float:
        """Calculate historical win rate to penalize or boost signals.
        - Win Rate < 30% -> 0.7x multiplier (Toxic)
        - Win Rate > 60% -> 1.2x multiplier (Golden)
        - Otherwise -> 1.0x
        Requires at least 10 trades to judge fairly.
        """
        trades = await self.get_recent(symbol, count=10)
        
        # If fewer than 10 trades, we don't have enough statistical significance, default to 1.0
        if len(trades) < 10:
            return 1.0
            
        wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0.0)
        win_rate = wins / len(trades)
        
        if win_rate < 0.30:
            logger.info(f"Adaptive Symbol: {symbol} has {win_rate*100:.1f}% win-rate. Applying 0.7x penalty.")
            return 0.7
        elif win_rate > 0.60:
            logger.info(f"Adaptive Symbol: {symbol} has {win_rate*100:.1f}% win-rate. Applying 1.2x bonus.")
            return 1.2
            
        return 1.0

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

    async def get_active_cooldowns(self, cooldown_mins: int = 45) -> list[str]:
        """Return a list of symbols currently in cooldown."""
        if not self.redis.redis:
            return []
        keys = await self.redis.redis.keys("karsa:memory:*")
        symbols = []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode()
            symbol = key.split(":")[-1]
            if await self.is_in_cooldown(symbol, cooldown_mins):
                symbols.append(symbol)
        return symbols

    async def clear_cooldown(self, symbol: str) -> None:
        """Clear the cooldown for a specific symbol by removing its memory key."""
        key = self._key(symbol)
        try:
            if self.redis.redis:
                await self.redis.redis.delete(key)
                logger.info(f"Cooldown cleared for {symbol}")
        except Exception as e:
            logger.error(f"Failed to clear cooldown for {symbol}: {e}")

"""Position Store — Redis-backed lifecycle tracking per position.

Tracks: entry price, peak price, ATR, SL order ID, checkpoint state, timestamps.
Key: karsa:position:{symbol}:{side}
Callers: main.py, TrailingStopManager, CheckpointManager, SectorCap, executor_task.
Change: switch from ast.literal_eval(str(dict)) to json.dumps/loads for safety.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

from app.core.redis_client import RedisClient


class PositionStore:
    """Redis-backed position lifecycle state.

    ponytail: flat dict per position, no ORM. Simple get/set/del.
    """

    def __init__(self, redis_client: RedisClient) -> None:
        logger.debug("PositionStore.__init__: entering")
        self.redis = redis_client
        logger.debug("PositionStore.__init__: returning")

    def _key(self, symbol: str, side: str) -> str:
        return f"karsa:position:{symbol}:{side}"

    async def save(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        amount: Decimal,
        sl_order_id: Optional[str] = None,
        atr: Optional[Decimal] = None,
    ) -> None:
        """Save new position state."""
        key = self._key(symbol, side)
        data = {
            "symbol": symbol,
            "side": side,
            "entry_price": str(entry_price),
            "amount": str(amount),
            "peak_price": str(entry_price),
            "sl_order_id": sl_order_id or "",
            "atr": str(atr) if atr else "",
            "checkpoint": "OPEN",
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.redis.set(key, json.dumps(data))
        logger.info(f"Position saved: {symbol} {side} @ {entry_price}")

    async def get(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        """Get position state."""
        key = self._key(symbol, side)
        raw = await self.redis.redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def update_peak(self, symbol: str, side: str, price: Decimal) -> None:
        """Update peak price if new high."""
        pos = await self.get(symbol, side)
        if not pos:
            return
        peak = Decimal(pos.get("peak_price", "0"))
        if price > peak:
            pos["peak_price"] = str(price)
            pos["last_check_at"] = datetime.now(timezone.utc).isoformat()
            await self.redis.redis.set(self._key(symbol, side), json.dumps(pos))

    async def update_sl(self, symbol: str, side: str, sl_order_id: str) -> None:
        """Update SL order ID."""
        pos = await self.get(symbol, side)
        if not pos:
            return
        pos["sl_order_id"] = sl_order_id
        pos["last_check_at"] = datetime.now(timezone.utc).isoformat()
        await self.redis.redis.set(self._key(symbol, side), json.dumps(pos))

    async def update_checkpoint(self, symbol: str, side: str, checkpoint: str) -> None:
        """Update checkpoint state."""
        pos = await self.get(symbol, side)
        if not pos:
            return
        pos["checkpoint"] = checkpoint
        pos["last_check_at"] = datetime.now(timezone.utc).isoformat()
        await self.redis.redis.set(self._key(symbol, side), json.dumps(pos))

    async def remove(self, symbol: str, side: str) -> None:
        """Remove position (on close)."""
        key = self._key(symbol, side)
        await self.redis.redis.delete(key)
        logger.info(f"Position removed: {symbol} {side}")

    async def has_position(self, symbol: str, side: Optional[str] = None) -> bool:
        """Check if position exists."""
        if side:
            return await self.get(symbol, side) is not None
        long_pos = await self.get(symbol, "buy")
        short_pos = await self.get(symbol, "sell")
        return long_pos is not None or short_pos is not None

    async def list_all(self) -> list[Dict[str, Any]]:
        """List all active positions."""
        keys = await self.redis.redis.keys("karsa:position:*")
        positions = []
        for key in keys:
            raw = await self.redis.redis.get(key)
            if raw:
                try:
                    positions.append(json.loads(raw))
                except Exception:
                    pass
        return positions

    async def cleanup_stale(self, exchange_symbols: set[str]) -> int:
        """Remove position keys for symbols no longer held on exchange.

        Args:
            exchange_symbols: set of Bybit-format symbols (e.g. "BTCUSDT") from fetch_positions().

        Returns:
            Number of orphaned keys removed.
        """
        keys = await self.redis.redis.keys("karsa:position:*")
        removed = 0
        for key in keys:
            key_str = key if isinstance(key, str) else key.decode()
            raw = await self.redis.redis.get(key)
            if not raw:
                await self.redis.redis.delete(key_str)
                removed += 1
                continue
            try:
                pos = json.loads(raw)
                sym = pos.get("symbol", "")
                # Convert ccxt format (BTC/USDT) to Bybit format (BTCUSDT)
                bybit_sym = sym.replace("/", "")
                if bybit_sym not in exchange_symbols:
                    await self.redis.redis.delete(key_str)
                    logger.info(f"Cleaned orphaned position: {sym} {pos.get('side', '')}")
                    removed += 1
            except Exception:
                await self.redis.redis.delete(key_str)
                removed += 1
        return removed

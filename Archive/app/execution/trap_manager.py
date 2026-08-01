"""Trap Manager — Manages the lifecycle of resting SNIPER limit orders.

Ensures that 4-hour TTLs and invalidation conditions are respected,
and reconciles orphaned limit orders on startup.
"""

import asyncio
from datetime import UTC, datetime

from loguru import logger

from app.core.database import DatabaseEngine
from app.core.decision_context import SniperTrapMetadata
from app.core.redis_client import RedisClient
from app.execution.bybit_client import BybitClient


class TrapManager:
    """Manages active, unfilled Sniper traps."""

    def __init__(
        self, redis_client: RedisClient, bybit_client: BybitClient, db_engine: DatabaseEngine
    ) -> None:
        self.redis = redis_client
        self.bybit = bybit_client
        self.db = db_engine

    async def register_trap(self, order_id: str, symbol: str, metadata: SniperTrapMetadata) -> None:
        """Save trap state to Redis."""
        key = f"sniper:trap:{symbol}:{order_id}"
        data = metadata.model_dump_json()
        await self.redis.redis.set(key, data)
        # expire automatically in Redis after TTL + 1 hour grace
        ttl_seconds = int((metadata.expires_at - datetime.now(UTC)).total_seconds())
        if ttl_seconds > 0:
            await self.redis.redis.expire(key, ttl_seconds + 3600)
        logger.info(f"TrapManager: Registered trap {order_id} for {symbol} (expires in {ttl_seconds}s)")

    async def remove_trap(self, order_id: str, symbol: str) -> None:
        """Remove trap state from Redis (e.g., when filled or canceled)."""
        key = f"sniper:trap:{symbol}:{order_id}"
        await self.redis.redis.delete(key)
        logger.debug(f"TrapManager: Removed trap {order_id} for {symbol}")

    async def startup_reconciliation(self) -> None:
        """Cancel orphan open limit orders on exchange not tracked locally."""
        logger.info("TrapManager.startup_reconciliation: Running startup orphan check...")
        try:
            # fetch all open orders
            open_orders = await self.bybit.fetch_open_orders()
            if not open_orders:
                logger.info("TrapManager: No open orders on Bybit.")
                return

            # fetch local active traps
            keys = await self.redis.redis.keys("sniper:trap:*")
            tracked_ids = set()
            for k in keys:
                parts = k.split(":")
                if len(parts) >= 4:
                    tracked_ids.add(parts[-1])
            
            cancel_count = 0
            for order in open_orders:
                order_id = order.get("orderId")
                order_type = order.get("orderType")
                symbol = order.get("symbol")
                status = order.get("orderStatus", order.get("status"))
                
                # SL/TP are conditional. Limit orders placed by Sniper will be 'Limit'
                if order_type == "Limit" and order_id not in tracked_ids and status in ("New", "PartiallyFilled"):
                    logger.warning(f"TrapManager: Found orphan limit order {order_id} for {symbol}. Canceling...")
                    try:
                        await self.bybit.cancel_order(order_id, symbol)
                        cancel_count += 1
                    except Exception as ce:
                        logger.error(f"TrapManager: Failed to cancel orphan order {order_id}: {ce}")
            
            logger.info(f"TrapManager.startup_reconciliation: Canceled {cancel_count} orphan orders.")
        except Exception as e:
            logger.error(f"TrapManager.startup_reconciliation failed: {e}")

    async def check_traps(self, kill_switch: asyncio.Event) -> None:
        """Background loop to check TTL and invalidation conditions."""
        await asyncio.sleep(5)  # wait for startup
        logger.info("TrapManager.check_traps: Loop started")
        while not kill_switch.is_set():
            try:
                keys = await self.redis.redis.keys("sniper:trap:*")
                for k in keys:
                    data = await self.redis.redis.get(k)
                    if not data:
                        continue
                    
                    parts = k.split(":")
                    if len(parts) < 4:
                        continue
                    symbol = parts[-2]
                    order_id = parts[-1]
                    
                    meta = SniperTrapMetadata.model_validate_json(data)
                    now = datetime.now(UTC)
                    
                    if now > meta.expires_at:
                        logger.info(f"TrapManager: Trap {order_id} for {symbol} expired TTL. Canceling.")
                        try:
                            await self.bybit.cancel_order(order_id, symbol)
                        except Exception as ce:
                            logger.error(f"TrapManager: Failed to cancel expired trap {order_id}: {ce}")
                        await self.remove_trap(order_id, symbol)
                        continue
                    
                    # Implementation of dynamic invalidation checks could go here
                    
            except Exception as e:
                logger.error(f"TrapManager.check_traps error: {e}")
            
            await asyncio.sleep(60)  # check every minute

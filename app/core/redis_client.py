"""Redis Client — high-speed state caching per DATA_MODEL.md §2."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import get_settings


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj: object) -> object:
        logger.debug("DecimalEncoder.default: entering")
        if isinstance(obj, Decimal):
            logger.debug("DecimalEncoder.default: returning str")
            return str(obj)
        result = super().default(obj)
        logger.debug("DecimalEncoder.default: returning")
        return result


class RedisClient:
    """Async Redis client with connection pooling."""

    def __init__(self) -> None:
        logger.debug("RedisClient.__init__: entering")
        self.settings = get_settings()
        self.redis: Optional[aioredis.Redis] = None
        logger.debug("RedisClient.__init__: returning")

    async def connect(self) -> None:
        """Establish Redis connection."""
        logger.debug("connect: entering")
        self.redis = aioredis.from_url(
            self.settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(f"Connected to Redis: {self.settings.redis_url}")
        logger.debug("connect: returning None")

    async def disconnect(self) -> None:
        """Close Redis connection."""
        logger.debug("disconnect: entering")
        if self.redis:
            await self.redis.close()
            logger.info("Disconnected from Redis")
        logger.debug("disconnect: returning None")

    async def ping(self) -> bool:
        """Health check — returns True if Redis is reachable."""
        logger.debug("ping: entering")
        if not self.redis:
            logger.debug("ping: returning False (no connection)")
            return False
        try:
            await self.redis.ping()
            logger.debug("ping: returning True")
            return True
        except Exception as e:
            logger.error(f"ping: error={e}")
            return False

    # --- Generic Key/Value ---

    async def set(self, key: str, value: str) -> None:
        """Set a generic Redis key."""
        logger.debug(f"set: entering key={key}")
        if not self.redis:
            raise RuntimeError("Redis not connected")
        await self.redis.set(key, value)
        logger.debug("set: returning None")

    async def get(self, key: str) -> str | None:
        """Get a generic Redis key."""
        logger.debug(f"get: entering key={key}")
        if not self.redis:
            raise RuntimeError("Redis not connected")
        result = await self.redis.get(key)
        logger.debug(f"get: returning result_type={type(result).__name__}")
        return result

    async def delete(self, key: str) -> None:
        """Delete a generic Redis key."""
        logger.debug(f"delete: entering key={key}")
        if not self.redis:
            raise RuntimeError("Redis not connected")
        await self.redis.delete(key)
        logger.debug("delete: returning None")

    # --- GlobalState Cache ---

    async def set_global_state(self, symbol: str, state: dict) -> None:
        """Cache global state for a symbol (TTL 60s)."""
        logger.debug(f"set_global_state: entering symbol={symbol}")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        key = f"global:state:{symbol}"
        value = json.dumps(state, cls=DecimalEncoder)
        await self.redis.setex(key, 60, value)
        logger.debug("set_global_state: returning None")

    async def get_global_state(self, symbol: str) -> Optional[dict]:
        """Get cached global state for a symbol."""
        logger.debug(f"get_global_state: entering symbol={symbol}")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        key = f"global:state:{symbol}"
        value = await self.redis.get(key)
        if value:
            logger.debug("get_global_state: returning dict")
            return json.loads(value)
        logger.debug("get_global_state: returning None")
        return None

    # --- System Heartbeat ---

    async def set_heartbeat(self) -> None:
        """Set system heartbeat (TTL 30s)."""
        logger.debug("set_heartbeat: entering")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        key = "system:heartbeat"
        value = datetime.now(timezone.utc).isoformat()
        await self.redis.setex(key, 30, value)
        logger.debug("set_heartbeat: returning None")

    async def get_heartbeat(self) -> Optional[str]:
        """Get system heartbeat timestamp."""
        logger.debug("get_heartbeat: entering")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        result = await self.redis.get("system:heartbeat")
        logger.debug(f"get_heartbeat: returning result_type={type(result).__name__}")
        return result

    # --- Circuit Breaker State ---

    async def set_circuit_breaker(self, status: str, reason: str | None = None) -> None:
        """Set circuit breaker state (no TTL)."""
        logger.debug(f"set_circuit_breaker: entering status={status}")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        key = "system:circuit_breaker"
        value = json.dumps({
            "status": status,
            "reason": reason,
            "triggered_at": datetime.now(timezone.utc).isoformat() if status == "TRIGGERED" else None,
        })
        await self.redis.set(key, value)
        logger.debug("set_circuit_breaker: returning None")

    async def get_circuit_breaker(self) -> Optional[dict]:
        """Get circuit breaker state."""
        logger.debug("get_circuit_breaker: entering")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        value = await self.redis.get("system:circuit_breaker")
        if value:
            logger.debug("get_circuit_breaker: returning dict")
            return json.loads(value)
        logger.debug("get_circuit_breaker: returning None")
        return None

    # --- Session Config ---

    async def set_session_config(self, regime: str) -> None:
        """Set session/regime config (no TTL)."""
        logger.debug(f"set_session_config: entering regime={regime}")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        key = "system:config:regime"
        await self.redis.set(key, regime)
        logger.debug("set_session_config: returning None")

    async def get_session_config(self) -> str | None:
        """Get session/regime config."""
        logger.debug("get_session_config: entering")
        if not self.redis:
            raise RuntimeError("Redis not connected")

        result = await self.redis.get("system:config:regime")
        logger.debug(f"get_session_config: returning result_type={type(result).__name__}")
        return result

    # --- AI Cache ---

    async def get_ai_cache(self, cache_key: str) -> Optional[dict]:
        """Get cached AI result."""
        if not self.redis:
            return None
        try:
            raw = await self.redis.get(f"ai:cache:{cache_key}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.error(f"get_ai_cache: error={e}")
        return None

    async def set_ai_cache(self, cache_key: str, result: dict, ttl: int = 300) -> None:
        """Set cached AI result with TTL."""
        if not self.redis:
            return
        try:
            await self.redis.set(f"ai:cache:{cache_key}", json.dumps(result), ex=ttl)
        except Exception as e:
            logger.error(f"set_ai_cache: error={e}")

    # --- Per-Exchange Heartbeats ---

    async def set_exchange_heartbeat(self, exchange: str) -> None:
        """Record heartbeat timestamp for an exchange."""
        if not self.redis:
            return
        try:
            await self.redis.hset(
                "system:heartbeats",
                exchange,
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error(f"set_exchange_heartbeat: error={e}")

    async def get_exchange_heartbeats(self) -> dict:
        """Get all exchange heartbeat timestamps."""
        if not self.redis:
            return {}
        try:
            raw = await self.redis.hgetall("system:heartbeats")
            return raw if raw else {}
        except Exception as e:
            logger.error(f"get_exchange_heartbeats: error={e}")
            return {}

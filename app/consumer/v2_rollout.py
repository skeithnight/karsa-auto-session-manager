"""V2 Rollout Control — Runtime flag for DecisionEngineV2 activation.

This module provides runtime control for activating DecisionEngineV2
in shadow and live modes, addressing audit finding #1:
"the live and shadow containers still run the old decision engine"

Usage:
    from app.consumer.v2_rollout import V2Rollout

    rollout = V2Rollout(redis_client)
    use_v2 = await rollout.should_use_v2("shadow")
    if use_v2:
        engine = DecisionEngineV2(...)
    else:
        engine = DecisionEngine(...)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class V2Rollout:
    """Controls DecisionEngineV2 activation via Redis flags.

    Redis keys:
        karsa:v2:shadow_enabled — "1" to enable V2 in shadow
        karsa:v2:live_enabled — "1" to enable V2 in live
        karsa:v2:mode — "shadow_only", "live_only", "both", "none"
    """

    def __init__(self, redis_client: Any = None) -> None:
        """Initialize V2 rollout controller.

        Args:
            redis_client: Redis client for reading rollout flags.
        """
        self._redis = redis_client

    async def should_use_v2(self, mode: str = "shadow") -> bool:
        """Check if V2 engine should be used.

        Args:
            mode: "shadow" or "live"

        Returns:
            True if V2 engine should be used.
        """
        if self._redis is None:
            return False

        try:
            # Check mode-specific flag
            if mode == "shadow":
                raw = await self._redis.get("karsa:v2:shadow_enabled")
            elif mode == "live":
                raw = await self._redis.get("karsa:v2:live_enabled")
            else:
                return False

            if raw:
                value = raw.decode() if isinstance(raw, bytes) else str(raw)
                return value == "1"

            # Check global mode
            raw_mode = await self._redis.get("karsa:v2:mode")
            if raw_mode:
                mode_value = raw_mode.decode() if isinstance(raw_mode, bytes) else str(raw_mode)
                if mode_value == "both":
                    return True
                elif mode_value == "shadow_only" and mode == "shadow":
                    return True
                elif mode_value == "live_only" and mode == "live":
                    return True

        except Exception as e:
            logger.debug("V2Rollout check failed: %s", e)

        return False

    async def enable_v2(self, mode: str = "shadow") -> None:
        """Enable V2 engine for specified mode.

        Args:
            mode: "shadow", "live", or "both"
        """
        if self._redis is None:
            return

        try:
            if mode in ("shadow", "both"):
                await self._redis.set("karsa:v2:shadow_enabled", "1")
            if mode in ("live", "both"):
                await self._redis.set("karsa:v2:live_enabled", "1")
            if mode in ("shadow_only", "live_only", "both", "none"):
                await self._redis.set("karsa:v2:mode", mode)
            logger.info("V2Rollout: enabled for %s", mode)
        except Exception as e:
            logger.error("V2Rollout enable failed: %s", e)

    async def disable_v2(self, mode: str = "shadow") -> None:
        """Disable V2 engine for specified mode.

        Args:
            mode: "shadow", "live", or "both"
        """
        if self._redis is None:
            return

        try:
            if mode in ("shadow", "both"):
                await self._redis.set("karsa:v2:shadow_enabled", "0")
            if mode in ("live", "both"):
                await self._redis.set("karsa:v2:live_enabled", "0")
            if mode in ("shadow_only", "live_only", "both", "none"):
                await self._redis.set("karsa:v2:mode", "none")
            logger.info("V2Rollout: disabled for %s", mode)
        except Exception as e:
            logger.error("V2Rollout disable failed: %s", e)

    async def get_status(self) -> dict[str, Any]:
        """Get current rollout status.

        Returns:
            Dict with shadow_enabled, live_enabled, mode.
        """
        status = {
            "shadow_enabled": False,
            "live_enabled": False,
            "mode": "none",
        }

        if self._redis is None:
            return status

        try:
            raw = await self._redis.get("karsa:v2:shadow_enabled")
            if raw:
                status["shadow_enabled"] = (raw.decode() if isinstance(raw, bytes) else str(raw)) == "1"

            raw = await self._redis.get("karsa:v2:live_enabled")
            if raw:
                status["live_enabled"] = (raw.decode() if isinstance(raw, bytes) else str(raw)) == "1"

            raw = await self._redis.get("karsa:v2:mode")
            if raw:
                status["mode"] = raw.decode() if isinstance(raw, bytes) else str(raw)

        except Exception as e:
            logger.debug("V2Rollout status check failed: %s", e)

        return status

"""Autonomous Session Manager — manages auto-trading sessions."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from loguru import logger

from app.core import metrics


class AutonomousSessionManager:
    """Manages autonomous trading sessions via Redis state."""

    def __init__(
        self,
        redis_client: Any,
        kill_switch: asyncio.Event,
    ) -> None:
        logger.debug("AutonomousSessionManager.__init__: entering")
        self.redis = redis_client
        self.kill_switch = kill_switch
        logger.debug("AutonomousSessionManager.__init__: returning")

    async def start_session(
        self,
        duration_min: int,
        risk_pct: int,
        max_pos: int,
        interval_min: int = 15,
    ) -> None:
        """Start autonomous session — write config to Redis."""
        logger.debug(f"start_session: entering duration_min={duration_min} risk_pct={risk_pct}")
        config = {
            "risk_pct": risk_pct,
            "max_pos": max_pos,
            "interval_min": interval_min,
            "duration_min": duration_min,
        }
        logger.debug(f"start_session: writing config to Redis: {config}")
        await self.redis.set("karsa:auto:config", json.dumps(config))
        await self.redis.set("karsa:auto:state:active", "1")
        await self.redis.set("karsa:auto:start_time", str(time.time()))
        # Verify write
        verify = await self.redis.get("karsa:auto:state:active")
        logger.info(f"start_session: state={verify} config={config}")
        logger.debug("start_session: returning None")

    async def stop_session(self) -> None:
        """Stop session — clear active state."""
        logger.debug("stop_session: entering")
        logger.debug("stop_session: clearing Redis keys")
        await self.redis.set("karsa:auto:state:active", "0")
        await self.redis.delete("karsa:auto:config")
        await self.redis.delete("karsa:auto:start_time")
        verify = await self.redis.get("karsa:auto:state:active")
        logger.info(f"stop_session: state={verify}")
        logger.debug("stop_session: returning None")

    async def is_active(self) -> bool:
        """Check if session is active."""
        logger.debug("is_active: entering")
        val = await self.redis.get("karsa:auto:state:active")
        logger.debug(f"is_active: raw={val!r}")
        result = val == "1"
        logger.debug(f"is_active: returning {result}")
        return result

    async def get_config(self) -> Optional[dict]:
        """Get current session config."""
        logger.debug("get_config: entering")
        raw = await self.redis.get("karsa:auto:config")
        logger.debug(f"get_config: raw={raw!r}")
        if raw:
            result = json.loads(raw)
            logger.debug("get_config: returning dict")
            return result
        logger.debug("get_config: returning None")
        return None

    async def run_loop(self) -> None:
        """Main loop — check expiry at interval."""
        logger.debug("run_loop: entering")
        logger.info("ASM run_loop: starting")
        while not self.kill_switch.is_set():
            try:
                active = await self.is_active()
                if active:
                    metrics.asm_session_active.set(1)
                    config = await self.get_config()
                    if config:
                        metrics.asm_risk_pct.set(float(config.get("risk_pct", 0)))
                        start_time = await self.redis.get("karsa:auto:start_time")
                        if start_time and config.get("duration_min", 0) > 0:
                            elapsed = time.time() - float(start_time)
                            if elapsed >= config["duration_min"] * 60:
                                logger.info("ASM run_loop: session expired — stopping")
                                await self.stop_session()
                            else:
                                remaining = config["duration_min"] - (elapsed / 60)
                                logger.debug(f"ASM run_loop: active — {remaining:.1f}min remaining")
                        else:
                            logger.debug(f"ASM run_loop: active — no duration limit, config={config}")
                    else:
                        logger.warning("ASM run_loop: active but no config found")
                else:
                    metrics.asm_session_active.set(0)
                    metrics.asm_risk_pct.set(0)
                    logger.debug("ASM run_loop: idle")
            except Exception as e:
                logger.error(f"ASM run_loop: error={e}")
                logger.debug(f"run_loop: error={e}")

            await asyncio.sleep(30)
        logger.debug("run_loop: returning None")

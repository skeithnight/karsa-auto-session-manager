"""System Doctor AI — End-to-End Diagnostic Agent.

Triggered when the system enters a Circuit Breaker or severe drawdown state.
Collects system state (regime, slippage, active configurations) and sends it
to the LLM (via 9router) for a diagnosis. 

Can autonomously apply "Treatments" by updating the karsa:auto:config in Redis.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from app.core.redis_client import RedisClient
from app.core.ai_client import AIClient


class SystemDoctor:
    def __init__(
        self,
        redis_client: RedisClient,
        ai_client: AIClient,
        alert_service: object = None,
    ):
        self._redis = redis_client
        self._ai = ai_client
        self._alert = alert_service
        
    async def diagnose_and_treat(self, trigger_reason: str) -> None:
        """Run a full system diagnosis and automatically apply treatment."""
        logger.warning(f"SystemDoctor activated. Trigger: {trigger_reason}")
        
        # 1. Collect System Context
        context = await self._gather_context()
        
        prompt = (
            f"You are the Karsa System Doctor. The trading system just hit a circuit breaker.\n"
            f"Trigger Reason: {trigger_reason}\n\n"
            f"SYSTEM STATE:\n"
            f"{json.dumps(context, indent=2)}\n\n"
            f"Diagnose the root cause of the failure and prescribe a treatment.\n"
            f"Respond EXACTLY in this JSON format:\n"
            f"{{\n"
            f"  \"diagnosis\": \"Brief explanation of what is going wrong\",\n"
            f"  \"treatment\": \"Description of the fix\",\n"
            f"  \"config_updates\": {{\"shadow_live_ratio\": 0.5, \"disable_chop\": true, \"disable_range\": false}}\n"
            f"}}"
        )

        try:
            # 2. Call the AI
            response = await self._ai.ask(prompt)
            if not response:
                logger.error("SystemDoctor: AI returned empty diagnosis.")
                return
                
            # Clean response
            response = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(response)
            
            diagnosis = data.get("diagnosis", "Unknown")
            treatment = data.get("treatment", "None")
            config_updates = data.get("config_updates", {})
            
            logger.info(f"SystemDoctor Diagnosis: {diagnosis}")
            logger.info(f"SystemDoctor Treatment: {treatment}")
            
            if self._alert:
                msg = (
                    f"🩺 *System Doctor Activated*\n\n"
                    f"⚠️ *Trigger:* {trigger_reason}\n"
                    f"🔍 *Diagnosis:* {diagnosis}\n"
                    f"💊 *Treatment:* {treatment}\n"
                    f"⚙️ *Applying Updates:* {json.dumps(config_updates)}"
                )
                await self._alert.send_message(msg)
                
            # 3. Apply the Treatment to Redis
            if config_updates:
                await self._apply_treatment(config_updates)
                
        except Exception as e:
            logger.error(f"SystemDoctor failed to complete diagnosis: {e}")

    async def _gather_context(self) -> dict:
        """Gather current regime, performance, and config state."""
        context = {}
        try:
            # Get current config
            raw_config = await self._redis.get("karsa:auto:config")
            if raw_config:
                context["current_config"] = json.loads(raw_config)
            
            # Count regimes
            keys = await self._redis.keys("system:regime:*")
            regimes = {}
            for k in keys:
                r = await self._redis.get(k)
                if r:
                    regimes[r] = regimes.get(r, 0) + 1
            context["market_regimes"] = regimes
            
        except Exception as e:
            logger.warning(f"SystemDoctor: partial context gathered: {e}")
            
        return context

    async def _apply_treatment(self, updates: dict) -> None:
        """Merge treatment updates into karsa:auto:config."""
        raw_config = await self._redis.get("karsa:auto:config")
        config = json.loads(raw_config) if raw_config else {}
        
        for k, v in updates.items():
            config[k] = v
            
        await self._redis.set("karsa:auto:config", json.dumps(config))
        logger.info(f"SystemDoctor: Treatment applied. New config: {config}")

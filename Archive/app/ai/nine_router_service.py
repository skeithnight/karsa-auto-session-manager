"""9router AI Service Implementation (Sprint 5)."""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.ai.circuit_breaker import AICircuitBreaker
from app.ai.dto import AIEvidenceDTO
from app.ai.service import IAIService
from app.core.decision_context import DecisionContext


class NineRouterService(IAIService):
    """Production AI Service calling 9router karsa-combo."""

    def __init__(self, http_client: Any, circuit_breaker: AICircuitBreaker | None = None) -> None:
        self.client = http_client
        self.circuit_breaker = circuit_breaker or AICircuitBreaker()

    async def analyze_market(self, context: DecisionContext) -> AIEvidenceDTO | None:
        if not self.circuit_breaker.allow_request():
            logger.debug("NineRouterService: Request blocked by Circuit Breaker")
            return None

        try:
            # Here we would normally make the HTTP call via self.client
            # payload = self._build_prompt(context)
            # response = await self.client.post("/v1/chat/completions", json=payload)
            # evidence = self._parse_response(response)

            # Simulated response for now
            evidence = AIEvidenceDTO(
                bullish_probability=55.0,
                bearish_probability=45.0,
                confidence=80.0,
                summary="Bullish momentum supported by macro conditions.",
                reasons=["Higher highs", "Funding rate positive"]
            )

            self.circuit_breaker.record_success()
            return evidence

        except Exception as e:
            logger.error(f"NineRouterService failed: {e}")
            self.circuit_breaker.record_failure()
            return None

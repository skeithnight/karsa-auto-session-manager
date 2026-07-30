"""9router AI Service Implementation.

Makes real HTTP calls to the 9router OpenAI-compatible endpoint, with
multi-provider fallback (OpenAI, Anthropic) and Redis caching.

Fallback behaviour:
  1. 9router (primary)
  2. OpenAI GPT-4-turbo (if OPENAI_API_KEY set)
  3. Anthropic Claude 3.5 Sonnet (if ANTHROPIC_API_KEY set)

Cache invalidation triggers:
  - Regime change (caller passes regime string)
  - >5% price move from cached price
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from loguru import logger

from app.ai.circuit_breaker import AICircuitBreaker
from app.ai.dto import (
    AIDecisionDTO,
    EntryStrategy,
    PositionSize,
    RiskLevel,
    StopLossStrategy,
)
from app.ai.parser import ParseError, parse_decision
from app.ai.prompt_builder import build_prompts
from app.ai.service import IAIService
from app.core.config import get_settings
from app.core.decision_context import DecisionContext

# ---------------------------------------------------------------------------
# Cache constants
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours
_CACHE_KEY_PREFIX = "ai:decision"
_PRICE_MOVE_THRESHOLD = 0.05  # 5%


# ---------------------------------------------------------------------------
# Default fallback (confidence=50, BLOCK)
# ---------------------------------------------------------------------------
def _default_decision(reason: str, provider: str = "fallback") -> AIDecisionDTO:
    """Return a safe BLOCK decision when all providers fail."""
    return AIDecisionDTO(
        confidence_score=50,
        risk_level=RiskLevel.HIGH,
        position_size=PositionSize.BLOCK,
        entry_strategy=EntryStrategy.WAIT_PULLBACK,
        stop_loss_strategy=StopLossStrategy.NORMAL,
        reasoning=reason,
        key_risks=["AI service unavailable — defaulting to no action"],
        key_opportunities=[],
        bullish_probability=50.0,
        bearish_probability=50.0,
        summary="AI unavailable, blocking entry.",
        provider=provider,
        model="fallback",
    )


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------
class _ProviderConfig:
    """Immutable config for a single LLM provider."""

    __slots__ = ("name", "base_url", "api_key", "model", "headers")

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if extra_headers:
            self.headers.update(extra_headers)


def _build_provider_chain() -> list[_ProviderConfig]:
    """Build ordered list of available providers from env vars."""
    settings = get_settings()
    chain: list[_ProviderConfig] = []

    # 1. 9router (always present)
    chain.append(
        _ProviderConfig(
            name="9router",
            base_url=settings.nine_router_base_url,
            api_key=settings.nine_router_auth_token,
            model=settings.nine_router_model,
        )
    )

    # 2. OpenAI (optional)
    import os

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        chain.append(
            _ProviderConfig(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key=openai_key,
                model="gpt-4-turbo",
            )
        )

    # 3. Anthropic (optional)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        chain.append(
            _ProviderConfig(
                name="anthropic",
                base_url="https://api.anthropic.com/v1",
                api_key=anthropic_key,
                model="claude-3-5-sonnet-20241022",
                extra_headers={"anthropic-version": "2023-06-01"},
            )
        )

    return chain


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------
class NineRouterService(IAIService):
    """Production AI Service calling 9router / OpenAI / Anthropic.

    Uses httpx.AsyncClient for HTTP, PromptBuilder for prompt construction,
    and parser for JSON validation.  Falls back to default BLOCK decision
    on any failure.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        circuit_breaker: AICircuitBreaker | None = None,
        redis_client: Any | None = None,
        provider_chain: list[_ProviderConfig] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._owned_client = http_client is None
        self.client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            verify=False,  # ponytail: internal network, no SSL needed
        )
        self.circuit_breaker = circuit_breaker or AICircuitBreaker()
        self.redis = redis_client
        self.timeout_seconds = timeout_seconds
        self._provider_chain = provider_chain or _build_provider_chain()

        # Provider health tracking
        self._provider_failures: dict[str, int] = {p.name: 0 for p in self._provider_chain}
        self._provider_costs: dict[str, float] = {p.name: 0.0 for p in self._provider_chain}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def analyze_market(self, context: DecisionContext) -> AIDecisionDTO | None:
        """Full decision pipeline: cache check → prompt → HTTP → parse → cache store."""
        if not self.circuit_breaker.allow_request():
            logger.debug("NineRouterService: blocked by circuit breaker")
            return None

        # --- Cache lookup ---
        cached = await self._get_cached(context)
        if cached is not None:
            logger.debug(f"NineRouterService: cache hit for {context.symbol}")
            return cached

        # --- Build prompts ---
        prompt_bundle = build_prompts(context)
        messages = prompt_bundle["messages"]

        # --- Try provider chain ---
        last_error: str | None = None
        for provider in self._provider_chain:
            if self._provider_failures.get(provider.name, 0) >= 3:
                logger.debug(f"NineRouterService: skipping degraded provider {provider.name}")
                continue

            try:
                # Retry up to 2 times on parse errors (model sometimes returns empty/malformed)
                raw_response = None
                for attempt in range(2):
                    try:
                        raw_response = await self._call_provider(provider, messages)
                        decision = parse_decision(
                            raw_response,
                            provider=provider.name,
                            model=provider.model,
                        )
                        break  # success
                    except ParseError as exc:
                        if attempt == 0:
                            logger.debug(f"NineRouterService: parse retry for {context.symbol}: {exc}")
                            await asyncio.sleep(0.5)
                            continue
                        raise  # second attempt failed, propagate

                # DEBUG: log raw response for troubleshooting
                logger.debug(
                    f"NineRouterService: raw response for {context.symbol} "
                    f"(first 500 chars): {raw_response[:500]}"
                )
                self.circuit_breaker.record_success()
                self._provider_failures[provider.name] = 0

                # --- Cache store ---
                await self._set_cached(context, decision)
                logger.info(
                    f"NineRouterService: decision for {context.symbol} "
                    f"via {provider.name} — confidence={decision.confidence_score} "
                    f"size={decision.position_size.value} reasoning={decision.reasoning[:100]}"
                )
                return decision

            except ParseError as exc:
                last_error = f"{provider.name}: parse error: {exc}"
                logger.warning(last_error)
                self._provider_failures[provider.name] = self._provider_failures.get(provider.name, 0) + 1
                continue

            except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
                last_error = f"{provider.name}: transport error: {type(exc).__name__}: {exc}"
                logger.warning(last_error)
                self._provider_failures[provider.name] = self._provider_failures.get(provider.name, 0) + 1
                # ponytail: retry once after brief delay on transport errors
                await asyncio.sleep(1.0)
                continue

            except Exception as exc:
                last_error = f"{provider.name}: unexpected error: {exc}"
                logger.error(last_error)
                self._provider_failures[provider.name] = self._provider_failures.get(provider.name, 0) + 1
                continue

        # --- All providers failed ---
        self.circuit_breaker.record_failure()
        logger.error(f"NineRouterService: all providers failed for {context.symbol}: {last_error}")
        return _default_decision(reason=f"All AI providers failed: {last_error}")

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owned_client and self.client:
            await self.client.aclose()

    # ------------------------------------------------------------------
    # Provider HTTP call
    # ------------------------------------------------------------------
    async def _call_provider(self, provider: _ProviderConfig, messages: list[dict]) -> str:
        """Call a single provider and return raw response text."""
        url = f"{provider.base_url}/v1/chat/completions"
        payload = {
            "model": provider.model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.1,
            "stream": False,
        }

        start = time.monotonic()
        resp = await self.client.post(
            url,
            json=payload,
            headers=provider.headers,
        )
        elapsed = time.monotonic() - start
        logger.debug(f"NineRouterService: {provider.name} responded in {elapsed:.2f}s (status={resp.status_code})")

        if resp.status_code == 429:
            raise httpx.HTTPStatusError(
                "Rate limited",
                request=resp.request,
                response=resp,
            )

        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            raise ParseError(f"{provider.name}: empty choices in response")

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise ParseError(f"{provider.name}: empty content in response")

        # Track cost (approximate tokens)
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        self._provider_costs[provider.name] = self._provider_costs.get(provider.name, 0.0) + (
            prompt_tokens + completion_tokens
        )

        return content

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------
    def _cache_key(self, context: DecisionContext) -> str:
        return f"{_CACHE_KEY_PREFIX}:{context.symbol}:{context.direction}"

    async def _get_cached(self, context: DecisionContext) -> AIDecisionDTO | None:
        """Return cached decision if valid, else None."""
        if self.redis is None:
            return None
        try:
            key = self._cache_key(context)
            raw = await self.redis.get(key)
            if raw is None:
                return None
            data = json.loads(raw)

            # Invalidate on regime change
            if data.get("regime") != context.regime.value:
                await self.redis.delete(key)
                logger.debug(f"NineRouterService: cache invalidated (regime change) for {context.symbol}")
                return None

            # Invalidate on >5% price move
            cached_price = data.get("cached_price")
            current_price = context.features.close
            if cached_price is not None and current_price is not None:
                price_change = abs(current_price - cached_price) / cached_price
                if price_change > _PRICE_MOVE_THRESHOLD:
                    await self.redis.delete(key)
                    logger.debug(
                        f"NineRouterService: cache invalidated " f"(price move {price_change:.1%}) for {context.symbol}"
                    )
                    return None

            # Reconstruct DTO
            return AIDecisionDTO(
                confidence_score=data["confidence_score"],
                risk_level=RiskLevel(data["risk_level"]),
                position_size=PositionSize(data["position_size"]),
                entry_strategy=EntryStrategy(data["entry_strategy"]),
                stop_loss_strategy=StopLossStrategy(data["stop_loss_strategy"]),
                reasoning=data["reasoning"],
                key_risks=data.get("key_risks", []),
                key_opportunities=data.get("key_opportunities", []),
                bullish_probability=data.get("bullish_probability", 50.0),
                bearish_probability=data.get("bearish_probability", 50.0),
                summary=data.get("summary", ""),
                provider=data.get("provider", "cache"),
                model=data.get("model", "cache"),
            )
        except Exception as exc:
            logger.warning(f"NineRouterService: cache read error for {context.symbol}: {exc}")
            return None

    async def _set_cached(self, context: DecisionContext, decision: AIDecisionDTO) -> None:
        """Store decision in cache with TTL."""
        if self.redis is None:
            return
        try:
            key = self._cache_key(context)
            payload = {
                "confidence_score": decision.confidence_score,
                "risk_level": decision.risk_level.value,
                "position_size": decision.position_size.value,
                "entry_strategy": decision.entry_strategy.value,
                "stop_loss_strategy": decision.stop_loss_strategy.value,
                "reasoning": decision.reasoning,
                "key_risks": decision.key_risks,
                "key_opportunities": decision.key_opportunities,
                "bullish_probability": decision.bullish_probability,
                "bearish_probability": decision.bearish_probability,
                "summary": decision.summary,
                "provider": decision.provider,
                "model": decision.model,
                # Cache metadata for invalidation
                "regime": context.regime.value,
                "cached_price": context.features.close,
                "cached_at": time.time(),
            }
            await self.redis.set(key, json.dumps(payload))
            # Also set TTL via separate call (Redis setex not always available on async client)
            try:
                await self.redis.expire(key, _CACHE_TTL_SECONDS)
            except Exception:
                pass  # Some Redis clients don't support expire separately
        except Exception as exc:
            logger.warning(f"NineRouterService: cache write error for {context.symbol}: {exc}")

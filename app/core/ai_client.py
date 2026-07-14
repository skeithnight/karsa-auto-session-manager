"""AI Client — async HTTP client for 9router OpenAI-compatible API.

No Anthropic SDK needed. Just aiohttp POST to 9router container.
Handles auth, retries, timeouts. Returns None on any failure (graceful degradation).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp
from loguru import logger

from app.core import metrics


class AIClient:
    """Async client for 9router AI proxy (OpenAI-compatible format)."""

    def __init__(
        self,
        router_url: str,
        auth_token: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self.router_url = router_url.rstrip("/")
        self.auth_token = auth_token
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=timeout,
            )
        return self._session

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """Send chat completion request. Returns response text or None on failure."""
        session = await self._get_session()
        start_time = time.monotonic()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        url = f"{self.router_url}/v1/chat/completions"

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        elapsed = time.monotonic() - start_time
                        metrics.ai_analyst_latency.observe(elapsed)
                        data = await resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            content = choices[0].get("message", {}).get("content", "")
                            metrics.ai_analyst_calls.labels(result="success").inc()
                            logger.debug(f"AI complete: model={self.model}")
                            return content
                        logger.warning("AI complete: empty choices")
                        return None

                    if resp.status == 429:
                        body = await resp.text()
                        last_error = f"rate_limited: {body}"
                        wait = 2 ** attempt
                        logger.warning(f"AI rate limited, retry in {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    if 400 <= resp.status < 500:
                        body = await resp.text()
                        logger.error(f"AI client error {resp.status}: {body}")
                        return None

                    body = await resp.text()
                    last_error = f"status_{resp.status}: {body}"
                    wait = 2 ** attempt
                    logger.warning(f"AI server error {resp.status}, retry {attempt + 1}/{self.max_retries} in {wait}s")
                    await asyncio.sleep(wait)

            except asyncio.TimeoutError:
                last_error = "timeout"
                metrics.ai_analyst_calls.labels(result="timeout").inc()
                logger.warning(f"AI timeout, retry {attempt + 1}/{self.max_retries}")
            except aiohttp.ClientError as e:
                last_error = str(e)
                logger.warning(f"AI client error: {e}, retry {attempt + 1}/{self.max_retries}")
                await asyncio.sleep(1)

        metrics.ai_analyst_calls.labels(result="failure").inc()
        logger.error(f"AI complete failed after {self.max_retries + 1} attempts: {last_error}")
        return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

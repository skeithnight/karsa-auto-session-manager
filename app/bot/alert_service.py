"""AlertService — proactive Telegram push alerts from trading pipeline.

Lazy bot registration: created in main() with chat_id, bot registered by run_bot() after PTB init.
Alerts before bot is ready are silently dropped (acceptable — no trades before bot starts).

Rate limiting: same-prefix messages deduplicated within 60s cooldown.
Emergency messages (🚨 prefix) bypass rate limit — circuit breaker / watchdog must always reach user.

Callers: main.py (creates), run_bot (registers bot), SOR (fill alerts),
         CheckpointManager (exit alerts), CircuitBreaker (halt alerts).
Affected API: AlertService.send(text: str) -> None
Data schemas: none — sends HTML text to Telegram.
User instruction: "b" (set up Telegram alerts).
"""

from __future__ import annotations

import time

from loguru import logger


class AlertService:
    """Push alerts to Telegram from the trading pipeline.

    Rate limiting: same-prefix messages deduplicated within cooldown window.
    Emergency messages (🚨 prefix) always send — circuit breaker / watchdog.
    """

    # Trade & emergency prefixes that always bypass rate limiting
    _ALWAYS_SEND_PREFIXES = ("🚨", "🚀", "🎯", "🛑", "🛡️")

    def __init__(self, chat_id: str, rate_limit_seconds: int = 60) -> None:
        self._chat_id = int(chat_id) if chat_id else 0
        self._bot = None
        self._queue: list[str] = []
        # Rate limiting: prefix (first 50 chars including symbol) → last send timestamp
        self._send_history: dict[str, float] = {}
        self._rate_limit_seconds = rate_limit_seconds

    def register_bot(self, bot) -> None:
        """Set bot instance. Called by run_bot() after PTB application starts."""
        self._bot = bot
        logger.info(f"AlertService bot registered, chat_id={self._chat_id}")

        # Flush any queued messages
        import asyncio

        for msg in self._queue:
            asyncio.create_task(self.send(msg))
        self._queue.clear()

    async def send(self, text: str) -> None:
        """Send HTML message to configured chat. Queues if bot not ready.

        Trade execution and emergency messages always send immediately.
        Other repetitive messages use 60s rate limit per prefix.
        """
        if not self._chat_id:
            return
        if not self._bot:
            self._queue.append(text)
            logger.debug("AlertService: Bot not ready, queued message.")
            return

        # Always send trade execution and emergency alerts immediately
        prefix = text[:50]
        is_bypass = any(p in text for p in self._ALWAYS_SEND_PREFIXES)

        if not is_bypass:
            now = time.monotonic()
            last_send = self._send_history.get(prefix, 0.0)
            if now - last_send < self._rate_limit_seconds:
                logger.debug(
                    f"alert_rate_limited: {prefix!r} "
                    f"cooldown_remaining={self._rate_limit_seconds - (now - last_send):.1f}s"
                )
                return
            self._send_history[prefix] = now

        try:
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"alert_send_failed: {e}")

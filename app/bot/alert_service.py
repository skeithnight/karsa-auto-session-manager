"""AlertService — proactive Telegram push alerts from trading pipeline.

Lazy bot registration: created in main() with chat_id, bot registered by run_bot() after PTB init.
Alerts before bot is ready are silently dropped (acceptable — no trades before bot starts).

Callers: main.py (creates), run_bot (registers bot), SOR (fill alerts),
         CheckpointManager (exit alerts), CircuitBreaker (halt alerts).
Affected API: AlertService.send(text: str) -> None
Data schemas: none — sends HTML text to Telegram.
User instruction: "b" (set up Telegram alerts).
"""

from __future__ import annotations

from loguru import logger


class AlertService:
    """Push alerts to Telegram from the trading pipeline."""

    def __init__(self, chat_id: str) -> None:
        self._chat_id = int(chat_id) if chat_id else 0
        self._bot = None

    def register_bot(self, bot) -> None:
        """Set bot instance. Called by run_bot() after PTB application starts."""
        self._bot = bot
        logger.info(f"AlertService bot registered, chat_id={self._chat_id}")

    async def send(self, text: str) -> None:
        """Send HTML message to configured chat. No-op if bot not registered or chat_id empty."""
        if not self._bot or not self._chat_id:
            return
        try:
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"alert_send_failed: {e}")

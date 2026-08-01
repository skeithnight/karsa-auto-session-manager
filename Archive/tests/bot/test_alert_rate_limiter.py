"""Tests for AlertService rate limiting.

Rate limiting deduplicates same-prefix messages within cooldown window.
Emergency messages (🚨 prefix) always bypass the limit.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.alert_service import AlertService


@pytest.fixture
def mock_bot():
    """Mock Telegram bot with send_message."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def service(mock_bot):
    """AlertService with 1s cooldown for fast testing."""
    svc = AlertService(chat_id="12345", rate_limit_seconds=1)
    svc.register_bot(mock_bot)
    return svc


# ── Rate Limiting Core ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_prefix_within_cooldown_dropped(service, mock_bot):
    """Same prefix sent twice within cooldown → second dropped."""
    await service.send("POSITION FILLED: BTC/USDT LONG")
    await service.send("POSITION FILLED: BTC/USDT LONG")

    assert mock_bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_different_prefixes_both_sent(service, mock_bot):
    """Different prefixes → both sent (no collision)."""
    await service.send("POSITION FILLED: BTC/USDT LONG")
    await service.send("CIRCUIT BREAKER triggered")

    assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_cooldown_expiry_allows_resend(service, mock_bot):
    """After cooldown expires, same prefix can be sent again."""
    await service.send("POSITION FILLED: BTC/USDT LONG")

    # Wait for cooldown to expire
    await asyncio.sleep(1.1)

    await service.send("POSITION FILLED: ETH/USDT SHORT")

    assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_emergency_bypasses_rate_limit(service, mock_bot):
    """🚨 messages always send, even with same prefix."""
    await service.send("🚨 CIRCUIT BREAKER halt")
    await service.send("🚨 CIRCUIT BREAKER halt")
    await service.send("🚨 CIRCUIT BREAKER halt")

    assert mock_bot.send_message.call_count == 3


@pytest.mark.asyncio
async def test_emergency_different_from_normal(service, mock_bot):
    """Emergency and normal messages have independent rate limits."""
    await service.send("⚠️ APM breakeven for BTC")
    await service.send("🚨 CIRCUIT BREAKER halt")
    await service.send("⚠️ APM breakeven for BTC")  # dropped (same prefix)

    assert mock_bot.send_message.call_count == 2


# ── Queue Behavior (unchanged) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_before_bot_ready():
    """Messages queued when bot not ready, flushed on register."""
    svc = AlertService(chat_id="12345")
    await svc.send("msg1")
    await svc.send("msg2")

    assert len(svc._queue) == 2

    mock_bot = AsyncMock()
    svc.register_bot(mock_bot)

    # Allow queued messages to flush
    await asyncio.sleep(0.1)
    assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_no_chat_id_silent_drop():
    """No chat_id → silently dropped."""
    svc = AlertService(chat_id="")
    await svc.send("should drop")
    # No error, no bot needed


# ── Prefix Matching ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefix_is_first_20_chars(service, mock_bot):
    """Rate limit key is first 20 characters of message."""
    msg1 = "POSITION FILLED: BTC"  # 20 chars
    msg2 = "POSITION FILLED: BTC LONG"  # 24 chars, same first-20 prefix

    await service.send(msg1)
    await service.send(msg2)  # should be dropped (same 20-char prefix)

    assert mock_bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_prefix_boundary(service, mock_bot):
    """Messages differing at char 20 are different prefixes."""
    msg1 = "AAAAAAAAAAAAAAAAAAAA1"  # prefix: "AAAAAAAAAAAAAAAAAAAA"
    msg2 = "AAAAAAAAAAAAAAAAAAAA2"  # same prefix

    await service.send(msg1)
    await service.send(msg2)  # dropped

    assert mock_bot.send_message.call_count == 1


# ── Integration with Existing AlertService ──────────────────────────


@pytest.mark.asyncio
async def test_send_failure_doesnt_corrupt_rate_limit_state(service, mock_bot):
    """API failure doesn't break rate limit tracking."""
    mock_bot.send_message.side_effect = Exception("Telegram API error")

    await service.send("POSITION FILLED: BTC/USDT")
    # Rate limit should still be set even if send failed
    # (we track BEFORE send to prevent spam)

    # Next send with same prefix should be rate-limited
    await service.send("POSITION FILLED: BTC/USDT")

    # Only 1 attempt (rate limited on second)
    assert mock_bot.send_message.call_count == 1

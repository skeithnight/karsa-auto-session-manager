"""Tests for AI Client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.ai_client import AIClient


@pytest.fixture
def client():
    return AIClient(
        router_url="http://127.0.0.1:20129",
        auth_token="test-token",
        model="claude-haiku-3-5",
        timeout_seconds=5.0,
        max_retries=1,
    )


class TestAIClient:
    @pytest.mark.asyncio
    async def test_successful_completion(self, client):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"content": '{"direction": "LONG", "confidence": 80}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.closed = False
        client._session = mock_session

        result = await client.complete("test prompt")
        assert result == '{"direction": "LONG", "confidence": 80}'

    @pytest.mark.asyncio
    async def test_server_error_retry(self, client):
        error_resp = AsyncMock()
        error_resp.status = 502
        error_resp.text = AsyncMock(return_value="bad gateway")
        error_resp.__aenter__ = AsyncMock(return_value=error_resp)
        error_resp.__aexit__ = AsyncMock(return_value=False)

        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.json = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=[error_resp, ok_resp])
        mock_session.closed = False
        client._session = mock_session

        result = await client.complete("test")
        assert result == "ok"
        assert mock_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_client_error_no_retry(self, client):
        error_resp = AsyncMock()
        error_resp.status = 400
        error_resp.text = AsyncMock(return_value="bad request")
        error_resp.__aenter__ = AsyncMock(return_value=error_resp)
        error_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=error_resp)
        mock_session.closed = False
        client._session = mock_session

        result = await client.complete("test")
        assert result is None
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, client):
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError)
        mock_session.closed = False
        client._session = mock_session

        result = await client.complete("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_choices_returns_none(self, client):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"choices": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.closed = False
        client._session = mock_session

        result = await client.complete("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_close(self, client):
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.close()
        mock_session.close.assert_called_once()
        assert client._session is None

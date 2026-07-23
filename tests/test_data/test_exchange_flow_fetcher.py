"""Unit tests for ExchangeFlowFetcher 2.0s timeout and neutral context fallback."""

import pytest
from unittest.mock import patch, AsyncMock
from app.data.exchange_flow_fetcher import ExchangeFlowFetcher


@pytest.mark.asyncio
async def test_exchange_flow_fetcher_missing_url_returns_neutral():
    fetcher = ExchangeFlowFetcher(api_url="")
    data = await fetcher.fetch_flow_data("BTC/USDT")
    assert data["usdt_inflow_m"] == 0.0
    assert data["liq_dominant_side"] == "Neutral"


@pytest.mark.asyncio
async def test_exchange_flow_fetcher_timeout_returns_neutral():
    """Verify strict 2.0s timeout returns neutral context gracefully."""
    fetcher = ExchangeFlowFetcher(api_url="http://invalid.url.test")

    with patch("aiohttp.ClientSession.get", side_effect=TimeoutError()):
        data = await fetcher.fetch_flow_data("BTC/USDT")
        assert data["usdt_inflow_m"] == 0.0
        assert data["btc_outflow_count"] == 0.0
        assert data["liq_volume_m"] == 0.0
        assert data["liq_dominant_side"] == "Neutral"

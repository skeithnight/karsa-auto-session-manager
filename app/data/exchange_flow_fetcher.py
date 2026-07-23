"""Exchange Flow Fetcher — Alternative Data (Netflow & Liquidation Cascades).

Fetches 1H USDT Inflows, BTC Outflows, and 1H Liquidation Volume.
Enforces strict 2.0s async timeout (connect=1.0s) to guarantee zero latency impact
on order execution. Degrades gracefully to neutral context.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from loguru import logger


class ExchangeFlowFetcher:
    """Fetches alternative exchange flow and liquidation data with strict 2.0s timeout."""

    def __init__(self, api_url: str = "", api_key: str = "") -> None:
        self.api_url = api_url
        self.api_key = api_key

    def get_neutral_context(self) -> dict[str, Any]:
        """Default neutral context on timeout, error, or missing API credentials."""
        return {
            "usdt_inflow_m": 0.0,
            "btc_outflow_count": 0.0,
            "liq_volume_m": 0.0,
            "liq_dominant_side": "Neutral",
        }

    async def fetch_flow_data(self, symbol: str) -> dict[str, Any]:
        """Fetch 1H netflow and liquidation metrics with 2.0s hard timeout."""
        if not self.api_url:
            return self.get_neutral_context()

        headers = {}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key

        timeout = aiohttp.ClientTimeout(total=2.0, connect=1.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self.api_url}/v1/flow/{symbol.replace('/', '')}"
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "usdt_inflow_m": float(data.get("usdt_inflow_m", 0.0)),
                            "btc_outflow_count": float(data.get("btc_outflow_count", 0.0)),
                            "liq_volume_m": float(data.get("liq_volume_m", 0.0)),
                            "liq_dominant_side": str(data.get("liq_dominant_side", "Neutral")),
                        }
                    else:
                        logger.warning(
                            f"ExchangeFlowFetcher: API returned status {resp.status} for {symbol}, using neutral context"
                        )
                        return self.get_neutral_context()
        except (TimeoutError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"ExchangeFlowFetcher: strict 2.0s timeout/error for {symbol}: {e}. Returning neutral context.")
            return self.get_neutral_context()
        except Exception as exc:
            logger.error(f"ExchangeFlowFetcher: unexpected exception for {symbol}: {exc}")
            return self.get_neutral_context()

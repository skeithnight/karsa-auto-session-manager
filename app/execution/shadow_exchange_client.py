"""ShadowExchangeClient — Redis-based exchange stub for shadow mode.

Wraps Redis for APM, same interface as BybitClient but reads live prices
from Redis global:state:* keys instead of making API calls.

No real orders are placed — all operations return empty or no-op responses.
"""

from __future__ import annotations

import json as _json
import uuid
from decimal import Decimal

from loguru import logger

from app.core.config import get_settings
from app.core.redis_client import RedisClient


class ShadowExchangeClient:
    """Same interface as BybitClient for APM, reads live prices from Redis."""

    def __init__(self, redis_client: RedisClient) -> None:
        self._redis = redis_client

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def fetch_tickers(self) -> dict:
        """Read live prices from Redis global:state:* keys (written by RedisClient.set_global_state)."""
        result: dict[str, dict] = {}
        try:
            settings = get_settings()
            for symbol in settings.symbols[:10]:
                raw = await self._redis.get(f"global:state:{symbol}")
                if raw:
                    data = _json.loads(raw)
                    bid = Decimal(str(data.get("best_bid", "0")))
                    ask = Decimal(str(data.get("best_ask", "0")))
                    # use mid price for last
                    last = (bid + ask) / 2 if bid and ask else Decimal("0")
                    if bid > 0 and ask > 0:
                        result[symbol] = {"bid": bid, "ask": ask, "last": last}
        except Exception as e:
            logger.warning(f"ShadowExchangeClient: fetch_tickers error: {e}")
        return result

    async def fetch_positions(self) -> list:
        """Return empty — no exchange positions in shadow mode."""
        return []

    async def fetch_open_orders(self) -> list:
        """Return empty — no exchange orders in shadow mode."""
        return []

    async def place_stop_loss(
        self, symbol: str, side: str, price: Decimal, amount: Decimal
    ) -> dict | None:
        """No-op — shadow positions use virtual SL."""
        return None

    async def amend_stop_loss(
        self, symbol: str, side: str, price: Decimal
    ) -> dict | None:
        """No-op."""
        return None

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """No-op."""
        return True

    async def create_market_order(
        self, symbol: str, side: str, amount: Decimal
    ) -> dict | None:
        """Virtual market order — returns fake fill."""
        return {
            "id": f"SHADOW-MKT-{uuid.uuid4().hex[:8]}",
            "status": "filled",
            "is_shadow": True,
        }

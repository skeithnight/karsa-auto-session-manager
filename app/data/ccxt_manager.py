"""Global Data Engine — CCXT Pro WebSocket connections."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict

import ccxt.pro as ccxt_pro
from loguru import logger


class CCXTManager:
    """Manages WebSocket connections to multiple exchanges via CCXT Pro."""

    def __init__(self) -> None:
        logger.debug("CCXTManager.__init__: entering")
        self.exchanges: Dict[str, ccxt_pro.Exchange] = {}
        self.last_update: Dict[str, datetime] = {}
        self.stale_threshold_seconds: int = 15
        logger.debug("CCXTManager.__init__: returning")

    async def start(self) -> None:
        """Initialize exchange connections and start WebSocket streams."""
        logger.debug("start: entering")
        # Binance — spot only
        binance = ccxt_pro.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "defaultSubType": "spot",
                "fetchMarkets": ["spot"],
            },
        })
        self.exchanges["binance"] = binance

        # OKX — spot only
        okx = ccxt_pro.okx({
            "enableRateLimit": True,
        })
        okx.options["defaultType"] = "spot"
        self.exchanges["okx"] = okx

        # Bybit — USDT perpetual
        bybit = ccxt_pro.bybit({
            "enableRateLimit": True,
        })
        bybit.options["defaultType"] = "swap"
        self.exchanges["bybit"] = bybit

        logger.info(f"Initialized exchanges: {list(self.exchanges.keys())}")
        logger.debug("start: returning None")

    async def watch_orderbook(self, symbol: str, exchange_id: str) -> dict:
        """Watch L2 orderbook for a symbol on a specific exchange."""
        logger.debug(f"watch_orderbook: entering symbol={symbol} exchange_id={exchange_id}")
        exchange = self.exchanges.get(exchange_id)
        if not exchange:
            raise ValueError(f"Unknown exchange: {exchange_id}")

        try:
            orderbook = await exchange.watch_order_book(symbol)
            self.last_update[exchange_id] = datetime.now(timezone.utc)
            logger.debug("watch_orderbook: returning dict")
            return orderbook
        except Exception as e:
            logger.error(f"WebSocket error on {exchange_id}: {e}")
            logger.debug(f"watch_orderbook: error={e}")
            raise

    async def watch_trades(self, symbol: str, exchange_id: str) -> list:
        """Watch trades for a symbol on a specific exchange."""
        logger.debug(f"watch_trades: entering symbol={symbol} exchange_id={exchange_id}")
        exchange = self.exchanges.get(exchange_id)
        if not exchange:
            raise ValueError(f"Unknown exchange: {exchange_id}")

        try:
            trades = await exchange.watch_trades(symbol)
            self.last_update[exchange_id] = datetime.now(timezone.utc)
            logger.debug("watch_trades: returning list")
            return trades
        except Exception as e:
            logger.error(f"WebSocket error on {exchange_id}: {e}")
            logger.debug(f"watch_trades: error={e}")
            raise

    def is_stale(self, exchange_id: str) -> bool:
        """Check if an exchange feed is stale (no update >15s)."""
        logger.debug(f"is_stale: entering exchange_id={exchange_id}")
        last = self.last_update.get(exchange_id)
        if not last:
            logger.debug("is_stale: returning True (no last update)")
            return True

        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        result = elapsed > self.stale_threshold_seconds
        logger.debug(f"is_stale: returning {result}")
        return result

    async def close(self) -> None:
        """Close all exchange connections."""
        logger.debug("close: entering")
        for exchange_id, exchange in self.exchanges.items():
            try:
                await exchange.close()
                logger.info(f"Closed {exchange_id} connection")
            except Exception as e:
                logger.error(f"Error closing {exchange_id}: {e}")
        logger.debug("close: returning None")

"""Bybit Executor — Private WebSocket connection for order execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import ccxt.pro as ccxt_pro
from loguru import logger

from app.core.config import get_settings


class BybitClient:
    """Manages Bybit connection and order execution."""

    def __init__(self) -> None:
        logger.debug("BybitClient.__init__: entering")
        self.settings = get_settings()
        self.exchange: Optional[ccxt_pro.Exchange] = None
        self.connected: bool = False
        logger.debug("BybitClient.__init__: returning")

    async def connect(self) -> None:
        """Initialize Bybit exchange connection."""
        logger.debug("connect: entering")
        self.exchange = ccxt_pro.bybit({
            "apiKey": self.settings.bybit_api_key,
            "secret": self.settings.bybit_api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        # Bypass load_markets (triggers private fetch_currencies). Load spot+swap via public API only.
        try:
            spot = await self.exchange.publicGetV5MarketInstrumentsInfo({"category": "spot"})
            swap = await self.exchange.publicGetV5MarketInstrumentsInfo({"category": "linear"})
            all_markets = spot.get("result", {}).get("list", []) + swap.get("result", {}).get("list", [])
            self.exchange.set_markets(self.exchange.parse_markets(all_markets), {"USDT": {"id": "USDT", "code": "USDT", "name": "Tether", "active": True, "fee": None, "precision": 8, "limits": {"amount": {"min": None, "max": None}, "withdraw": {"min": None, "max": None}}, "networks": {}}})
        except Exception as e:
            logger.warning(f"Market loading failed: {e}")
        # Prevent CCXT from calling private fetch_currencies on any subsequent API call
        self.exchange.options["fetchCurrencies"] = False
        self.exchange.currenciesLoaded = True
        self.connected = True
        logger.info("Bybit connected")
        logger.debug("connect: returning None")

    async def disconnect(self) -> None:
        """Close Bybit connection."""
        logger.debug("disconnect: entering")
        if self.exchange:
            await self.exchange.close()
            self.connected = False
            logger.info("Bybit disconnected")
        logger.debug("disconnect: returning None")

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage for a symbol."""
        logger.debug(f"set_leverage: entering symbol={symbol} leverage={leverage}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            result = await self.exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set: {symbol} = {leverage}x")
            logger.debug("set_leverage: returning dict")
            return result
        except Exception as e:
            logger.error(f"Failed to set leverage: {e}")
            logger.debug(f"set_leverage: error={e}")
            raise

    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place a limit order (Post-Only by default)."""
        logger.debug(f"create_limit_order: entering symbol={symbol} side={side}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        order_params = params or {}
        order_params["postOnly"] = True

        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=float(amount),
                price=float(price),
                params=order_params,
            )
            logger.info(f"Limit order placed: {order['id']} {side} {amount} @ {price}")
            logger.debug("create_limit_order: returning dict")
            return order
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            logger.debug(f"create_limit_order: error={e}")
            raise

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place a market order (IOC by default)."""
        logger.debug(f"create_market_order: entering symbol={symbol} side={side}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        order_params = params or {}
        order_params["timeInForce"] = "IOC"

        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=float(amount),
                params=order_params,
            )
            logger.info(f"Market order placed: {order['id']} {side} {amount}")
            logger.debug("create_market_order: returning dict")
            return order
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            logger.debug(f"create_market_order: error={e}")
            raise

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an open order."""
        logger.debug(f"cancel_order: entering order_id={order_id}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            result = await self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Order cancelled: {order_id}")
            logger.debug("cancel_order: returning dict")
            return result
        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            logger.debug(f"cancel_order: error={e}")
            raise

    async def amend_order(
        self,
        order_id: str,
        symbol: str,
        price: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Amend an existing order's price/amount."""
        logger.debug(f"amend_order: entering order_id={order_id}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        params: Dict[str, Any] = {"orderId": order_id}
        try:
            if price is not None and amount is not None:
                order = await self.exchange.edit_order(
                    order_id, symbol, "limit", "buy" if amount > 0 else "sell",
                    float(amount), float(price), params,
                )
            elif price is not None:
                order = await self.exchange.edit_order(
                    order_id, symbol, "limit", "buy",
                    0, float(price), params,
                )
            else:
                raise ValueError("Price required for amend")
            logger.info(f"Order amended: {order_id} -> {price}")
            logger.debug("amend_order: returning dict")
            return order
        except Exception as e:
            logger.error(f"Amend order failed: {e}")
            logger.debug(f"amend_order: error={e}")
            raise

    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch current USDT balance."""
        logger.debug("fetch_balance: entering")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            balance = await self.exchange.fetch_balance({"type": "spot"})
            usdt = balance.get("USDT", {})
            free = Decimal(str(usdt.get("free", 0)))
            used = Decimal(str(usdt.get("used", 0)))
            total = Decimal(str(usdt.get("total", 0)))
            result = {"free": free, "used": used, "total": total}
            logger.debug("fetch_balance: returning dict")
            return result
        except Exception as e:
            logger.error(f"Fetch balance failed: {e}")
            logger.debug(f"fetch_balance: error={e}")
            raise

    async def get_wallet_balance(self) -> dict:
        """Get wallet balance — returns {balance, available} for dashboard."""
        logger.debug("get_wallet_balance: entering")
        try:
            balance_data = await self.fetch_balance()
            result = {
                "balance": balance_data.get("total", Decimal("0")),
                "available": balance_data.get("free", Decimal("0")),
            }
            logger.info(f"get_wallet_balance: balance={result['balance']} available={result['available']}")
            logger.debug("get_wallet_balance: returning dict")
            return result
        except Exception as e:
            logger.error(f"get_wallet_balance: error={e}")
            return {"balance": Decimal("0"), "available": Decimal("0"), "error": str(e)}

    async def fetch_positions(self) -> list:
        """Fetch all open positions."""
        logger.debug("fetch_positions: entering")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            positions = await self.exchange.fetch_positions(params={"category": "linear", "settleCoin": "USDT"})
            result = [
                {
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "contracts": Decimal(str(p["contracts"])),
                    "entry_price": Decimal(str(p["entryPrice"] or 0)),
                    "unrealized_pnl": Decimal(str(p["unrealizedPnl"] or 0)),
                }
                for p in positions if p["contracts"]
            ]
            logger.debug(f"fetch_positions: returning list_len={len(result)}")
            return result
        except Exception as e:
            logger.error(f"Fetch positions failed: {e}")
            logger.debug(f"fetch_positions: error={e}")
            raise

    async def fetch_open_orders(self) -> list:
        """Fetch all open orders."""
        logger.debug("fetch_open_orders: entering")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            orders = await self.exchange.fetch_open_orders()
            result = [
                {
                    "id": o["id"],
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "price": Decimal(str(o["price"])),
                    "amount": Decimal(str(o["amount"])),
                    "status": o["status"],
                }
                for o in orders
            ]
            logger.debug(f"fetch_open_orders: returning list_len={len(result)}")
            return result
        except Exception as e:
            logger.error(f"Fetch open orders failed: {e}")
            logger.debug(f"fetch_open_orders: error={e}")
            raise

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_price: Decimal,
        amount: Decimal,
    ) -> Optional[Dict[str, Any]]:
        """Place exchange-side stop-loss (conditional market order).

        CLAUDE.md Rule 5: Every position MUST get an exchange-side SL immediately on fill.
        """
        logger.debug(f"place_stop_loss: entering symbol={symbol} side={side} stop_price={stop_price}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        # Close side is opposite of position side
        close_side = "sell" if side == "buy" else "buy"
        try:
            order = await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                float(amount),
                None,
                {"stopLossPrice": str(stop_price)},
            )
            logger.info(f"Stop-loss placed: {order.get('id')} @ {stop_price}")
            logger.debug(f"place_stop_loss: returning order_id={order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Stop-loss placement FAILED: {e}")
            logger.debug(f"place_stop_loss: error={e}")
            raise

    async def amend_stop_loss(
        self,
        order_id: str,
        symbol: str,
        side: str,
        new_price: Decimal,
        amount: Decimal,
    ) -> Optional[Dict[str, Any]]:
        """Amend existing stop-loss order. Cancels old, places new."""
        logger.debug(f"amend_stop_loss: entering order_id={order_id} new_price={new_price}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            await self.cancel_order(order_id, symbol)
            logger.info(f"Cancelled old SL: {order_id}")
        except Exception as e:
            logger.warning(f"Failed to cancel old SL {order_id}: {e}")

        new_order = await self.place_stop_loss(symbol, side, new_price, amount)
        logger.debug("amend_stop_loss: returning new order")
        return new_order

    async def watch_orders(self, symbol: Optional[str] = None) -> list:
        """Watch for order updates via WebSocket."""
        logger.debug(f"watch_orders: entering symbol={symbol}")
        if not self.connected or not self.exchange:
            raise RuntimeError("Bybit not connected")

        try:
            orders = await self.exchange.watch_orders(symbol)
            logger.debug("watch_orders: returning list")
            return orders
        except Exception as e:
            logger.error(f"Watch orders error: {e}")
            logger.debug(f"watch_orders: error={e}")
            raise

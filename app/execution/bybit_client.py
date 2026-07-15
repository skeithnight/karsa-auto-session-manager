"""Bybit Executor — pybit (Bybit Unified Trading API) for order execution.

Replaces CCXT Pro for Bybit-specific private API calls.
CCXT remains for Binance/OKX public data feeds.
Reference: karsa-claude-trading/src/data/bybit_client.py pattern.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger
from pybit.unified_trading import HTTP

from app.core import metrics
from app.core.config import get_settings


def _safe_decimal(value: Any, default: str = "0") -> Decimal:
    """Convert value to Decimal safely."""
    from decimal import DecimalException
    try:
        return Decimal(str(value)) if value is not None else Decimal(default)
    except (ValueError, TypeError, DecimalException):
        return Decimal(default)


class BybitClient:
    """Manages Bybit connection and order execution via pybit."""

    def __init__(self) -> None:
        logger.debug("BybitClient.__init__: entering")
        self.settings = get_settings()
        self.session: Optional[HTTP] = None
        self.connected: bool = False
        self._lock = asyncio.Lock()
        self._symbol_map: Dict[str, str] = {}  # ccxt symbol → bybit symbol
        self._lot_sizes: Dict[str, Decimal] = {}  # ccxt symbol → lot size step
        self._min_qty: Dict[str, Decimal] = {}  # ccxt symbol → min order qty
        logger.debug("BybitClient.__init__: returning")

    async def connect(self) -> None:
        """Initialize pybit HTTP session and build symbol mapping."""
        logger.debug("connect: entering")
        self.session = HTTP(
            api_key=self.settings.bybit_api_key,
            api_secret=self.settings.bybit_api_secret,
            testnet=self.settings.bybit_testnet,
        )
        self.connected = True
        mode = "TESTNET" if self.settings.bybit_testnet else "LIVE"
        # Build symbol map: ccxt "PEPE/USDT" → bybit "1000PEPEUSDT"
        try:
            resp = await asyncio.to_thread(
                self.session.get_instruments_info, category="linear"
            )
            if resp.get("retCode") == 0:
                for inst in resp["result"]["list"]:
                    bybit_sym = inst["symbol"]
                    if not bybit_sym.endswith("USDT"):
                        continue  # skip PERP contracts
                    base = bybit_sym.removesuffix("USDT")
                    # Strip leading multiplier digits: 1000PEPE → PEPE
                    i = 0
                    while i < len(base) and base[i].isdigit():
                        i += 1
                    token = base[i:] if i > 0 else base
                    if not token:  # skip pure-numeric symbols like "4USDT"
                        continue
                    ccxt_sym = f"{token}/USDT"
                    self._symbol_map[ccxt_sym] = bybit_sym
                    # Store lot size and min qty for order rounding
                    lot_filter = inst.get("lotSizeFilter", {})
                    ls = lot_filter.get("qtyStep", "1")
                    mq = lot_filter.get("minOrderQty", "1")
                    self._lot_sizes[ccxt_sym] = Decimal(str(ls))
                    self._min_qty[ccxt_sym] = Decimal(str(mq))
                logger.info(
                    f"Bybit connected ({mode}), {len(self._symbol_map)} symbols mapped"
                )
            else:
                logger.warning(f"Failed to fetch instruments: {resp.get('retMsg')}")
        except Exception as e:
            logger.warning(f"Symbol map fetch failed: {e}, using naive mapping")
        logger.debug("connect: returning None")

    async def disconnect(self) -> None:
        """Close pybit session."""
        logger.debug("disconnect: entering")
        self.connected = False
        self.session = None
        logger.info("Bybit disconnected")
        logger.debug("disconnect: returning None")

    def _to_bybit_symbol(self, symbol: str) -> str:
        """Convert ccxt symbol to Bybit format (handles 1000x prefixes)."""
        if symbol in self._symbol_map:
            return self._symbol_map[symbol]
        return symbol.replace("/", "")

    def _round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """Round quantity to Bybit's lot size step, enforce minimum."""
        lot = self._lot_sizes.get(symbol, Decimal("1"))
        min_q = self._min_qty.get(symbol, Decimal("1"))
        if lot > 0:
            qty = (qty / lot).to_integral_value() * lot
        return max(qty, min_q)

    async def _execute(self, func, *args, **kwargs) -> dict:
        """Run sync pybit call in thread with retry and timeout."""
        async with self._lock:
            last_exc = None
            for attempt in range(3):
                try:
                    start = time.monotonic()
                    resp = await asyncio.wait_for(
                        asyncio.to_thread(func, *args, **kwargs),
                        timeout=15,
                    )
                    elapsed_ms = (time.monotonic() - start) * 1000
                    metrics.proxy_latency.observe(elapsed_ms)
                    if resp.get("retCode") == 0:
                        return resp.get("result", {})
                    ret_code = resp.get("retCode")
                    ret_msg = resp.get("retMsg", "")
                    if ret_code in (10001, 10002, 10003):
                        raise Exception(f"Bybit auth error: {ret_msg}")
                    raise Exception(f"Bybit API error [{ret_code}]: {ret_msg}")
                except asyncio.TimeoutError:
                    last_exc = Exception(f"Bybit timeout on attempt {attempt + 1}")
                    logger.warning(f"pybit_timeout attempt={attempt + 1}")
                    if attempt < 2:
                        await asyncio.sleep(1 * (attempt + 1))
                except Exception as e:
                    last_exc = e
                    logger.warning(f"pybit_error attempt={attempt + 1}: {e}")
                    if attempt < 2:
                        await asyncio.sleep(1 * (attempt + 1))
            raise last_exc or Exception("Bybit call failed after 3 attempts")

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage for a symbol."""
        logger.debug(f"set_leverage: entering symbol={symbol} leverage={leverage}")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        result = await self._execute(
            self.session.set_leverage,
            category="linear",
            symbol=self._to_bybit_symbol(symbol),
            buyLeverage=str(leverage),
            sellLeverage=str(leverage),
        )
        logger.info(f"Leverage set: {symbol} = {leverage}x")
        return result

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
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        order_params = {
            "category": "linear",
            "symbol": self._to_bybit_symbol(symbol),
            "side": side.capitalize(),
            "orderType": "Limit",
            "qty": str(self._round_qty(symbol, amount)),
            "price": str(price),
            "timeInForce": "PostOnly",
        }
        if params:
            order_params.update(params)
        result = await self._execute(self.session.place_order, **order_params)
        logger.info(f"Limit order placed: {result.get('orderId')} {side} {amount} @ {price}")
        return result

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Place a market order."""
        logger.debug(f"create_market_order: entering symbol={symbol} side={side}")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        order_params = {
            "category": "linear",
            "symbol": self._to_bybit_symbol(symbol),
            "side": side.capitalize(),
            "orderType": "Market",
            "qty": str(self._round_qty(symbol, amount)),
        }
        if params:
            order_params.update(params)
        result = await self._execute(self.session.place_order, **order_params)
        logger.info(f"Market order placed: {result.get('orderId')} {side} {amount}")
        return result

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an open order."""
        logger.debug(f"cancel_order: entering order_id={order_id}")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        result = await self._execute(
            self.session.cancel_order,
            category="linear",
            symbol=self._to_bybit_symbol(symbol),
            orderId=order_id,
        )
        logger.info(f"Order cancelled: {order_id}")
        return result

    async def amend_order(
        self,
        order_id: str,
        symbol: str,
        price: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Amend an existing order's price/amount."""
        logger.debug(f"amend_order: entering order_id={order_id}")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        if price is None:
            raise ValueError("Price required for amend")
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": self._to_bybit_symbol(symbol),
            "orderId": order_id,
            "price": str(price),
        }
        if amount is not None:
            params["qty"] = str(amount)
        result = await self._execute(self.session.amend_order, **params)
        logger.info(f"Order amended: {order_id} -> {price}")
        return result

    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch current USDT balance."""
        logger.debug("fetch_balance: entering")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        result = await self._execute(
            self.session.get_wallet_balance,
            accountType="UNIFIED",
        )
        coins = result.get("list", [{}])[0].get("coin", [])
        usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
        total = _safe_decimal(usdt.get("equity"))
        # ponytail: availableToWithdraw is empty on UNIFIED accounts.
        # available = equity - order margin - position margin
        order_im = _safe_decimal(usdt.get("totalOrderIM"))
        pos_im = _safe_decimal(usdt.get("totalPositionIM"))
        used = order_im + pos_im
        free = total - used
        balance = {"free": free, "used": used, "total": total}
        logger.debug("fetch_balance: returning dict")
        return balance

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
            return result
        except Exception as e:
            logger.error(f"get_wallet_balance: error={e}")
            return {"balance": Decimal("0"), "available": Decimal("0"), "error": str(e)}

    async def fetch_positions(self) -> list:
        """Fetch all open positions."""
        logger.debug("fetch_positions: entering")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        result = await self._execute(
            self.session.get_positions,
            category="linear",
            settleCoin="USDT",
        )
        positions = [
            {
                "symbol": p["symbol"],
                "side": "buy" if p.get("side") == "Buy" else "sell",
                "contracts": _safe_decimal(p.get("size")),
                "entry_price": _safe_decimal(p.get("avgPrice")),
                "unrealized_pnl": _safe_decimal(p.get("unrealisedPnl")),
            }
            for p in result.get("list", [])
            if _safe_decimal(p.get("size")) > 0
        ]
        logger.debug(f"fetch_positions: returning list_len={len(positions)}")
        return positions

    async def fetch_open_orders(self) -> list:
        """Fetch all open orders."""
        logger.debug("fetch_open_orders: entering")
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        result = await self._execute(
            self.session.get_open_orders,
            category="linear",
            settleCoin="USDT",
        )
        orders = [
            {
                "id": o["orderId"],
                "symbol": o["symbol"],
                "side": "buy" if o.get("side") == "Buy" else "sell",
                "price": _safe_decimal(o.get("price")),
                "amount": _safe_decimal(o.get("qty")),
                "status": o.get("orderStatus", "").lower(),
            }
            for o in result.get("list", [])
        ]
        logger.debug(f"fetch_open_orders: returning list_len={len(orders)}")
        return orders

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
        if not self.connected or not self.session:
            raise RuntimeError("Bybit not connected")
        close_side = "Sell" if side == "buy" else "Buy"
        result = await self._execute(
            self.session.place_order,
            category="linear",
            symbol=self._to_bybit_symbol(symbol),
            side=close_side,
            orderType="Market",
            qty=str(amount),
            stopLoss=str(stop_price),
            tpslMode="Full",
        )
        logger.info(f"Stop-loss placed: {result.get('orderId')} @ {stop_price}")
        return result

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
        if not self.connected or not self.session:
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
        """Watch for order updates — not supported via pybit HTTP.
        Returns empty list; order tracking done via polling fetch_open_orders.
        """
        logger.debug(f"watch_orders: entering symbol={symbol}")
        logger.warning("watch_orders: pybit HTTP does not support WebSocket order streaming; use fetch_open_orders polling")
        return []

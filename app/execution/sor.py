"""Smart Order Routing — Post-Only → Reprice → Market fallback."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

from app.core import metrics
from app.execution.bybit_client import BybitClient


class SmartOrderRouter:
    """3-step order routing: Post-Only → Reprice → Market/IOC."""

    def __init__(self, bybit_client: BybitClient, max_reprice_attempts: int = 2, reprice_delay_seconds: float = 2.0, alert_service: object | None = None) -> None:
        logger.debug("SmartOrderRouter.__init__: entering")
        self.client = bybit_client
        self.max_reprice_attempts = max_reprice_attempts
        self.reprice_delay_seconds = reprice_delay_seconds
        self.skip_to_market = False
        self.alert_service = alert_service
        logger.debug("SmartOrderRouter.__init__: returning")

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal = Decimal("0.01"),
        max_loss_usd: Decimal = Decimal("1.00"),
    ) -> Optional[Dict[str, Any]]:
        """Execute order with 3-step fallback + exchange-side SL on fill.
        Callers: executor_task in main.py.
        API change: sl_distance_pct replaced with max_loss_usd (absolute USD loss cap).
        SL price = fill_price - (max_loss_usd / amount) for LONG, + for SHORT.
        1. Post-Only Limit
        2. Reprice (up to max_reprice_attempts)
        3. Market/IOC fallback
        4. Place exchange-side Stop-Loss immediately on fill (CLAUDE.md Rule 5)
        """
        logger.debug(f"execute: entering symbol={symbol} side={side}")
        order: Optional[Dict[str, Any]] = None

        # High latency mode — skip to market directly
        if self.skip_to_market:
            logger.info(f"SOR: latency mode — market order {side} {amount}")
            try:
                market_order = await self.client.create_market_order(symbol, side, amount)
                await self._place_sl_after_fill(symbol, side, price, amount, max_loss_usd)
                return market_order
            except Exception as e:
                logger.error(f"SOR market fallback failed: {e}")
                return None

        # Step 1: Post-Only Limit
        logger.info(f"SOR Step 1: Post-Only Limit {side} {amount} @ {price}")
        try:
            order = await self.client.create_limit_order(symbol, side, amount, price)
            if order.get("status") == "open":
                logger.info(f"Post-Only filled: {order['id']}")
                metrics.orders_placed.labels(symbol=symbol, side=side).inc()
                await self._place_sl_after_fill(symbol, side, price, amount, max_loss_usd)
                logger.debug("execute: returning dict (Post-Only filled)")
                return order
        except Exception as e:
            logger.warning(f"Post-Only failed: {e}")

        # Step 2: Reprice attempts
        current_price = price
        for attempt in range(self.max_reprice_attempts):
            await asyncio.sleep(self.reprice_delay_seconds)

            # Move price toward market (buy: higher, sell: lower)
            if side == "buy":
                current_price += price_tick
            else:
                current_price -= price_tick

            logger.info(f"SOR Step 2: Reprice attempt {attempt + 1} @ {current_price}")
            try:
                # Cancel unfilled order if exists
                if order and order.get("id"):
                    await self.client.cancel_order(order["id"], symbol)

                order = await self.client.create_limit_order(symbol, side, amount, current_price)
                if order.get("status") == "open":
                    logger.info(f"Reprice filled: {order['id']}")
                    await self._place_sl_after_fill(symbol, side, current_price, amount, max_loss_usd)
                    logger.debug("execute: returning dict (Reprice filled)")
                    return order
            except Exception as e:
                logger.warning(f"Reprice failed: {e}")

        # Step 3: Market/IOC fallback
        logger.info("SOR Step 3: Market/IOC fallback")
        try:
            if order and order.get("id"):
                await self.client.cancel_order(order["id"], symbol)

            market_order = await self.client.create_market_order(symbol, side, amount)
            logger.info(f"Market fallback filled: {market_order['id']}")
            metrics.orders_placed.labels(symbol=symbol, side=side).inc()
            await self._place_sl_after_fill(symbol, side, price, amount, max_loss_usd)
            logger.debug("execute: returning dict (Market fallback)")
            return market_order
        except Exception as e:
            metrics.orders_failed.labels(symbol=symbol, error_type=type(e).__name__).inc()
            logger.error(f"Market fallback failed: {e}")
            logger.debug(f"execute: error={e}")
            return None

    async def _place_sl_after_fill(
        self,
        symbol: str,
        side: str,
        fill_price: Decimal,
        amount: Decimal,
        max_loss_usd: Decimal,
    ) -> None:
        """Place exchange-side SL immediately after fill. CLAUDE.md Rule 5.
        SL price: loss = max_loss_usd / amount, so SL is at fill_price - loss (LONG) or + loss (SHORT)."""
        try:
            sl_distance = max_loss_usd / amount if amount > 0 else Decimal("0")
            if side == "buy":
                sl_price = fill_price - sl_distance
            else:
                sl_price = fill_price + sl_distance

            sl_order = await self.client.place_stop_loss(symbol, side, sl_price, amount)
            if sl_order:
                metrics.stop_loss_placement.labels(symbol=symbol, result="success").inc()
                logger.info(f"Exchange-side SL placed: {sl_order.get('id')} @ {sl_price}")
                # Push entry alert to Telegram
                if self.alert_service:
                    from app.bot.utils.formatters import format_entry_alert
                    await self.alert_service.send(format_entry_alert(symbol, side, fill_price, amount, sl_price))
            else:
                metrics.stop_loss_placement.labels(symbol=symbol, result="failed").inc()
                logger.critical(f"SL PLACEMENT RETURNED NONE for {symbol} {side} — position unprotected!")
        except Exception as e:
            metrics.stop_loss_placement.labels(symbol=symbol, result="failed").inc()
            logger.critical(f"SL PLACEMENT FAILED for {symbol} {side}: {e} — position UNPROTECTED!")
            logger.debug(f"_place_sl_after_fill: error={e}")

    async def cancel_all(self, symbol: str) -> None:
        """Cancel all open orders for a symbol."""
        logger.debug(f"cancel_all: entering symbol={symbol}")
        try:
            orders = await self.client.fetch_open_orders()
            for order in orders:
                if order["symbol"] == symbol:
                    await self.client.cancel_order(order["id"], symbol)
                    logger.info(f"Cancelled order: {order['id']}")
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            logger.debug(f"cancel_all: error={e}")
        logger.debug("cancel_all: returning None")

    async def cancel_all_positions(self) -> None:
        """Cancel all open orders across all symbols — used by emergency kill/sell-all."""
        logger.debug("cancel_all_positions: entering")
        try:
            orders = await self.client.fetch_open_orders()
            for order in orders:
                await self.client.cancel_order(order["id"], order["symbol"])
                logger.info(f"Cancelled order: {order['id']} ({order['symbol']})")
        except Exception as e:
            logger.error(f"Cancel all positions failed: {e}")
            logger.debug(f"cancel_all_positions: error={e}")
        logger.debug("cancel_all_positions: returning None")

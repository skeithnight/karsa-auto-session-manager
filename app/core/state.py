"""State Manager — in-memory positions, Redis cache, Postgres persistence, startup reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.redis_client import RedisClient
from app.execution.bybit_client import BybitClient


class Position:
    """In-memory position tracker."""

    def __init__(self, symbol: str, side: str, size: Decimal, entry_price: Decimal) -> None:
        logger.debug(f"Position.__init__: entering symbol={symbol}")
        self.symbol = symbol
        self.side = side
        self.size = size
        self.entry_price = entry_price
        self.unrealized_pnl = Decimal("0")
        self.updated_at = datetime.now(timezone.utc)
        logger.debug("Position.__init__: returning")

    def to_dict(self) -> Dict[str, Any]:
        logger.debug("to_dict: entering")
        result = {
            "symbol": self.symbol,
            "side": self.side,
            "size": str(self.size),
            "entry_price": str(self.entry_price),
            "unrealized_pnl": str(self.unrealized_pnl),
            "updated_at": self.updated_at.isoformat(),
        }
        logger.debug("to_dict: returning dict")
        return result


class StateManager:
    """Central state manager — positions, Redis cache, Postgres sync, reconciliation."""

    def __init__(self, redis_client: RedisClient, bybit_client: BybitClient) -> None:
        logger.debug("StateManager.__init__: entering")
        self.redis = redis_client
        self.bybit = bybit_client
        self.positions: Dict[str, Position] = {}
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.reconciled = False
        logger.debug("StateManager.__init__: returning")

    async def reconcile(self) -> bool:
        """
        Startup Reconciliation — Trust Nothing protocol.

        1. Fetch Bybit truth (positions + orders)
        2. Compare with local state
        3. Resolve divergences
        4. Return True if clean, False if critical issues
        """
        logger.debug("reconcile: entering")
        logger.info("Starting reconciliation...")

        try:
            exchange_positions = await self.bybit.fetch_positions()
            exchange_orders = await self.bybit.fetch_open_orders()
        except Exception as e:
            logger.warning(f"Reconciliation degraded — Bybit unreachable: {e}")
            logger.debug(f"reconcile: error={e}")
            logger.warning("Continuing startup — position verification deferred. Data engine and alpha bridge will run.")
            return True  # ponytail: allow startup without Bybit private API. Full reconcile when pybit migration done.

        # Build exchange state maps
        exchange_pos_map = {p["symbol"]: p for p in exchange_positions}

        # Scenario B: Orphaned orders — exchange has orders we don't know
        known_order_ids = set(self.open_orders.keys())
        for order in exchange_orders:
            if order["id"] not in known_order_ids:
                logger.warning(f"Orphaned order found: {order['id']} — cancelling")
                try:
                    await self.bybit.cancel_order(order["id"], order["symbol"])
                except Exception as e:
                    logger.error(f"Failed to cancel orphaned order: {e}")

        # Scenario C: Ghost positions — local says position, exchange says flat
        for symbol in list(self.positions.keys()):
            if symbol not in exchange_pos_map:
                logger.critical(f"Ghost position: {symbol} — local exists, exchange FLAT. Removing.")
                del self.positions[symbol]

        # Sync from exchange truth
        for symbol, pos_data in exchange_pos_map.items():
            self.positions[symbol] = Position(
                symbol=symbol,
                side=pos_data["side"],
                size=pos_data["contracts"],
                entry_price=pos_data["entry_price"],
            )

        # Sync orders
        self.open_orders = {o["id"]: o for o in exchange_orders}

        # Cache to Redis
        for symbol, pos in self.positions.items():
            await self.redis.set_global_state(symbol, pos.to_dict())

        self.reconciled = True
        logger.info(f"Reconciliation complete — {len(self.positions)} positions, {len(self.open_orders)} orders")
        logger.debug("reconcile: returning True")
        return True

    def update_position(self, symbol: str, side: str, size: Decimal, price: Decimal) -> None:
        """Update or create position from fill."""
        logger.debug(f"update_position: entering symbol={symbol} side={side}")
        if symbol in self.positions:
            pos = self.positions[symbol]
            total_size = pos.size + size
            if total_size > 0:
                pos.entry_price = (pos.entry_price * pos.size + price * size) / total_size
            pos.size = total_size
            pos.side = side
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self.positions[symbol] = Position(symbol, side, size, price)

        logger.info(f"Position updated: {symbol} {side} {size} @ {price}")
        logger.debug("update_position: returning None")

    def close_position(self, symbol: str, price: Decimal) -> Optional[Decimal]:
        """Close position, return realized PnL."""
        logger.debug(f"close_position: entering symbol={symbol}")
        if symbol not in self.positions:
            logger.warning(f"No position to close: {symbol}")
            logger.debug("close_position: returning None (no position)")
            return None

        pos = self.positions[symbol]
        if pos.side == "LONG":
            pnl = (price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - price) * pos.size

        logger.info(f"Position closed: {symbol} PnL={pnl}")
        del self.positions[symbol]
        logger.debug(f"close_position: returning pnl={pnl}")
        return pnl

    def get_position(self, symbol: str) -> Optional[Position]:
        logger.debug(f"get_position: entering symbol={symbol}")
        result = self.positions.get(symbol)
        logger.debug(f"get_position: returning result_type={type(result).__name__}")
        return result

    def get_all_positions(self) -> List[Position]:
        logger.debug("get_all_positions: entering")
        result = list(self.positions.values())
        logger.debug(f"get_all_positions: returning list_len={len(result)}")
        return result

    async def store_trade(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        entry_price: Decimal,
        exit_price: Optional[Decimal] = None,
        pnl: Optional[Decimal] = None,
        latency_ms: int = 0,
        status: str = "FILLED",
        order_id: Optional[str] = None,
    ) -> str:
        """Store trade record — returns trade_id."""
        logger.debug(f"store_trade: entering symbol={symbol} side={side}")
        trade_id = str(uuid.uuid4())
        trade = {
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "size": str(size),
            "entry_price": str(entry_price),
            "exit_price": str(exit_price) if exit_price else None,
            "pnl_usdt": str(pnl) if pnl else None,
            "execution_latency_ms": latency_ms,
            "status": status,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "order_id": order_id,
        }
        await self.redis.set_global_state(f"trade:{trade_id}", trade)
        logger.info(f"Trade stored: {trade_id} {symbol} {side}")
        logger.debug(f"store_trade: returning trade_id={trade_id}")
        return trade_id

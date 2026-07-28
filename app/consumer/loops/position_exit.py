"""Position Exit Loop.

Monitors open positions and exits when conditions are met.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def position_exit_loop(
    bybit: Any,
    position_store: Any,
    trade_store: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 10,
) -> None:
    """Monitor open positions and exit when conditions are met.

    Args:
        bybit: BybitClient instance.
        position_store: PositionStore instance.
        trade_store: TradeStore instance.
        redis_client: Redis client.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 10).
    """
    while not shutdown_event.is_set():
        try:
            open_positions = await position_store.list_all()

            for pos in open_positions:
                symbol = pos.get("symbol", "")
                side = pos.get("side", "LONG")
                entry_price = float(pos.get("entry_price", 0))
                sl_price = float(pos.get("sl_price", 0))
                tp_price = float(pos.get("tp_price", 0))
                amount = float(pos.get("amount", 0))

                if not symbol or entry_price == 0:
                    continue

                # Get current price
                try:
                    ticker = await bybit.get_ticker(symbol)
                    current_price = float(ticker.get("last", 0))
                except Exception:
                    continue

                if current_price == 0:
                    continue

                # Check stop loss
                if side == "LONG" and current_price <= sl_price:
                    logger.info("position_exit: %s LONG hit SL (%.2f <= %.2f)", symbol, current_price, sl_price)
                    await _exit_position(bybit, position_store, trade_store, pos, current_price, "stop_loss")
                elif side == "SHORT" and current_price >= sl_price:
                    logger.info("position_exit: %s SHORT hit SL (%.2f >= %.2f)", symbol, current_price, sl_price)
                    await _exit_position(bybit, position_store, trade_store, pos, current_price, "stop_loss")

                # Check take profit
                if tp_price > 0:
                    if side == "LONG" and current_price >= tp_price:
                        logger.info("position_exit: %s LONG hit TP (%.2f >= %.2f)", symbol, current_price, tp_price)
                        await _exit_position(bybit, position_store, trade_store, pos, current_price, "take_profit")
                    elif side == "SHORT" and current_price <= tp_price:
                        logger.info("position_exit: %s SHORT hit TP (%.2f <= %.2f)", symbol, current_price, tp_price)
                        await _exit_position(bybit, position_store, trade_store, pos, current_price, "take_profit")

            logger.debug("position_exit: checked %d positions", len(open_positions))

        except Exception as e:
            logger.error("position_exit_loop failed: %s", e)

        await asyncio.sleep(interval_s)


async def _exit_position(
    bybit: Any,
    position_store: Any,
    trade_store: Any,
    pos: dict,
    exit_price: float,
    exit_reason: str,
) -> None:
    """Exit a position."""
    try:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        amount = float(pos.get("amount", 0))
        entry_price = float(pos.get("entry_price", 0))

        # Place close order
        close_side = "SELL" if side == "LONG" else "BUY"
        order = await bybit.place_order(
            symbol=symbol,
            side=close_side,
            order_type="MARKET",
            qty=str(amount),
        )

        if order and order.get("orderId"):
            # Calculate PnL
            if side == "LONG":
                pnl = (exit_price - entry_price) * amount
            else:
                pnl = (entry_price - exit_price) * amount

            # Record trade
            await trade_store.record_trade(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                amount=amount,
                realized_pnl=pnl,
                exit_reason=exit_reason,
            )

            # Remove from position store
            await position_store.remove(symbol, side)

            logger.info(
                "position_exit: %s %s closed at %.2f (PnL=%.2f, reason=%s)",
                symbol, side, exit_price, pnl, exit_reason,
            )

    except Exception as e:
        logger.error("position_exit: failed to exit %s %s: %s", pos.get("symbol"), pos.get("side"), e)

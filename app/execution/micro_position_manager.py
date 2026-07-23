"""Micro Position Manager — 45s Time-Stop Engine for Scalps."""
from __future__ import annotations

import asyncio
import time
from typing import Any
from decimal import Decimal

from loguru import logger


class MicroPositionManager:
    """Manages active Micro-Scalper positions with a strict 45-second time-stop."""
    
    def __init__(
        self,
        bybit_client: Any,
        position_store: Any,
        trade_store: Any,
    ) -> None:
        self.bybit = bybit_client
        self.position_store = position_store
        self.trade_store = trade_store
        self.running = False

    async def start(self) -> None:
        self.running = True
        logger.info("MicroPositionManager started.")
        while self.running:
            try:
                await self._manage_positions()
            except asyncio.CancelledError:
                self.running = False
                break
            except Exception as e:
                logger.error(f"MicroPositionManager loop error: {e}")
            await asyncio.sleep(1.0) # Check every 1 second

    def stop(self) -> None:
        self.running = False

    async def _manage_positions(self) -> None:
        positions = await self.position_store.list_all()
        now = time.time()
        
        for pos in positions:
            # Only manage micro scalps
            if not pos.get("is_micro_scalper", False):
                continue
                
            symbol = pos.get("symbol")
            entry_time = float(pos.get("entry_time", 0))
            
            # Time-Stop Logic
            # If PnL <= 0 after 45 seconds, execute immediate market close.
            hold_time = now - entry_time
            if hold_time >= 45.0:
                pnl = float(pos.get("pnl", 0))
                if pnl <= 0:
                    logger.warning(f"MicroPositionManager: 45s time-stop triggered for {symbol}. PnL={pnl}. Closing at market.")
                    await self._close_position(pos, "TIME_STOP_45S")

    async def _close_position(self, position: dict[str, Any], reason: str) -> None:
        symbol = position.get("symbol")
        side = position.get("side", "buy")
        amount = position.get("amount", 0)
        
        close_side = "sell" if side.lower() == "buy" else "buy"
        
        try:
            # 1. Cancel resting SL/TP (usually Bybit One-Way Mode does this via reduce_only)
            # 2. Market close
            order = await self.bybit.place_order(
                symbol=symbol,
                side=close_side,
                order_type="market",
                qty=Decimal(str(amount)),
                reduce_only=True
            )
            
            if order:
                logger.info(f"Micro-Scalp {symbol} closed. Reason: {reason}")
                await self.position_store.remove(symbol, side)
                
                # Update TradeStore
                await self.trade_store.update_exit(
                    symbol=symbol,
                    exit_price=Decimal("0"), # Needs PnL reconciliation later
                    exit_time=time.time(),
                    pnl_usdt=Decimal("0"),
                    pnl_pct=Decimal("0"),
                    exit_reason=reason
                )
        except Exception as e:
            logger.error(f"Failed to close micro-scalp {symbol}: {e}")

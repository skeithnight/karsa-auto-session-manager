"""Trailing Stop Manager — amend exchange-side SL when price moves favorably.

Runs every 60s. Per position: track peak, recalc stop = peak - (ATR * regime_mult).
Amend Bybit SL if new_stop > current_stop. 60s cooldown per symbol.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core.position_store import PositionStore
from app.execution.bybit_client import BybitClient


class TrailingStopManager:
    """Amend exchange-side SL when price moves favorably.

    Runs every 60s. Per position: track peak, recalc stop = peak - (ATR x regime_mult).
    Amend Bybit SL if new_stop > current_stop. 60s cooldown per symbol.
    """

    def __init__(
        self,
        position_store: PositionStore,
        bybit_client: BybitClient,
        atr_multiplier: Decimal = Decimal("2.0"),
        cooldown_seconds: int = 60,
        max_loss_usd: Decimal = Decimal("1.00"),
    ) -> None:
        logger.debug("TrailingStopManager.__init__: entering")
        self.store = position_store
        self.client = bybit_client
        self.atr_multiplier = atr_multiplier
        self.cooldown_seconds = cooldown_seconds
        self.max_loss_usd = max_loss_usd
        self._last_amend: dict[str, float] = {}
        logger.debug("TrailingStopManager.__init__: returning")

    async def run(
        self,
        kill_switch: asyncio.Event,
        price_getter: Callable[[str], Coroutine[Any, Any, Decimal | None]],
    ) -> None:
        """Main loop. price_getter: async callable(symbol) -> Optional[Decimal]."""
        logger.info("Trailing Stop Manager starting...")
        while not kill_switch.is_set():
            try:
                positions = await self.store.list_all()
                for pos in positions:
                    await self._evaluate(pos, price_getter)
            except Exception as e:
                logger.error(f"TrailingStop error: {e}")
            await asyncio.sleep(60)
        logger.info("Trailing Stop Manager stopped")

    async def _evaluate(self, pos: dict, price_getter) -> None:
        symbol = pos["symbol"]
        side = pos["side"]
        entry = Decimal(pos["entry_price"])
        peak = Decimal(pos.get("peak_price", pos["entry_price"]))
        atr_str = pos.get("atr", "")
        atr = Decimal(atr_str) if atr_str else Decimal("0")

        current_price = await price_getter(symbol)
        if current_price is None:
            return

        # Update peak
        if (
            side == "buy"
            and current_price > peak
            or side == "sell"
            and current_price < peak
        ):
            peak = current_price
            await self.store.update_peak(symbol, side, current_price)

        # Calculate new SL
        amount = Decimal(pos.get("amount", "0"))
        if amount <= 0:
            return

        max_distance = self.max_loss_usd / amount

        if atr <= 0:
            # No trailing stop available, just enforce the static max_loss cap
            if side == "buy":
                new_sl = entry - max_distance
                if new_sl <= 0:
                    new_sl = Decimal("0.000001")
            else:
                new_sl = entry + max_distance
        else:
            new_sl = self._calc_sl(side, peak, atr)

            # Cap: never widen SL beyond max_loss_usd from entry
            if side == "buy":
                floor_sl = entry - max_distance
                new_sl = max(new_sl, floor_sl)
                if new_sl <= 0:
                    new_sl = Decimal("0.000001")
            else:
                ceiling_sl = entry + max_distance
                new_sl = min(new_sl, ceiling_sl)

        old_sl_str = pos.get("sl_price", "")
        old_sl = Decimal(old_sl_str) if old_sl_str else Decimal("0")

        # Only amend if there's no SL yet, or new SL is better (higher for long, lower for short)
        now = time.time()
        if (
            old_sl == 0
            or side == "buy"
            and new_sl > old_sl
            or side == "sell"
            and new_sl < old_sl
        ):
            await self._amend(pos, new_sl, now)

    def _calc_sl(self, side: str, peak: Decimal, atr: Decimal) -> Decimal:
        distance = atr * self.atr_multiplier
        if side == "buy":
            return peak - distance
        return peak + distance

    async def _amend(self, pos: dict, new_sl: Decimal, now: float) -> None:
        symbol = pos["symbol"]
        key = f"{symbol}:{pos['side']}"
        last = self._last_amend.get(key, 0)
        if now - last < self.cooldown_seconds:
            return

        # Atomic SL via set_trading_stop — no conditional order to track
        await self.client.set_trading_stop(symbol, pos["side"], stop_loss=new_sl)
        self._last_amend[key] = now
        logger.info(f"SL amended: {symbol} {pos['side']} -> {new_sl}")

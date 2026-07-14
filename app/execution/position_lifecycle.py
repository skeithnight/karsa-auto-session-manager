"""Position Lifecycle — trailing stop + performance checkpoints.

Runs as two async tasks:
  - Trailing stop: every 60s, amend SL if price moves favorably
  - Checkpoint manager: every 5min, evaluate time-based exits and hard stops
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from app.alpha.position_judge import JudgeVerdict, PositionJudge
from app.core.position_store import PositionStore
from app.execution.bybit_client import BybitClient


class TrailingStopManager:
    """Amend exchange-side SL when price moves favorably.

    Runs every 60s. Per position: track peak, recalc stop = peak - (ATR × regime_mult).
    Amend Bybit SL if new_stop > current_stop. 60s cooldown per symbol.
    """

    def __init__(
        self,
        position_store: PositionStore,
        bybit_client: BybitClient,
        atr_multiplier: Decimal = Decimal("2.0"),
        cooldown_seconds: int = 60,
    ) -> None:
        logger.debug("TrailingStopManager.__init__: entering")
        self.store = position_store
        self.client = bybit_client
        self.atr_multiplier = atr_multiplier
        self.cooldown_seconds = cooldown_seconds
        self._last_amend: dict[str, float] = {}
        logger.debug("TrailingStopManager.__init__: returning")

    async def run(
        self,
        kill_switch: asyncio.Event,
        price_getter: Callable[[str], Coroutine[Any, Any, Optional[Decimal]]],
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
        if side == "buy" and current_price > peak:
            peak = current_price
            await self.store.update_peak(symbol, side, current_price)
        elif side == "sell" and current_price < peak:
            peak = current_price
            await self.store.update_peak(symbol, side, current_price)

        # Calculate new SL
        if atr <= 0:
            return
        new_sl = self._calc_sl(side, peak, atr)
        old_sl_str = pos.get("sl_price", "")
        old_sl = Decimal(old_sl_str) if old_sl_str else Decimal("0")

        # Only amend if new SL is better (higher for long, lower for short)
        now = time.time()
        if side == "buy" and new_sl > old_sl:
            await self._amend(pos, new_sl, now)
        elif side == "sell" and new_sl < old_sl:
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

        sl_id = pos.get("sl_order_id", "")
        if not sl_id:
            return

        amount = Decimal(pos["amount"])
        new_order = await self.client.amend_stop_loss(
            sl_id, symbol, pos["side"], new_sl, amount,
        )
        if new_order:
            await self.store.update_sl(symbol, pos["side"], new_order.get("id", ""))
            self._last_amend[key] = now
            logger.info(f"SL amended: {symbol} {pos['side']} -> {new_sl}")


class CheckpointManager:
    """Evaluate time-based exits and hard stops. Runs every 5 min.

    Schedule: 1h / 4h / 24h / 72h time stops.
    HARD_FAIL: -2%+ in first 30min or -3%+ ever → immediate exit.
    CLEAR_WIN: gain > 3x ATR → activate trailing stop.
    TIME_STOP: held > 72h → exit.
    """

    CHECKPOINT_1H = 3600
    CHECKPOINT_4H = 14400
    CHECKPOINT_24H = 86400
    CHECKPOINT_72H = 259200

    def __init__(
        self,
        position_store: PositionStore,
        bybit_client: BybitClient,
        hard_fail_30min_pct: Decimal = Decimal("-0.02"),
        hard_fail_ever_pct: Decimal = Decimal("-0.03"),
        clear_win_atr_mult: Decimal = Decimal("3"),
        position_judge: Optional[PositionJudge] = None,
    ) -> None:
        logger.debug("CheckpointManager.__init__: entering")
        self.store = position_store
        self.client = bybit_client
        self.hard_fail_30min = hard_fail_30min_pct
        self.hard_fail_ever = hard_fail_ever_pct
        self.clear_win_atr_mult = clear_win_atr_mult
        self.position_judge = position_judge
        logger.debug("CheckpointManager.__init__: returning")

    async def run(
        self,
        kill_switch: asyncio.Event,
        price_getter: Callable[[str], Coroutine[Any, Any, Optional[Decimal]]],
        state_manager=None,
    ) -> None:
        """Main loop. price_getter: async callable(symbol) -> Optional[Decimal]."""
        logger.info("Checkpoint Manager starting...")
        while not kill_switch.is_set():
            try:
                positions = await self.store.list_all()
                for pos in positions:
                    await self._evaluate(pos, price_getter, state_manager)
            except Exception as e:
                logger.error(f"Checkpoint error: {e}")
            await asyncio.sleep(300)  # 5 min
        logger.info("Checkpoint Manager stopped")

    async def _evaluate(self, pos: dict, price_getter, state_manager) -> None:
        symbol = pos["symbol"]
        side = pos["side"]
        entry = Decimal(pos["entry_price"])
        atr_str = pos.get("atr", "")
        atr = Decimal(atr_str) if atr_str else Decimal("0")
        checkpoint = pos.get("checkpoint", "OPEN")

        entered_str = pos.get("entered_at", "")
        if not entered_str:
            return
        entered_at = datetime.fromisoformat(entered_str)
        now = datetime.now(timezone.utc)
        elapsed = (now - entered_at).total_seconds()

        current_price = await price_getter(symbol)
        if current_price is None:
            return

        # Calculate PnL %
        if side == "buy":
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry

        # HARD_FAIL checks
        if elapsed < 1800 and pnl_pct <= self.hard_fail_30min:  # 30 min
            logger.critical(f"HARD_FAIL 30min: {symbol} {side} pnl={pnl_pct:.4f}")
            await self._exit(pos, state_manager)
            return

        if pnl_pct <= self.hard_fail_ever:
            logger.critical(f"HARD_FAIL ever: {symbol} {side} pnl={pnl_pct:.4f}")
            await self._exit(pos, state_manager)
            return

        # AI judge — ambiguous zone (survived HARD_FAIL, not yet CLEAR_WIN)
        if self.position_judge and pnl_pct > self.hard_fail_ever:
            peak_str = pos.get("peak_price", pos["entry_price"])
            peak = Decimal(peak_str) if peak_str else entry
            regime = pos.get("regime", "UNKNOWN")
            verdict = await self.position_judge.judge(
                symbol=symbol,
                side=side,
                entry_price=entry,
                current_price=current_price,
                peak_price=peak,
                atr=atr,
                regime=regime,
                elapsed_seconds=elapsed,
            )
            if verdict:
                logger.info(f"PositionJudge: {symbol} {side} → {verdict.action} ({verdict.tier_used})")
                if verdict.action == "EXIT":
                    await self._exit(pos, state_manager)
                    return
                elif verdict.action == "TIGHTEN_STOP":
                    await self._tighten_stop(pos, current_price, atr)

        # CLEAR_WIN: gain > 3x ATR
        if atr > 0 and pnl_pct > 0:
            gain_atr = (abs(current_price - entry)) / atr
            if gain_atr >= self.clear_win_atr_mult and checkpoint != "TRAILING":
                await self.store.update_checkpoint(symbol, side, "TRAILING")
                logger.info(f"CLEAR_WIN: {symbol} {side} gain={gain_atr:.1f}x ATR")

        # TIME_STOP: held > 72h
        if elapsed >= self.CHECKPOINT_72H:
            logger.warning(f"TIME_STOP: {symbol} {side} held {elapsed/3600:.1f}h")
            await self._exit(pos, state_manager)
            return

        # Update checkpoint based on elapsed time
        if checkpoint == "OPEN":
            if elapsed >= self.CHECKPOINT_24H:
                await self.store.update_checkpoint(symbol, side, "24H")
            elif elapsed >= self.CHECKPOINT_4H:
                await self.store.update_checkpoint(symbol, side, "4H")
            elif elapsed >= self.CHECKPOINT_1H:
                await self.store.update_checkpoint(symbol, side, "1H")

    async def _exit(self, pos: dict, state_manager) -> None:
        """Execute exit for a position."""
        symbol = pos["symbol"]
        side = pos["side"]
        amount = Decimal(pos["amount"])
        close_side = "sell" if side == "buy" else "buy"

        try:
            if state_manager:
                await state_manager.close_position(symbol, side, amount)
            else:
                await self.client.create_market_order(symbol, close_side, amount)
            await self.store.remove(symbol, side)
            logger.info(f"Position exited: {symbol} {side}")
        except Exception as e:
            logger.error(f"Exit failed: {symbol} {side}: {e}")

    async def _tighten_stop(self, pos: dict, current_price: Decimal, atr: Decimal) -> None:
        """Amend SL closer to current price on TIGHTEN_STOP verdict."""
        symbol = pos["symbol"]
        side = pos["side"]
        amount = Decimal(pos["amount"])
        if atr <= 0:
            return
        if side == "buy":
            new_sl = current_price - (atr * Decimal("1"))
        else:
            new_sl = current_price + (atr * Decimal("1"))
        try:
            await self.client.amend_stop_loss(symbol, side, new_sl, amount)
            logger.info(f"Tightened SL: {symbol} {side} → {new_sl}")
        except Exception as e:
            logger.error(f"Tighten SL failed: {symbol} {side}: {e}")

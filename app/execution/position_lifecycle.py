"""Position Lifecycle — trailing stop + performance checkpoints.

Runs as two async tasks:
  - Trailing stop: every 60s, amend SL if price moves favorably
  - Checkpoint manager: every 5min, evaluate time-based exits and hard stops
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.alpha.position_judge import PositionJudge
from app.core import metrics
from app.core.position_store import PositionStore
from app.execution.bybit_client import BybitClient
from app.execution.sor import SmartOrderRouter


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


class CheckpointManager:
    """Evaluate time-based exits and hard stops. Runs every 5 min.

    Schedule: 1h / 4h / 24h / 72h time stops.
    HARD_FAIL: -2%+ in first 30min or -3%+ ever → immediate exit.
    PROFIT_DRAWDOWN: position dropped 50%+ from profit peak → lock-in remaining profit.
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
        position_judge: PositionJudge | None = None,
        trade_store: Any | None = None,
        alert_service: Any | None = None,
        trade_memory: Any | None = None,
        min_profit_to_protect_usd: Decimal = Decimal("0.50"),
        profit_drawdown_ratio: Decimal = Decimal("0.50"),
        hard_fail_30min_usd: Decimal | None = None,
        hard_fail_ever_usd: Decimal | None = None,
        sor: SmartOrderRouter | None = None,
    ) -> None:
        """Callers: main.py (passes trade_store, alert_service). No schema change."""
        logger.debug("CheckpointManager.__init__: entering")
        self.store = position_store
        self.client = bybit_client
        self.sor = sor
        self.hard_fail_30min = hard_fail_30min_pct
        self.hard_fail_ever = hard_fail_ever_pct
        self.clear_win_atr_mult = clear_win_atr_mult
        self.position_judge = position_judge
        self.trade_store = trade_store
        self.alert_service = alert_service
        self.trade_memory = trade_memory
        self.min_profit_to_protect_usd = min_profit_to_protect_usd
        self.profit_drawdown_ratio = profit_drawdown_ratio
        self.hard_fail_30min_usd = hard_fail_30min_usd
        self.hard_fail_ever_usd = hard_fail_ever_usd

        self.checkpoint_1h_min_pnl = Decimal("-0.005")
        self.checkpoint_4h_min_pnl = Decimal("0.0")
        self.checkpoint_24h_min_pnl = Decimal("0.005")
        logger.debug("CheckpointManager.__init__: returning")

    async def run(
        self,
        kill_switch: asyncio.Event,
        price_getter: Callable[[str], Coroutine[Any, Any, Decimal | None]],
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
        now = datetime.now(UTC)
        elapsed = (now - entered_at).total_seconds()

        current_price = await price_getter(symbol)
        if current_price is None:
            return

        # Update position gauges
        amount = Decimal(pos.get("amount", "0"))
        metrics.position_size.labels(symbol=symbol).set(float(amount))
        metrics.position_entry_price.labels(symbol=symbol).set(float(entry))
        metrics.position_duration.labels(symbol=symbol).set(elapsed)

        # Calculate PnL %
        if side == "buy":
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry
        pnl_usdt = float(pnl_pct * amount * entry)
        metrics.position_unrealized_pnl.labels(symbol=symbol).set(pnl_usdt)

        # Expose SL price for dashboard
        sl_price_str = pos.get("sl_price", "")
        if sl_price_str:
            metrics.position_sl_price.labels(symbol=symbol).set(float(sl_price_str))

        # HARD_FAIL checks
        if elapsed < 1800 and pnl_pct <= self.hard_fail_30min:  # 30 min
            logger.critical(f"HARD_FAIL 30min: {symbol} {side} pnl={pnl_pct:.4f}")
            await self._exit(pos, state_manager, current_price=current_price)
            return

        if pnl_pct <= self.hard_fail_ever:
            logger.critical(f"HARD_FAIL ever: {symbol} {side} pnl={pnl_pct:.4f}")
            await self._exit(pos, state_manager, current_price=current_price)
            return

        # PROFIT DRAWDOWN PROTECTION
        # If peak profit >= min_profit_to_protect and current profit has drawn down by
        # >= profit_drawdown_ratio from that peak, lock in remaining profit immediately.
        peak_str = pos.get("peak_price", pos["entry_price"])
        peak = Decimal(peak_str) if peak_str else entry
        if side == "buy":
            peak_pnl_usd = (peak - entry) * amount
            current_pnl_usd = (current_price - entry) * amount
        else:
            peak_pnl_usd = (entry - peak) * amount
            current_pnl_usd = (entry - current_price) * amount

        if peak_pnl_usd >= self.min_profit_to_protect_usd and current_pnl_usd >= 0:
            drawdown_from_peak = peak_pnl_usd - current_pnl_usd
            if peak_pnl_usd > 0:
                drawdown_ratio = drawdown_from_peak / peak_pnl_usd
                if drawdown_ratio >= self.profit_drawdown_ratio:
                    logger.warning(
                        f"PROFIT_DRAWDOWN: {symbol} {side} "
                        f"peak=${float(peak_pnl_usd):.2f} "
                        f"current=${float(current_pnl_usd):.2f} "
                        f"drawdown={float(drawdown_ratio):.0%} — locking in profit"
                    )
                    await self._exit(
                        pos,
                        state_manager,
                        exit_reason="profit_drawdown",
                        current_price=current_price,
                    )
                    return

        # AI judge — ambiguous zone (survived HARD_FAIL, not yet CLEAR_WIN)
        if self.position_judge and pnl_pct > self.hard_fail_ever:
            regime = pos.get("regime", "UNKNOWN")
            trade_ctx = ""
            if self.trade_memory:
                trade_ctx = await self.trade_memory.get_prompt_context(
                    symbol, regime=regime if regime != "UNKNOWN" else None
                )
            verdict = await self.position_judge.judge(
                symbol=symbol,
                side=side,
                entry_price=entry,
                current_price=current_price,
                peak_price=peak,
                atr=atr,
                regime=regime,
                elapsed_seconds=elapsed,
                recent_trades=trade_ctx,
            )
            if verdict:
                logger.info(
                    f"PositionJudge: {symbol} {side} → {verdict.action} ({verdict.tier_used})"
                )
                if self.trade_store:
                    try:
                        import json

                        await self.trade_store.record_ai_decision(
                            symbol=symbol,
                            decision_type="position_judge",
                            model=verdict.tier_used,
                            output_json=json.dumps(
                                {
                                    "action": verdict.action,
                                    "confidence": verdict.confidence,
                                    "reasoning": verdict.reasoning,
                                    "pnl_pct": float(pnl_pct),
                                }
                            ),
                        )
                    except Exception:
                        pass  # non-critical audit trail
                if verdict.action == "EXIT":
                    await self._exit(pos, state_manager, current_price=current_price)
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
            logger.info(f"TIME_STOP (72h) triggered: {symbol} {side}")
            await self._exit(
                pos, state_manager, exit_reason="time_stop", current_price=current_price
            )
            return

        pnl_usd = float(pnl_pct * amount * entry)
        if (
            self.hard_fail_30min_usd
            and elapsed < 1800
            and pnl_usd <= float(self.hard_fail_30min_usd)
        ):
            logger.info(
                f"HARD_FAIL (30m, USD) triggered: {symbol} {side} pnl=${pnl_usd:.2f}"
            )
            await self._exit(
                pos,
                state_manager,
                exit_reason="hard_fail_usd",
                current_price=current_price,
            )
            return

        if self.hard_fail_ever_usd and pnl_usd <= float(self.hard_fail_ever_usd):
            logger.info(
                f"HARD_FAIL (Ever, USD) triggered: {symbol} {side} pnl=${pnl_usd:.2f}"
            )
            await self._exit(
                pos,
                state_manager,
                exit_reason="hard_fail_usd",
                current_price=current_price,
            )
            return

        # Checkpoint-specific performance checks
        is_checkpoint_review = False
        min_pnl = None
        new_checkpoint = checkpoint

        if checkpoint == "OPEN" and elapsed >= self.CHECKPOINT_1H:
            is_checkpoint_review = True
            min_pnl = self.checkpoint_1h_min_pnl
            new_checkpoint = "1H"
        elif checkpoint == "1H" and elapsed >= self.CHECKPOINT_4H:
            is_checkpoint_review = True
            min_pnl = self.checkpoint_4h_min_pnl
            new_checkpoint = "4H"
        elif checkpoint == "4H" and elapsed >= self.CHECKPOINT_24H:
            is_checkpoint_review = True
            min_pnl = self.checkpoint_24h_min_pnl
            new_checkpoint = "24H"

        if is_checkpoint_review and min_pnl is not None:
            # First, update the checkpoint in DB so we don't trigger this again
            await self.store.update_checkpoint(symbol, side, new_checkpoint)

            # Check performance
            if pnl_pct < min_pnl:
                logger.warning(
                    f"Checkpoint {new_checkpoint}: {symbol} {side} underperforming (pnl={pnl_pct:.4f} < min={min_pnl:.4f})"
                )

                # Invoke PositionJudge explicitly for checkpoint review if available
                if self.position_judge:
                    regime = pos.get("regime", "UNKNOWN")
                    trade_ctx = ""
                    if self.trade_memory:
                        trade_ctx = await self.trade_memory.get_prompt_context(
                            symbol, regime=regime if regime != "UNKNOWN" else None
                        )
                    verdict = await self.position_judge.judge(
                        symbol=symbol,
                        side=side,
                        entry_price=entry,
                        current_price=current_price,
                        peak_price=peak,
                        atr=atr,
                        regime=regime,
                        elapsed_seconds=elapsed,
                        recent_trades=trade_ctx,
                        is_checkpoint_review=True,
                    )
                    if verdict:
                        logger.info(
                            f"PositionJudge (Checkpoint): {symbol} {side} → {verdict.action} ({verdict.tier_used})"
                        )
                        if self.trade_store:
                            try:
                                import json

                                await self.trade_store.record_ai_decision(
                                    symbol=symbol,
                                    decision_type="position_judge",
                                    model=verdict.tier_used,
                                    output_json=json.dumps(
                                        {
                                            "action": verdict.action,
                                            "confidence": verdict.confidence,
                                            "reasoning": verdict.reasoning,
                                            "pnl_pct": float(pnl_pct),
                                            "checkpoint": new_checkpoint,
                                            "is_checkpoint_review": True,
                                        }
                                    ),
                                )
                            except Exception:
                                pass  # non-critical audit trail
                        if verdict.action == "EXIT":
                            await self._exit(
                                pos,
                                state_manager,
                                exit_reason=f"checkpoint_{new_checkpoint}_fail",
                                current_price=current_price,
                            )
                            return
                        elif verdict.action == "TIGHTEN_STOP":
                            await self._tighten_stop(pos, current_price, atr)

    async def _exit(
        self,
        pos: dict,
        state_manager,
        exit_reason: str = "checkpoint",
        current_price: Decimal | None = None,
    ) -> None:
        """Execute exit for a position.

        Profitable/non-urgent exits route through SOR post-only to save taker fees.
        Hard fails and guaranteed-fill exits use market order directly.
        """
        symbol = pos["symbol"]
        side = pos["side"]
        amount = Decimal(pos["amount"])
        close_side = "sell" if side == "buy" else "buy"

        # Hard fails need guaranteed fill — market order. Everything else tries post-only first.
        is_hard_fail = exit_reason.startswith("hard_fail")
        use_sor = self.sor and not is_hard_fail and current_price and current_price > 0

        try:
            if use_sor:
                logger.info(
                    f"Exit via SOR post-only: {symbol} {close_side} reason={exit_reason}"
                )
                order = await self.sor.execute_exit(
                    symbol, close_side, amount, current_price
                )
                if order is None:
                    logger.warning(
                        f"SOR exit returned None, falling back to market: {symbol}"
                    )
                    order = await self.client.create_market_order(
                        symbol, close_side, amount
                    )
            else:
                order = await self.client.create_market_order(
                    symbol, close_side, amount
                )
            order_id = order.get("orderId")
            exit_price = Decimal("0")

            if order_id:
                await asyncio.sleep(0.5)  # Wait for fill to settle
                try:
                    history = await self.client.get_order_history(
                        symbol=symbol, order_id=order_id
                    )
                    orders = history.get("orders", [])
                    if orders:
                        avg_price = orders[0].get("avgPrice")
                        if avg_price and float(avg_price) > 0:
                            exit_price = Decimal(str(avg_price))
                        # Verify full fill — if partial, close remainder at market
                        cum_exec = Decimal(str(orders[0].get("cumExecQty", "0")))
                        if 0 < cum_exec < amount:
                            remaining = amount - cum_exec
                            logger.warning(
                                f"Partial fill on exit: {symbol} {side} filled={cum_exec} "
                                f"of {amount}, closing remainder {remaining} at market"
                            )
                            close_side = "sell" if side == "buy" else "buy"
                            await self.client.create_market_order(
                                symbol, close_side, remaining
                            )
                except Exception as e:
                    logger.warning(f"Could not fetch exit price for {order_id}: {e}")

            if exit_price == 0:
                exit_price = Decimal(str(order.get("average", order.get("price", 0))))

            # Final fallback: use current_price from evaluation context
            if exit_price == 0 and current_price and current_price > 0:
                exit_price = current_price
                logger.warning(
                    f"Using current_price as exit_price fallback for {symbol}: {exit_price}"
                )

            if state_manager and exit_price > 0:
                state_manager.close_position(symbol, exit_price)
            await self.store.remove(symbol, side)
            # Record lifecycle duration
            entered_str = pos.get("entered_at", "")
            if entered_str:
                entered_at = datetime.fromisoformat(entered_str)
                duration = (datetime.now(UTC) - entered_at).total_seconds()
                metrics.position_lifecycle_duration.observe(duration)
            logger.info(f"Position exited: {symbol} {side}")
            entry_price = Decimal(pos.get("entry_price", "0"))
            pnl = (
                (exit_price - entry_price) * amount
                if side == "buy"
                else (entry_price - exit_price) * amount
            )

            # Record trade exit in Postgres — refuse if exit_price is zero (corrupted data)
            if self.trade_store:
                if exit_price <= 0:
                    logger.error(
                        f"REFUSING to close trade with exit_price=0 for {symbol} {side}. Order may have failed. Order ID: {order_id}"
                    )
                    if self.alert_service:
                        try:
                            await self.alert_service.send(
                                f"⚠️ CRITICAL: {symbol} exit_price=0 — manual review needed. Order ID: {order_id}"
                            )
                        except Exception:
                            pass
                    return
                try:
                    await self.trade_store.close_trade(
                        symbol, exit_price, pnl, exit_reason
                    )
                except Exception as te:
                    logger.error(f"Trade store close_trade failed: {te}")
            # Record trade memory for AI context
            if self.trade_memory:
                try:
                    entry_price_d = Decimal(pos.get("entry_price", "0"))
                    pnl_pct_d = (
                        (pnl / (entry_price_d * amount) * 100)
                        if (entry_price_d * amount)
                        else Decimal("0")
                    )
                    entered_str = pos.get("entered_at", "")
                    hold_min = 0
                    if entered_str:
                        entered_at = datetime.fromisoformat(entered_str)
                        hold_min = int(
                            (datetime.now(UTC) - entered_at).total_seconds() / 60
                        )
                    regime = pos.get("regime", "UNKNOWN")
                    entry_conf = (
                        Decimal(str(pos.get("entry_confidence", "0")))
                        if pos.get("entry_confidence")
                        else Decimal("0")
                    )
                    await self.trade_memory.store(
                        symbol=symbol,
                        pnl_pct=pnl_pct_d,
                        hold_duration_min=hold_min,
                        regime=regime,
                        exit_reason=exit_reason,
                        entry_confidence=entry_conf,
                    )
                except Exception as me:
                    logger.error(f"TradeMemory store failed: {me}")
            # Push exit alert to Telegram
            if self.alert_service:
                try:
                    from app.bot.utils.formatters import (
                        format_breakeven_alert,
                        format_sl_alert,
                        format_tp_alert,
                    )

                    pnl_pct = (
                        (pnl / (entry_price * amount) * 100)
                        if entry_price * amount
                        else Decimal("0")
                    )
                    if pnl > 0:
                        await self.alert_service.send(
                            format_tp_alert(
                                symbol,
                                side,
                                float(entry_price),
                                float(exit_price),
                                float(pnl),
                                float(pnl_pct),
                            )
                        )
                    elif pnl < 0:
                        await self.alert_service.send(
                            format_sl_alert(
                                symbol,
                                side,
                                float(entry_price),
                                float(exit_price),
                                float(pnl),
                                float(pnl_pct),
                            )
                        )
                    else:
                        await self.alert_service.send(
                            format_breakeven_alert(
                                symbol,
                                side,
                                float(entry_price),
                                float(exit_price),
                                float(pnl),
                                float(pnl_pct),
                            )
                        )
                except Exception as ae:
                    logger.error(f"Exit alert failed: {ae}")
        except Exception as e:
            logger.error(f"Exit failed: {symbol} {side}: {e}")

    async def _tighten_stop(
        self, pos: dict, current_price: Decimal, atr: Decimal
    ) -> None:
        """Amend SL closer to current price on TIGHTEN_STOP verdict."""
        symbol = pos["symbol"]
        side = pos["side"]
        if atr <= 0:
            return
        if side == "buy":
            new_sl = current_price - (atr * Decimal("1"))
        else:
            new_sl = current_price + (atr * Decimal("1"))
        try:
            await self.client.set_trading_stop(symbol, side, stop_loss=new_sl)
            logger.info(f"Tightened SL: {symbol} {side} → {new_sl}")
        except Exception as e:
            logger.error(f"Tighten SL failed: {symbol} {side}: {e}")

"""Exit Manager — extracted from ActivePositionManager.

Handles trend trailing stops, time-based exits, regime shift kill switch,
momentum exhaustion detection, force close, and R-multiple calculation.
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timezone
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from loguru import logger

from app.execution.constants import (
    APM_BREAKEVEN_LOCK_R,
    APM_TREND_TRAIL_ACTIVATE_R,
    APM_TREND_TRAIL_ATR_MULT,
    REGIME_FAMILY,
    REGIME_SHIFT_CONFIRM_COUNT,
)

# Close-based trailing stop constants
CLOSE_TRAIL_ATR_MULT = Decimal("2.0")  # trailing distance = 2x ATR from highest close
PROFIT_LOCK_R = Decimal("3.0")  # move SL to breakeven when profit > 3R


class ExitManager:
    """Manages trailing stops, time exits, regime shift kill switch, and force close."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        regime_classifier: object,
        alert_service: object,
        trade_memory: object | None = None,
        logger_: Any | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self._regime = regime_classifier
        self._alert = alert_service
        self._trade_memory = trade_memory
        self._log = logger_ or logger
        self._regime_shift_counts: dict[str, int] = {}
        self._recently_force_closed: dict[str, float] = {}

    # ------------------------------------------------------------------
    # R-multiple calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_r_multiple(side: str, entry_price: Decimal, live_price: Decimal, initial_risk: Decimal) -> Decimal:
        """Calculate R-multiple. Zero-division guard."""
        try:
            if initial_risk <= 0:
                return Decimal("0")
            if side == "LONG":
                return (live_price - entry_price) / initial_risk
            else:
                return (entry_price - live_price) / initial_risk
        except (DivisionByZero, InvalidOperation):
            return Decimal("0")

    # ------------------------------------------------------------------
    # SNIPER Helpers
    # ------------------------------------------------------------------

    async def _check_momentum_exhaustion(self, symbol: str, side: str) -> bool:
        """Check if 1-minute RSI signals a V-shape top/bottom exhaustion."""
        try:
            bybit_symbol = symbol.replace("/", "")
            if hasattr(self._client, "session") and self._client.session:
                raw = await self._client._execute(
                    self._client.session.get_kline,
                    category="linear",
                    symbol=bybit_symbol,
                    interval="1",
                    limit=20
                )
                if raw and raw.get("retCode") == 0 and raw.get("result"):
                    # Bybit returns newest first, reverse it
                    kline_list = raw["result"].get("list", [])
                    if len(kline_list) >= 14:
                        kline_list.reverse()
                        closes = [float(k[4]) for k in kline_list]

                        # Calculate simple RSI
                        import numpy as np
                        deltas = np.diff(closes)
                        seed = deltas[:14]
                        up = seed[seed >= 0].sum() / 14
                        down = -seed[seed < 0].sum() / 14
                        rs = up / down if down != 0 else 0
                        rsi = np.zeros_like(closes)
                        rsi[:14] = 100. - 100. / (1. + rs)
                        for i in range(14, len(closes)):
                            delta = deltas[i - 1]
                            upval = delta if delta > 0 else 0.
                            downval = -delta if delta < 0 else 0.
                            up = (up * 13 + upval) / 14
                            down = (down * 13 + downval) / 14
                            rs = up / down if down != 0 else 0
                            rsi[i] = 100. - 100. / (1. + rs)

                        current_rsi = rsi[-1]

                        if side == "LONG" and current_rsi > 80.0:
                            return True
                        elif side == "SHORT" and current_rsi < 20.0:
                            return True
        except Exception as e:
            self._log.debug(f"APM SNIPER: momentum exhaustion check failed for {symbol}: {e}")
        return False

    # ------------------------------------------------------------------
    # Trend trailing stop
    # ------------------------------------------------------------------

    async def _manage_trend_trailing_stop(
        self,
        pos: dict[str, Any],
        live_price: Decimal,
        r_multiple: Decimal,
        side: str,
        current_sl: Decimal,
        candle_close: Decimal | None = None,
    ) -> bool:
        """Close-based trailing stop — uses highest close (not wicks).

        Returns True if the position should be force-closed (close below trailing stop).

        Close-based logic:
          - Track highest close since entry (not highest wick)
          - trailing_stop = highest_close - (2.0 * ATR)
          - Only trigger on candle CLOSE below trailing stop (ignore wicks)
          - Profit lock: if profit > 3R, move SL to breakeven

        Crypto scam wicks frequently hit wick-based stops then reverse.
        Close-based stops avoid this by only triggering on confirmed closes.
        """
        symbol = pos.get("symbol", "")
        entry_price = Decimal(str(pos.get("entry_price", "0")))
        atr = Decimal(str(pos.get("atr", "0")))

        if entry_price <= 0 or atr <= 0:
            return False

        # --- Profit Lock at 3R: move SL to breakeven ---
        if r_multiple >= PROFIT_LOCK_R:
            if side == "LONG":
                breakeven_sl = entry_price
            else:
                breakeven_sl = entry_price

            if side == "LONG" and current_sl < breakeven_sl:
                await self._amend_sl(pos, symbol, side, breakeven_sl)
                self._log.info(
                    f"APM: PROFIT LOCK {symbol} {side} — R={r_multiple:.2f} >= {PROFIT_LOCK_R}, "
                    f"SL moved to breakeven {breakeven_sl}"
                )
                return False
            elif side == "SHORT" and current_sl > breakeven_sl:
                await self._amend_sl(pos, symbol, side, breakeven_sl)
                self._log.info(
                    f"APM: PROFIT LOCK {symbol} {side} — R={r_multiple:.2f} >= {PROFIT_LOCK_R}, "
                    f"SL moved to breakeven {breakeven_sl}"
                )
                return False

        # --- Close-based trailing stop ---
        if r_multiple < APM_TREND_TRAIL_ACTIVATE_R:
            return False

        trail_distance = atr * CLOSE_TRAIL_ATR_MULT

        # LONG: track highest close since entry → trailing stop trails UP
        # SHORT: track lowest close since entry → trailing stop trails DOWN
        if side == "LONG":
            ref_close = Decimal(str(pos.get("highest_close", str(live_price))))
            if candle_close is not None and candle_close > ref_close:
                ref_close = candle_close
            pos["highest_close"] = str(ref_close)
            new_sl = ref_close - trail_distance
        else:
            ref_close = Decimal(str(pos.get("lowest_close", str(live_price))))
            if candle_close is not None and candle_close < ref_close:
                ref_close = candle_close
            pos["lowest_close"] = str(ref_close)
            new_sl = ref_close + trail_distance

        # --- Check if candle close triggers stop (before amendment) ---
        if candle_close is not None:
            if side == "LONG" and candle_close < new_sl:
                self._log.warning(
                    f"APM: CLOSE-BASED STOP TRIGGERED {symbol} {side} — "
                    f"close={candle_close} < trailing_stop={new_sl}"
                )
                return True
            elif side == "SHORT" and candle_close > new_sl:
                self._log.warning(
                    f"APM: CLOSE-BASED STOP TRIGGERED {symbol} {side} — "
                    f"close={candle_close} > trailing_stop={new_sl}"
                )
                return True

        # --- Amend SL only if more protective ---
        if side == "LONG" and new_sl > current_sl:
            await self._amend_sl(pos, symbol, side, new_sl)
            self._log.info(
                f"APM: close-based trailing SL for {symbol} → {new_sl} "
                f"(highest_close={ref_close}, ATR={atr})"
            )
        elif side == "SHORT" and new_sl < current_sl:
            await self._amend_sl(pos, symbol, side, new_sl)
            self._log.info(
                f"APM: close-based trailing SL for {symbol} → {new_sl} "
                f"(lowest_close={ref_close}, ATR={atr})"
            )

        return False

    async def _amend_sl(
        self,
        pos: dict[str, Any],
        symbol: str,
        side: str,
        new_sl: Decimal,
    ) -> None:
        """Amend stop-loss order and update local state."""
        try:
            sl_order_id = pos.get("sl_order_id", "")
            amount = Decimal(str(pos.get("amount", "0")))
            api_side = "buy" if side == "LONG" else "sell"
            await self._client.amend_stop_loss(
                sl_order_id, symbol, api_side, new_sl, amount
            )  # type: ignore[attr-defined]
            new_sl_str = str(new_sl)
            pos["current_sl"] = new_sl_str
            pos["stop_loss"] = new_sl_str
        except Exception:
            self._log.exception(f"APM: SL amend failed for {symbol}")

    # ------------------------------------------------------------------
    # Time exit
    # ------------------------------------------------------------------

    async def _manage_time_exit(
        self,
        pos: dict[str, Any],
        entry_time: object,
        max_minutes: int,
        live_price: Decimal,
        entry_price: Decimal,
        side: str,
        r_mult: Decimal,
        entry_regime: str,
    ) -> bool:
        """Force close if position held beyond max_hold_time_mins or if it is stale underwater.

        BUG-6 fix: entry_time from Redis is always an ISO string, not a datetime.
        Parse it here before the isinstance guard.
        Returns True if the position was closed, False otherwise.
        """
        # Parse string to timezone-aware datetime if needed
        if isinstance(entry_time, str):
            try:
                entry_time = datetime.fromisoformat(entry_time)
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
            except Exception:
                self._log.debug("APM: could not parse entry_time=%r for time-exit", entry_time)
                return False

        if not isinstance(entry_time, datetime):
            return False

        now = datetime.now(timezone.utc)
        held_mins = (now - entry_time).total_seconds() / 60.0

        is_hyper = str(entry_regime).startswith("HYPER")

        # --- ASYMMETRIC TIME EXITS (Profitability Triage Sprint) ---
        # State-dependent time limits based on unrealized PnL.
        # Philosophy: If the thesis was right, it would be green by now.
        # Losers get cut fast. Winners are allowed to run.
        symbol = pos.get("symbol", "")

        if r_mult < Decimal("0"):
            # -- LOSING: Kill in 3 minutes (fail-closed) --
            losing_max_mins = 3
            if held_mins >= losing_max_mins:
                self._log.warning(
                    f"APM: ASYMMETRIC LOSING EXIT {symbol} {side} -- "
                    f"held {held_mins:.0f}min (>{losing_max_mins}min), R={r_mult:.2f}"
                )
                await self._force_close_position(pos, f"asymmetric_losing_exit_{held_mins:.0f}min")
                return True

        elif r_mult == Decimal("0"):
            # -- BREAKEVEN: Kill in 15 minutes --
            be_max_mins = 15
            if held_mins >= be_max_mins:
                self._log.warning(
                    f"APM: ASYMMETRIC BREAKEVEN EXIT {symbol} {side} -- "
                    f"held {held_mins:.0f}min (>{be_max_mins}min), R={r_mult:.2f}"
                )
                await self._force_close_position(pos, f"asymmetric_be_exit_{held_mins:.0f}min")
                return True

        else:
            # -- WINNING: No time limit -- let trailing stop / regime shift handle it --
            # Quick profit exit for extreme spikes (safety net)
            quick_profit_mins = 3 if is_hyper else 5
            quick_profit_r = Decimal("1.0") if is_hyper else Decimal("3.0")
            if held_mins <= quick_profit_mins and r_mult >= quick_profit_r:
                self._log.warning(
                    f"APM: QUICK PROFIT exit {symbol} after {held_mins:.0f}min (R={r_mult:.2f})"
                )
                await self._force_close_position(pos, f"quick_profit_exit_R{r_mult:.1f}")
                return True

            # Momentum decay exit: if winning but R hasn't moved in 10 min, exit
            peak_r = Decimal(str(pos.get("peak_r_multiple", str(r_mult))))
            if r_mult > peak_r:
                pos["peak_r_multiple"] = str(r_mult)
                pos["peak_r_ts"] = str(datetime.now(timezone.utc).timestamp())
                peak_r = r_mult
            elif r_mult > Decimal("0"):
                peak_r_ts = float(pos.get("peak_r_ts", "0") or "0")
                stale_mins = (datetime.now(timezone.utc).timestamp() - peak_r_ts) / 60.0
                if stale_mins >= 10 and r_mult < peak_r * Decimal("0.8"):
                    self._log.warning(
                        f"APM: MOMENTUM DECAY EXIT {symbol} -- R stalled at {r_mult:.2f} "
                        f"(peak {peak_r:.2f}) for {stale_mins:.0f}min"
                    )
                    await self._force_close_position(pos, f"momentum_decay_exit_R{r_mult:.1f}")
                    return True
        # -------------------------------------------------------------------------

        # Fallback: hard max hold (fail-safe -- should never hit with asymmetric exits)
        if held_mins > max_minutes:
            self._log.warning(f"APM: hard max hold exit {symbol} after {held_mins:.0f}min (max {max_minutes}m)")
            await self._force_close_position(pos, f"time_exit_{held_mins:.0f}min")
            return True
        return False

    # ------------------------------------------------------------------
    # Regime shift kill switch (with hysteresis)
    # ------------------------------------------------------------------

    async def _check_regime_shift(self, pos: dict[str, Any], symbol: str, entry_regime: str) -> bool:
        """Kill switch: force close if regime shifted N consecutive checks.
        Returns True if the position was closed, False otherwise.
        """
        # UNKNOWN regime: orphan positions with lost historical context are exempt.
        # We don't have enough information to determine if a regime shift occurred.
        if entry_regime == "UNKNOWN":
            return False

        try:
            current_regime = await self._regime.get_current_regime(symbol)  # type: ignore[attr-defined]
            current_value = current_regime.value if hasattr(current_regime, "value") else str(current_regime)

            # Regime family guard: shifts within the same family are noise, not real regime changes.
            # RANGE->RANGE_LOW_VOL or TREND_BULL->TREND_BEAR should NOT trigger the kill switch.
            entry_family = REGIME_FAMILY.get(entry_regime, entry_regime)
            current_family = REGIME_FAMILY.get(current_value, current_value)
            if entry_family == current_family:
                # Same family -- noise, not a real regime shift. Reset counter.
                self._regime_shift_counts.pop(symbol, None)
                return False

            if current_value != entry_regime:
                self._regime_shift_counts[symbol] = self._regime_shift_counts.get(symbol, 0) + 1
                if self._regime_shift_counts[symbol] >= REGIME_SHIFT_CONFIRM_COUNT:
                    self._log.warning(
                        f"APM: regime shift kill switch {symbol} -- "
                        f"{entry_regime} -> {current_value} ({self._regime_shift_counts[symbol]} checks)"
                    )
                    await self._force_close_position(pos, f"regime_shift_{entry_regime}_to_{current_value}")
                    self._regime_shift_counts.pop(symbol, None)
                    return True
            else:
                self._regime_shift_counts.pop(symbol, None)

        except Exception:
            self._log.exception(f"APM: regime check failed for {symbol}")
        return False

    # ------------------------------------------------------------------
    # Force close
    # ------------------------------------------------------------------

    async def _force_close_position(self, pos: dict[str, Any], reason: str) -> None:
        """Cancel all orders -> market close -> update state -> alert."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        qty = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"

        exchange_closed = False
        try:
            # Cancel all open orders for this symbol (SL/TP/limit)
            orders = await self._client.fetch_open_orders()  # type: ignore[attr-defined]
            for order in orders:
                if order.get("symbol") == symbol:
                    await self._client.cancel_order(order["id"], symbol)  # type: ignore[attr-defined]
            # Market close with reduceOnly -- capture fill price from response
            fill_price = Decimal("0")
            if qty > 0:
                close_side = "SELL" if side == "LONG" else "BUY"
                close_result = await self._client.create_market_order(
                    symbol, close_side, qty, {"reduceOnly": True}
                )  # type: ignore[attr-defined]
                # Extract fill price from Bybit response
                fill_price = Decimal(str(close_result.get("avgPrice", close_result.get("price", "0"))))

                # VERIFICATION: Ensure the position actually closed on Bybit
                await asyncio.sleep(1.0)  # Wait for execution
                exchange_positions = await self._client.fetch_positions()  # type: ignore[attr-defined]
                still_open = False
                for p in exchange_positions:
                    if p["symbol"] == symbol and p["side"] == api_side and float(p.get("contracts", 0)) > 0:
                        still_open = True
                        break

                if still_open:
                    raise RuntimeError("Bybit accepted the order but position is still open (Price Protection or partial fill).")

            exchange_closed = True
            # Track force-close timestamp for orphan sync grace period (prevents phantom loop)
            self._recently_force_closed[symbol] = datetime.now(timezone.utc).timestamp()

        except Exception as e:
            err_str = str(e)
            if "110017" in err_str or "position is zero" in err_str:
                exchange_closed = True
                fill_price = Decimal("0")
                # Track force-close timestamp even for "already closed" (prevents phantom re-sync)
                self._recently_force_closed[symbol] = datetime.now(timezone.utc).timestamp()
                self._log.warning(f"APM: {symbol} already closed on exchange (handled in phase 1)")
            else:
                self._log.exception(f"APM: CRITICAL force close failed for {symbol}")
                # Set 5-min retry cooldown to prevent 2s spam-loop (same fail every cycle)
                pos["force_close_retry_at"] = datetime.now(timezone.utc).timestamp() + 300
                try:
                    from app.core.position_store import _normalize_side
                    side_key = _normalize_side(side)
                    redis_key = f"karsa:position:{symbol}:{side_key}"
                    await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                except Exception:
                    pass
                if self._alert:
                    await self._alert.send(
                        f"APM FORCE CLOSE FAILED {symbol} -- retry in 5min, MANUAL INTERVENTION NEEDED"
                    )  # type: ignore[attr-defined]
                return

        if exchange_closed:
            try:
                # Store exit price in Redis BEFORE removing so exit loop can read it
                if fill_price > 0:
                    pos["exit_price"] = str(fill_price)
                    pos["exit_reason"] = reason
                    pos["closed_at"] = datetime.now(timezone.utc).isoformat()
                    try:
                        from app.core.position_store import _normalize_side
                        side_key = _normalize_side(side)
                        redis_key = f"karsa:position:{symbol}:{side_key}"
                        await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                    except Exception:
                        pass

                # Remove from local state (side needed for Redis key)
                try:
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                except Exception as e:
                    self._log.warning(f"APM: failed to remove {symbol} from store: {e}")

                self._log.warning(f"APM: force closed {symbol} -- {reason}")
                if self._alert:
                    await self._alert.send(f"APM force closed {symbol}: {reason}")  # type: ignore[attr-defined]

                # Record trade in memory for cooldown / AI context
                entry_price = Decimal(str(pos.get("entry_price", "0")))
                if self._trade_memory and fill_price > 0 and entry_price > 0:
                    try:
                        pnl = (
                            (fill_price - entry_price) * qty
                            if side == "LONG"
                            else (entry_price - fill_price) * qty
                        )
                        pnl_pct = (
                            pnl / (entry_price * qty) * 100
                            if entry_price * qty > 0
                            else Decimal("0")
                        )
                        hold_min = 0
                        entry_time_str = pos.get("entry_time", pos.get("entered_at", ""))
                        if entry_time_str:
                            try:
                                et = datetime.fromisoformat(entry_time_str)
                                if et.tzinfo is None:
                                    et = et.replace(tzinfo=timezone.utc)
                                hold_min = int((datetime.now(timezone.utc) - et).total_seconds() / 60)
                            except Exception:
                                pass
                        await self._trade_memory.store(
                            symbol=symbol,
                            pnl_pct=pnl_pct,
                            hold_duration_min=hold_min,
                            regime=pos.get("entry_regime", "UNKNOWN"),
                            exit_reason=reason,
                            entry_confidence=Decimal(str(pos.get("entry_confidence", "0"))),
                        )
                        self._log.info(f"APM: trade_memory stored {symbol} pnl={pnl_pct:.2f}% reason={reason}")
                    except Exception as e:
                        self._log.warning(f"APM: trade_memory store failed for {symbol}: {e}")

            except Exception as e:
                self._log.error(f"APM: post-close cleanup failed for {symbol}: {e}")
                # Ensure it's removed from local state to prevent orphan loop
                try:
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                except Exception:
                    pass

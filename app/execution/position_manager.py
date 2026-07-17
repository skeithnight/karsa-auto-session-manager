"""Active Position Manager — Phase 6 position lifecycle management.

Replaces TrailingStopManager and CheckpointManager when APM_ENABLED=True.
Runs on 2s interval. All errors → backoff, never crash loop.

Core responsibilities:
  - +1R breakeven lock (exchange-side SL amend)
  - Regime-aware trailing stop (3x ATR Chandelier for TREND)
  - Time-based exits (max_hold_time_mins from RiskProfile)
  - Regime Shift Kill Switch (with 3-check hysteresis)
  - Position reconciliation (ghost detection)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from loguru import logger

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.4) ---
APM_MONITOR_INTERVAL_S: int = 2
APM_ERROR_BACKOFF_S: int = 5
APM_RECONCILE_INTERVAL_S: int = 300
APM_BREAKEVEN_FEE_PCT = Decimal("0.001")
APM_TREND_TRAIL_ATR_MULT = Decimal("3.0")
APM_TREND_TRAIL_ACTIVATE_R = Decimal("1.5")
APM_BREAKEVEN_LOCK_R = Decimal("1.0")

# Regime shift hysteresis: require N consecutive shifted checks
REGIME_SHIFT_CONFIRM_COUNT: int = 3


class ActivePositionManager:
    """Manages open positions: breakeven, trailing, time exit, regime kill switch."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        regime_classifier: object,
        alert_service: object,
        logger_: Any | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self._regime = regime_classifier
        self._alert = alert_service
        self._log = logger_ or logger
        self._regime_shift_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start_monitoring(self) -> None:
        """Main monitoring loop — runs forever with error backoff."""
        last_reconcile = 0.0
        while True:
            try:
                now = datetime.now(timezone.utc).timestamp()

                positions = await self._store.list_all()  # type: ignore[attr-defined]

                # Fetch live mark prices for all held symbols
                live_prices: dict[str, Decimal] = {}
                try:
                    tickers = await self._client.fetch_tickers()  # type: ignore[attr-defined]
                    for t in tickers:
                        sym = t.get("symbol", "")
                        last = t.get("last") or t.get("close")
                        if sym and last:
                            live_prices[sym] = Decimal(str(last))
                except Exception:
                    self._log.warning("APM: failed to fetch live prices, using entry_price fallback")

                for pos in positions:
                    symbol = pos.get("symbol", "")
                    if symbol in live_prices:
                        pos["live_price"] = str(live_prices[symbol])
                    await self._manage_single_position(pos)

                if now - last_reconcile > APM_RECONCILE_INTERVAL_S:
                    await self._reconcile_positions()
                    last_reconcile = now

                await asyncio.sleep(APM_MONITOR_INTERVAL_S)

            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("APM: error in monitoring loop")
                await asyncio.sleep(APM_ERROR_BACKOFF_S)

    # ------------------------------------------------------------------
    # Per-position management
    # ------------------------------------------------------------------

    async def _manage_single_position(self, pos: dict[str, Any]) -> None:
        """Run all position checks: breakeven, trailing, time, regime."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        entry_price = Decimal(str(pos.get("entry_price", "0")))
        live_price = Decimal(str(pos.get("live_price", pos.get("entry_price", "0"))))
        entry_regime = pos.get("entry_regime", "UNKNOWN")
        sl_price = Decimal(str(pos.get("current_sl", pos.get("stop_loss", "0"))))
        initial_risk = Decimal(str(pos.get("initial_risk_per_unit", "0")))
        moved_to_be = pos.get("moved_to_breakeven", False)
        entry_time = pos.get("entry_time")
        max_hold_mins = int(pos.get("max_hold_time_mins", 1440))

        if entry_price <= 0 or initial_risk <= 0:
            return

        r_mult = self._calculate_r_multiple(side, entry_price, live_price, initial_risk)

        # Exchange-side TP for RANGE/CHOP — place once on first loop
        if not pos.get("tp_placed") and entry_regime in ("RANGE", "CHOP"):
            await self._ensure_take_profit(pos, entry_price, initial_risk, side)

        # Scale-out: RANGE/CHOP close 50% at +1R, TREND close 30% at +2R
        scale_threshold = APM_BREAKEVEN_LOCK_R if "TREND" not in entry_regime else Decimal("2.0")
        scale_pct = Decimal("0.50") if "TREND" not in entry_regime else Decimal("0.30")
        if not pos.get("scaled_out") and r_mult >= scale_threshold:
            await self._scale_out_position(pos, scale_pct, entry_price, side)

        if not moved_to_be and r_mult >= APM_BREAKEVEN_LOCK_R:
            await self._move_stop_to_breakeven(pos, entry_price, side)

        if "TREND" in entry_regime:
            await self._manage_trend_trailing_stop(
                pos, live_price, r_mult, side, sl_price
            )

        if entry_time is not None:
            await self._manage_time_exit(pos, entry_time, max_hold_mins)

        await self._check_regime_shift(pos, symbol, entry_regime)

    # ------------------------------------------------------------------
    # R-multiple calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_r_multiple(
        side: str, entry_price: Decimal, live_price: Decimal, initial_risk: Decimal
    ) -> Decimal:
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
    # Exchange-side Take Profit
    # ------------------------------------------------------------------

    async def _ensure_take_profit(
        self, pos: dict[str, Any], entry_price: Decimal, initial_risk: Decimal, side: str
    ) -> None:
        """Place exchange-side TP once for RANGE/CHOP regimes."""
        symbol = pos.get("symbol", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            if side == "LONG":
                tp_price = entry_price + initial_risk  # 1:1 R/R
            else:
                tp_price = entry_price - initial_risk

            result = await self._client.place_take_profit(symbol, api_side, tp_price, amount)  # type: ignore[attr-defined]
            if result:
                pos["tp_placed"] = True
                self._log.info(f"APM: exchange-side TP placed for {symbol} @ {tp_price}")
        except Exception:
            self._log.exception(f"APM: TP placement failed for {symbol}")

    # ------------------------------------------------------------------
    # Scale-out (partial close)
    # ------------------------------------------------------------------

    async def _scale_out_position(
        self, pos: dict[str, Any], pct: Decimal, entry_price: Decimal, side: str
    ) -> None:
        """Partial close to lock profit. RANGE/CHOP: 50% at +1R. TREND: 30% at +2R."""
        symbol = pos.get("symbol", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            close_qty = (amount * pct).quantize(Decimal("0.001"))
            if close_qty <= 0:
                return
            await self._client.reduce_position(symbol, api_side, close_qty)  # type: ignore[attr-defined]
            pos["scaled_out"] = True
            self._log.info(
                f"APM: scale-out {pct*100}% ({close_qty}) for {symbol}"
            )
        except Exception:
            self._log.exception(f"APM: scale-out failed for {symbol}")

    # ------------------------------------------------------------------
    # Breakeven
    # ------------------------------------------------------------------

    async def _move_stop_to_breakeven(
        self, pos: dict[str, Any], entry_price: Decimal, side: str
    ) -> None:
        """Move SL to entry ± fee buffer. Exchange-side amend with retry."""
        symbol = pos.get("symbol", "")
        sl_order_id = pos.get("sl_order_id", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            if side == "LONG":
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT
            else:
                new_sl = entry_price - entry_price * APM_BREAKEVEN_FEE_PCT

            new_sl_str = str(new_sl)
            try:
                await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_sl, amount)  # type: ignore[attr-defined]
            except Exception:
                self._log.warning(f"APM: breakeven amend failed for {symbol}, retrying")
                await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_sl, amount)  # type: ignore[attr-defined]

            await self._store.update_sl(symbol, api_side, sl_order_id)  # type: ignore[attr-defined]
            self._log.info(f"APM: breakeven locked for {symbol} at {new_sl_str}")

        except Exception:
            self._log.exception(f"APM: breakeven CRITICAL failure for {symbol}")
            if self._alert:
                await self._alert.send(f"⚠️ APM breakeven FAILED for {symbol}")  # type: ignore[attr-defined]

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
    ) -> None:
        """3x ATR Chandelier trailing — only amend if more protective."""
        if r_multiple < APM_TREND_TRAIL_ACTIVATE_R:
            return

        symbol = pos.get("symbol", "")
        atr = Decimal(str(pos.get("atr", "0")))
        if atr <= 0:
            return

        trail_distance = atr * APM_TREND_TRAIL_ATR_MULT

        if side == "LONG":
            new_sl = live_price - trail_distance
            if new_sl <= current_sl:
                return
        else:
            new_sl = live_price + trail_distance
            if new_sl >= current_sl:
                return

        try:
            sl_order_id = pos.get("sl_order_id", "")
            amount = Decimal(str(pos.get("amount", "0")))
            api_side = "buy" if side == "LONG" else "sell"
            await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_sl, amount)  # type: ignore[attr-defined]
            self._log.info(f"APM: trailing SL amended for {symbol} to {new_sl}")
        except Exception:
            self._log.exception(f"APM: trailing SL amend failed for {symbol}")

    # ------------------------------------------------------------------
    # Time exit
    # ------------------------------------------------------------------

    async def _manage_time_exit(
        self, pos: dict[str, Any], entry_time: object, max_minutes: int
    ) -> None:
        """Force close if position held beyond max_hold_time_mins."""
        if not isinstance(entry_time, datetime):
            return

        now = datetime.now(timezone.utc)
        held_mins = (now - entry_time).total_seconds() / 60.0

        if held_mins > max_minutes:
            symbol = pos.get("symbol", "")
            self._log.warning(
                f"APM: time exit {symbol} after {held_mins:.0f}min (max {max_minutes})"
            )
            await self._force_close_position(pos, f"time_exit_{held_mins:.0f}min")

    # ------------------------------------------------------------------
    # Regime shift kill switch (with hysteresis)
    # ------------------------------------------------------------------

    async def _check_regime_shift(
        self, pos: dict[str, Any], symbol: str, entry_regime: str
    ) -> None:
        """Kill switch: force close if regime shifted N consecutive checks."""
        try:
            current_regime = await self._regime.get_current_regime(symbol)  # type: ignore[attr-defined]
            current_value = (
                current_regime.value
                if hasattr(current_regime, "value")
                else str(current_regime)
            )

            if current_value != entry_regime:
                self._regime_shift_counts[symbol] = (
                    self._regime_shift_counts.get(symbol, 0) + 1
                )
                if self._regime_shift_counts[symbol] >= REGIME_SHIFT_CONFIRM_COUNT:
                    self._log.warning(
                        f"APM: regime shift kill switch {symbol} — "
                        f"{entry_regime} → {current_value} ({self._regime_shift_counts[symbol]} checks)"
                    )
                    await self._force_close_position(
                        pos, f"regime_shift_{entry_regime}_to_{current_value}"
                    )
                    self._regime_shift_counts.pop(symbol, None)
            else:
                self._regime_shift_counts.pop(symbol, None)

        except Exception:
            self._log.exception(f"APM: regime check failed for {symbol}")

    # ------------------------------------------------------------------
    # Force close
    # ------------------------------------------------------------------

    async def _force_close_position(self, pos: dict[str, Any], reason: str) -> None:
        """Cancel all orders → market close → update state → alert."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")
        qty = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"

        try:
            # Cancel all open orders for this symbol (SL/TP/limit)
            orders = await self._client.fetch_open_orders()  # type: ignore[attr-defined]
            for order in orders:
                if order.get("symbol") == symbol:
                    await self._client.cancel_order(order["id"], symbol)  # type: ignore[attr-defined]
            # Market close with reduceOnly
            if qty > 0:
                close_side = "SELL" if side == "LONG" else "BUY"
                await self._client.create_market_order(symbol, close_side, qty, {"reduceOnly": True})  # type: ignore[attr-defined]
            # Remove from local state (side needed for Redis key)
            await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]

            self._log.warning(f"APM: force closed {symbol} — {reason}")
            if self._alert:
                await self._alert.send(f"🔴 APM force closed {symbol}: {reason}")  # type: ignore[attr-defined]

        except Exception:
            self._log.exception(f"APM: CRITICAL force close failed for {symbol}")
            if self._alert:
                await self._alert.send(f"🚨 APM FORCE CLOSE FAILED {symbol} — MANUAL INTERVENTION NEEDED")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_positions(self) -> None:
        """Compare internal state vs Bybit — fix ghost positions.

        Also verifies SL order still exists on exchange; re-places if missing.
        """
        try:
            internal = await self._store.list_all()  # type: ignore[attr-defined]
            external = await self._client.fetch_positions()  # type: ignore[attr-defined]
            external_symbols = {p.get("symbol") for p in external}

            for pos in internal:
                symbol = pos.get("symbol", "")
                if symbol not in external_symbols:
                    self._log.warning(
                        f"APM: ghost position detected — {symbol} not on Bybit, removing"
                    )
                    api_side = pos.get("side", "buy")
                    await self._store.remove(symbol, api_side)  # type: ignore[attr-defined]
                    continue

                # Verify SL order still exists on exchange
                sl_order_id = pos.get("sl_order_id", "")
                if sl_order_id:
                    open_orders = await self._client.fetch_open_orders()  # type: ignore[attr-defined]
                    sl_alive = any(
                        o.get("id") == sl_order_id for o in open_orders
                    )
                    if not sl_alive:
                        self._log.warning(
                            f"APM: SL order {sl_order_id} missing for {symbol}, re-placing"
                        )
                        entry_price = Decimal(str(pos.get("entry_price", "0")))
                        amount = Decimal(str(pos.get("amount", "0")))
                        api_side = pos.get("side", "buy")
                        if entry_price > 0 and amount > 0:
                            new_sl = await self._client.place_stop_loss(  # type: ignore[attr-defined]
                                symbol, api_side, entry_price, amount
                            )
                            if new_sl:
                                new_id = new_sl.get("orderId", "")
                                await self._store.update_sl(symbol, api_side, new_id)  # type: ignore[attr-defined]

        except Exception:
            self._log.exception("APM: reconciliation failed")

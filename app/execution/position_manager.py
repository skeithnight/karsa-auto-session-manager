"""Active Position Manager — Phase 6 position lifecycle management.

Replaces TrailingStopManager and CheckpointManager when APM_ENABLED=True.
Runs on 2s interval. All errors -> backoff, never crash loop.

Core responsibilities:
  - +1R breakeven lock (exchange-side SL amend)
  - Regime-aware trailing stop (3x ATR Chandelier for TREND)
  - Time-based exits (max_hold_time_mins from RiskProfile)
  - Regime Shift Kill Switch (with 3-check hysteresis)
  - Position reconciliation (ghost detection)

Sub-modules (extracted from this file):
  - tp_manager.py      — Take-profit, scale-out, breakeven
  - exit_manager.py    — Trailing stop, time exit, regime shift, force close, R-multiple
  - position_reconciler.py — Position field repair, ATR, ghost detection, health check
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

# --- Re-export all constants from shared module (backward compatibility) ---
from app.execution.constants import (  # noqa: E402,F401
    APM_BREAKEVEN_ATR_MULT,
    APM_BREAKEVEN_FEE_PCT,
    APM_BREAKEVEN_LOCK_R,
    APM_ERROR_BACKOFF_S,
    APM_MONITOR_INTERVAL_S,
    APM_RECONCILE_INTERVAL_S,
    APM_TREND_TRAIL_ACTIVATE_R,
    APM_TREND_TRAIL_ATR_MULT,
    ORPHAN_RE_ENTRY_GRACE_S,
    REGIME_FAMILY,
    REGIME_SHIFT_CONFIRM_COUNT,
    TRAILING_LIMIT_ACTIVATE_R,
    TRAILING_LIMIT_OFFSET_PCT,
    TRAILING_LIMIT_TIMEOUT_S,
    _safe_dec,
)
from app.execution.exit_manager import ExitManager  # noqa: E402,F401
from app.execution.position_reconciler import PositionReconciler  # noqa: E402,F401

# --- Re-export extracted classes for backward compatibility ---
from app.execution.tp_manager import TakeProfitManager  # noqa: E402,F401


class ActivePositionManager:
    """Manages open positions: breakeven, trailing, time exit, regime kill switch."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        redis_client: object,
        regime_classifier: object,
        alert_service: object,
        trade_memory: object | None = None,
        logger_: Any | None = None,
        trade_store: object | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self.redis_client = redis_client
        self._regime = regime_classifier
        self._alert = alert_service
        self._trade_memory = trade_memory
        self._log = logger_ or logger
        self._regime_shift_counts: dict[str, int] = {}
        self._recently_force_closed: dict[str, float] = {}  # symbol -> timestamp of force close

        # Compose extracted managers
        self._tp_manager = TakeProfitManager(
            bybit_client=bybit_client,
            position_store=position_store,
            alert_service=alert_service,
            logger_=self._log,
        )
        self._exit_manager = ExitManager(
            bybit_client=bybit_client,
            position_store=position_store,
            regime_classifier=regime_classifier,
            alert_service=alert_service,
            trade_memory=trade_memory,
            logger_=self._log,
            trade_store=trade_store,
        )
        self._reconciler = PositionReconciler(
            bybit_client=bybit_client,
            position_store=position_store,
            regime_classifier=regime_classifier,
            alert_service=alert_service,
            recently_force_closed=self._recently_force_closed,
            logger_=self._log,
        )

    def set_trade_store(self, trade_store: object) -> None:
        """Set or update Postgres trade store instance."""
        self._exit_manager.set_trade_store(trade_store)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start_monitoring(self) -> None:
        """Main monitoring loop -- runs forever with error backoff."""
        last_reconcile = 0.0
        _cached_exchange_positions: list = []
        _last_positions_fetch = 0.0
        _POSITIONS_CACHE_TTL = 30.0  # seconds -- REST rate limit protection

        while True:
            try:
                now = datetime.now(UTC).timestamp()

                positions = await self._store.list_all()  # type: ignore[attr-defined]

                # Sync Bybit positions missing from Redis -- refresh every 30s, not every 2s
                if now - _last_positions_fetch >= _POSITIONS_CACHE_TTL:
                    _cached_exchange_positions = await self._client.fetch_positions()  # type: ignore[attr-defined]
                    _last_positions_fetch = now

                exchange_positions = _cached_exchange_positions
                existing_syms = {(p.get("symbol", ""), p.get("side", "")) for p in positions}
                for ep in exchange_positions:
                    ep_sym = ep.get("symbol", "")
                    ep_side = "LONG" if ep.get("side") == "buy" else "SHORT"
                    ccxt_sym = ep_sym[:-4] + "/" + ep_sym[-4:] if len(ep_sym) > 4 else ep_sym
                    if (ccxt_sym, ep_side) not in existing_syms:
                        # Orphan sync grace period: skip if this symbol was force-closed recently.
                        # Prevents the orphan->force-close->re-sync phantom loop (forensic P0).
                        recently_closed_at = self._recently_force_closed.get(ccxt_sym, 0)
                        if recently_closed_at > 0 and (now - recently_closed_at) < ORPHAN_RE_ENTRY_GRACE_S:
                            self._log.debug(
                                f"APM: skipping orphan {ccxt_sym} {ep_side} -- "
                                f"force-closed {now - recently_closed_at:.0f}s ago (grace={ORPHAN_RE_ENTRY_GRACE_S}s)"
                            )
                            continue
                        entry = Decimal(str(ep.get("entry_price", 0)))
                        amount = Decimal(str(ep.get("contracts", 0)))
                        # Skip tiny positions (< 5 USDT notional) -- below Bybit minimum order
                        notional = entry * amount
                        if notional < Decimal("5"):
                            self._log.debug(
                                f"APM: skipping tiny orphan {ccxt_sym} {ep_side} "
                                f"(notional={notional:.2f} < 5 USDT minimum)"
                            )
                            continue
                        if entry > 0 and amount > 0:
                            await self._store.save(
                                symbol=ccxt_sym,
                                side=ep_side,
                                entry_price=entry,
                                amount=amount,
                            )
                            # Mark orphan with UNKNOWN regime -- historical context is lost.
                            # Prevents false-positive regime kill switches on guessed data.
                            try:
                                saved_pos = await self._store.get(ccxt_sym, ep_side)
                                if saved_pos:
                                    saved_pos["entry_regime"] = "UNKNOWN"
                                    saved_pos["regime"] = "UNKNOWN"
                                    from app.core.position_store import _normalize_side
                                    side_key = _normalize_side(ep_side)
                                    redis_key = f"karsa:position:{ccxt_sym}:{side_key}"
                                    await self._store.redis.set(redis_key, _json.dumps(saved_pos))  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            # Re-read the saved pos to get full dict for reconciliation
                            saved = await self._store.get(ccxt_sym, ep_side)
                            if saved:
                                positions.append(saved)
                            self._log.warning(f"APM: synced orphan {ccxt_sym} {ep_side} from Bybit")

                # Fetch live mark prices for all held symbols
                live_prices: dict[str, Decimal] = {}
                missing_symbols = []
                try:
                    for pos in positions:
                        sym = pos.get("symbol", "")
                        if not sym:
                            continue
                        raw = await self.redis_client.get(f"global:state:{sym}")
                        state = _json.loads(raw) if raw else None
                        if state and state.get("best_bid") and state.get("best_ask"):
                            # Use mid price from orderbook
                            mid = (Decimal(str(state["best_bid"])) + Decimal(str(state["best_ask"]))) / Decimal("2")
                            live_prices[sym] = mid
                        else:
                            missing_symbols.append(sym)

                    if missing_symbols:
                        self._log.debug(f"APM: Redis missing live price for {missing_symbols}, falling back to Bybit REST")
                        # fallback to ccxt fetch_tickers
                        tickers = await self._client.fetch_tickers()  # type: ignore[attr-defined]
                        # Bybit fetch_tickers returns a list or dict depending on ccxt version.
                        # Handle both.
                        if isinstance(tickers, dict):
                            tickers_list = list(tickers.values())
                        else:
                            tickers_list = tickers

                        for t in tickers_list:
                            sym = t.get("symbol", "")
                            if sym in missing_symbols:
                                last = t.get("last") or t.get("close")
                                if last:
                                    live_prices[sym] = Decimal(str(last))
                except Exception as e:
                    self._log.warning(f"APM: failed to fetch live prices: {e}")

                for pos in positions:
                    symbol = pos.get("symbol", "")
                    if symbol in live_prices:
                        pos["live_price"] = str(live_prices[symbol])
                    await self._manage_single_position(pos)

                if now - last_reconcile > APM_RECONCILE_INTERVAL_S:
                    await self._reconciler._reconcile_positions()
                    last_reconcile = now

                    # Adaptive Rebalance: check if any position should be closed to fund stronger signal
                    await self._check_rebalance_opportunities(positions)

                await asyncio.sleep(APM_MONITOR_INTERVAL_S)

            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("APM: error in monitoring loop")
                await asyncio.sleep(APM_ERROR_BACKOFF_S)

    async def _check_rebalance_opportunities(self, positions: list[dict[str, Any]]) -> None:
        """Adaptive Rebalance: evaluate if any open position should be closed
        to fund a stronger incoming signal.

        Reads pending signals from Redis and compares EV with open positions.
        If a pending signal has >1.5x EV of the weakest position, logs a rebalance alert.
        """
        if not self.redis_client or not positions:
            return

        try:
            # Find weakest open position by unrealized PnL
            weakest = None
            lowest_pnl = Decimal("0")
            for pos in positions:
                entry = _safe_dec(pos.get("entry_price", "0"))
                live = _safe_dec(pos.get("live_price", "0"))
                side = pos.get("side", "LONG")
                if entry <= 0 or live <= 0:
                    continue
                pnl_pct = (live - entry) / entry if side == "LONG" else (entry - live) / entry
                if pnl_pct < lowest_pnl:
                    lowest_pnl = pnl_pct
                    weakest = pos

            if weakest is None or lowest_pnl >= Decimal("-0.02"):
                return  # No position losing >2%, no rebalance needed

            # Read pending signals from Redis
            raw = await self.redis_client.get("karsa:pending_signals")
            if not raw:
                return

            import json
            pending = json.loads(raw)
            if not pending:
                return

            # Find best pending signal
            best_signal = max(pending, key=lambda s: s.get("score", 0))
            signal_score = best_signal.get("score", 0)

            # Rebalance if signal score > 80 and weakest position losing >2%
            if signal_score > 80 and lowest_pnl < Decimal("-0.02"):
                sym = weakest.get("symbol", "?")
                self._log.warning(
                    f"APM REBALANCE OPPORTUNITY: {sym} losing {float(lowest_pnl)*100:.1f}% "
                    f"vs pending signal {best_signal.get('symbol', '?')} score={signal_score:.0f}. "
                    f"Consider closing loser to fund winner."
                )
                # Write rebalance alert to Redis for bot notification
                try:
                    alert_data = json.dumps({
                        "type": "rebalance_opportunity",
                        "losing_symbol": sym,
                        "losing_pnl_pct": float(lowest_pnl),
                        "pending_symbol": best_signal.get("symbol", "?"),
                        "pending_score": signal_score,
                    })
                    await self.redis_client.set("karsa:alert:rebalance", alert_data, ex=300)
                except Exception:
                    pass

        except Exception as e:
            self._log.debug(f"APM rebalance check failed: {e}")

    # ------------------------------------------------------------------
    # Health check loop (delegated to reconciler)
    # ------------------------------------------------------------------

    async def start_health_check_loop(self, interval_s: int = 60) -> None:
        """Scheduled position health check -- delegates to PositionReconciler."""
        await self._reconciler.start_health_check_loop(interval_s)

    # ------------------------------------------------------------------
    # Delegate methods (backward compatibility)
    # ------------------------------------------------------------------

    # --- Take-profit delegates ---
    async def _ensure_take_profit(self, pos: dict[str, Any], entry_price: Decimal, initial_risk: Decimal, side: str) -> None:
        await self._tp_manager._ensure_take_profit(pos, entry_price, initial_risk, side)

    async def _scale_out_position(self, pos: dict[str, Any], pct: Decimal, entry_price: Decimal, side: str) -> None:
        await self._tp_manager._scale_out_position(pos, pct, entry_price, side)

    async def proactive_scale_out(self, symbol: str, side: str = "LONG", ratio: Decimal = Decimal("0.50")) -> bool:
        return await self._tp_manager.proactive_scale_out(symbol, side, ratio)

    async def _move_stop_to_breakeven(self, pos: dict[str, Any], entry_price: Decimal, side: str) -> None:
        await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)

    # --- Exit delegates ---
    async def _manage_trend_trailing_stop(self, pos: dict[str, Any], live_price: Decimal, r_multiple: Decimal, side: str, current_sl: Decimal) -> None:
        await self._exit_manager._manage_trend_trailing_stop(pos, live_price, r_multiple, side, current_sl)

    async def _manage_time_exit(self, pos: dict[str, Any], entry_time: object, max_minutes: int, live_price: Decimal, entry_price: Decimal, side: str, r_mult: Decimal, entry_regime: str) -> bool:
        return await self._exit_manager._manage_time_exit(pos, entry_time, max_minutes, live_price, entry_price, side, r_mult, entry_regime)

    async def _check_regime_shift(self, pos: dict[str, Any], symbol: str, entry_regime: str) -> bool:
        return await self._exit_manager._check_regime_shift(pos, symbol, entry_regime)

    async def _check_momentum_exhaustion(self, symbol: str, side: str) -> bool:
        return await self._exit_manager._check_momentum_exhaustion(symbol, side)

    async def _force_close_position(self, pos: dict[str, Any], reason: str) -> None:
        await self._exit_manager._force_close_position(pos, reason)

    # --- Static delegate ---
    @staticmethod
    def _calculate_r_multiple(side: str, entry_price: Decimal, live_price: Decimal, initial_risk: Decimal) -> Decimal:
        return ExitManager._calculate_r_multiple(side, entry_price, live_price, initial_risk)

    # --- Reconciler delegates ---
    async def _reconcile_position(self, pos: dict[str, Any]) -> bool:
        return await self._reconciler._reconcile_position(pos)

    async def _reconcile_positions(self) -> None:
        await self._reconciler._reconcile_positions()

    async def _compute_atr(self, symbol: str, period: int = 14) -> Decimal:
        return await self._reconciler._compute_atr(symbol, period)

    # ------------------------------------------------------------------
    # Per-position management (orchestration)
    # ------------------------------------------------------------------

    async def _manage_single_position(self, pos: dict[str, Any]) -> None:
        """Run all position checks: breakeven, trailing, time, regime."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")

        # Skip if force-close failed recently -- retry cooldown (5 min)
        retry_at = pos.get("force_close_retry_at", 0)
        if retry_at and datetime.now(UTC).timestamp() < retry_at:
            return

        # Reconcile ALL missing fields from Bybit + candles
        await self._reconciler._reconcile_position(pos)

        _raw_entry = pos.get("entry_price", "0") or "0"
        entry_price = Decimal(str(_raw_entry))
        _raw_live = pos.get("live_price", pos.get("entry_price", "0")) or "0"
        live_price = Decimal(str(_raw_live))
        entry_regime = pos.get("entry_regime", "UNKNOWN")
        _raw_sl = pos.get("current_sl", pos.get("stop_loss", "0")) or "0"
        sl_price = Decimal(str(_raw_sl))
        _raw_risk = pos.get("initial_risk_per_unit", "0") or "0"
        initial_risk = Decimal(str(_raw_risk))
        moved_to_be = pos.get("moved_to_breakeven", False)

        # entry_time was recently fixed in position_store to write natively, but fallback to entered_at for existing/old positions
        _raw_time = pos.get("entry_time") or pos.get("entered_at")
        entry_time = _raw_time if _raw_time else None
        max_hold_mins = int(pos.get("max_hold_time_mins", 1440))

        if entry_price <= 0:
            return

        if initial_risk <= 0:
            return

        r_mult = self._exit_manager._calculate_r_multiple(side, entry_price, live_price, initial_risk)

        is_hyper = str(entry_regime).startswith("HYPER")
        wick_long = Decimal("-0.015") if is_hyper else Decimal("-0.03")
        wick_short = Decimal("0.015") if is_hyper else Decimal("0.03")
        be_lock_r = Decimal("0.3") if is_hyper else APM_BREAKEVEN_LOCK_R
        half_be_r = Decimal("0.1") if is_hyper else Decimal("0.5")

        # Flash-Crash Micro-Circuit Breaker (Wick Guard)
        _raw_last_tick = pos.get("last_tick_price", pos.get("live_price", "0")) or "0"
        last_tick_price = Decimal(str(_raw_last_tick))
        if last_tick_price > 0 and live_price > 0:
            tick_delta = (live_price - last_tick_price) / last_tick_price
            # If price moves > wick_threshold against us in a single check, trigger emergency exit
            if (side == "LONG" and tick_delta <= wick_long) or (side == "SHORT" and tick_delta >= wick_short):
                self._log.critical(
                    f"APM WICK GUARD: {symbol} {side} dropped/spiked {tick_delta:.2%} instantly! "
                    f"live={live_price} last={last_tick_price}. FRONT-RUNNING CASCADE!"
                )

                import time
                last_wick_ts = float(pos.get("last_wick_guard_ts", 0))
                if time.time() - last_wick_ts < 10.0:  # 10s debounce
                    pos["last_tick_price"] = str(live_price)  # Still update price reference
                    try:
                        from app.core.position_store import _normalize_side
                        side_key = _normalize_side(side)
                        redis_key = f"karsa:position:{symbol}:{side_key}"
                        await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    return
                pos["last_wick_guard_ts"] = str(time.time())

                if self._alert:
                    asyncio.create_task(self._alert.send(
                        f"WICK GUARD EMERGENCY EXIT: {symbol} {side} moved {tick_delta:.2%} instantly! Front-running slippage."
                    ))
                # Instead of market-selling into a thin book, tighten SL to 1.5% of current price
                if side == "LONG":
                    tight_sl = live_price * Decimal("0.985")
                else:
                    tight_sl = live_price * Decimal("1.015")
                try:
                    sl_order_id = pos.get("sl_order_id", "")
                    amount = Decimal(str(pos.get("amount", "0")))
                    api_side = "buy" if side == "LONG" else "sell"
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, tight_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(tight_sl)
                    pos["stop_loss"] = str(tight_sl)
                    sl_price = tight_sl
                    self._log.warning(f"APM WICK GUARD: tightened SL for {symbol} to {tight_sl}")
                except Exception:
                    self._log.exception(f"APM WICK GUARD: SL tighten failed for {symbol}")

                pos["last_tick_price"] = str(live_price)
                try:
                    from app.core.position_store import _normalize_side
                    side_key = _normalize_side(side)
                    redis_key = f"karsa:position:{symbol}:{side_key}"
                    await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
                except Exception:
                    pass
                return

        pos["last_tick_price"] = str(live_price)

        # Track Peak Price for Chandelier Trailing
        peak_price = Decimal(str(pos.get("peak_price", entry_price)))
        peak_updated = False
        if side == "LONG" and live_price > peak_price or side == "SHORT" and live_price < peak_price:
            peak_price = live_price
            peak_updated = True

        if peak_updated:
            pos["peak_price"] = str(peak_price)

        # NOTE: $1 hard cap removed -- ATR-based SL (via max_loss_usd) is the correct stop.
        # A $1 cap on a position sized by available_balance * risk_pct conflicts with
        # risk-proportional sizing and causes premature SL hits. See walkthrough_max_loss.md.

        # Exchange-side TP for RANGE/CHOP -- place once on first loop
        if not pos.get("tp_placed") and entry_regime in ("RANGE", "CHOP"):
            await self._tp_manager._ensure_take_profit(pos, entry_price, initial_risk, side)

        # Multi-Tier Scale-Outs
        scale_tier = pos.get("scale_tier", 0)

        # Range/Chop/Hyper/Sniper logic
        if entry_regime == "SNIPER":
            # Asymmetric V-Shape Exits
            if scale_tier < 1 and r_mult >= Decimal("1.5"):
                # Exit 50% immediately at +1.5R and move SL to BE
                await self._tp_manager._scale_out_position(pos, Decimal("0.50"), entry_price, side)
                pos["scale_tier"] = 1
                if not moved_to_be:
                    await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)
                    moved_to_be = True

            # Momentum Exhaustion Check (1m RSI > 80 or < 20)
            if r_mult > Decimal("0.2"):
                exhausted = await self._exit_manager._check_momentum_exhaustion(symbol, side)
                if exhausted:
                    self._log.warning(f"APM SNIPER: Momentum Exhaustion detected for {symbol}. Market closing remainder.")
                    if self._alert:
                        asyncio.create_task(self._alert.send(f"SNIPER MOMENTUM EXHAUSTION EXIT: {symbol} {side} closed at {live_price} to avoid dead-cat bounce!"))
                    await self._exit_manager._force_close_position(pos, symbol, side)
                    return

        elif "TREND" not in entry_regime:
            if scale_tier < 1 and r_mult >= be_lock_r:
                await self._tp_manager._scale_out_position(pos, Decimal("0.50"), entry_price, side)
                pos["scale_tier"] = 1
                # CRITICAL FIX: Move SL to breakeven after scale-out
                # Previously missing -- caused -4.4% avg loss on breakeven exits
                if not moved_to_be:
                    await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)
                    moved_to_be = True

        # Microstructure-Aware CVD Trailing Stop (Exit Alpha)
        if r_mult >= Decimal("0.8") and not moved_to_be:
            try:
                cvd_slope_raw = await self._store.redis.get(f"karsa:market:{symbol}:cvd_slope")  # type: ignore[attr-defined]
                if cvd_slope_raw:
                    cvd_slope = float(cvd_slope_raw)
                    if (side == "LONG" and cvd_slope < -0.3) or (side == "SHORT" and cvd_slope > 0.3):
                        self._log.warning(
                            f"APM CVD EXHAUSTION: {symbol} {side} CVD slope flipped ({cvd_slope:.2f}) at {r_mult:.2f}R! "
                            f"Locking Breakeven + 1 tick immediately."
                        )
                        await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)
                        moved_to_be = True
                        if self._alert:
                            asyncio.create_task(self._alert.send(
                                f"APM CVD EXHAUSTION LOCK: {symbol} {side} moved SL to Breakeven at {r_mult:.2f}R due to CVD slope reversal ({cvd_slope:.2f})."
                            ))
            except Exception as cvd_err:
                self._log.debug(f"APM CVD slope check error for {symbol}: {cvd_err}")
        # Trend logic: 33% at 1.5R (Tier 1), 33% at 3.0R (Tier 2)
        elif scale_tier < 1 and r_mult >= Decimal("1.5"):
            await self._tp_manager._scale_out_position(pos, Decimal("0.33"), entry_price, side)
            pos["scale_tier"] = 1
            # Force breakeven lock upon Tier 1 scale-out to secure a free ride
            if not moved_to_be:
                await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)
                moved_to_be = True
        elif scale_tier < 2 and r_mult >= Decimal("3.0"):
            await self._tp_manager._scale_out_position(pos, Decimal("0.33"), entry_price, side)
            pos["scale_tier"] = 2

        # --- MOON BAG TIERED EXIT (Phase 4) ---
        # 80% closed at +2.0R (after TREND scale-out at 1.5R), 20% rides with breakeven SL
        amount = Decimal(str(pos.get("amount", "0")))  # CRITICAL-2 fix: define amount at top level
        tranche_state = pos.get("tranche_state", "INITIAL")
        if tranche_state == "INITIAL" and r_mult >= Decimal("2.0"):
            if amount > 0:
                # Calculate close amount (80% of position)
                close_amount = (amount * Decimal("0.8")).quantize(Decimal("0.001"))
                moon_bag_amount = amount - close_amount

                if close_amount > 0:
                    self._log.warning(
                        f"APM MOON BAG: {symbol} {side} hit +{r_mult:.2f}R -- executing TIERED EXIT "
                        f"(80% closed, 20% moon bag)"
                    )

                    try:
                        # 1. Execute 80% partial close (reduceOnly)
                        api_side = "buy" if side == "LONG" else "sell"
                        await self._client.reduce_position(
                            symbol=symbol.replace("/", ""),
                            side=api_side,
                            amount=close_amount,
                        )

                        # 2. Update position state immediately
                        pos["tranche_state"] = "MOON_BAG_ACTIVE"
                        pos["amount"] = str(moon_bag_amount)
                        pos["moon_bag_amount"] = str(moon_bag_amount)

                        # 3. Set Breakeven SL for remaining 20% via BybitClient
                        try:
                            moon_bag_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT
                            await self._client.place_stop_loss(  # type: ignore[attr-defined]
                                symbol=symbol.replace("/", ""),
                                side=api_side,
                                stop_price=moon_bag_sl,
                                amount=moon_bag_amount,
                            )
                            pos["moon_bag_sl"] = str(moon_bag_sl)
                            pos["current_sl"] = str(moon_bag_sl)
                            pos["stop_loss"] = str(moon_bag_sl)
                            sl_price = moon_bag_sl
                            self._log.info(
                                f"APM MOON BAG: {symbol} SL set to breakeven {moon_bag_sl} (entry+fee)"
                            )
                        except Exception as e:
                            # FAIL-SAFE: Close remaining 20% if SL placement fails
                            self._log.critical(
                                f"APM MOON BAG: FAILED to set SL for {symbol}: {e}. "
                                f"Emergency closing remaining 20% to protect capital."
                            )
                            await self._client.reduce_position(
                                symbol=symbol.replace("/", ""),
                                side=api_side,
                                amount=moon_bag_amount,
                            )
                            pos["amount"] = "0"
                            return

                        # 4. Alert
                        if self._alert:
                            asyncio.create_task(self._alert.send(
                                f"{symbol} {side} MOON BAG: 80% closed at +{r_mult:.1f}R, "
                                f"20% ({moon_bag_amount}) riding with breakeven SL"
                            ))

                    except Exception as e:
                        self._log.error(f"APM MOON BAG: tiered exit FAILED for {symbol}: {e}")
                        # Don't update state -- will retry next cycle

        # Moon Bag Ultra-Wide Trailing (5x ATR for moon bag positions)
        if tranche_state == "MOON_BAG_ACTIVE":
            atr = Decimal(str(pos.get("atr", "0")))
            if atr > 0:
                highest = Decimal(str(pos.get("highest_since_partial", "0") or "0"))
                if live_price > highest:
                    highest = live_price
                    pos["highest_since_partial"] = str(highest)

                current_moon_bag_sl = _safe_dec(pos.get("moon_bag_sl", "0"))
                # Moon bag trailing: 5x ATR from highest since partial close
                if side == "LONG":
                    new_sl = highest - (atr * Decimal("5"))
                    if new_sl > sl_price and new_sl > current_moon_bag_sl:
                        sl_price = new_sl
                        try:
                            api_side = "buy" if side == "LONG" else "sell"
                            sl_order_id = pos.get("sl_order_id", "")
                            await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_sl, Decimal(str(pos.get("amount", "0"))))  # type: ignore[attr-defined]
                            pos["current_sl"] = str(new_sl)
                            pos["stop_loss"] = str(new_sl)
                            pos["moon_bag_sl"] = str(new_sl)
                            self._log.info(f"APM MOON BAG: {symbol} trailing SL amended to {new_sl} (5x ATR)")
                        except Exception as e:
                            self._log.debug(f"APM MOON BAG: trailing amend failed for {symbol}: {e}")
                else:
                    new_sl = highest + (atr * Decimal("5"))
                    if (sl_price == 0 or new_sl < sl_price) and (current_moon_bag_sl == 0 or new_sl < current_moon_bag_sl):
                        sl_price = new_sl
                        try:
                            api_side = "buy" if side == "LONG" else "sell"
                            sl_order_id = pos.get("sl_order_id", "")
                            await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_sl, Decimal(str(pos.get("amount", "0"))))  # type: ignore[attr-defined]
                            pos["current_sl"] = str(new_sl)
                            pos["stop_loss"] = str(new_sl)
                            pos["moon_bag_sl"] = str(new_sl)
                            self._log.info(f"APM MOON BAG: {symbol} trailing SL amended to {new_sl} (5x ATR)")
                        except Exception as e:
                            self._log.debug(f"APM MOON BAG: trailing amend failed for {symbol}: {e}")
        # -------------------------------------------------------------------------

        # --- SPRINT 1: TRAILING LIMIT EXIT (Maker-Only) ---
        # Instead of relying on wide 5x ATR trailing stops (which exit via market),
        # place a tight limit order that trails the current price for maker fills.
        # Fail-safe: if limit not filled within 60s, cancel and market exit.
        tl_state = pos.get("trailing_limit_state", "INACTIVE")

        if tl_state == "INACTIVE" and r_mult >= TRAILING_LIMIT_ACTIVATE_R:
            # Activate trailing limit exit
            pos["trailing_limit_state"] = "TRAILING_LIMIT_ACTIVE"
            pos["trailing_limit_placed_at"] = str(datetime.now(UTC).timestamp())
            tl_state = "TRAILING_LIMIT_ACTIVE"
            self._log.info(
                f"APM TRAILING LIMIT: Activating for {symbol} {side} at +{r_mult:.2f}R"
            )

        if tl_state == "TRAILING_LIMIT_ACTIVE":
            import time as _time
            now_ts = _time.time()
            placed_at = float(pos.get("trailing_limit_placed_at", now_ts))

            # FAIL-SAFE: Timeout check -- if limit order not filled within timeout, market exit
            timeout_s = TRAILING_LIMIT_TIMEOUT_S
            if now_ts - placed_at > timeout_s:
                self._log.warning(
                    f"APM TRAILING LIMIT: TIMEOUT for {symbol} after {timeout_s}s -- "
                    f"canceling limit, executing market exit"
                )
                # Cancel any existing limit order
                existing_order_id = pos.get("trailing_limit_order_id", "")
                if existing_order_id:
                    try:
                        await self._client.cancel_order(existing_order_id, symbol)  # type: ignore[attr-defined]
                    except Exception:
                        pass

                # Market exit
                await self._exit_manager._force_close_position(pos, f"trailing_limit_timeout_{timeout_s}s")
                pos["trailing_limit_state"] = "FILLED"
                return

            # Calculate ideal limit exit price (tight trail)
            offset_pct = TRAILING_LIMIT_OFFSET_PCT
            if side == "LONG":
                # Place limit SELL slightly below best bid for maker fill
                ideal_exit = live_price * (Decimal("1") - offset_pct)
                # Only move limit UP (never down -- that would worsen our fill)
                current_limit = Decimal(str(pos.get("trailing_limit_price", "0")))
                if current_limit <= 0 or ideal_exit > current_limit:
                    new_limit_price = ideal_exit
                else:
                    new_limit_price = current_limit
            else:
                # Place limit BUY slightly above best ask for maker fill
                ideal_exit = live_price * (Decimal("1") + offset_pct)
                # Only move limit DOWN (never up)
                current_limit = Decimal(str(pos.get("trailing_limit_price", "0")))
                if current_limit <= 0 or ideal_exit < current_limit:
                    new_limit_price = ideal_exit
                else:
                    new_limit_price = current_limit

            existing_order_id = pos.get("trailing_limit_order_id", "")

            # Check if we already have a limit order -- if price improved, replace it
            needs_new_order = False
            if not existing_order_id:
                needs_new_order = True
            else:
                # Check if price moved enough to warrant repricing (> 0.05% improvement)
                old_price = Decimal(str(pos.get("trailing_limit_price", "0")))
                if old_price > 0:
                    price_improvement = abs(new_limit_price - old_price) / old_price
                    if price_improvement > offset_pct:
                        needs_new_order = True
                        # Cancel old order
                        try:
                            await self._client.cancel_order(existing_order_id, symbol)  # type: ignore[attr-defined]
                        except Exception:
                            pass

            if needs_new_order:
                try:
                    api_side = "buy" if side == "LONG" else "sell"
                    amount = Decimal(str(pos.get("amount", "0")))
                    if amount > 0 and new_limit_price > 0:
                        # Place reduce-only limit order for maker fill
                        order = await self._client.create_limit_order(  # type: ignore[attr-defined]
                            symbol,
                            api_side,
                            amount,
                            new_limit_price,
                            params={"reduceOnly": True},
                        )
                        order_id = order.get("orderId", order.get("id", ""))
                        pos["trailing_limit_order_id"] = str(order_id)
                        pos["trailing_limit_price"] = str(new_limit_price)
                        pos["trailing_limit_placed_at"] = str(now_ts)
                        self._log.info(
                            f"APM TRAILING LIMIT: {symbol} {side} limit placed @ {new_limit_price} "
                            f"(live={live_price}, offset={offset_pct})"
                        )
                except Exception as e:
                    self._log.debug(f"APM TRAILING LIMIT: order placement failed for {symbol}: {e}")

        # -------------------------------------------------------------------------

        # ATR-based BE trigger: price must move beyond noise threshold
        atr = Decimal(str(pos.get("atr", "0")))
        if atr > 0:
            price_move = abs(live_price - entry_price)
            be_triggered = price_move >= atr * APM_BREAKEVEN_ATR_MULT
        else:
            # Fallback to fixed 1R when ATR unavailable
            be_triggered = r_mult >= be_lock_r

        if not moved_to_be and be_triggered:
            await self._tp_manager._move_stop_to_breakeven(pos, entry_price, side)
            pos["moved_to_breakeven"] = True
            moved_to_be = True

        # --- HALF-BREAKEVEN ---
        moved_to_half_be = pos.get("moved_to_half_be", False)
        if not moved_to_be and not moved_to_half_be and r_mult >= half_be_r:
            half_trail_r = Decimal("-0.5")  # Move SL to -0.5R
            if side == "LONG":
                new_step_sl = entry_price + (initial_risk * half_trail_r)
            else:
                new_step_sl = entry_price - (initial_risk * half_trail_r)

            sl_tighter = (side == "LONG" and new_step_sl > sl_price) or (side == "SHORT" and new_step_sl < sl_price)
            if sl_tighter:
                try:
                    amount = Decimal(str(pos.get("amount", "0")))
                    api_side = "buy" if side == "LONG" else "sell"
                    sl_order_id = pos.get("sl_order_id", "")
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_step_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(new_step_sl)
                    pos["stop_loss"] = str(new_step_sl)
                    sl_price = new_step_sl
                    pos["moved_to_half_be"] = True
                    self._log.info(f"APM: Half-Breakeven locked for {symbol} to {new_step_sl}")
                except Exception as e:
                    self._log.debug(f"APM: Half-Breakeven amend failed for {symbol}: {e}")
        # -------------------------------------------------------------------------

        # --- AGGRESSIVE PROFIT-PROTECT TRAILING ---
        # User request: "secure when position already in profit and then goes down then immedietly close profit"
        peak_r_mult = self._exit_manager._calculate_r_multiple(side, entry_price, peak_price, initial_risk)

        # Dynamic activation threshold based on regime
        is_hyper = "HYPER" in entry_regime
        activation_threshold = Decimal("0.5") if is_hyper else Decimal("1.5")

        if initial_risk > 0 and peak_r_mult >= activation_threshold:
            if is_hyper:
                # HYPER: Ultra-tight trail. We lock in peak - 0.2R.
                # If peak is 1.0R, we lock in 0.8R.
                trail_r = peak_r_mult - Decimal("0.2")
                # HYPER absolute floor is 0.3R
                trail_r = max(trail_r, Decimal("0.3"))
            elif peak_r_mult >= Decimal("2.5"):
                trail_r = peak_r_mult - Decimal("0.3")
            else:
                trail_r = peak_r_mult - Decimal("0.5")

            # Ensure we lock in at least 0.5R for non-hyper
            if not is_hyper:
                trail_r = max(trail_r, Decimal("0.5"))

            if side == "LONG":
                new_step_sl = entry_price + (initial_risk * trail_r)
            else:
                new_step_sl = entry_price - (initial_risk * trail_r)

            # Only amend if the new SL is tighter (more protective)
            sl_tighter = (side == "LONG" and new_step_sl > sl_price) or (side == "SHORT" and new_step_sl < sl_price)

            # Anti-spam: Only amend if the new SL is at least 0.15R better than current SL
            current_sl_r = self._exit_manager._calculate_r_multiple(side, entry_price, sl_price, initial_risk)
            significant_move = (trail_r - current_sl_r) >= Decimal("0.15")

            if sl_tighter and significant_move:
                try:
                    api_side = "buy" if side == "LONG" else "sell"
                    sl_order_id = pos.get("sl_order_id", "")
                    await self._client.amend_stop_loss(sl_order_id, symbol, api_side, new_step_sl, amount)  # type: ignore[attr-defined]
                    pos["current_sl"] = str(new_step_sl)
                    pos["stop_loss"] = str(new_step_sl)
                    sl_price = new_step_sl
                    self._log.info(f"APM: Profit-Protect SL amended for {symbol} to {new_step_sl} (Peak R: {peak_r_mult:.2f}, Locked: +{trail_r:.2f}R)")
                except Exception as e:
                    self._log.debug(f"APM: Profit-Protect amend failed for {symbol}: {e}")
        # -------------------------------------------------------------------------

        if "TREND" in entry_regime:
            await self._exit_manager._manage_trend_trailing_stop(pos, live_price, r_mult, side, sl_price)

        if entry_time is not None:
            closed = await self._exit_manager._manage_time_exit(pos, entry_time, max_hold_mins, live_price, entry_price, side, r_mult, entry_regime)
            if closed:
                return

        closed = await self._exit_manager._check_regime_shift(pos, symbol, entry_regime)
        if closed:
            return

        pos["last_check_at"] = datetime.now(UTC).isoformat()
        try:
            from app.core.position_store import _normalize_side

            side_key = _normalize_side(side)
            redis_key = f"karsa:position:{symbol}:{side_key}"
            await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
        except Exception as persist_err:
            self._log.warning("APM: failed to persist state for %s: %s", symbol, persist_err)

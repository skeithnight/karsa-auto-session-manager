"""ShadowAPM — virtual ActivePositionManager for shadow mode.

Wraps real APM, adds SL hit + wick + funding logic for simulated positions.
Refinements applied:
  - Wick miss prevention: tracks worst_price_seen in Redis position state
  - Funding rate drag: deducts 8h funding fee on held positions
  - Pending limit fill: activates PENDING orders when price crosses entry
"""

from __future__ import annotations

import asyncio
import json as _json
import redis
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core import metrics
from app.core.config import get_settings
from app.core.redis_client import RedisClient
from app.execution.shadow_executor import ShadowExecutor
from app.execution.position_manager import (
    APM_BREAKEVEN_FEE_PCT,
    APM_BREAKEVEN_LOCK_R,
    APM_TREND_TRAIL_ACTIVATE_R,
    APM_TREND_TRAIL_ATR_MULT,
    REGIME_SHIFT_CONFIRM_COUNT,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SHADOW_PENDING_TTL_SECS: int = 600  # limit orders expire after 10 min
SHADOW_FUNDING_INTERVAL_HOURS: int = 8


class ShadowAPM:
    """Shadow ActivePositionManager.

    Refinements applied:
      - Wick miss prevention: tracks worst_price_seen in Redis position state
      - Funding rate drag: deducts 8h funding fee on held positions
      - Pending limit fill: activates PENDING orders when price crosses entry
    """

    def __init__(
        self,
        real_apm: object,
        shadow_executor: ShadowExecutor,
        redis_client: RedisClient,
        position_store: object,
        trade_store: object,
    ) -> None:
        self._apm = real_apm
        self._executor = shadow_executor
        self._redis = redis_client
        self._pos_store = position_store
        self._trade_store = trade_store
        self._regime_shift_counts: dict[str, int] = {}
        self._log = logger

    async def run(self) -> None:
        """Main monitoring loop — 2s interval."""
        logger.info("ShadowAPM: starting monitoring loop")
        while True:
            try:
                positions = await self._pos_store.list_all()
                for pos in positions:
                    await self._manage_shadow_position(pos)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as e:
                logger.warning(f"ShadowAPM: Redis transient error: {e}. Backing off 3s...")
                await asyncio.sleep(3)
            except Exception:
                logger.exception("ShadowAPM: error in monitoring loop")
                await asyncio.sleep(5)

    async def _manage_time_exit(
        self,
        pos: dict[str, Any],
        entry_time: datetime,
        max_minutes: int,
        live_price: Decimal,
        entry_price: Decimal,
        side: str,
        r_mult: Decimal = Decimal("0"),
    ) -> bool:
        held_mins = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60
        if max_minutes > 0 and held_mins > max_minutes:
            symbol = pos.get("symbol", "")
            self._log.warning(f"ShadowAPM: time exit {symbol} after {held_mins:.0f}min")
            await self._close_shadow_position(pos, live_price, f"time_exit_{held_mins:.0f}min")
            return True

        is_hyper = str(pos.get("regime", "")).startswith("HYPER")
        quick_profit_mins = 3 if is_hyper else 5
        quick_profit_r = Decimal("1.0") if is_hyper else Decimal("2.0")
        stag_mins = 3 if is_hyper else 5  # Reduced from 10 to 5 for faster exits
        stag_r = Decimal("0.5") if is_hyper else Decimal("0.2")

        # Quick Profit Exit
        if held_mins <= quick_profit_mins and r_mult >= quick_profit_r:
            symbol = pos.get("symbol", "")
            self._log.warning(f"ShadowAPM: QUICK PROFIT exit {symbol} after {held_mins:.0f}min (R={r_mult:.2f})")
            await self._close_shadow_position(pos, live_price, f"quick_profit_exit_R{r_mult:.1f}")
            return True

        # Stagnation Exit
        if held_mins >= stag_mins and r_mult < stag_r:
            symbol = pos.get("symbol", "")
            self._log.warning(f"ShadowAPM: STAGNATION exit {symbol} after {held_mins:.0f}min (R={r_mult:.2f})")
            await self._close_shadow_position(pos, live_price, f"stagnation_exit_{held_mins:.0f}min")
            return True

        # Underwater Stale Exit (15 mins)
        if held_mins >= 15:
            is_underwater = (side == "LONG" and live_price <= entry_price) or (side == "SHORT" and live_price >= entry_price)
            if is_underwater:
                symbol = pos.get("symbol", "")
                self._log.warning(f"ShadowAPM: stale underwater exit {symbol} after {held_mins:.0f}min")
                await self._close_shadow_position(pos, live_price, f"stale_exit_{held_mins:.0f}min")
                return True
        return False

    async def _manage_shadow_position(self, pos: dict) -> None:
        """Dispatch: pending fills first, then open position management."""
        status = pos.get("status", "OPEN")

        if status == "PENDING_VIRTUAL_FILL":
            await self._check_pending_fill(pos)
            return

        if status == "OPEN":
            await self._manage_open_position(pos)

    @staticmethod
    def _calculate_r_multiple(
        side: str, entry_price: Decimal, live_price: Decimal, initial_risk: Decimal
    ) -> Decimal:
        try:
            if initial_risk <= 0:
                return Decimal("0")
            if side == "LONG":
                return (live_price - entry_price) / initial_risk
            else:
                return (entry_price - live_price) / initial_risk
        except Exception:
            return Decimal("0")

    # --- Refinement 4: Pending limit fill detection ---

    async def _check_pending_fill(self, pos: dict) -> None:
        """Check if live price crossed virtual entry to fill pending limit."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))

        if not symbol or not side or entry_price <= 0:
            return

        # Expire stale pending orders
        pending_since = pos.get("pending_since", "")
        if pending_since:
            try:
                since = datetime.fromisoformat(pending_since)
                age = (datetime.now(timezone.utc) - since).total_seconds()
                if age > SHADOW_PENDING_TTL_SECS:
                    logger.info(
                        f"SHADOW PENDING EXPIRED: {symbol} {side} after {age:.0f}s"
                    )
                    metrics.karsa_shadow_limit_orders_unfilled_total.labels(
                        symbol=symbol
                    ).inc()
                    await self._pos_store.remove(symbol, side)
                    return
            except Exception:
                pass

        try:
            mid = await self._executor._get_mid_price(symbol)
        except ValueError:
            return

        # Long fills when price dips to entry; short fills when price rises
        filled = False
        if (
            side == "LONG"
            and mid <= entry_price
            or side == "SHORT"
            and mid >= entry_price
        ):
            filled = True

        if filled:
            logger.info(
                f"SHADOW PENDING FILLED: {symbol} {side} @ {entry_price} (mid={mid})"
            )
            pos["status"] = "OPEN"
            pos["worst_price_seen"] = str(entry_price)
            pos["last_funding_ts"] = datetime.now(timezone.utc).isoformat()
            key = self._pos_store._key(symbol, side)
            await self._redis.set(key, _json.dumps(pos))

    # --- Refinement 2: Wick miss prevention + SL detection ---

    async def _get_current_regime(self, symbol: str) -> str:
        """Fetch regime from Redis (written by RegimeEngine)."""
        raw = await self._redis.get(f"global:regime:{symbol}")
        if raw:
            try:
                data = _json.loads(raw)
                return data.get("regime", "")
            except Exception:
                pass
        return ""

    async def _manage_open_position(self, pos: dict) -> None:
        """Check live price against virtual SL, using worst_price_seen for wicks."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))
        virtual_sl = Decimal(pos.get("virtual_sl", "0"))
        entry_regime = pos.get("regime", "")

        if not symbol or not side or entry_price <= 0:
            return

        try:
            mid = await self._executor._get_mid_price(symbol)
        except ValueError:
            logger.debug(f"ShadowAPM: could not fetch price for {symbol}")
            return

        # Refinement 2: update worst_price_seen
        worst = Decimal(pos.get("worst_price_seen", str(mid)))
        if side == "LONG" and mid < worst or side == "SHORT" and mid > worst:
            worst = mid
        pos["worst_price_seen"] = str(worst)

        # Update peak for trailing stop logic
        await self._pos_store.update_peak(symbol, side, mid)
        peak = Decimal(str(pos.get("peak_price", mid)))

        key = self._pos_store._key(symbol, side)

        # --- Regime Shift Kill Switch ---
        current_regime = await self._get_current_regime(symbol)
        if current_regime and entry_regime and current_regime != entry_regime:
            self._regime_shift_counts[symbol] = (
                self._regime_shift_counts.get(symbol, 0) + 1
            )
            if self._regime_shift_counts[symbol] >= REGIME_SHIFT_CONFIRM_COUNT:
                logger.warning(
                    f"SHADOW KILL SWITCH: {symbol} regime shift {entry_regime} -> {current_regime}"
                )
                await self._close_shadow_position(
                    pos, mid, f"regime_shift_{entry_regime}_to_{current_regime}"
                )
                self._regime_shift_counts.pop(symbol, None)
                return
        else:
            self._regime_shift_counts.pop(symbol, None)

        # Calculate R-Multiple
        try:
            risk_profile = _json.loads(pos.get("risk_profile_json", "{}"))
            initial_risk_pct = Decimal(str(risk_profile.get("sl_pct", "0")))
            initial_risk = entry_price * initial_risk_pct
        except Exception:
            initial_risk = Decimal("0")

        r_multiple = self._calculate_r_multiple(side, entry_price, mid, initial_risk)

        is_hyper = str(entry_regime).startswith("HYPER")
        wick_long = Decimal("-0.015") if is_hyper else Decimal("-0.03")
        wick_short = Decimal("0.015") if is_hyper else Decimal("0.03")
        be_lock_r = Decimal("0.3") if is_hyper else APM_BREAKEVEN_LOCK_R
        step_r = Decimal("0.1") if is_hyper else Decimal("0.2")
        step_activate_r = Decimal("0.5") if is_hyper else Decimal("1.2")

        # Time management
        try:
            risk_profile = _json.loads(pos.get("risk_profile_json", "{}"))
            max_hold_mins = risk_profile.get("max_hold_time_mins", 0)
            entered_at = pos.get("entered_at", "")
            if entered_at:
                is_closed = await self._manage_time_exit(
                    pos,
                    datetime.fromisoformat(entered_at),
                    max_hold_mins,
                    mid,
                    entry_price,
                    side,
                    r_multiple
                )
                if is_closed:
                    return
        except Exception:
            pass

        # Flash-Crash Micro-Circuit Breaker (Wick Guard)
        _raw_last_tick = pos.get("last_tick_price", pos.get("worst_price_seen", "0")) or "0"
        last_tick_price = Decimal(str(_raw_last_tick))
        if last_tick_price > 0 and mid > 0:
            tick_delta = (mid - last_tick_price) / last_tick_price
            if (side == "LONG" and tick_delta <= wick_long) or (side == "SHORT" and tick_delta >= wick_short):
                logger.critical(
                    f"SHADOW WICK GUARD: {symbol} {side} dropped/spiked {tick_delta:.2%} instantly! "
                    f"live={mid} last={last_tick_price}. FRONT-RUNNING CASCADE!"
                )
                await self._close_shadow_position(pos, mid, f"wick_guard_{tick_delta:.2%}")
                return

        pos["last_tick_price"] = str(mid)

        # --- +1R Breakeven Lock ---
        if not pos.get("moved_to_breakeven") and r_multiple >= be_lock_r:
            if side == "LONG":
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT
            else:
                # SHORT: SL must be BELOW entry to lock in breakeven
                new_sl = entry_price - entry_price * APM_BREAKEVEN_FEE_PCT
            pos["virtual_sl"] = str(new_sl)
            pos["moved_to_breakeven"] = True
            virtual_sl = new_sl
            logger.info(
                f"SHADOW BREAKEVEN LOCK: {symbol} {side} SL moved to {new_sl} (R={r_multiple:.2f})"
            )

        # --- HFT Step-Trailing Stop ---
        if pos.get("moved_to_breakeven") and initial_risk > 0 and r_multiple >= step_activate_r:
            floored_r = (r_multiple // step_r) * step_r
            trail_r = floored_r - (step_activate_r - step_r)
            if trail_r > 0:
                if side == "LONG":
                    new_step_sl = entry_price + (initial_risk * trail_r)
                else:
                    new_step_sl = entry_price - (initial_risk * trail_r)

                sl_tighter = (side == "LONG" and new_step_sl > virtual_sl) or (side == "SHORT" and (new_step_sl < virtual_sl or virtual_sl == 0))
                if sl_tighter:
                    pos["virtual_sl"] = str(new_step_sl)
                    virtual_sl = new_step_sl
                    logger.info(f"SHADOW STEP-TRAILING: {symbol} {side} SL amended to {new_step_sl} (+{trail_r}R)")

        # --- Trend Trailing Stop (Chandelier ATR) ---
        if (
            entry_regime in ("TREND_BULL", "TREND_BEAR")
            and r_multiple >= APM_TREND_TRAIL_ACTIVATE_R
        ):
            atr = Decimal(str(pos.get("atr", "0")))
            if atr > 0:
                trail_distance = atr * APM_TREND_TRAIL_ATR_MULT
                if side == "LONG":
                    new_sl = peak - trail_distance
                    if new_sl > virtual_sl:
                        pos["virtual_sl"] = str(new_sl)
                        virtual_sl = new_sl
                        logger.info(
                            f"SHADOW TRAILING STOP: {symbol} {side} SL raised to {new_sl} (Peak={peak})"
                        )
                else:
                    new_sl = peak + trail_distance
                    if new_sl < virtual_sl or virtual_sl == 0:
                        pos["virtual_sl"] = str(new_sl)
                        virtual_sl = new_sl
                        logger.info(
                            f"SHADOW TRAILING STOP: {symbol} {side} SL lowered to {new_sl} (Peak={peak})"
                        )

        # Persist state back to Redis
        await self._redis.set(key, _json.dumps(pos))

        # --- $5 hard cap: if current SL would lose > $5, tighten SL to -$5 level ---
        amount = Decimal(pos.get("amount", "0"))
        if amount > 0 and virtual_sl > 0:
            if side == "LONG":
                sl_loss = (virtual_sl - entry_price) * amount
            else:
                sl_loss = (entry_price - virtual_sl) * amount
            if sl_loss < -5:
                # Current SL too loose — tighten to exactly -$5 loss
                if side == "LONG":
                    new_sl = entry_price - Decimal("5") / amount
                else:
                    new_sl = entry_price + Decimal("5") / amount
                pos["virtual_sl"] = str(new_sl)
                await self._redis.set(key, _json.dumps(pos))
                logger.warning(
                    f"SHADOW $5 CAP: {symbol} {side} SL tightened "
                    f"from {virtual_sl} to {new_sl} (was ${sl_loss:.4f} loss)"
                )

        # Stale position cleanup: no SL + older than 4h → auto-close
        if virtual_sl <= 0:
            try:
                entered_at = pos.get("entered_at", "")
                if entered_at:


                    entered = datetime.fromisoformat(entered_at)
                    if entered.tzinfo is None:
                        entered = entered.replace(tzinfo=timezone.utc)
                    age_mins = (datetime.now(timezone.utc) - entered).total_seconds() / 60
                    if age_mins >= 240:
                        logger.warning(
                            "SHADOW STALE CLEANUP: %s %s held %.0fm with no SL",
                            symbol,
                            side,
                            age_mins,
                        )
                        metrics.karsa_shadow_stale_cleanups_total.labels(
                            symbol=symbol, side=side
                        ).inc()
                        await self._close_shadow_position(pos, mid, "stale_cleanup")
                        return
            except Exception:
                pass

        # Refinement 3: funding rate drag
        await self._apply_funding_if_due(pos, symbol, side)

        # SL hit detection using worst_price_seen (catches wicks)
        if virtual_sl > 0:
            sl_hit = False
            if side == "LONG" and worst <= virtual_sl:
                sl_hit = True
            elif side == "SHORT" and worst >= virtual_sl:
                sl_hit = True

            if sl_hit:
                logger.warning(
                    f"SHADOW SL HIT: {symbol} {side} worst={worst} <= sl={virtual_sl}"
                )
                metrics.karsa_shadow_sl_hits_total.labels(
                    symbol=symbol, side=side
                ).inc()
                await self._close_shadow_position(pos, virtual_sl, "sl_hit")
                return

        # TP hit detection
        virtual_tp = Decimal(pos.get("virtual_tp", "0"))
        if virtual_tp > 0:
            tp_hit = False
            if side == "LONG" and virtual_tp > entry_price and mid >= virtual_tp:
                tp_hit = True
            elif side == "SHORT" and virtual_tp < entry_price and mid <= virtual_tp:
                tp_hit = True

            if tp_hit:
                logger.info(
                    f"SHADOW TP HIT: {symbol} {side} mid={mid} >= tp={virtual_tp}"
                )
                metrics.karsa_shadow_tp_hits_total.labels(
                    symbol=symbol, side=side
                ).inc()
                await self._close_shadow_position(pos, virtual_tp, "tp_hit")
                return

    # --- Refinement 3: Funding rate drag ---

    async def _apply_funding_if_due(self, pos: dict, symbol: str, side: str) -> None:
        """Deduct 8h funding fee from virtual PnL if funding interval elapsed."""
        last_funding_ts_str = pos.get("last_funding_ts", "")
        if not last_funding_ts_str:
            return

        try:
            last_funding = datetime.fromisoformat(last_funding_ts_str)
        except Exception:
            return

        now = datetime.now(timezone.utc)
        elapsed = now - last_funding

        if elapsed < timedelta(hours=SHADOW_FUNDING_INTERVAL_HOURS):
            return

        # Fetch current funding rate from Redis
        funding_rate = await self._get_funding_rate(symbol)
        if funding_rate <= 0:
            pos["last_funding_ts"] = now.isoformat()
            return

        # Funding fee = position_notional * funding_rate
        amount = Decimal(pos.get("amount", "0"))
        entry_price = Decimal(pos.get("entry_price", "0"))
        notional = entry_price * amount

        if side == "LONG":
            funding_fee = notional * funding_rate
        else:
            funding_fee = notional * funding_rate * Decimal("-1")

        existing_fees = Decimal(pos.get("total_funding_fees", "0"))
        pos["total_funding_fees"] = str(existing_fees + funding_fee)
        pos["last_funding_ts"] = now.isoformat()

        key = self._pos_store._key(symbol, side)
        await self._redis.set(key, _json.dumps(pos))

        if funding_fee > 0:
            logger.info(
                f"SHADOW FUNDING: {symbol} {side} fee={funding_fee:.4f} "
                f"(rate={funding_rate}, notional={notional:.2f})"
            )
            metrics.karsa_shadow_funding_fees_total_usdt.inc(float(funding_fee))

    async def _get_funding_rate(self, symbol: str) -> Decimal:
        """Read funding rate from Redis. Returns 0 if unavailable."""
        raw = await self._redis.get(f"funding:{symbol}")
        if raw:
            try:
                data = _json.loads(raw)
                return Decimal(str(data.get("funding_rate", "0")))
            except Exception:
                pass
        return Decimal("0")

    # --- Close shadow position ---

    async def _close_shadow_position(
        self, pos: dict, exit_price: Decimal, reason: str
    ) -> None:
        """Record virtual close trade in shadow_trades table."""
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        entry_price = Decimal(pos.get("entry_price", "0"))
        amount = Decimal(pos.get("amount", "0"))

        if side in ("buy", "LONG"):
            pnl = (exit_price - entry_price) * amount
        else:
            pnl = (entry_price - exit_price) * amount

        # Fee asymmetry: entry fee depends on order type
        settings = get_settings()
        entry_fee_rate = (
            Decimal(settings.shadow_maker_fee_pct)
            if pos.get("fee_type") == "maker"
            else Decimal(settings.shadow_taker_fee_pct)
        )
        exit_fee_rate = Decimal(settings.shadow_taker_fee_pct)

        entry_fee = entry_price * amount * entry_fee_rate
        exit_fee = exit_price * amount * exit_fee_rate
        total_fees = entry_fee + exit_fee

        # Refinement 3: add accumulated funding fees
        total_funding = Decimal(pos.get("total_funding_fees", "0"))
        net_pnl = pnl - total_fees - total_funding

        logger.info(
            f"SHADOW CLOSE: {symbol} {side} entry={entry_price} "
            f"exit={exit_price} pnl={pnl:.2f} fees={total_fees:.2f} "
            f"funding={total_funding:.4f} net={net_pnl:.2f} reason={reason}"
        )

        metrics.karsa_shadow_pnl_usdt.observe(float(net_pnl))
        metrics.karsa_shadow_fees_total_usdt.inc(float(total_fees))

        try:
            await self._trade_store.close_trade(
                symbol=symbol,
                exit_price=exit_price,
                exit_reason=reason,
                pnl=net_pnl,
            )
        except Exception as e:
            logger.error(f"ShadowAPM: failed to close shadow trade: {e}")

        await self._pos_store.remove(symbol, side)
